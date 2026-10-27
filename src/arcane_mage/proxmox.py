from __future__ import annotations

import asyncio
import contextlib
import json
import types
import urllib.parse
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path
from socket import AF_INET
from time import monotonic
from typing import Literal, NotRequired, TypedDict

import aiohttp
from aiohttp import (
    ClientError,
    ClientResponseError,
    ClientTimeout,
    ConnectionTimeoutError,
    ContentTypeError,
    TCPConnector,
)
from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config

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


@dataclass(frozen=True)
class ApiSuccess[T]:
    """A successful Proxmox API call: its `data`, validated against the endpoint's shape."""

    payload: T
    status: int = 200

    @property
    def unauthorized(self) -> Literal[False]:
        return False

    def __bool__(self) -> Literal[True]:
        return True


@dataclass(frozen=True)
class ApiFailure:
    """A failed Proxmox API call: refused, unreachable, timed out, or answered with data
    that does not match the endpoint's shape."""

    status: int | None = None
    error: str | None = None
    timed_out: bool = False

    @property
    def unauthorized(self) -> bool:
        return self.status == 401

    @property
    def reason(self) -> str:
        """The failure in one line, for a message."""
        if self.timed_out:
            return "request timed out"
        if self.status is None:
            return self.error or "request failed"
        return f"HTTP {self.status}: {self.error}" if self.error else f"HTTP {self.status}"

    def __bool__(self) -> Literal[False]:
        return False


# `if not res:` narrows to ApiFailure; past it, `res.payload` has the endpoint's type.
type ApiResponse[T] = ApiSuccess[T] | ApiFailure


@dataclass(frozen=True)
class TaskOutcome:
    """How a Proxmox task ended. Truthy when it ended OK; otherwise `error` is Proxmox's
    exit status, or why the wait gave up."""

    error: str | None = None

    def __bool__(self) -> bool:
        return self.error is None


# Payload shapes name the fields arcane-mage reads; every other field Proxmox sends is kept.
_KEEP_ALL_FIELDS = ConfigDict(extra="allow")


@with_config(_KEEP_ALL_FIELDS)
class HypervisorNode(TypedDict):
    node: str
    status: NotRequired[str]


@with_config(_KEEP_ALL_FIELDS)
class ClusterConfigNode(TypedDict):
    node: str


@with_config(_KEEP_ALL_FIELDS)
class NodeVersion(TypedDict):
    version: str
    release: NotRequired[str]
    repoid: NotRequired[str]


@with_config(_KEEP_ALL_FIELDS)
class NodeStorage(TypedDict):
    storage: str
    content: str
    avail: NotRequired[int]
    total: NotRequired[int]
    used: NotRequired[int]


@with_config(_KEEP_ALL_FIELDS)
class StorageConfig(TypedDict):
    storage: str
    type: str
    content: NotRequired[str]
    shared: NotRequired[int]
    nodes: NotRequired[str]


@with_config(_KEEP_ALL_FIELDS)
class StorageVolume(TypedDict):
    volid: str
    content: str


@with_config(_KEEP_ALL_FIELDS)
class NetworkIface(TypedDict):
    iface: str
    type: NotRequired[str]


@with_config(_KEEP_ALL_FIELDS)
class VmSummary(TypedDict):
    vmid: int
    status: str
    name: NotRequired[str]
    cpu: NotRequired[float]
    mem: NotRequired[int]
    maxmem: NotRequired[int]
    disk: NotRequired[int]
    maxdisk: NotRequired[int]


@with_config(_KEEP_ALL_FIELDS)
class TaskStatus(TypedDict):
    status: str
    exitstatus: NotRequired[str]


