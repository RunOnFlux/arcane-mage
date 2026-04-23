from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from arcane_mage.models import ArcaneOsConfigGroup
from arcane_mage.provisioner import TIER_CONFIG, Provisioner, is_api_min_version
from arcane_mage.proxmox import ApiResponse


class TestIsApiMinVersion:
    def test_exact_min_version(self):
        assert is_api_min_version("8.4.1") is True

    def test_above_min_version_patch(self):
        assert is_api_min_version("8.4.2") is True

    def test_above_min_version_minor(self):
        assert is_api_min_version("8.5.0") is True

    def test_above_min_version_major(self):
        assert is_api_min_version("9.0.0") is True

    def test_below_min_version_patch(self):
        assert is_api_min_version("8.4.0") is False

    def test_below_min_version_minor(self):
        assert is_api_min_version("8.3.9") is False

    def test_below_min_version_major(self):
        assert is_api_min_version("7.9.9") is False

    def test_invalid_format(self):
        assert is_api_min_version("8.4") is False
        assert is_api_min_version("invalid") is False
        assert is_api_min_version("") is False

    def test_non_numeric_parts(self):
        assert is_api_min_version("8.4.x") is False


class TestTierConfig:
    def test_cumulus(self):
        assert "cumulus" in TIER_CONFIG
        assert TIER_CONFIG["cumulus"]["cpu_cores"] == 4

    def test_nimbus(self):
        assert "nimbus" in TIER_CONFIG
        assert TIER_CONFIG["nimbus"]["cpu_cores"] == 8

    def test_stratus(self):
        assert "stratus" in TIER_CONFIG
        assert TIER_CONFIG["stratus"]["cpu_cores"] == 16


