from __future__ import annotations

import asyncio
import os
import pwd

from itertools import count
from json import JSONDecodeError
from socket import AF_INET
from typing import Any, Generator, Literal, AsyncGenerator
from pathlib import Path
import aiofiles
from yarl import URL

from aiohttp import (
    BasicAuth as _BasicAuth,
)
from aiohttp import (
    ClientConnectorError,
    ClientOSError,
    ClientSession,
    ClientTimeout,
    ConnectionTimeoutError,
    ContentTypeError,
    TCPConnector,
    ClientResponse,
    ServerDisconnectedError,
)

from arcane_mage.log import log


class BasicAuth(_BasicAuth):
    def __rich_repr__(self) -> Generator[tuple[str, str], None, None]:
        yield "login", self.login
        yield "password", "*****"
        yield "encoding", self.encoding


class ExecBinaryError(ChildProcessError):
    def __init__(self, cmd: list[str], stdout: bytes, stderr: bytes):
        self.__cmd = cmd
        self.__stderr = stderr
        super().__init__([cmd, (stdout, stderr)])

    def stderr(self):
        return self.__stderr.decode(errors="ignore")

    def __str__(self):
        return "Failed to execute '{}', error: {}".format(
            " ".join(self.__cmd), self.stderr()
        )


def demote(user_uid, user_gid):
    def result():
        os.setgid(user_gid)
        os.setuid(user_uid)

    return result


async def exec_binary(
    cmd: list[str],
    expect_returncode: int = 0,
    cwd: str | None = None,
    user: str | None = None,
    env: dict | None = None,
) -> str:
    if len(cmd) == 0:
        raise ChildProcessError("Cannot execute empty cmd")

    extra_params = {}

    if cwd is not None:
        extra_params["cwd"] = cwd

    if user is not None:
        try:
            pw_record = pwd.getpwnam(user)
        except KeyError:
            raise ChildProcessError("User not found")

        extra_params["preexec_fn"] = demote(pw_record.pw_uid, pw_record.pw_gid)

    if env:
        extra_params["env"] = env

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **extra_params,
        )
    except FileNotFoundError:
        raise ChildProcessError(f"Binary not found: {cmd[0]}, is it installed?")

    try:
        stdout, stderr = await proc.communicate()
        if proc.returncode != expect_returncode:
            raise ExecBinaryError(cmd, stdout, stderr)
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.terminate()
            return_code = await proc.wait()
            log.info(
                "Cmd: %s cancelled and terminated. Exit code: %s",
                cmd[0],
                return_code,
            )
        raise asyncio.CancelledError()

    return stdout.decode()


async def do_http(
    url: URL | str,
    verb: Literal["get", "post", "head"] = "get",
    *,
    data: Any = None,
    connect_timeout: int = 3,
    max_tries: int = 3,
    retry_interval: int = 1,
    credentials: list[str] | None = None,
    headers: dict | None = None,
    verify_ssl: bool | None = None,
    total_timeout: int | None = None,
) -> Any:
    timeout = ClientTimeout(connect=connect_timeout, total=total_timeout)
    conn = TCPConnector(family=AF_INET)

    auth = None
    res = None

    if credentials:
        # user password
        auth = BasicAuth(*credentials)

    async with ClientSession(
        connector=conn,
    ) as session:
        for attempt in count(start=1):
            if max_tries and attempt > max_tries:
                break

            remaining = max(0, max_tries - attempt)

            try:
                method = getattr(session, verb)
                async with method(
                    url,
                    timeout=timeout,
                    auth=auth,
                    headers=headers,
                    json=data,
                    ssl=verify_ssl,
                ) as resp:
                    resp: ClientResponse  # type: ignore
                    if resp.status in [429, 500, 502, 503, 504]:
                        log.debug("bad response: %s", resp.status)
                        if remaining or not max_tries:
                            await asyncio.sleep(retry_interval)

                        continue

                    if resp.status in [401, 403, 404]:
                        log.info("URL: %s, status: %s", url, resp.status)
                        return None

                    if verb == "head":
                        return resp.headers

                    print(await resp.read())

                    try:
                        res = await resp.json()
                    except (
                        JSONDecodeError,
                        ContentTypeError,
                        UnicodeDecodeError,
                    ):
                        res = None
                    break

            except (
                ClientConnectorError,
                ConnectionTimeoutError,
                ClientOSError,
                ServerDisconnectedError,
                asyncio.TimeoutError,
            ):
                # ClientOSError: have seen Connection reset by peer
                log.info(
                    "Unable to connect to: %s. Retries: %s remaining. Interval: %ss",
                    url,
                    remaining,
                    retry_interval,
                )

                if remaining or not max_tries:
                    await asyncio.sleep(retry_interval)

    return res


