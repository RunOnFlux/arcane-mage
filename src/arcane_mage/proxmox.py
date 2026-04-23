from __future__ import annotations

import asyncio
import contextlib
import json
import types
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path
from socket import AF_INET
from time import monotonic
from typing import Literal

import aiohttp
from aiohttp import (
    ClientError,
    ClientResponseError,
    ClientTimeout,
    ConnectionTimeoutError,
    ContentTypeError,
    TCPConnector,
)

# Concrete type for HTTP request data passed to aiohttp methods
type HttpData = dict | aiohttp.MultipartWriter | None


@dataclass(frozen=True)
class ParsedToken:
    """Parsed Proxmox API token."""

    user: str
    token_name: str
    token_value: str

    @property
    def username(self) -> str:
        """The user part without the realm, e.g. 'davew' from 'davew@pam'."""
        return self.user.partition("@")[0] or self.user


@dataclass(frozen=True)
class ParsedUserPass:
    """Parsed Proxmox user/password credential."""

    user: str
    password: str

    @property
    def username(self) -> str:
        """The user part without the realm."""
        return self.user.partition("@")[0] or self.user


@dataclass(frozen=True)
class ResolvedConnection:
    """Resolved Proxmox connection details (URL + parsed token)."""

    url: str
    token: ParsedToken


@dataclass
class ApiResponse:
    """Response wrapper for Proxmox API calls, with status, payload, and error info."""

    status: int | None = None
    payload: dict | list | str | None = None
    timed_out: bool = False
    error: str | None = None

    @property
    def unauthorized(self) -> bool:
        return self.status == 401

    def __bool__(self) -> bool:
        return self.status == 200 and not self.error