class TestProvisionerValidation:
    @pytest.fixture
    def mock_api(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def provisioner(self, mock_api: AsyncMock) -> Provisioner:
        return Provisioner(api=mock_api)

    async def test_validate_api_version_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_api_version.return_value = ApiResponse(
            status=200,
            payload={"version": "8.4.1"},
        )

        ok, msg = await provisioner.validate_api_version("node1")

        assert ok is True
        assert msg == "8.4.1"

    async def test_validate_api_version_too_old(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_api_version.return_value = ApiResponse(
            status=200,
            payload={"version": "7.0.0"},
        )

        ok, msg = await provisioner.validate_api_version("node1")

        assert ok is False
        assert "too old" in msg

    async def test_validate_api_version_unreachable(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_api_version.return_value = ApiResponse(error="Connection refused")

        ok, msg = await provisioner.validate_api_version("node1")

        assert ok is False
        assert "Unable to get" in msg

    async def test_validate_network_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_networks.return_value = ApiResponse(
            status=200,
            payload=[{"iface": "vmbr0"}, {"iface": "vmbr1"}],
        )

        result = await provisioner.validate_network("node1", "vmbr0")

        assert result is True

    async def test_validate_network_missing(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_networks.return_value = ApiResponse(
            status=200,
            payload=[{"iface": "vmbr0"}],
        )

        result = await provisioner.validate_network("node1", "vmbr99")

        assert result is False

    async def test_validate_iso_version_found(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_storage_content.return_value = ApiResponse(
            status=200,
            payload=[
                {"content": "iso", "volid": "local:iso/FluxLive-1749291196.iso"},
            ],
        )

        result = await provisioner.validate_iso_version("node1", "FluxLive-1749291196.iso", "local")

        assert result is True

    async def test_validate_iso_version_not_found(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_storage_content.return_value = ApiResponse(
            status=200,
            payload=[
                {"content": "iso", "volid": "local:iso/other.iso"},
            ],
        )

        result = await provisioner.validate_iso_version("node1", "FluxLive-1749291196.iso", "local")

        assert result is False

    async def test_start_vm(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.start_vm.return_value = ApiResponse(status=200, payload="UPID:task123")
        mock_api.wait_for_task.return_value = True

        result = await provisioner.start_vm(100, "node1")

        assert result is True
        mock_api.wait_for_task.assert_called_once_with("UPID:task123", "node1", 20)

    async def test_create_vm_config_cumulus(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_next_id.return_value = ApiResponse(status=200, payload=100)

        config = await provisioner.create_vm_config(
            vm_name="test-vm",
            tier="cumulus",
            network_bridge="vmbr0",
        )

        assert config is not None
        assert config.vmid == 100
        assert config.name == "test-vm"
        assert config.memory == 8192
        assert config.cores == 4

    async def test_create_vm_config_invalid_tier(self, provisioner: Provisioner, mock_api: AsyncMock):
        config = await provisioner.create_vm_config(
            vm_name="test-vm",
            tier="invalid",
            network_bridge="vmbr0",
        )

        assert config is None

    async def test_stop_vm_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.stop_vm.return_value = ApiResponse(status=200, payload="UPID:stop123")
        mock_api.wait_for_task.return_value = True

        result = await provisioner.stop_vm(100, "node1")

        assert result is True
        mock_api.stop_vm.assert_called_once_with(100, "node1")
        mock_api.wait_for_task.assert_called_once_with("UPID:stop123", "node1", 30)

    async def test_stop_vm_failure(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.stop_vm.return_value = ApiResponse(error="Connection refused")

        result = await provisioner.stop_vm(100, "node1")

        assert result is False

    async def test_delete_vm_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.delete_vm.return_value = ApiResponse(status=200, payload="UPID:del123")
        mock_api.wait_for_task.return_value = True

        result = await provisioner.delete_vm(100, "node1")

        assert result is True
        mock_api.delete_vm.assert_called_once_with(100, "node1")
        mock_api.wait_for_task.assert_called_once_with("UPID:del123", "node1", 30)

    async def test_delete_vm_failure(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.delete_vm.return_value = ApiResponse(error="Connection refused")

        result = await provisioner.delete_vm(100, "node1")

        assert result is False

    async def test_deprovision_node_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        """VM found, running, stopped, then deleted."""
        mock_api.get_vms.return_value = ApiResponse(
            status=200,
            payload=[{"vmid": 100, "name": "graham", "status": "running"}],
        )
        mock_api.stop_vm.return_value = ApiResponse(status=200, payload="UPID:stop1")
        mock_api.delete_vm.return_value = ApiResponse(status=200, payload="UPID:del1")
        mock_api.wait_for_task.return_value = True

        fluxnode = AsyncMock()
        fluxnode.hypervisor.node = "bigchug"
        fluxnode.hypervisor.vm_name = "graham"

        result = await provisioner.deprovision_node(fluxnode)

        assert result is True
        mock_api.stop_vm.assert_called_once_with(100, "bigchug")
        mock_api.delete_vm.assert_called_once_with(100, "bigchug")

    async def test_deprovision_node_vm_not_found(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_vms.return_value = ApiResponse(
            status=200,
            payload=[{"vmid": 200, "name": "other-vm", "status": "running"}],
        )

        fluxnode = AsyncMock()
        fluxnode.hypervisor.node = "bigchug"
        fluxnode.hypervisor.vm_name = "graham"

        messages = []
        result = await provisioner.deprovision_node(fluxnode, callback=lambda ok, msg: messages.append((ok, msg)))

        assert result is False
        assert any("not found" in msg for _, msg in messages)

    async def test_deprovision_node_no_hypervisor(self, provisioner: Provisioner, mock_api: AsyncMock):
        fluxnode = AsyncMock()
        fluxnode.hypervisor = None

        messages = []
        result = await provisioner.deprovision_node(fluxnode, callback=lambda ok, msg: messages.append((ok, msg)))

        assert result is False
        assert any("No hypervisor" in msg for _, msg in messages)

    async def test_deprovision_vm_by_name_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_vms.return_value = ApiResponse(
            status=200,
            payload=[{"vmid": 100, "name": "graham", "status": "stopped"}],
        )
        mock_api.delete_vm.return_value = ApiResponse(status=200, payload="UPID:del1")
        mock_api.wait_for_task.return_value = True

        result = await provisioner.deprovision_vm("bigchug", vm_name="graham")

        assert result is True
        mock_api.delete_vm.assert_called_once_with(100, "bigchug")

    async def test_deprovision_vm_by_id_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_vms.return_value = ApiResponse(
            status=200,
            payload=[{"vmid": 100, "name": "graham", "status": "stopped"}],
        )
        mock_api.delete_vm.return_value = ApiResponse(status=200, payload="UPID:del1")
        mock_api.wait_for_task.return_value = True

        result = await provisioner.deprovision_vm("bigchug", vm_id=100)

        assert result is True
        mock_api.delete_vm.assert_called_once_with(100, "bigchug")

    async def test_deprovision_vm_not_found(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_vms.return_value = ApiResponse(
            status=200,
            payload=[{"vmid": 200, "name": "other", "status": "running"}],
        )

        messages = []
        result = await provisioner.deprovision_vm("bigchug", callback=lambda ok, msg: messages.append((ok, msg)), vm_name="graham")

        assert result is False
        assert any("not found" in msg for _, msg in messages)

    async def test_discover_nodes_offline_node_returns_empty_list(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        """Offline cluster nodes return payload=None from get_vms; discover_nodes
        must coerce that to [] so downstream callers can iterate safely.
        Regression: bare None leaked through and crashed build_fluxnode_table."""
        mock_api.get_hypervisor_nodes.return_value = ApiResponse(
            status=200,
            payload=[{"node": "online-node"}, {"node": "offline-node"}],
        )

        async def get_vms_side_effect(name: str) -> ApiResponse:
            if name == "offline-node":
                return ApiResponse(status=200, payload=None)
            return ApiResponse(status=200, payload=[{"vmid": 100, "name": "vm1"}])

        mock_api.get_vms.side_effect = get_vms_side_effect

        discovery = await provisioner.discover_nodes(ArcaneOsConfigGroup())

        assert discovery is not None
        assert discovery.provisioned_vms["offline-node"] == []
        assert discovery.provisioned_vms["online-node"] == [{"vmid": 100, "name": "vm1"}]
        for vms in discovery.provisioned_vms.values():
            assert vms is not None
