from __future__ import annotations

import asyncio
import json
import urllib.parse
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path
from socket import AF_INET
from time import monotonic
from typing import Any, AsyncIterator, Literal

import aiohttp
from aiohttp import (
    ClientError,
    ClientResponseError,
    ClientTimeout,
    ConnectionTimeoutError,
    ContentTypeError,
    TCPConnector,
)


@dataclass
class ApiResponse:
    status: int | None = None
    payload: Any = None
    timed_out: bool = False
    error: str | None = None

    @property
    def unauthorized(self) -> bool:
        return self.status == 401

    def __bool__(self) -> bool:
        return self.status == 200 and not self.error


class ProxmoxApi:
    @classmethod
    async def from_user_pass(
        cls, url: str, user: str, password: str
    ) -> ProxmoxApi | None:
        ticket_url = f"{url}/api2/json/access/ticket"
        payload = {"username": f"{user}@pam", "password": password}

        conn = TCPConnector(family=AF_INET)
        timeout = ClientTimeout(connect=2)

        try:
            async with aiohttp.ClientSession(
                connector=conn, timeout=timeout
            ) as client:
                res = await client.post(ticket_url, data=payload, ssl=False)
        except ClientError:
            return None

        try:
            res.raise_for_status()
        except ClientResponseError:
            return None

        try:
            data: dict = await res.json()
        except (json.JSONDecodeError, ContentTypeError):
            return None

        try:
            ticket = data["data"]["ticket"]
        except KeyError:
            return None

        if not ticket:
            return None

        try:
            csrf = data["data"]["CSRFPreventionToken"]
        except KeyError:
            return None

        if not csrf:
            return None

        client = cls.build_password_client(url, ticket, csrf)

        return cls(auth_type="userpass", client=client)

    @classmethod
    def parse_user_pass(cls, user_pass: str) -> tuple[str, str] | None:
        try:
            user, password = user_pass.split(":")
        except ValueError:
            return None

        # normalize, we add it back on later
        user = user.removesuffix("@pam")

        return user, password

    @classmethod
    def parse_token(cls, token: str) -> tuple[str, str, str] | None:
        try:
            user, token_parts = token.split("!")
        except ValueError:
            return None

        try:
            token_name, token_value = token_parts.split("=")
        except ValueError:
            return None

        return user, token_name, token_value

    @classmethod
    def from_token(
        cls,
        url: str,
        user: str,
        token_name: str,
        token_value: str,
    ) -> ProxmoxApi:
        client = cls.build_token_client(url, user, token_name, token_value)

        return cls(auth_type="token", client=client)

    @staticmethod
    def build_password_client(
        url: str, ticket: str, csrf_token: str
    ) -> aiohttp.ClientSession:
        cookies = {"PVEAuthCookie": ticket}
        headers = {"CSRFPreventionToken": csrf_token}

        jar = aiohttp.CookieJar(quote_cookie=False)
        jar.update_cookies(cookies)

        return aiohttp.ClientSession(
            base_url=f"{url}/api2/json/",
            cookie_jar=jar,
            cookies=cookies,
            headers=headers,
        )

    @staticmethod
    def build_token_client(
        url: str, user: str, token_name: str, token_value: str
    ) -> aiohttp.ClientSession:
        headers = {
            "Authorization": f"PVEAPIToken={user}!{token_name}={token_value}"
        }
        conn = TCPConnector(family=AF_INET)
        timeout = ClientTimeout(connect=2)

        return aiohttp.ClientSession(
            base_url=f"{url}/api2/json/",
            connector=conn,
            headers=headers,
            timeout=timeout,
        )

    @staticmethod
    async def handle_api_response(
        response: aiohttp.ClientResponse,
    ) -> ApiResponse:
        try:
            response.raise_for_status()
        except ClientResponseError as e:
            return ApiResponse(status=e.status, error=e.message)

        try:
            payload: dict = await response.json()
        except (json.JSONDecodeError, ContentTypeError) as e:
            return ApiResponse(status=response.status, error=str(e))

        data = payload.get("data", None)
        message = payload.get("message", None)

        return ApiResponse(status=response.status, payload=data, error=message)

    def __init__(
        self,
        auth_type: Literal["token", "userpass"],
        client: aiohttp.ClientSession,
    ) -> None:
        self.type = auth_type
        self.client = client

    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await self.close()

    async def close(self) -> None:
        await self.client.close()

    async def do_http(
        self,
        verb: Literal["get", "put", "post", "delete"],
        path: str,
        data: Any = None,
    ) -> ApiResponse:
        method = getattr(self.client, verb)

        try:
            res = await method(path, ssl=False, data=data)
        except ConnectionTimeoutError:
            return ApiResponse(timed_out=True)

        data = await self.handle_api_response(res)

        print(res.url, data)

        return data

    async def do_get(self, path: str) -> ApiResponse:
        data = await self.do_http("get", path)

        return data

    async def do_post(self, path: str, data: Any = None) -> ApiResponse:
        data = await self.do_http("post", path, data)

        return data

    async def do_put(self, path: str, data: Any = None) -> ApiResponse:
        data = await self.do_http("put", path, data)

        return data

    async def do_delete(self, path: str) -> ApiResponse:
        data = await self.do_http("delete", path)

        return data

    async def get_api_version(self, node: str) -> ApiResponse:
        res = await self.do_get(f"nodes/{node}/version")

        return res

    async def get_cluster_nodes(self) -> ApiResponse:
        res = await self.do_get("cluster/config/nodes")

        return res

    async def get_hypervisor_nodes(self) -> ApiResponse:
        res = await self.do_get("nodes")

        return res

    async def get_networks(self, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/network"

        res = await self.do_get(endpoint)

        return res

    async def get_storage_state(self, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/storage"

        res = await self.do_get(endpoint)

        return res

    async def get_storage_content(
        self, node: str, location: str
    ) -> ApiResponse:
        endpoint = f"nodes/{node}/storage/{location}/content"

        res = await self.do_get(endpoint)

        return res

    async def get_storage_config(self) -> ApiResponse:
        res = await self.do_get("storage")

        return res

    async def set_storage_content(
        self, storage_id: str, content: str
    ) -> ApiResponse:
        endpoint = f"storage/{storage_id}"
        data = {"content": content}

        res = await self.do_put(endpoint, data=data)

        return res

    async def download_iso(
        self, url: str, filename: str, storage: str, verify_certs: bool = True
    ) -> ApiResponse:
        data = {
            "content": "iso",
            "filename": filename,
            "storage": storage,
            "url": url,
            "verify-certificates": verify_certs,
        }

        res = await self.do_post(url, data=data)

        return res

    async def get_next_id(self) -> ApiResponse:
        res = await self.do_get("cluster/nextid")

        return res

    async def create_vm(self, config: dict, node: str) -> ApiResponse:
        res = await self.do_post(f"nodes/{node}/qemu", data=config)

        return res

    async def start_vm(self, vm_id: int, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/qemu/{vm_id}/status/start"

        res = await self.do_post(endpoint)

        return res

    async def get_task(self, task_id: str, node: str) -> ApiResponse:
        quoted_task = urllib.parse.quote(task_id)
        endpoint = f"nodes/{node}/tasks/{quoted_task}/status"

        res = await self.do_get(endpoint)

        return res

    async def wait_for_task(
        self, task_id: str, node: str, max_wait_s: int = 10
    ) -> bool:
        task_res = await self.get_task(task_id, node)

        if not task_res:
            return False

        exit_status = task_res.payload.get("exitstatus")
        # we start the timer here, so we don't include the time it took
        # to get the first api request
        start = monotonic()
        elapsed = 0.0

        while exit_status != "OK" and elapsed < max_wait_s:
            await asyncio.sleep(1)

            task_res = await self.get_task(task_id, node)

            if not task_res:
                return False

            exit_status = task_res.payload.get("exitstatus")
            elapsed = monotonic() - start

        return exit_status == "OK"

    async def delete_file(
        self, file_name: str, node: str, storage: str, content: str
    ) -> ApiResponse:
        volume = f"{storage}:{content}/{file_name}"
        endpoint = f"nodes/{node}/storage/{storage}/content/{volume}"

        res = await self.do_delete(endpoint)

        return res

    async def upload_file(
        self,
        file: Path | bytes,
        node: str,
        storage: str,
        file_name: str | None = None,
    ) -> ApiResponse:
        endpoint = f"nodes/{node}/storage/{storage}/upload"
        payload: BufferedReader | bytes

        if isinstance(file, Path):
            payload = open(file, "rb")
        elif (is_bytes := isinstance(file, bytes)) and not file_name:
            return ApiResponse(error="Unuseable file")
        elif is_bytes:
            payload = file
        else:
            return ApiResponse(error="Unuseable file")

        # Why proxmox does this is beyond me. This took hours to figure out. Had to
        # packet capture and decode the ssl traffic with wireshark. The multipart data
        # has to be in this specific order, as well as the headers have to be in order.
        with aiohttp.MultipartWriter("form-data") as mpwriter:
            content_part = mpwriter.append(
                "import".encode("utf-8"),
            )
            content_part.set_content_disposition("form-data", name="content")

            file_part = mpwriter.append(
                payload,
            )

            # The name=filename must be present
            file_part.set_content_disposition(
                "form-data", name="filename", filename=file_name or file.name
            )

            for part in mpwriter:
                # this is because proxmox is insane, the content-disposition header
                # must be before the content-type. Absolutely cooked.
                content_type = part[0].headers.pop(aiohttp.hdrs.CONTENT_TYPE)
                part[0].headers[aiohttp.hdrs.CONTENT_TYPE] = content_type

            res = await self.do_post(
                endpoint,
                data=mpwriter,
            )

        return res

    async def get_vms(self, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/qemu"

        res = await self.do_get(endpoint)

        return res

    async def get_vm(self, vm_id: int, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/qemu/{vm_id}/config"

        res = await self.do_get(endpoint)

        return res
