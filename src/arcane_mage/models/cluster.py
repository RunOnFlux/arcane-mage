"""Cluster topology data models for Proxmox cluster-aware provisioning."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClusterNode:
    """A single node in a Proxmox cluster."""

    name: str
    online: bool
    local: bool  # is this the node we connected to


@dataclass(frozen=True)
class ClusterStorage:
    """A storage backend visible to the cluster."""

    name: str
    shared: bool
    content: str  # e.g. "images,iso,import"
    nodes: list[str] | None = None  # None = available on all nodes


@dataclass
class ClusterContext:
    """Read-only snapshot of cluster topology, built once during connection setup.

    Standalone Proxmox servers produce ``is_cluster=False`` with safe defaults.
    """

    is_cluster: bool = False
    cluster_name: str | None = None
    has_quorum: bool = True
    nodes: list[ClusterNode] = field(default_factory=list)
    storage: list[ClusterStorage] = field(default_factory=list)

    @classmethod
    def from_api_responses(
        cls,
        status_payload: list[dict],
        storage_payload: list[dict],
    ) -> ClusterContext:
        """Build from ``GET /cluster/status`` + ``GET /storage`` responses.

        If no ``type=cluster`` entry is present in the status payload, this is
        a standalone node and ``is_cluster`` will be ``False``.
        """
        cluster_item = next(
            (item for item in status_payload if item.get("type") == "cluster"),
            None,
        )

        if not cluster_item:
            return cls(is_cluster=False, has_quorum=True)

        nodes = [
            ClusterNode(
                name=item["name"],
                online=bool(item.get("online", 0)),
                local=bool(item.get("local", 0)),
            )
            for item in status_payload
            if item.get("type") == "node"
        ]

        storage = [
            ClusterStorage(
                name=item["storage"],
                shared=bool(item.get("shared", 0)),
                content=item.get("content", ""),
                nodes=item["nodes"].split(",") if item.get("nodes") else None,
            )
            for item in storage_payload
        ]

        return cls(
            is_cluster=True,
            cluster_name=cluster_item.get("name"),
            has_quorum=bool(cluster_item.get("quorate", 0)),
            nodes=nodes,
            storage=storage,
        )

    def is_storage_shared(self, storage_name: str) -> bool:
        """Return whether the named storage is shared across nodes."""
        return any(s.shared for s in self.storage if s.name == storage_name)

    def is_node_online(self, node_name: str) -> bool:
        """Return whether the named node is online in the cluster."""
        node = next((n for n in self.nodes if n.name == node_name), None)
        return node.online if node else False

    def storage_available_on(self, storage_name: str) -> list[str]:
        """Return the list of online nodes where the named storage is accessible."""
        store = next((s for s in self.storage if s.name == storage_name), None)
        if not store:
            return []

        online_names = {n.name for n in self.nodes if n.online}

        if store.nodes is None:
            return sorted(online_names)

        return sorted(set(store.nodes) & online_names)
