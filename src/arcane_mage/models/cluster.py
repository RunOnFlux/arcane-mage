from __future__ import annotations

from dataclasses import dataclass

from ..proxmox import ClusterStatusEntry


@dataclass(frozen=True)
class ClusterNode:
    """A member of a Proxmox cluster. `local` marks the node the API connection reaches."""

    name: str
    online: bool
    local: bool


@dataclass(frozen=True)
class ClusterStatus:
    """A Proxmox cluster's name, quorum and members, as `/cluster/status` gives them."""

    name: str
    quorate: bool
    nodes: tuple[ClusterNode, ...]

    @classmethod
    def from_entries(cls, entries: list[ClusterStatusEntry]) -> ClusterStatus | None:
        """The cluster `entries` describe, or None for a standalone host."""
        cluster = next((e for e in entries if e["type"] == "cluster"), None)

        if cluster is None:
            return None

        nodes = tuple(
            ClusterNode(name=e.get("name", ""), online=bool(e.get("online", 0)), local=bool(e.get("local", 0)))
            for e in entries
            if e["type"] == "node"
        )

        return cls(name=cluster.get("name", ""), quorate=bool(cluster.get("quorate", 0)), nodes=nodes)

    def node(self, name: str) -> ClusterNode | None:
        return next((n for n in self.nodes if n.name == name), None)
