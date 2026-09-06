from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from arcane_mage.batch import BatchProvisioner, BatchResult, NodePlan
from arcane_mage.models import ArcaneOsConfig
from arcane_mage.models.cluster import ClusterContext, ClusterNode, ClusterStorage


def _make_node(node_name: str, vm_name: str, storage_import: str = "local") -> ArcaneOsConfig:
    """Build a minimal ArcaneOsConfig with just enough hypervisor info for planning."""
    return ArcaneOsConfig.from_dict({
        "fluxnode": {
            "identity": {
                "flux_id": "122Q5f5dJdiaoNP7iLbBgEt5fVF3g3DDeA",
                "identity_key": "L4yreKb7oFfok5i38Zi5DZo7vA7wdjrGhs8gdPqNNxdsuNBaywcR",
                "tx_id": "657e17cd88d2e7993b62dfc957baedf7b026b0ae31083d30eb7c8851a2dd91ba",
                "output_id": 0,
            },
        },
        "system": {"hostname": vm_name, "hashed_console": "!"},
        "hypervisor": {
            "node": node_name,
            "vm_name": vm_name,
            "node_tier": "cumulus",
            "network": "vmbr0",
            "iso_name": "FluxLive-1749291196.iso",
            "storage_import": storage_import,
        },
    })


def _make_cluster_context(shared_storages: list[str] | None = None) -> ClusterContext:
    """Build a ClusterContext with configurable shared storage names."""
    shared = set(shared_storages or [])
    return ClusterContext(
        is_cluster=True,
        cluster_name="test",
        has_quorum=True,
        nodes=[
            ClusterNode("pve1", online=True, local=True),
            ClusterNode("pve2", online=True, local=False),
        ],
        storage=[
            ClusterStorage("local", shared=False, content="images,import"),
            *(
                ClusterStorage(name, shared=True, content="images,import")
                for name in shared
            ),
        ],
    )


class TestBuildPlan:
    def test_single_node(self):
        batch = BatchProvisioner(MagicMock(), cluster=None)
        nodes = [_make_node("pve1", "vm1")]
        plan = batch._build_plan(nodes)

        assert len(plan) == 1
        assert plan[0].skip_efi_upload is False
        assert plan[0].delete_efi is True

    def test_local_storage_no_cluster(self):
        batch = BatchProvisioner(MagicMock(), cluster=None)
        nodes = [
            _make_node("pve1", "vm1"),
            _make_node("pve1", "vm2"),
            _make_node("pve1", "vm3"),
        ]
        plan = batch._build_plan(nodes)

        # All upload, only last deletes
        assert plan[0].skip_efi_upload is False
        assert plan[0].delete_efi is False
        assert plan[1].skip_efi_upload is False
        assert plan[1].delete_efi is False
        assert plan[2].skip_efi_upload is False
        assert plan[2].delete_efi is True

    def test_shared_storage_upload_once(self):
        cluster = _make_cluster_context(shared_storages=["ceph"])
        batch = BatchProvisioner(MagicMock(), cluster=cluster)
        nodes = [
            _make_node("pve1", "vm1", storage_import="ceph"),
            _make_node("pve1", "vm2", storage_import="ceph"),
            _make_node("pve1", "vm3", storage_import="ceph"),
        ]
        plan = batch._build_plan(nodes)

        # First uploads, middle and last skip
        assert plan[0].skip_efi_upload is False
        assert plan[0].delete_efi is False
        assert plan[1].skip_efi_upload is True
        assert plan[1].delete_efi is False
        assert plan[2].skip_efi_upload is True
        assert plan[2].delete_efi is True

    def test_mixed_shared_and_local_groups(self):
        cluster = _make_cluster_context(shared_storages=["ceph"])
        batch = BatchProvisioner(MagicMock(), cluster=cluster)
        nodes = [
            _make_node("pve1", "vm1", storage_import="ceph"),
            _make_node("pve1", "vm2", storage_import="ceph"),
            _make_node("pve2", "vm3", storage_import="local"),
            _make_node("pve2", "vm4", storage_import="local"),
        ]
        plan = batch._build_plan(nodes)

        # ceph group: first uploads, second skips + deletes
        assert plan[0].skip_efi_upload is False
        assert plan[0].delete_efi is False
        assert plan[1].skip_efi_upload is True
        assert plan[1].delete_efi is True

        # local group: both upload, only last deletes
        assert plan[2].skip_efi_upload is False
        assert plan[2].delete_efi is False
        assert plan[3].skip_efi_upload is False
        assert plan[3].delete_efi is True

    def test_empty_nodes(self):
        batch = BatchProvisioner(MagicMock(), cluster=None)
        plan = batch._build_plan([])
        assert plan == []


class TestProvisionBatch:
    @pytest.mark.asyncio
    async def test_all_succeed(self):
        provisioner = MagicMock()
        provisioner.provision_node = AsyncMock(return_value=True)

        batch = BatchProvisioner(provisioner, cluster=None)
        nodes = [_make_node("pve1", "vm1"), _make_node("pve1", "vm2")]
        results = await batch.provision_batch(nodes)

        assert len(results) == 2
        assert all(r.ok for r in results)
        assert provisioner.provision_node.call_count == 2

    @pytest.mark.asyncio
    async def test_failure_does_not_short_circuit(self):
        provisioner = MagicMock()
        provisioner.provision_node = AsyncMock(side_effect=[False, True])

        batch = BatchProvisioner(provisioner, cluster=None)
        nodes = [_make_node("pve1", "vm1"), _make_node("pve1", "vm2")]
        results = await batch.provision_batch(nodes)

        assert len(results) == 2
        assert results[0].ok is False
        assert results[1].ok is True

    @pytest.mark.asyncio
    async def test_shared_storage_failure_recovery(self):
        """If the first node (EFI uploader) fails, the next node should upload EFI."""
        cluster = _make_cluster_context(shared_storages=["ceph"])
        provisioner = MagicMock()
        provisioner.provision_node = AsyncMock(side_effect=[False, True])

        batch = BatchProvisioner(provisioner, cluster=cluster)
        nodes = [
            _make_node("pve1", "vm1", storage_import="ceph"),
            _make_node("pve1", "vm2", storage_import="ceph"),
        ]
        results = await batch.provision_batch(nodes)

        # Second call should NOT have skip_efi_upload=True since first failed
        second_call = provisioner.provision_node.call_args_list[1]
        assert second_call.kwargs.get("skip_efi_upload") is False

    @pytest.mark.asyncio
    async def test_callback_receives_fluxnode(self):
        provisioner = MagicMock()
        provisioner.provision_node = AsyncMock(return_value=True)

        batch = BatchProvisioner(provisioner, cluster=None)
        nodes = [_make_node("pve1", "vm1")]

        received = []

        def cb(fluxnode, ok, msg):
            received.append((fluxnode.system.hostname, ok, msg))

        # The callback is called by provision_node's callback param,
        # but since we mock provision_node, it won't actually call our cb.
        # Instead verify that a callback was passed to provision_node.
        await batch.provision_batch(nodes, callback=cb)

        call_kwargs = provisioner.provision_node.call_args_list[0].kwargs
        assert "callback" in call_kwargs
        assert callable(call_kwargs["callback"])
