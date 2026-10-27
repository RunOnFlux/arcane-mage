from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from arcane_mage.provisioner import (
    CREATE_TASK_MAX_WAIT_S,
    SHUTDOWN_TIMEOUT_S,
    TIER_CONFIG,
    Provisioner,
    is_api_min_version,
)
from arcane_mage.proxmox import ApiFailure, ApiSuccess, TaskOutcome


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
        mock_api.get_api_version.return_value = ApiSuccess(
            payload={"version": "8.4.1"},
        )

        ok, msg = await provisioner.validate_api_version("node1")

        assert ok is True
        assert msg == "8.4.1"

    async def test_validate_api_version_too_old(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_api_version.return_value = ApiSuccess(
            payload={"version": "7.0.0"},
        )

        ok, msg = await provisioner.validate_api_version("node1")

        assert ok is False
        assert "too old" in msg

    async def test_validate_api_version_unreachable(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_api_version.return_value = ApiFailure(error="Connection refused")

        ok, msg = await provisioner.validate_api_version("node1")

        assert ok is False
        assert "Unable to get" in msg

    async def test_validate_network_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_networks.return_value = ApiSuccess(
            payload=[{"iface": "vmbr0"}, {"iface": "vmbr1"}],
        )

        result = await provisioner.validate_network("node1", "vmbr0")

        assert result is True

    async def test_validate_network_missing(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_networks.return_value = ApiSuccess(
            payload=[{"iface": "vmbr0"}],
        )

        result = await provisioner.validate_network("node1", "vmbr99")

        assert result is False

    async def test_validate_network_sdn_vnet(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_networks.return_value = ApiSuccess(
            payload=[
                {"iface": "vmbr0", "type": "bridge"},
                {"iface": "flux50", "type": "vnet"},
            ],
        )

        assert await provisioner.validate_network("node1", "flux50") is True

    async def test_validate_network_listing_fails(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_networks.return_value = ApiFailure(error="Connection Error")

        assert await provisioner.validate_network("node1", "vmbr0") is False

    async def test_validate_iso_version_found(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_storage_content.return_value = ApiSuccess(
            payload=[
                {"content": "iso", "volid": "local:iso/FluxLive-1749291196.iso"},
            ],
        )

        result = await provisioner.validate_iso_version("node1", "FluxLive-1749291196.iso", "local")

        assert result is True

    async def test_validate_iso_version_not_found(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_storage_content.return_value = ApiSuccess(
            payload=[
                {"content": "iso", "volid": "local:iso/other.iso"},
            ],
        )

        result = await provisioner.validate_iso_version("node1", "FluxLive-1749291196.iso", "local")

        assert result is False

    async def test_start_vm(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.start_vm.return_value = ApiSuccess(payload="UPID:task123")
        mock_api.wait_for_task.return_value = TaskOutcome()

        result = await provisioner.start_vm(100, "node1")

        assert result
        mock_api.wait_for_task.assert_called_once_with("UPID:task123", "node1", 20)

    async def test_create_vm_config_cumulus(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_next_id.return_value = ApiSuccess(payload=100)

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

    async def test_a_vm_has_the_clipboard_channel_and_a_serial_port(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        mock_api.get_next_id.return_value = ApiSuccess(payload=100)

        config = await provisioner.create_vm_config(
            vm_name="test-vm",
            tier="cumulus",
            network_bridge="vmbr0",
        )

        assert config is not None
        api = config.to_proxmox_dict()
        assert api["vga"] == "std,clipboard=vnc"
        assert api["serial0"] == "socket"

    async def test_create_vm_config_tags_description_and_memory(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_next_id.return_value = ApiSuccess(payload=100)
        description = "# node-a\nkind: paid"

        config = await provisioner.create_vm_config(
            vm_name="test-vm",
            tier="cumulus",
            network_bridge="vmbr0",
            tags="flux;cumulus",
            description=description,
            memory_mb=7680,
        )

        assert config is not None
        api = config.to_proxmox_dict()
        assert api["tags"] == "flux;cumulus"
        assert api["description"] == description
        assert api["memory"] == 7680

    async def test_create_vm_config_leaves_out_unset_tags_and_description(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        mock_api.get_next_id.return_value = ApiSuccess(payload=100)

        config = await provisioner.create_vm_config(vm_name="test-vm", tier="cumulus", network_bridge="vmbr0")

        assert config is not None
        api = config.to_proxmox_dict()
        assert "tags" not in api
        assert "description" not in api
        assert api["memory"] == TIER_CONFIG["cumulus"]["memory_mb"]

    async def test_a_vm_imports_its_own_efi_image(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_next_id.return_value = ApiSuccess(payload=100)

        config = await provisioner.create_vm_config(vm_name="test-vm", tier="cumulus", network_bridge="vmbr0")

        assert config is not None
        assert config.efidisk0.endswith("import-from=local:import/100_arcane_efi.raw")

    async def test_create_vm_config_invalid_tier(self, provisioner: Provisioner, mock_api: AsyncMock):
        config = await provisioner.create_vm_config(
            vm_name="test-vm",
            tier="invalid",  # ty: ignore[invalid-argument-type]
            network_bridge="vmbr0",
        )

        assert config is None

    async def test_stop_vm_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.stop_vm.return_value = ApiSuccess(payload="UPID:stop123")
        mock_api.wait_for_task.return_value = TaskOutcome()

        result = await provisioner.stop_vm(100, "node1")

        assert result
        mock_api.stop_vm.assert_called_once_with(100, "node1")
        mock_api.wait_for_task.assert_called_once_with("UPID:stop123", "node1", 30)

    async def test_stop_vm_failure(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.stop_vm.return_value = ApiFailure(error="Connection refused")

        result = await provisioner.stop_vm(100, "node1")

        assert result.error == "Connection refused"

    async def test_delete_vm_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.delete_vm.return_value = ApiSuccess(payload="UPID:del123")
        mock_api.wait_for_task.return_value = TaskOutcome()

        result = await provisioner.delete_vm(100, "node1")

        assert result
        mock_api.delete_vm.assert_called_once_with(100, "node1")
        mock_api.wait_for_task.assert_called_once_with("UPID:del123", "node1", 30)

    async def test_delete_vm_failure(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.delete_vm.return_value = ApiFailure(error="Connection refused")

        result = await provisioner.delete_vm(100, "node1")

        assert result.error == "Connection refused"

    async def test_deprovision_node_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        """VM found, running, shut down, then deleted."""
        mock_api.get_vms.return_value = ApiSuccess(
            payload=[{"vmid": 100, "name": "graham", "status": "running"}],
        )
        mock_api.shutdown_vm.return_value = ApiSuccess(payload="UPID:shutdown1")
        mock_api.delete_vm.return_value = ApiSuccess(payload="UPID:del1")
        mock_api.wait_for_task.return_value = TaskOutcome()

        fluxnode = AsyncMock()
        fluxnode.hypervisor.node = "bigchug"
        fluxnode.hypervisor.vm_name = "graham"

        result = await provisioner.deprovision_node(fluxnode)

        assert result is True
        mock_api.shutdown_vm.assert_called_once_with(100, "bigchug", SHUTDOWN_TIMEOUT_S)
        mock_api.stop_vm.assert_not_called()
        mock_api.delete_vm.assert_called_once_with(100, "bigchug")

    async def test_deprovision_hard_stops_a_vm_that_does_not_shut_down(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        mock_api.get_vms.return_value = ApiSuccess(
            payload=[{"vmid": 100, "name": "graham", "status": "running"}],
        )
        mock_api.shutdown_vm.return_value = ApiSuccess(payload="UPID:shutdown1")
        mock_api.stop_vm.return_value = ApiSuccess(payload="UPID:stop1")
        mock_api.delete_vm.return_value = ApiSuccess(payload="UPID:del1")

        async def wait(task: str, node: str, max_wait_s: int = 10) -> TaskOutcome:
            return TaskOutcome(error="task still running after 130s") if task == "UPID:shutdown1" else TaskOutcome()

        mock_api.wait_for_task.side_effect = wait

        messages: list[str] = []
        result = await provisioner.deprovision_vm("bigchug", lambda ok, msg: messages.append(msg), vm_id=100)

        assert result is True
        mock_api.stop_vm.assert_called_once_with(100, "bigchug")
        mock_api.delete_vm.assert_called_once_with(100, "bigchug")
        assert "VM did not shut down (task still running after 130s), stopping..." in messages

    async def test_deprovision_node_vm_not_found(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_vms.return_value = ApiSuccess(
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
        mock_api.get_vms.return_value = ApiSuccess(
            payload=[{"vmid": 100, "name": "graham", "status": "stopped"}],
        )
        mock_api.delete_vm.return_value = ApiSuccess(payload="UPID:del1")
        mock_api.wait_for_task.return_value = TaskOutcome()

        result = await provisioner.deprovision_vm("bigchug", vm_name="graham")

        assert result is True
        mock_api.delete_vm.assert_called_once_with(100, "bigchug")

    async def test_deprovision_vm_by_id_success(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_vms.return_value = ApiSuccess(
            payload=[{"vmid": 100, "name": "graham", "status": "stopped"}],
        )
        mock_api.delete_vm.return_value = ApiSuccess(payload="UPID:del1")
        mock_api.wait_for_task.return_value = TaskOutcome()

        result = await provisioner.deprovision_vm("bigchug", vm_id=100)

        assert result is True
        mock_api.delete_vm.assert_called_once_with(100, "bigchug")

    async def test_deprovision_vm_not_found(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_vms.return_value = ApiSuccess(
            payload=[{"vmid": 200, "name": "other", "status": "running"}],
        )

        messages = []
        result = await provisioner.deprovision_vm(
            "bigchug", callback=lambda ok, msg: messages.append((ok, msg)), vm_name="graham"
        )

        assert result is False
        assert any("not found" in msg for _, msg in messages)


class TestProvisionFailureReasons:
    @pytest.fixture
    def mock_api(self) -> AsyncMock:
        api = AsyncMock()
        api.upload_file.return_value = ApiSuccess(payload="UPID:up")
        api.create_vm.return_value = ApiSuccess(payload="UPID:create")
        api.start_vm.return_value = ApiSuccess(payload="UPID:start")
        api.delete_file.return_value = ApiSuccess(payload="UPID:del")
        api.wait_for_task.return_value = TaskOutcome()
        return api

    @pytest.fixture
    def provisioner(self, mock_api: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> Provisioner:
        p = Provisioner(api=mock_api)
        monkeypatch.setattr(p, "validate_api_version", AsyncMock(return_value=(True, "8.4.1")))
        monkeypatch.setattr(p, "validate_storage", AsyncMock(return_value=(True, "")))
        monkeypatch.setattr(p, "validate_iso_version", AsyncMock(return_value=True))
        monkeypatch.setattr(p, "validate_network", AsyncMock(return_value=True))
        return p

    @staticmethod
    def fluxnode() -> MagicMock:
        node = MagicMock()
        node.to_dict.return_value = {}
        hv = node.hypervisor
        hv.node, hv.vm_name, hv.vm_id, hv.node_tier = "pve1", "node-a", 264, "cumulus"
        hv.network, hv.iso_name = "vmbr0", "FluxLive.iso"
        hv.storage_images, hv.storage_iso, hv.storage_import = "local-lvm", "local", "local"
        hv.disk_limit = hv.memory_mb = hv.cpu_limit = hv.network_limit = None
        hv.startup_config = hv.tags = hv.description = None
        hv.start_on_creation = True
        return node

    async def failures(self, provisioner: Provisioner) -> list[str]:
        failed: list[str] = []
        ok = await provisioner.provision_node(self.fluxnode(), lambda good, msg: None if good else failed.append(msg))
        assert ok is False
        return failed

    async def test_a_failed_create_carries_the_task_error(self, provisioner: Provisioner, mock_api: AsyncMock):
        why = "unable to create VM 264 - cannot import from 'local:import/264_arcane_efi.raw'"

        async def wait(task: str, node: str, max_wait_s: int = 10) -> TaskOutcome:
            return TaskOutcome(error=why) if task == "UPID:create" else TaskOutcome()

        mock_api.wait_for_task.side_effect = wait

        assert await self.failures(provisioner) == [f"Unable to create VM on hypervisor: {why}"]

    async def test_a_refused_create_names_the_http_error(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.create_vm.return_value = ApiFailure(status=500, error="storage 'local-lvm' is full")

        assert await self.failures(provisioner) == [
            "Unable to create VM on hypervisor: HTTP 500: storage 'local-lvm' is full"
        ]

    async def test_the_create_waits_long_enough_for_a_busy_host(self, provisioner: Provisioner, mock_api: AsyncMock):
        await provisioner.provision_node(self.fluxnode())

        mock_api.wait_for_task.assert_any_await("UPID:create", "pve1", CREATE_TASK_MAX_WAIT_S)

    async def test_a_failed_config_upload_deletes_only_its_config_image(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        async def wait(task: str, node: str, max_wait_s: int = 10) -> TaskOutcome:
            return (
                TaskOutcome(error="task status unreadable (request timed out)") if task == "UPID:up" else TaskOutcome()
            )

        mock_api.wait_for_task.side_effect = wait

        assert await self.failures(provisioner) == [
            "Unable to upload Config image to hypervisor: task status unreadable (request timed out)"
        ]
        assert [c.args[0] for c in mock_api.delete_file.call_args_list] == ["264_arcane_config.raw"]

    async def test_a_run_uploads_and_deletes_only_its_own_images(self, provisioner: Provisioner, mock_api: AsyncMock):
        assert await provisioner.provision_node(self.fluxnode())

        assert mock_api.upload_file.call_args_list[1].kwargs["file_name"] == "264_arcane_efi.raw"
        deleted = sorted(c.args[0] for c in mock_api.delete_file.call_args_list)
        assert deleted == ["264_arcane_config.raw", "264_arcane_efi.raw"]

    async def test_an_assigned_vm_id_is_written_back(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.get_next_id.return_value = ApiSuccess(payload=312)
        fluxnode = self.fluxnode()
        fluxnode.hypervisor.vm_id = None

        assert await provisioner.provision_node(fluxnode)

        assert fluxnode.hypervisor.vm_id == 312