async def do_http_iter(
    url: str,
    verb: str = "get",
    *,
    data: Any = None,
    connect_timeout: int = 3,
    read_timeout: int = 10,
    max_tries: int = 3,
    retry_interval: int = 1,
    credentials: list | None = None,
    headers: dict | None = None,
    verify_ssl: bool | None = None,
) -> Any | AsyncGenerator:
    timeout = ClientTimeout(connect=connect_timeout, sock_read=read_timeout)
    conn = TCPConnector(family=AF_INET)

    auth = None
    ssl = None

    if credentials:
        # user password
        auth = BasicAuth(*credentials)

    if verify_ssl is not None:
        ssl = verify_ssl

    async with ClientSession(
        connector=conn,
    ) as session:
        for attempt in count(start=1):
            if max_tries and attempt > max_tries:
                break

            remaining = max(0, max_tries - attempt)

            try:
                method = getattr(session, verb)
                async with method(
                    url,
                    timeout=timeout,
                    auth=auth,
                    headers=headers,
                    json=data,
                    ssl=ssl,
                ) as resp:
                    resp: ClientResponse

                    if resp.status in [429, 500, 502, 503, 504]:
                        log.warning(
                            "URL: %s, Status: %s... retrying", url, resp.status
                        )
                        if remaining or not max_tries:
                            await asyncio.sleep(retry_interval)

                        continue

                    if resp.status in [401, 403]:
                        break

                    # This header must always be present
                    content_length = int(resp.headers.get("Content-Length"))
                    bytes_downloaded = 0
                    percent_logged = 0

                    async for chunk, _ in resp.content.iter_chunks():
                        bytes_downloaded += len(chunk)
                        percent = int((bytes_downloaded / content_length) * 100)
                        if percent > percent_logged and percent % 5 == 0:
                            percent_logged = percent
                            log.info("URL: %s, %s%% downloaded", url, percent)

                        yield chunk

                    break

            except (
                ClientConnectorError,
                ConnectionTimeoutError,
                ClientOSError,
                ServerDisconnectedError,
                asyncio.TimeoutError,
            ):
                log.warning(
                    "Unable to connect to: %s. Retries: %s remaining. Interval: %ss",
                    url,
                    remaining,
                    retry_interval,
                )

                if remaining or not max_tries:
                    await asyncio.sleep(retry_interval)


async def do_http_to_file(
    url: URL | str,
    dest_path: Path,
    connect_timeout: int = 3,
    read_timeout: int = 15,
) -> bool:
    timeout = ClientTimeout(connect=connect_timeout, sock_read=read_timeout)
    conn = TCPConnector(family=AF_INET)

    async with ClientSession(
        connector=conn,
    ) as session:
        try:
            file_handler = await aiofiles.open(dest_path, "wb")
        except PermissionError:
            return False

        try:
            async with session.get(url, timeout=timeout) as resp:
                resp: ClientResponse

                if resp.status != 200:
                    log.warning("Bad response received: %s", resp)

                    return False

                async for chunk in resp.content.iter_chunked(16777216):  # 16Mib
                    await file_handler.write(chunk)
        except (
            ClientConnectorError,
            ConnectionTimeoutError,
            ClientOSError,
            ServerDisconnectedError,
            asyncio.TimeoutError,
        ):
            # ClientOSError: have seen Connection reset by peer
            log.info(
                "Unable to connect to: %s",
                url,
            )
            return False

        finally:
            await file_handler.close()

        return True
