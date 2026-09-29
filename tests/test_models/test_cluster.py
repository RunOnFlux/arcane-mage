from __future__ import annotations

from arcane_mage.models.cluster import ClusterContext, ClusterNode, ClusterStorage


class TestClusterContextFromApiResponses:
    def test_standalone_no_cluster_entry(self):
        status = [{"type": "node", "name": "pve1", "online": 1, "local": 1}]
        storage = [{"storage": "local", "shared": 0, "content": "images"}]

        ctx = ClusterContext.from_api_responses(status, storage)

        assert ctx.is_cluster is False
        assert ctx.has_quorum is True
        assert ctx.nodes == []
        assert ctx.storage == []

    def test_cluster_with_quorum(self):
        status = [
            {"type": "cluster", "name": "moltentech", "quorate": 1, "version": 6},
            {"type": "node", "name": "pve35", "online": 1, "local": 1},
            {"type": "node", "name": "pve45", "online": 1, "local": 0},
            {"type": "node", "name": "pve50", "online": 0, "local": 0},
        ]
        storage = [
            {"storage": "local", "shared": 0, "content": "images"},
            {"storage": "ceph-pool", "shared": 1, "content": "images,import"},
            {"storage": "nfs-share", "shared": 1, "content": "iso", "nodes": "pve35,pve45"},
        ]

        ctx = ClusterContext.from_api_responses(status, storage)

        assert ctx.is_cluster is True
        assert ctx.cluster_name == "moltentech"
        assert ctx.has_quorum is True
        assert len(ctx.nodes) == 3
        assert len(ctx.storage) == 3

    def test_cluster_without_quorum(self):
        status = [
            {"type": "cluster", "name": "test", "quorate": 0},
            {"type": "node", "name": "pve1", "online": 1, "local": 1},
        ]
        storage = []

        ctx = ClusterContext.from_api_responses(status, storage)

        assert ctx.is_cluster is True
        assert ctx.has_quorum is False

    def test_node_online_and_local_flags(self):
        status = [
            {"type": "cluster", "name": "test", "quorate": 1},
            {"type": "node", "name": "local-node", "online": 1, "local": 1},
            {"type": "node", "name": "remote-node", "online": 1, "local": 0},
            {"type": "node", "name": "offline-node", "online": 0, "local": 0},
        ]

        ctx = ClusterContext.from_api_responses(status, [])

        local = next(n for n in ctx.nodes if n.name == "local-node")
        assert local.online is True
        assert local.local is True

        remote = next(n for n in ctx.nodes if n.name == "remote-node")
        assert remote.online is True
        assert remote.local is False

        offline = next(n for n in ctx.nodes if n.name == "offline-node")
        assert offline.online is False
        assert offline.local is False

    def test_storage_shared_flag(self):
        storage = [
            {"storage": "local-lvm", "shared": 0, "content": "images"},
            {"storage": "ceph-rbd", "shared": 1, "content": "images,import"},
        ]
        status = [{"type": "cluster", "name": "test", "quorate": 1}]

        ctx = ClusterContext.from_api_responses(status, storage)

        local = next(s for s in ctx.storage if s.name == "local-lvm")
        assert local.shared is False

        ceph = next(s for s in ctx.storage if s.name == "ceph-rbd")
        assert ceph.shared is True

    def test_storage_nodes_restriction(self):
        storage = [
            {"storage": "nfs", "shared": 1, "content": "iso", "nodes": "pve1,pve2"},
            {"storage": "ceph", "shared": 1, "content": "images"},
        ]
        status = [{"type": "cluster", "name": "test", "quorate": 1}]

        ctx = ClusterContext.from_api_responses(status, storage)

        nfs = next(s for s in ctx.storage if s.name == "nfs")
        assert nfs.nodes == ["pve1", "pve2"]

        ceph = next(s for s in ctx.storage if s.name == "ceph")
        assert ceph.nodes is None


class TestClusterContextHelpers:
    def _make_context(self):
        return ClusterContext(
            is_cluster=True,
            cluster_name="test",
            has_quorum=True,
            nodes=[
                ClusterNode("pve1", online=True, local=True),
                ClusterNode("pve2", online=True, local=False),
                ClusterNode("pve3", online=False, local=False),
            ],
            storage=[
                ClusterStorage("local", shared=False, content="images"),
                ClusterStorage("ceph", shared=True, content="images,import"),
                ClusterStorage("nfs", shared=True, content="iso", nodes=["pve1", "pve2"]),
            ],
        )

    def test_is_storage_shared(self):
        ctx = self._make_context()
        assert ctx.is_storage_shared("ceph") is True
        assert ctx.is_storage_shared("local") is False
        assert ctx.is_storage_shared("nonexistent") is False

    def test_is_node_online(self):
        ctx = self._make_context()
        assert ctx.is_node_online("pve1") is True
        assert ctx.is_node_online("pve2") is True
        assert ctx.is_node_online("pve3") is False
        assert ctx.is_node_online("unknown") is False

    def test_storage_available_on_shared_all_nodes(self):
        ctx = self._make_context()
        # ceph has no node restriction, so available on all online nodes
        available = ctx.storage_available_on("ceph")
        assert available == ["pve1", "pve2"]

    def test_storage_available_on_restricted(self):
        ctx = self._make_context()
        # nfs restricted to pve1, pve2 — both online
        available = ctx.storage_available_on("nfs")
        assert available == ["pve1", "pve2"]

    def test_storage_available_on_local(self):
        ctx = self._make_context()
        # local has no node restriction, available on all online nodes
        available = ctx.storage_available_on("local")
        assert available == ["pve1", "pve2"]

    def test_storage_available_on_nonexistent(self):
        ctx = self._make_context()
        assert ctx.storage_available_on("missing") == []

    def test_storage_available_on_respects_offline(self):
        ctx = ClusterContext(
            is_cluster=True,
            cluster_name="test",
            has_quorum=True,
            nodes=[
                ClusterNode("pve1", online=True, local=True),
                ClusterNode("pve2", online=False, local=False),
            ],
            storage=[
                ClusterStorage("nfs", shared=True, content="iso", nodes=["pve1", "pve2"]),
            ],
        )
        # pve2 is offline, so only pve1
        assert ctx.storage_available_on("nfs") == ["pve1"]
