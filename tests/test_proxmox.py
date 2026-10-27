from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from arcane_mage.proxmox import ApiFailure, ApiSuccess, ParsedToken, ParsedUserPass, ProxmoxApi, ResolvedConnection


class TestApiResponse:
    def test_ok_is_truthy(self):
        assert bool(ApiSuccess(payload="UPID:task")) is True

    def test_error_is_falsy(self):
        assert bool(ApiFailure(status=500, error="Internal Server Error")) is False

    def test_unauthorized(self):
        assert ApiFailure(status=401).unauthorized

    def test_error_not_unauthorized(self):
        assert not ApiFailure(status=500).unauthorized

    def test_ok_never_unauthorized(self):
        assert not ApiSuccess(payload=None).unauthorized

    def test_timed_out(self):
        resp = ApiFailure(timed_out=True)
        assert resp.timed_out
        assert bool(resp) is False

    def test_reason_names_the_status_and_error(self):
        assert ApiFailure(status=500, error="storage full").reason == "HTTP 500: storage full"

    def test_reason_without_a_status_is_the_error(self):
        assert ApiFailure(error="Connection Error").reason == "Connection Error"

    def test_reason_for_a_timeout(self):
        assert ApiFailure(timed_out=True).reason == "request timed out"


TOKEN = ParsedToken("user@pam", "t", "v")

type Route = tuple[str, str, int, dict[str, object]]


@asynccontextmanager
async def proxmox_answering(*routes: Route) -> AsyncGenerator[ProxmoxApi]:
    """A ProxmoxApi talking to a local server that answers each (method, path) with (status, body)."""
    app = web.Application()

    for method, path, status, body in routes:

        async def handler(_: web.Request, status: int = status, body: dict[str, object] = body) -> web.Response:
            return web.json_response(body, status=status)

        app.router.add_route(method, f"/api2/json/{path}", handler)

    async with TestServer(app) as server, ProxmoxApi.from_token(str(server.make_url("")).rstrip("/"), TOKEN) as api:
        yield api


class TestResponseShapes:
    async def test_payload_validated(self):
        async with proxmox_answering(
            ("GET", "nodes/pve1/qemu", 200, {"data": [{"vmid": "101", "status": "running"}]})
        ) as api:
            res = await api.get_vms("pve1")

        assert res
        assert res.payload == [{"vmid": 101, "status": "running"}]

    async def test_unlisted_fields_kept(self):
        vm = {"vmid": 101, "status": "running", "name": "a", "uptime": 42, "tags": "flux"}
        async with proxmox_answering(("GET", "nodes/pve1/qemu", 200, {"data": [vm]})) as api:
            res = await api.get_vms("pve1")

        assert res
        assert res.payload == [vm]

    async def test_shape_mismatch_is_error(self):
        async with proxmox_answering(("GET", "nodes/pve1/qemu", 200, {"data": None})) as api:
            res = await api.get_vms("pve1")

        assert not res
        assert res.status == 200
        assert res.error and res.error.startswith("Unexpected response")

    async def test_missing_required_field_is_error(self):
        async with proxmox_answering(("GET", "nodes/pve1/network", 200, {"data": [{"type": "bridge"}]})) as api:
            res = await api.get_networks("pve1")

        assert not res
        assert res.error == "Unexpected response at 0.iface: Field required"

    async def test_message_is_error(self):
        async with proxmox_answering(("GET", "cluster/nextid", 200, {"data": None, "message": "no permission"})) as api:
            res = await api.get_next_id()

        assert not res
        assert res.error == "no permission"

    async def test_http_error(self):
        async with proxmox_answering(("GET", "nodes", 401, {"data": None})) as api:
            res = await api.get_hypervisor_nodes()

        assert not res
        assert res.unauthorized

    async def test_null_payload_endpoint(self):
        async with proxmox_answering(("PUT", "storage/local", 200, {"data": None})) as api:
            res = await api.set_storage_content("local", "iso,import")

        assert res
        assert res.payload is None


class TestProxmoxApiParsing:
    def test_parse_token_valid(self):
        result = ProxmoxApi.parse_token("user@pam!mytoken=secret-value")

        assert result is not None
        assert result == ParsedToken("user@pam", "mytoken", "secret-value")
        assert result.user == "user@pam"
        assert result.username == "user"
        assert result.token_name == "mytoken"
        assert result.token_value == "secret-value"

    def test_parse_token_invalid(self):
        result = ProxmoxApi.parse_token("invalid-token-format")

        assert result is None

    def test_parse_token_username_no_realm(self):
        result = ProxmoxApi.parse_token("admin!mytoken=value")

        assert result is not None
        assert result.username == "admin"

    def test_parse_user_pass_valid(self):
        result = ProxmoxApi.parse_user_pass("admin:password123")

        assert result is not None
        assert result == ParsedUserPass("admin", "password123")
        assert result.username == "admin"

    def test_parse_user_pass_invalid(self):
        result = ProxmoxApi.parse_user_pass("no-colon-here")

        assert result is None

    def test_parse_user_pass_strips_pam_suffix(self):
        result = ProxmoxApi.parse_user_pass("admin@pam:password123")

        assert result is not None
        assert result == ParsedUserPass("admin", "password123")
        assert result.username == "admin"


