from __future__ import annotations

from arcane_mage.models import ClusterNode, ClusterStatus


class TestClusterStatus:
    def test_a_standalone_host_is_not_a_cluster(self):
        assert ClusterStatus.from_entries([{"type": "node", "name": "pve1", "online": 1, "local": 1}]) is None

    def test_a_cluster_with_its_members(self):
        status = ClusterStatus.from_entries(
            [
                {"type": "cluster", "name": "lab", "quorate": 1},
                {"type": "node", "name": "pve1", "online": 1, "local": 1},
                {"type": "node", "name": "pve2", "online": 0},
            ]
        )

        assert status == ClusterStatus(
            name="lab",
            quorate=True,
            nodes=(ClusterNode("pve1", online=True, local=True), ClusterNode("pve2", online=False, local=False)),
        )
        assert status.local_node == ClusterNode("pve1", online=True, local=True)
        assert status.node("pve2") == ClusterNode("pve2", online=False, local=False)
        assert status.node("pve9") is None