# A task-starting call answers with the task's UPID.
_UPID = TypeAdapter(str)
_NONE = TypeAdapter(type(None))
_HYPERVISOR_NODES = TypeAdapter(list[HypervisorNode])
_CLUSTER_CONFIG_NODES = TypeAdapter(list[ClusterConfigNode])
_NODE_VERSION = TypeAdapter(NodeVersion)
_NODE_STORAGE = TypeAdapter(list[NodeStorage])
_STORAGE_CONFIG = TypeAdapter(list[StorageConfig])
_STORAGE_VOLUMES = TypeAdapter(list[StorageVolume])
_NETWORK_IFACES = TypeAdapter(list[NetworkIface])
_VMS = TypeAdapter(list[VmSummary])
_VM_CONFIG = TypeAdapter(dict[str, str | int | float])
_TASK_STATUS = TypeAdapter(TaskStatus)
_NEXT_ID = TypeAdapter(int)


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
            async with aiohttp.ClientSession(connector=conn, timeout=timeout) as client:
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
    def build_password_client(url: str, ticket: str, csrf_token: str) -> aiohttp.ClientSession:
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
    def build_token_client(url: str, user: str, token_name: str, token_value: str) -> aiohttp.ClientSession:
        headers = {"Authorization": f"PVEAPIToken={user}!{token_name}={token_value}"}
        conn = TCPConnector(family=AF_INET)
        timeout = ClientTimeout(connect=2)

        return aiohttp.ClientSession(
            base_url=f"{url}/api2/json/",
            connector=conn,
            headers=headers,
            timeout=timeout,
        )

    @staticmethod
    async def handle_api_response[T](
        response: aiohttp.ClientResponse,
        shape: TypeAdapter[T],
    ) -> ApiResponse[T]:
        try:
            response.raise_for_status()
        except ClientResponseError as e:
            return ApiFailure(status=e.status, error=e.message)

        try:
            body: dict = await response.json()
        except (json.JSONDecodeError, ContentTypeError) as e:
            return ApiFailure(status=response.status, error=str(e))

        if message := body.get("message"):
            return ApiFailure(status=response.status, error=message)

        try:
            payload = shape.validate_python(body.get("data"))
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(part) for part in first["loc"]) or "data"
            return ApiFailure(status=response.status, error=f"Unexpected response at {where}: {first['msg']}")

        return ApiSuccess(payload=payload, status=response.status)

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
    async def session(self) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            await self.close()

    async def close(self) -> None:
        await self.client.close()

    async def _do_http[T](
        self,
        verb: Literal["get", "put", "post", "delete"],
        path: str,
        shape: TypeAdapter[T],
        data: HttpData = None,
    ) -> ApiResponse[T]:
        method = getattr(self.client, verb)

        try:
            res = await method(path, ssl=self.verify_ssl, data=data)
        except ConnectionTimeoutError:
            return ApiFailure(timed_out=True)
        except ClientError:
            return ApiFailure(error="Connection Error")

        return await self.handle_api_response(res, shape)

    async def _do_get[T](self, path: str, shape: TypeAdapter[T]) -> ApiResponse[T]:
        return await self._do_http("get", path, shape)

    async def _do_post[T](self, path: str, shape: TypeAdapter[T], data: HttpData = None) -> ApiResponse[T]:
        return await self._do_http("post", path, shape, data)

    async def _do_put[T](self, path: str, shape: TypeAdapter[T], data: HttpData = None) -> ApiResponse[T]:
        return await self._do_http("put", path, shape, data)

    async def _do_delete[T](self, path: str, shape: TypeAdapter[T]) -> ApiResponse[T]:
        return await self._do_http("delete", path, shape)

    async def get_api_version(self, node: str) -> ApiResponse[NodeVersion]:
        res = await self._do_get(f"nodes/{node}/version", _NODE_VERSION)

        return res

    async def get_cluster_nodes(self) -> ApiResponse[list[ClusterConfigNode]]:
        res = await self._do_get("cluster/config/nodes", _CLUSTER_CONFIG_NODES)

        return res

    async def get_hypervisor_nodes(self) -> ApiResponse[list[HypervisorNode]]:
        res = await self._do_get("nodes", _HYPERVISOR_NODES)

        return res

    async def get_networks(self, node: str) -> ApiResponse[list[NetworkIface]]:
        """The bridges on `node` a VM can attach to, SDN VNets applied there included."""
        endpoint = f"nodes/{node}/network?type=any_bridge"

        res = await self._do_get(endpoint, _NETWORK_IFACES)

        return res

    async def get_storage_state(self, node: str) -> ApiResponse[list[NodeStorage]]:
        endpoint = f"nodes/{node}/storage"

        res = await self._do_get(endpoint, _NODE_STORAGE)

        return res

    async def get_storage_content(self, node: str, location: str) -> ApiResponse[list[StorageVolume]]:
        endpoint = f"nodes/{node}/storage/{location}/content"

        res = await self._do_get(endpoint, _STORAGE_VOLUMES)

        return res

    async def get_storage_config(self) -> ApiResponse[list[StorageConfig]]:
        res = await self._do_get("storage", _STORAGE_CONFIG)

        return res

    async def set_storage_content(self, storage_id: str, content: str) -> ApiResponse[None]:
        endpoint = f"storage/{storage_id}"
        data = {"content": content}

        res = await self._do_put(endpoint, _NONE, data=data)

        return res

    async def download_iso(
        self, node: str, url: str, filename: str, storage: str, verify_certs: bool = True
    ) -> ApiResponse[str]:
        endpoint = f"nodes/{node}/storage/{storage}/download-url"
        data = {
            "content": "iso",
            "filename": filename,
            "url": url,
            "verify-certificates": int(verify_certs),
        }

        res = await self._do_post(endpoint, _UPID, data=data)

        return res

    async def get_next_id(self) -> ApiResponse[int]:
        res = await self._do_get("cluster/nextid", _NEXT_ID)

        return res

    async def create_vm(self, config: dict, node: str) -> ApiResponse[str]:
        res = await self._do_post(f"nodes/{node}/qemu", _UPID, data=config)

        return res

    async def start_vm(self, vm_id: int, node: str) -> ApiResponse[str]:
        endpoint = f"nodes/{node}/qemu/{vm_id}/status/start"

        res = await self._do_post(endpoint, _UPID)

        return res

    async def get_task(self, task_id: str, node: str) -> ApiResponse[TaskStatus]:
        # A UPID ("UPID:<node>:...") names the node the task runs on, which can differ from
        # `node`: an upload runs on the node that accepted the connection.
        upid_parts = task_id.split(":")
        if len(upid_parts) > 1 and upid_parts[0] == "UPID" and upid_parts[1]:
            node = upid_parts[1]

        quoted_task = urllib.parse.quote(task_id)
        endpoint = f"nodes/{node}/tasks/{quoted_task}/status"

        res = await self._do_get(endpoint, _TASK_STATUS)

        return res

    async def wait_for_task(self, task_id: str, node: str, max_wait_s: int = 10) -> TaskOutcome:
        """Poll the task until it stops, for at most `max_wait_s` after the first answer."""
        task_res = await self.get_task(task_id, node)

        if not task_res:
            return TaskOutcome(error=f"task status unreadable ({task_res.reason})")

        task = task_res.payload
        start = monotonic()

        while task["status"] != "stopped" and monotonic() - start < max_wait_s:
            await asyncio.sleep(1)

            task_res = await self.get_task(task_id, node)

            if not task_res:
                return TaskOutcome(error=f"task status unreadable ({task_res.reason})")

            task = task_res.payload

        if task["status"] != "stopped":
            return TaskOutcome(error=f"task still running after {max_wait_s}s")

        exit_status = task.get("exitstatus")

        if exit_status != "OK":
            return TaskOutcome(error=exit_status or "task stopped without an exit status")

        return TaskOutcome()

    async def delete_file(self, file_name: str, node: str, storage: str, content: str) -> ApiResponse[str]:
        volume = f"{storage}:{content}/{file_name}"
        endpoint = f"nodes/{node}/storage/{storage}/content/{volume}"

        res = await self._do_delete(endpoint, _UPID)

        return res

    async def upload_file(
        self,
        file: Path | bytes,
        node: str,
        storage: str,
        file_name: str | None = None,
    ) -> ApiResponse[str]:
        endpoint = f"nodes/{node}/storage/{storage}/upload"

        if isinstance(file, bytes) and not file_name:
            return ApiFailure(error="Unuseable file")

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
                return ApiFailure(error="Unuseable file")

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

                res = await self._do_post(endpoint, _UPID, data=mpwriter)

        return res

    async def get_vms(self, node: str) -> ApiResponse[list[VmSummary]]:
        endpoint = f"nodes/{node}/qemu"

        res = await self._do_get(endpoint, _VMS)

        return res

    async def get_vm(self, vm_id: int, node: str) -> ApiResponse[dict[str, str | int | float]]:
        endpoint = f"nodes/{node}/qemu/{vm_id}/config"

        res = await self._do_get(endpoint, _VM_CONFIG)

        return res

    async def stop_vm(self, vm_id: int, node: str) -> ApiResponse[str]:
        endpoint = f"nodes/{node}/qemu/{vm_id}/status/stop"

        res = await self._do_post(endpoint, _UPID)

        return res

    async def delete_vm(
        self, vm_id: int, node: str, purge: bool = True, destroy_disks: bool = True
    ) -> ApiResponse[str]:
        params = []
        if purge:
            params.append("purge=1")
        if destroy_disks:
            params.append("destroy-unreferenced-disks=1")
        query = "&".join(params)
        endpoint = f"nodes/{node}/qemu/{vm_id}{'?' + query if query else ''}"

        res = await self._do_delete(endpoint, _UPID)

        return res