class ProxmoxApi:
    """Async client for the Proxmox VE API, supporting token and user/password auth."""

    @classmethod
    async def from_user_pass(
        cls, url: str, credentials: ParsedUserPass, *, verify_ssl: bool = False
    ) -> ProxmoxApi | None:
        """Authenticate with username/password and return an API client, or None on failure."""
        ticket_url = f"{url}/api2/json/access/ticket"
        payload = {"username": f"{credentials.user}@pam", "password": credentials.password}

        conn = TCPConnector(family=AF_INET)
        timeout = ClientTimeout(connect=2)

        try:
            async with aiohttp.ClientSession(
                connector=conn, timeout=timeout
            ) as client:
                res = await client.post(ticket_url, data=payload, ssl=verify_ssl)
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

        return cls(auth_type="userpass", client=client, verify_ssl=verify_ssl)

    @classmethod
    def parse_user_pass(cls, user_pass: str) -> ParsedUserPass | None:
        try:
            user, password = user_pass.split(":")
        except ValueError:
            return None

        # normalize, we add it back on later
        user = user.removesuffix("@pam")

        return ParsedUserPass(user, password)

    @classmethod
    def parse_token(cls, token: str) -> ParsedToken | None:
        try:
            user, token_parts = token.split("!")
        except ValueError:
            return None

        try:
            token_name, token_value = token_parts.split("=")
        except ValueError:
            return None

        return ParsedToken(user, token_name, token_value)

    @classmethod
    def from_token(
        cls,
        url: str,
        token: ParsedToken,
        *,
        verify_ssl: bool = False,
    ) -> ProxmoxApi:
        """Create an API client using a PVE API token."""
        client = cls.build_token_client(url, token.user, token.token_name, token.token_value)

        return cls(auth_type="token", client=client, verify_ssl=verify_ssl)

    @staticmethod
    def build_password_client(
        url: str, ticket: str, csrf_token: str
    ) -> aiohttp.ClientSession:
        cookies = {"PVEAuthCookie": ticket}
        headers = {"CSRFPreventionToken": csrf_token}

        jar = aiohttp.CookieJar(quote_cookie=False)
        jar.update_cookies(cookies)

        conn = TCPConnector(family=AF_INET)
        timeout = ClientTimeout(connect=2)

        return aiohttp.ClientSession(
            base_url=f"{url}/api2/json/",
            connector=conn,
            cookie_jar=jar,
            cookies=cookies,
            headers=headers,
            timeout=timeout,
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

        data = payload.get("data")
        message = payload.get("message")

        return ApiResponse(status=response.status, payload=data, error=message)

    def __init__(
        self,
        auth_type: Literal["token", "userpass"],
        client: aiohttp.ClientSession,
        *,
        verify_ssl: bool = False,
    ) -> None:
        self.auth_type = auth_type
        self.client = client
        self.verify_ssl = verify_ssl

    async def __aenter__(self) -> ProxmoxApi:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        await self.close()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await self.close()

    async def close(self) -> None:
        await self.client.close()

    async def _do_http(
        self,
        verb: Literal["get", "put", "post", "delete"],
        path: str,
        data: HttpData = None,
    ) -> ApiResponse:
        method = getattr(self.client, verb)

        try:
            res = await method(path, ssl=self.verify_ssl, data=data)
        except ConnectionTimeoutError:
            return ApiResponse(timed_out=True)
        except ClientError:
            return ApiResponse(error="Connection Error")

        return await self.handle_api_response(res)

    async def _do_get(self, path: str) -> ApiResponse:
        return await self._do_http("get", path)

    async def _do_post(self, path: str, data: HttpData = None) -> ApiResponse:
        return await self._do_http("post", path, data)

    async def _do_put(self, path: str, data: HttpData = None) -> ApiResponse:
        return await self._do_http("put", path, data)

    async def _do_delete(self, path: str) -> ApiResponse:
        return await self._do_http("delete", path)

    async def get_api_version(self, node: str) -> ApiResponse:
        res = await self._do_get(f"nodes/{node}/version")

        return res

    async def get_cluster_nodes(self) -> ApiResponse:
        res = await self._do_get("cluster/config/nodes")

        return res

    async def get_cluster_status(self) -> ApiResponse:
        """GET /cluster/status — cluster name, quorum, node membership."""
        res = await self._do_get("cluster/status")

        return res

    async def get_cluster_resources(
        self, resource_type: str | None = None
    ) -> ApiResponse:
        """GET /cluster/resources — cluster-wide VM/storage/node list."""
        path = "cluster/resources"
        if resource_type:
            path += f"?type={resource_type}"

        res = await self._do_get(path)

        return res

    async def get_hypervisor_nodes(self) -> ApiResponse:
        res = await self._do_get("nodes")

        return res

    async def get_networks(self, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/network"

        res = await self._do_get(endpoint)

        return res

    async def get_storage_state(self, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/storage"

        res = await self._do_get(endpoint)

        return res

    async def get_storage_content(
        self, node: str, location: str
    ) -> ApiResponse:
        endpoint = f"nodes/{node}/storage/{location}/content"

        res = await self._do_get(endpoint)

        return res

    async def get_storage_config(self) -> ApiResponse:
        res = await self._do_get("storage")

        return res

    async def set_storage_content(
        self, storage_id: str, content: str
    ) -> ApiResponse:
        endpoint = f"storage/{storage_id}"
        data = {"content": content}

        res = await self._do_put(endpoint, data=data)

        return res

    async def download_iso(
        self, node: str, url: str, filename: str, storage: str, verify_certs: bool = True
    ) -> ApiResponse:
        endpoint = f"nodes/{node}/storage/{storage}/download-url"
        data = {
            "content": "iso",
            "filename": filename,
            "url": url,
            "verify-certificates": verify_certs,
        }

        res = await self._do_post(endpoint, data=data)

        return res

    async def get_next_id(self) -> ApiResponse:
        res = await self._do_get("cluster/nextid")

        return res

    async def create_vm(self, config: dict, node: str) -> ApiResponse:
        res = await self._do_post(f"nodes/{node}/qemu", data=config)

        return res

    async def start_vm(self, vm_id: int, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/qemu/{vm_id}/status/start"

        res = await self._do_post(endpoint)

        return res

    async def get_task(self, task_id: str, node: str) -> ApiResponse:
        # UPID format: "UPID:<node>:<pid>:<pstart>:<starttime>:<type>:<id>:<user>:"
        # The task runs on whichever node's pveproxy accepted the request, which
        # in cluster setups may differ from the caller's target node (e.g. uploads
        # run on the connection node, not hv.node). Extract the real task node
        # from the UPID so the status endpoint path matches.
        upid_parts = task_id.split(":")
        if len(upid_parts) >= 2 and upid_parts[0] == "UPID" and upid_parts[1]:
            node = upid_parts[1]

        quoted_task = urllib.parse.quote(task_id)
        endpoint = f"nodes/{node}/tasks/{quoted_task}/status"

        res = await self._do_get(endpoint)

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

        res = await self._do_delete(endpoint)

        return res

    async def upload_file(
        self,
        file: Path | bytes,
        node: str,
        storage: str,
        file_name: str | None = None,
    ) -> ApiResponse:
        endpoint = f"nodes/{node}/storage/{storage}/upload"

        if isinstance(file, bytes) and not file_name:
            return ApiResponse(error="Unuseable file")

        # aiohttp's MultipartWriter.append() requires a synchronous file-like
        # object, so we open with the stdlib and manage the handle with ExitStack
        # to guarantee cleanup regardless of which code path we take.
        with contextlib.ExitStack() as stack:
            if isinstance(file, Path):
                payload: BufferedReader | bytes = stack.enter_context(open(file, "rb"))
                resolved_name = file_name or file.name
            elif isinstance(file, bytes):
                payload = file
                resolved_name = file_name  # already validated non-None above
            else:
                return ApiResponse(error="Unuseable file")

            # Why proxmox does this is beyond me. This took hours to figure out. Had to
            # packet capture and decode the ssl traffic with wireshark. The multipart data
            # has to be in this specific order, as well as the headers have to be in order.
            with aiohttp.MultipartWriter("form-data") as mpwriter:
                content_part = mpwriter.append(b"import")
                content_part.set_content_disposition("form-data", name="content")

                file_part = mpwriter.append(payload)

                # The name=filename must be present
                file_part.set_content_disposition("form-data", name="filename", filename=resolved_name)

                for part in mpwriter:
                    # this is because proxmox is insane, the content-disposition header
                    # must be before the content-type. Absolutely cooked.
                    content_type = part[0].headers.pop(aiohttp.hdrs.CONTENT_TYPE)
                    part[0].headers[aiohttp.hdrs.CONTENT_TYPE] = content_type

                res = await self._do_post(
                    endpoint,
                    data=mpwriter,
                )

        return res

    async def get_vms(self, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/qemu"

        res = await self._do_get(endpoint)

        return res

    async def get_vm(self, vm_id: int, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/qemu/{vm_id}/config"

        res = await self._do_get(endpoint)

        return res

    async def stop_vm(self, vm_id: int, node: str) -> ApiResponse:
        endpoint = f"nodes/{node}/qemu/{vm_id}/status/stop"

        res = await self._do_post(endpoint)

        return res

    async def delete_vm(self, vm_id: int, node: str, purge: bool = True, destroy_disks: bool = True) -> ApiResponse:
        params = []
        if purge:
            params.append("purge=1")
        if destroy_disks:
            params.append("destroy-unreferenced-disks=1")
        query = "&".join(params)
        endpoint = f"nodes/{node}/qemu/{vm_id}{'?' + query if query else ''}"

        res = await self._do_delete(endpoint)

        return res
