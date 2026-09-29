"""Batch provisioning with cluster-aware optimizations."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from .models import ArcaneOsConfig
from .models.cluster import ClusterContext
from .provisioner import Provisioner

log = logging.getLogger(__name__)


@dataclass
class NodePlan:
    """Per-node provisioning instructions computed by BatchProvisioner."""

    fluxnode: ArcaneOsConfig
    skip_efi_upload: bool = False
    delete_efi: bool = True


@dataclass
class BatchResult:
    """Result of provisioning a single node within a batch."""

    fluxnode: ArcaneOsConfig
    ok: bool


class BatchProvisioner:
    """Coordinates provisioning multiple VMs with cluster-aware optimizations.

    Handles upload deduplication for shared storage, failure recovery
    (re-uploads if a prior node on shared storage failed), and EFI
    cleanup sequencing.
    """

    def __init__(
        self,
        provisioner: Provisioner,
        cluster: ClusterContext | None = None,
    ) -> None:
        self.provisioner = provisioner
        self.cluster = cluster

    def _build_plan(self, nodes: list[ArcaneOsConfig]) -> list[NodePlan]:
        """Build provisioning plan with EFI upload/delete optimization.

        Groups nodes by ``(hypervisor_node, storage_import)``. For shared
        storage, only the first node in each group uploads the EFI image, and
        only the last deletes it. For non-shared storage (or no cluster
        context), every node uploads and only the last in the full batch
        deletes — matching current behaviour.
        """
        if not nodes:
            return []

        # Group indices by (hypervisor_node, storage_import)
        groups: dict[tuple[str, str], list[int]] = defaultdict(list)
        for i, node in enumerate(nodes):
            hv = node.hypervisor
            if hv:
                groups[(hv.node, hv.storage_import)].append(i)

        plans = [NodePlan(fluxnode=n) for n in nodes]

        for (_, storage), indices in groups.items():
            is_shared = (
                self.cluster is not None
                and self.cluster.is_storage_shared(storage)
            )

            if is_shared:
                # Shared storage: upload once, delete once
                for j, idx in enumerate(indices):
                    plans[idx].skip_efi_upload = j > 0  # only first uploads
                    plans[idx].delete_efi = j == len(indices) - 1  # only last deletes
            else:
                # Local storage: every node uploads, only last deletes
                for j, idx in enumerate(indices):
                    plans[idx].skip_efi_upload = False
                    plans[idx].delete_efi = j == len(indices) - 1

        return plans

    async def provision_batch(
        self,
        nodes: list[ArcaneOsConfig],
        callback: Callable[[ArcaneOsConfig, bool, str], None] | None = None,
    ) -> list[BatchResult]:
        """Provision multiple nodes with cluster-aware optimizations.

        Builds an upload plan based on storage topology, iterates through
        nodes, and adjusts the plan on failure. The callback receives
        ``(fluxnode, ok, message)`` so callers know which node each status
        update belongs to.

        Does not short-circuit on failure — all nodes are attempted and results
        are returned for every node.
        """
        plan = self._build_plan(nodes)
        results: list[BatchResult] = []

        # Track which groups have had a successful EFI upload
        efi_uploaded: set[tuple[str, str]] = set()

        for i, entry in enumerate(plan):
            hv = entry.fluxnode.hypervisor
            group_key = (hv.node, hv.storage_import) if hv else ("", "")

            # Failure recovery: if we should skip upload but EFI was never
            # successfully uploaded for this group, upload anyway.
            skip = entry.skip_efi_upload
            if skip and group_key not in efi_uploaded:
                skip = False

            def node_callback(ok: bool, msg: str, _node=entry.fluxnode) -> None:
                if callback:
                    callback(_node, ok, msg)

            ok = await self.provisioner.provision_node(
                entry.fluxnode,
                callback=node_callback,
                delete_efi=entry.delete_efi,
                skip_efi_upload=skip,
            )

            if ok and not skip:
                efi_uploaded.add(group_key)

            results.append(BatchResult(fluxnode=entry.fluxnode, ok=ok))

        return results