class TestResolvedConnection:
    def test_creation(self):
        token = ParsedToken("user@pam", "mytoken", "secret-value")
        conn = ResolvedConnection(url="https://pve.local:8006", token=token)

        assert conn.url == "https://pve.local:8006"
        assert conn.token == token
        assert conn.token.user == "user@pam"
        assert conn.token.token_name == "mytoken"
        assert conn.token.token_value == "secret-value"

    def test_frozen(self):
        token = ParsedToken("user@pam", "mytoken", "secret-value")
        conn = ResolvedConnection(url="https://pve.local:8006", token=token)

        with pytest.raises(AttributeError):
            conn.url = "https://other.local:8006"  # ty: ignore[invalid-assignment]

        with pytest.raises(AttributeError):
            conn.token = ParsedToken("other@pam", "t", "v")  # ty: ignore[invalid-assignment]


class TestGetNetworks:
    async def test_requests_any_bridge(self):
        app = web.Application()

        async def handler(request: web.Request) -> web.Response:
            if request.query.get("type") != "any_bridge":
                return web.json_response({"data": None}, status=400)
            return web.json_response({"data": [{"iface": "flux50", "type": "vnet"}]})

        app.router.add_get("/api2/json/nodes/pve1/network", handler)

        async with TestServer(app) as server, ProxmoxApi.from_token(str(server.make_url("")).rstrip("/"), TOKEN) as api:
            res = await api.get_networks("pve1")

        assert res
        assert res.payload == [{"iface": "flux50", "type": "vnet"}]


UPID = "UPID:pve2:0000A1B2:00C3D4E5:671E0000:qmcreate:101:root@pam:"


class TestWaitForTask:
    async def test_polls_the_node_the_upid_names(self):
        task = {"status": "stopped", "exitstatus": "OK"}
        async with proxmox_answering(("GET", "nodes/pve2/tasks/{upid}/status", 200, {"data": task})) as api:
            outcome = await api.wait_for_task(UPID, "pve1")

        assert outcome
        assert outcome.error is None

    async def test_a_failed_task_carries_its_exit_status(self):
        why = "unable to create VM 101 - cannot import from 'local:import/arcane_efi.raw'"
        task = {"status": "stopped", "exitstatus": why}
        async with proxmox_answering(("GET", "nodes/pve2/tasks/{upid}/status", 200, {"data": task})) as api:
            outcome = await api.wait_for_task(UPID, "pve2", max_wait_s=600)

        assert not outcome
        assert outcome.error == why

    async def test_a_task_that_outlasts_the_wait_says_so(self):
        task = {"status": "running"}
        async with proxmox_answering(("GET", "nodes/pve2/tasks/{upid}/status", 200, {"data": task})) as api:
            outcome = await api.wait_for_task(UPID, "pve2", max_wait_s=0)

        assert outcome.error == "task still running after 0s"

    async def test_an_unreadable_task_says_so(self):
        async with proxmox_answering(("GET", "nodes/pve2/tasks/{upid}/status", 500, {"data": None})) as api:
            outcome = await api.wait_for_task(UPID, "pve2")

        assert outcome.error == "task status unreadable (HTTP 500: Internal Server Error)"


class TestVmPower:
    @staticmethod
    @asynccontextmanager
    async def proxmox_recording(path: str, sent: dict[str, str]) -> AsyncGenerator[ProxmoxApi]:
        """A ProxmoxApi whose POSTs to `path` record their form fields into `sent`."""
        app = web.Application()

        async def handler(request: web.Request) -> web.Response:
            sent.update({k: str(v) for k, v in (await request.post()).items()})
            return web.json_response({"data": UPID})

        app.router.add_post(f"/api2/json/{path}", handler)

        async with TestServer(app) as server, ProxmoxApi.from_token(str(server.make_url("")).rstrip("/"), TOKEN) as api:
            yield api

    async def test_shutdown_passes_its_timeout(self):
        sent: dict[str, str] = {}
        async with self.proxmox_recording("nodes/pve2/qemu/101/status/shutdown", sent) as api:
            res = await api.shutdown_vm(101, "pve2", 120)

        assert res
        assert sent == {"timeout": "120"}

    async def test_stop_overrules_a_running_shutdown(self):
        sent: dict[str, str] = {}
        async with self.proxmox_recording("nodes/pve2/qemu/101/status/stop", sent) as api:
            res = await api.stop_vm(101, "pve2")

        assert res
        assert sent == {"overrule-shutdown": "1"}
