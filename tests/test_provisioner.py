from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from arcane_mage.models import ArcaneOsConfigGroup
from arcane_mage.models.cluster import ClusterContext
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

    async def test_create_vm_config_memory_override(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        mock_api.get_next_id.return_value = ApiResponse(status=200, payload=100)

        config = await provisioner.create_vm_config(
            vm_name="test-vm",
            tier="cumulus",
            network_bridge="vmbr0",
            memory_mb=7680,
        )

        assert config is not None
        assert config.memory == 7680
        # Only RAM moves; the rest of the tier is untouched.
        assert config.cores == 4
        assert config.scsi0.startswith("local-lvm:220,")

    async def test_create_vm_config_tags_and_description(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        mock_api.get_next_id.return_value = ApiResponse(status=200, payload=100)
        description = "# flux-hub\nkind:     paid\n--- signed ---\n{\"a\": 1}"

        config = await provisioner.create_vm_config(
            vm_name="test-vm",
            tier="cumulus",
            network_bridge="vmbr0",
            tags="flux-hub;paid;cumulus",
            description=description,
        )

        assert config is not None
        assert config.tags == "flux-hub;paid;cumulus"
        # Multi-line descriptions reach Proxmox byte for byte; nothing re-wraps them.
        assert config.description == description
        assert config.to_proxmox_dict()["description"] == description

    async def test_create_vm_config_omits_unset_tags_and_description(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        """An operator on an older hub sends neither; Proxmox must not see the keys at all."""
        mock_api.get_next_id.return_value = ApiResponse(status=200, payload=100)

        config = await provisioner.create_vm_config(
            vm_name="test-vm",
            tier="cumulus",
            network_bridge="vmbr0",
        )

        assert config is not None
        assert config.tags is None
        assert config.description is None
        proxmox_dict = config.to_proxmox_dict()
        assert "tags" not in proxmox_dict
        assert "description" not in proxmox_dict

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


class TestRefreshIso:
    @pytest.fixture
    def mock_api(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def provisioner(self, mock_api: AsyncMock) -> Provisioner:
        return Provisioner(api=mock_api)

    @staticmethod
    def _release(**overrides) -> dict:
        base = {
            "iso": "FluxLive-111.iso",
            "build": "111",
            "severity": "low",
            "release": "test release",
            "checksums": "sums-111.sha256",
        }
        base.update(overrides)
        return base

    async def test_already_staged_is_a_no_op(
        self, provisioner: Provisioner, mock_api: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "arcane_mage.provisioner.do_http", AsyncMock(return_value=self._release())
        )
        mock_api.get_storage_content.return_value = ApiResponse(
            status=200,
            payload=[{"content": "iso", "volid": "local:iso/FluxLive-111.iso"}],
        )

        result = await provisioner.refresh_iso("node1", "local")

        assert result.ok is True
        assert result.changed is False
        assert result.iso == "FluxLive-111.iso"
        mock_api.upload_file.assert_not_called()

    async def test_unreachable_release_feed(self, provisioner: Provisioner, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("arcane_mage.provisioner.do_http", AsyncMock(return_value=None))

        result = await provisioner.refresh_iso("node1", "local")

        assert result.ok is False
        assert result.error is not None

    async def test_malformed_release_response(self, provisioner: Provisioner, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "arcane_mage.provisioner.do_http",
            AsyncMock(return_value=self._release(checksums=None)),
        )

        result = await provisioner.refresh_iso("node1", "local")

        assert result.ok is False
        assert "Malformed" in result.error

    async def test_downloads_verifies_and_uploads_when_stale(
        self, provisioner: Provisioner, mock_api: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        iso_bytes = b"fake-iso-bytes"
        digest = hashlib.sha256(iso_bytes).hexdigest()

        monkeypatch.setattr(
            "arcane_mage.provisioner.do_http",
            AsyncMock(return_value=self._release(iso="FluxLive-222.iso", build="222", checksums="sums-222.sha256")),
        )
        mock_api.get_storage_content.return_value = ApiResponse(status=200, payload=[])

        async def fake_download(url: str, dest_path, **kwargs) -> bool:
            if dest_path.name == "FluxLive-222.iso":
                dest_path.write_bytes(iso_bytes)
            else:
                dest_path.write_text(f"{digest}  FluxLive-222.iso\n")
            return True

        monkeypatch.setattr("arcane_mage.provisioner.do_http_to_file", fake_download)
        mock_api.upload_file.return_value = ApiResponse(status=200, payload="UPID:task")
        mock_api.wait_for_task.return_value = True

        result = await provisioner.refresh_iso("node1", "local", current_iso="FluxLive-111.iso")

        assert result.ok is True
        assert result.changed is True
        assert result.iso == "FluxLive-222.iso"
        assert result.previous == "FluxLive-111.iso"
        assert result.build == "222"
        mock_api.upload_file.assert_awaited_once()
        _, kwargs = mock_api.upload_file.call_args
        assert kwargs["content"] == "iso"
        assert kwargs["file_name"] == "FluxLive-222.iso"
        mock_api.wait_for_task.assert_awaited_once()

    async def test_checksum_mismatch_fails_closed(
        self, provisioner: Provisioner, mock_api: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "arcane_mage.provisioner.do_http",
            AsyncMock(return_value=self._release(iso="FluxLive-333.iso", build="333", checksums="sums-333.sha256")),
        )
        mock_api.get_storage_content.return_value = ApiResponse(status=200, payload=[])

        async def fake_download(url: str, dest_path, **kwargs) -> bool:
            if dest_path.name == "FluxLive-333.iso":
                dest_path.write_bytes(b"some-iso-bytes")
            else:
                dest_path.write_text("0000000000000000000000000000000000000000000000000000000000000000  FluxLive-333.iso\n")
            return True

        monkeypatch.setattr("arcane_mage.provisioner.do_http_to_file", fake_download)

        result = await provisioner.refresh_iso("node1", "local")

        assert result.ok is False
        assert "Checksum mismatch" in result.error
        mock_api.upload_file.assert_not_called()

    async def test_download_failure_fails_closed(
        self, provisioner: Provisioner, mock_api: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "arcane_mage.provisioner.do_http", AsyncMock(return_value=self._release())
        )
        mock_api.get_storage_content.return_value = ApiResponse(status=200, payload=[])
        monkeypatch.setattr(
            "arcane_mage.provisioner.do_http_to_file", AsyncMock(return_value=False)
        )

        result = await provisioner.refresh_iso("node1", "local")

        assert result.ok is False
        assert "Failed to download" in result.error
        mock_api.upload_file.assert_not_called()


class TestStandalonePath:
    """The cluster work's headline claim is that a standalone Proxmox server behaves
    exactly as it did before. Two seams carry that claim, and neither was pinned:
    detection has to leave ``cluster`` as None, and ``provision_node`` has to skip the
    whole pre-flight block when it is. Each test carries its cluster-side control so a
    detector that always returned None, or a pre-flight that never ran, cannot pass."""

    @pytest.fixture
    def mock_api(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def provisioner(self, mock_api: AsyncMock) -> Provisioner:
        return Provisioner(api=mock_api)

    STANDALONE_STATUS = [{"type": "node", "name": "pve50", "online": 1, "local": 1}]
    CLUSTER_STATUS = [
        {"type": "cluster", "name": "moltentech", "quorate": 1},
        {"type": "node", "name": "pve55", "online": 1, "local": 1},
        {"type": "node", "name": "pve30", "online": 1, "local": 0},
    ]
    STORAGE = [{"storage": "local-lvm", "shared": 0, "content": "images"}]

    async def test_detect_cluster_leaves_a_standalone_host_uncluttered(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        """A standalone node's /cluster/status has no type=cluster entry, so nothing
        cluster-shaped may be built from it."""
        mock_api.get_storage_config.return_value = ApiResponse(status=200, payload=self.STORAGE)
        mock_api.get_cluster_status.return_value = ApiResponse(
            status=200, payload=self.STANDALONE_STATUS
        )

        await provisioner.detect_cluster()

        assert provisioner.cluster is None

        # Control: the same call against a real cluster must set it, or the assertion
        # above would hold for a detector that had stopped working entirely.
        mock_api.get_cluster_status.return_value = ApiResponse(
            status=200, payload=self.CLUSTER_STATUS
        )

        await provisioner.detect_cluster()

        assert provisioner.cluster is not None
        assert provisioner.cluster.cluster_name == "moltentech"

    async def test_provision_node_skips_the_cluster_preflight_when_standalone(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        """With no cluster context, provisioning must reach the version check without
        emitting a cluster step or making a cluster-wide call — that step count is what
        the CLI's --json consumers see."""
        fluxnode = AsyncMock()
        fluxnode.hypervisor.node_tier = "cumulus"
        fluxnode.hypervisor.node = "pve50"
        fluxnode.hypervisor.vm_name = "ms-186-c6"

        # Fail at the first step after the pre-flight block, so the run stops somewhere
        # provable rather than walking the whole provision.
        mock_api.get_api_version.return_value = ApiResponse(status=500, error="unreachable")

        messages: list[tuple[bool, str]] = []
        result = await provisioner.provision_node(
            fluxnode, callback=lambda ok, msg: messages.append((ok, msg))
        )

        assert result is False
        assert any("Unable to get Proxmox api version" in msg for _, msg in messages)
        assert not any("luster" in msg for _, msg in messages)
        mock_api.get_cluster_resources.assert_not_called()

        # Control: the same node under a cluster that has lost quorum must be refused
        # before the version check is ever reached.
        provisioner.cluster = ClusterContext(
            is_cluster=True, cluster_name="moltentech", has_quorum=False
        )

        messages.clear()
        result = await provisioner.provision_node(
            fluxnode, callback=lambda ok, msg: messages.append((ok, msg))
        )

        assert result is False
        assert any("lost quorum" in msg for _, msg in messages)


class TestClusterDetectionIsNotSilent:
    """A failed detection read and a standalone host both leave ``cluster`` as None,
    and only one of them is safe to provision on. Proxmox makes them easy to confuse:
    a least-privilege token gets a permission result that reads as an absence of
    features, not as a refusal. Detection must keep the two apart."""

    @pytest.fixture
    def mock_api(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def provisioner(self, mock_api: AsyncMock) -> Provisioner:
        return Provisioner(api=mock_api)

    @staticmethod
    def _fluxnode() -> AsyncMock:
        fluxnode = AsyncMock()
        fluxnode.hypervisor.node_tier = "cumulus"
        fluxnode.hypervisor.node = "pve55"
        fluxnode.hypervisor.vm_name = "mt-187-c2"
        return fluxnode

    async def test_an_unreadable_cluster_status_is_not_a_standalone_host(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        mock_api.get_cluster_status.return_value = ApiResponse(
            status=403, error="Permission check failed"
        )

        await provisioner.detect_cluster()

        assert provisioner.cluster is None
        assert provisioner.cluster_detection_error is not None
        assert "/cluster/status" in provisioner.cluster_detection_error
        assert "Permission check failed" in provisioner.cluster_detection_error

    async def test_provisioning_refuses_when_membership_is_unknown(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        """The pre-flight checks are the point. Skipping them silently because the
        token could not answer is the failure this guards."""
        mock_api.get_cluster_status.return_value = ApiResponse(status=403, error="denied")

        await provisioner.detect_cluster()

        messages: list[tuple[bool, str]] = []
        result = await provisioner.provision_node(
            self._fluxnode(), callback=lambda ok, msg: messages.append((ok, msg))
        )

        assert result is False
        assert any("cluster membership is unknown" in msg for _, msg in messages)
        # It must stop at the gate, not fail later and further in.
        mock_api.get_api_version.assert_not_called()

    async def test_a_cluster_whose_storage_cannot_be_read_is_reported_not_downgraded(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        """/storage classifies shared-vs-local for EFI dedup. Losing it on a host we
        know is clustered must not quietly demote that host to standalone."""
        mock_api.get_cluster_status.return_value = ApiResponse(
            status=200,
            payload=[
                {"type": "cluster", "name": "moltentech", "quorate": 1},
                {"type": "node", "name": "pve55", "online": 1, "local": 1},
            ],
        )
        mock_api.get_storage_config.return_value = ApiResponse(status=500, error="boom")

        await provisioner.detect_cluster()

        assert provisioner.cluster_detection_error is not None
        assert "/storage" in provisioner.cluster_detection_error

    async def test_a_standalone_host_never_needs_storage_to_prove_itself(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        """/storage exists here only to classify cluster storage. Requiring it on a
        standalone host would refuse provisioning that works today."""
        mock_api.get_cluster_status.return_value = ApiResponse(
            status=200, payload=[{"type": "node", "name": "pve50", "online": 1, "local": 1}]
        )
        mock_api.get_storage_config.return_value = ApiResponse(status=403, error="denied")

        await provisioner.detect_cluster()

        assert provisioner.cluster is None
        assert provisioner.cluster_detection_error is None
        mock_api.get_storage_config.assert_not_called()

    async def test_a_later_clean_detection_clears_a_stale_error(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        """The error is per-detection state, not a latch — a token fixed between
        attempts must not stay locked out."""
        mock_api.get_cluster_status.return_value = ApiResponse(status=403, error="denied")
        await provisioner.detect_cluster()
        assert provisioner.cluster_detection_error is not None

        mock_api.get_cluster_status.return_value = ApiResponse(
            status=200, payload=[{"type": "node", "name": "pve50", "online": 1, "local": 1}]
        )
        await provisioner.detect_cluster()

        assert provisioner.cluster_detection_error is None


class TestInstallDiskRaces:
    """prod 2026-09-28, pve65: VM 268's create ran past the 10 s wait, and the give-up path
    deleted its config image from under the still-running create. And two independent runs on
    one host shared ``arcane_efi.raw``, so either could delete the other's EFI mid-create."""

    @pytest.fixture
    def mock_api(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def provisioner(self, mock_api: AsyncMock) -> Provisioner:
        return Provisioner(api=mock_api)

    async def test_create_waits_long_enough_for_a_busy_host(self, provisioner: Provisioner, mock_api: AsyncMock):
        from arcane_mage.provisioner import CREATE_TASK_MAX_WAIT_S

        mock_api.create_vm.return_value = ApiResponse(status=200, payload="UPID:create")
        mock_api.wait_for_task.return_value = True
        config = await provisioner.create_vm_config(vm_name="n", tier="cumulus", network_bridge="vmbr0", vm_id=268)
        assert config is not None

        assert await provisioner.create_vm(config, "node1") is True
        mock_api.wait_for_task.assert_awaited_once_with("UPID:create", "node1", CREATE_TASK_MAX_WAIT_S)
        assert CREATE_TASK_MAX_WAIT_S >= 300

    async def test_uploads_wait_longer_than_ten_seconds(self, provisioner: Provisioner, mock_api: AsyncMock):
        from arcane_mage.provisioner import UPLOAD_TASK_MAX_WAIT_S

        mock_api.upload_file.return_value = ApiResponse(status=200, payload="UPID:up")
        mock_api.wait_for_task.return_value = True

        assert await provisioner.upload_arcane_efi("node1", "local", "268_arcane_efi.raw") is True
        assert mock_api.upload_file.call_args.kwargs["file_name"] == "268_arcane_efi.raw"
        mock_api.wait_for_task.assert_awaited_with("UPID:up", "node1", UPLOAD_TASK_MAX_WAIT_S)

        assert await provisioner.upload_arcane_config(b"nodes: []", 268, "node1", "local") is True
        mock_api.wait_for_task.assert_awaited_with("UPID:up", "node1", UPLOAD_TASK_MAX_WAIT_S)

    async def test_per_vm_efi_is_imported_and_deleted_by_its_own_name(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        own = await provisioner.create_vm_config(
            vm_name="n", tier="cumulus", network_bridge="vmbr0", vm_id=268, per_vm_efi=True
        )
        shared = await provisioner.create_vm_config(vm_name="n", tier="cumulus", network_bridge="vmbr0", vm_id=269)
        assert own is not None and shared is not None
        assert "import-from=local:import/268_arcane_efi.raw" in own.efidisk0
        assert "import-from=local:import/arcane_efi.raw" in shared.efidisk0

        mock_api.delete_file.return_value = ApiResponse(status=200, payload="UPID:del")
        mock_api.wait_for_task.return_value = True
        assert await provisioner.delete_install_disks(268, "node1", "local", True, "268_arcane_efi.raw") is True
        deleted = [c.args[0] for c in mock_api.delete_file.call_args_list]
        assert deleted == ["268_arcane_efi.raw", "268_arcane_config.raw"]


class TestWaitForTask:
    async def test_a_failed_task_returns_at_once_not_after_max_wait(self):
        from time import monotonic

        from arcane_mage.proxmox import ProxmoxApi

        api = ProxmoxApi.__new__(ProxmoxApi)
        api.get_task = AsyncMock(
            return_value=ApiResponse(status=200, payload={"status": "stopped", "exitstatus": "unable to create VM 268"})
        )
        start = monotonic()
        assert await api.wait_for_task("UPID:x", "node1", max_wait_s=600) is False
        assert monotonic() - start < 2
        assert api.last_task_error == "unable to create VM 268"

    async def test_a_task_that_outruns_the_wait_says_so(self):
        from arcane_mage.proxmox import ProxmoxApi

        api = ProxmoxApi.__new__(ProxmoxApi)
        api.get_task = AsyncMock(return_value=ApiResponse(status=200, payload={"status": "running"}))
        assert await api.wait_for_task("UPID:x", "node1", max_wait_s=1) is False
        assert api.last_task_error == "task still running after 1s"

    async def test_an_unreadable_task_says_so(self):
        from arcane_mage.proxmox import ProxmoxApi

        api = ProxmoxApi.__new__(ProxmoxApi)
        api.get_task = AsyncMock(return_value=ApiResponse(status=400, error="bad upid"))
        assert await api.wait_for_task("UPID:x", "node1") is False
        assert api.last_task_error == "task status unreadable (HTTP 400: bad upid)"

    async def test_a_running_task_is_waited_on_until_ok(self):
        from arcane_mage.proxmox import ProxmoxApi

        api = ProxmoxApi.__new__(ProxmoxApi)
        running = ApiResponse(status=200, payload={"status": "running"})
        done = ApiResponse(status=200, payload={"status": "stopped", "exitstatus": "OK"})
        api.get_task = AsyncMock(side_effect=[running, running, done])
        assert await api.wait_for_task("UPID:x", "node1", max_wait_s=600) is True
        assert api.get_task.await_count == 3
        assert api.last_task_error is None


class TestFailureReasons:
    """A create that fails because its EFI import file is missing (staging 08-26) and one
    that fails on a full disk both read "Unable to create VM on hypervisor", so a caller
    could never retry the first safely. Proxmox's own task error now rides on the step.
    And a failed upload can still land its file (prod 09-28, pve35), which then sat in
    local:import for good once the retry picked another vmid."""

    @pytest.fixture
    def mock_api(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def provisioner(self, mock_api: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> Provisioner:
        p = Provisioner(api=mock_api)
        monkeypatch.setattr(p, "validate_api_version", AsyncMock(return_value=(True, None)))
        monkeypatch.setattr(p, "validate_storage", AsyncMock(return_value=(True, None)))
        monkeypatch.setattr(p, "validate_iso_version", AsyncMock(return_value=True))
        monkeypatch.setattr(p, "validate_network", AsyncMock(return_value=True))
        return p

    @staticmethod
    def fluxnode() -> MagicMock:
        node = MagicMock()
        node.to_dict.return_value = {}
        hv = node.hypervisor
        hv.node, hv.vm_name, hv.vm_id, hv.node_tier = "pve35", "fh-mt-187-c4", 264, "cumulus"
        hv.network, hv.iso_name = "vmbr0", "FluxLive.iso"
        hv.storage_images, hv.storage_iso, hv.storage_import = "local-lvm", "local", "local"
        hv.disk_limit = hv.memory_mb = hv.cpu_limit = hv.network_limit = None
        hv.startup_config = hv.tags = hv.description = None
        hv.start_on_creation = True
        return node

    async def run(self, provisioner: Provisioner) -> list[str]:
        failed: list[str] = []
        ok = await provisioner.provision_node(
            self.fluxnode(), callback=lambda good, msg: None if good else failed.append(msg)
        )
        assert ok is False
        return failed

    async def test_a_failed_create_carries_the_proxmox_task_error(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        why = "unable to create VM 264 - cannot import from 'local:import/264_arcane_efi.raw'"
        mock_api.upload_file.return_value = ApiResponse(status=200, payload="UPID:up")
        mock_api.create_vm.return_value = ApiResponse(status=200, payload="UPID:create")
        mock_api.delete_file.return_value = ApiResponse(status=200, payload="UPID:del")

        async def wait(task: str, node: str, max_wait_s: int = 10) -> bool:
            mock_api.last_task_error = why if task == "UPID:create" else None
            return task != "UPID:create"

        mock_api.wait_for_task.side_effect = wait

        assert await self.run(provisioner) == [f"Unable to create VM on hypervisor: {why}"]

    async def test_a_refused_request_names_the_http_error(self, provisioner: Provisioner, mock_api: AsyncMock):
        mock_api.upload_file.return_value = ApiResponse(status=200, payload="UPID:up")
        mock_api.wait_for_task.return_value = True
        mock_api.last_task_error = None
        mock_api.create_vm.return_value = ApiResponse(status=500, error="storage 'local-lvm' is full")
        mock_api.delete_file.return_value = ApiResponse(status=200, payload="UPID:del")

        assert await self.run(provisioner) == [
            "Unable to create VM on hypervisor: HTTP 500: storage 'local-lvm' is full"
        ]

    async def test_a_failed_config_upload_deletes_its_own_file(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        mock_api.upload_file.return_value = ApiResponse(status=200, payload="UPID:up")
        mock_api.delete_file.return_value = ApiResponse(status=200, payload="UPID:del")

        async def wait(task: str, node: str, max_wait_s: int = 10) -> bool:
            mock_api.last_task_error = "task status request timed out" if task == "UPID:up" else None
            return task != "UPID:up"

        mock_api.wait_for_task.side_effect = wait

        assert await self.run(provisioner) == [
            "Unable to upload Config image to hypervisor: task status request timed out"
        ]
        # Only its own config image: the EFI was never uploaded, and the shared name may
        # belong to another run on the host.
        assert [c.args[0] for c in mock_api.delete_file.call_args_list] == ["264_arcane_config.raw"]
        mock_api.create_vm.assert_not_called()

    async def test_a_failed_efi_upload_deletes_both_of_its_files(
        self, provisioner: Provisioner, mock_api: AsyncMock
    ):
        mock_api.upload_file.side_effect = [
            ApiResponse(status=200, payload="UPID:config"),
            ApiResponse(status=200, payload="UPID:efi"),
        ]
        mock_api.delete_file.return_value = ApiResponse(status=200, payload="UPID:del")

        async def wait(task: str, node: str, max_wait_s: int = 10) -> bool:
            mock_api.last_task_error = "no space left on device" if task == "UPID:efi" else None
            return task != "UPID:efi"

        mock_api.wait_for_task.side_effect = wait

        assert await self.run(provisioner) == [
            "Unable to upload EFI image to hypervisor: no space left on device"
        ]
        assert [c.args[0] for c in mock_api.delete_file.call_args_list] == [
            "264_arcane_efi.raw",
            "264_arcane_config.raw",
        ]
        mock_api.create_vm.assert_not_called()
