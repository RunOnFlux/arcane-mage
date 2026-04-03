from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arcane_mage.models import ArcaneOsConfig, ArcaneOsConfigGroup


class TestArcaneOsConfig:
    def test_from_dict(self, minimal_node_dict: dict):
        config = ArcaneOsConfig.from_dict(minimal_node_dict)

        assert config.system.hostname == "test-node"
        assert config.fluxnode.identity.flux_id is not None
        assert config.hypervisor is not None
        assert config.hypervisor.node == "bigchug"

    def test_from_dict_missing_fluxnode(self, minimal_node_dict: dict):
        del minimal_node_dict["fluxnode"]

        with pytest.raises(ValueError, match="fluxnode config missing"):
            ArcaneOsConfig.from_dict(minimal_node_dict)

    def test_from_dict_missing_system(self, minimal_node_dict: dict):
        del minimal_node_dict["system"]

        with pytest.raises(ValueError, match="system config missing"):
            ArcaneOsConfig.from_dict(minimal_node_dict)

    def test_to_dict_roundtrip(self, minimal_node_dict: dict):
        config = ArcaneOsConfig.from_dict(minimal_node_dict)
        result = config.to_dict()

        assert "fluxnode" in result
        assert "system" in result
        assert "hypervisor" in result

    def test_as_row(self, minimal_node_dict: dict):
        config = ArcaneOsConfig.from_dict(minimal_node_dict)
        row = config.as_row()

        assert row[0] == "bigchug"  # node
        assert row[1] == "test-node"  # hostname
        assert row[2] == "cumulus"  # tier
        assert row[3] == "vmbr0"  # network
        assert row[4] == "dhcp"  # address (no static config)


class TestArcaneOsConfigGroup:
    def test_from_dict(self, minimal_config_dict: dict):
        group = ArcaneOsConfigGroup.from_dict(minimal_config_dict)

        assert len(group) == 1
        assert group.first is not None
        assert group.first.system.hostname == "test-node"

    def test_from_dict_empty_nodes_raises(self):
        with pytest.raises(ValueError, match="must contain nodes"):
            ArcaneOsConfigGroup.from_dict({"nodes": []})

    def test_from_dict_missing_nodes_raises(self):
        with pytest.raises(ValueError, match="must contain nodes"):
            ArcaneOsConfigGroup.from_dict({})

    def test_iteration(self, minimal_config_dict: dict):
        group = ArcaneOsConfigGroup.from_dict(minimal_config_dict)

        nodes = list(group)
        assert len(nodes) == 1

    def test_first_rest_last(self, minimal_config_dict: dict):
        group = ArcaneOsConfigGroup.from_dict(minimal_config_dict)

        assert group.first is not None
        assert group.last is not None
        assert group.first == group.last
        assert group.rest == []

    def test_empty_group(self):
        group = ArcaneOsConfigGroup()

        assert len(group) == 0
        assert group.first is None
        assert group.last is None
        assert group.rest == []

    def test_get_node_by_vm_name(self, minimal_config_dict: dict):
        group = ArcaneOsConfigGroup.from_dict(minimal_config_dict)

        found = group.get_node_by_vm_name("bigchug", "graham")
        assert found is not None
        assert found.system.hostname == "test-node"

    def test_get_node_by_vm_name_not_found(self, minimal_config_dict: dict):
        group = ArcaneOsConfigGroup.from_dict(minimal_config_dict)

        assert group.get_node_by_vm_name("bigchug", "nonexistent") is None

    def test_get_nodes_by_hypervisor_name(self, minimal_config_dict: dict):
        group = ArcaneOsConfigGroup.from_dict(minimal_config_dict)

        filtered = group.get_nodes_by_hypervisor_name("bigchug")
        assert len(filtered) == 1

    def test_get_nodes_by_hypervisor_name_none(self, minimal_config_dict: dict):
        group = ArcaneOsConfigGroup.from_dict(minimal_config_dict)

        filtered = group.get_nodes_by_hypervisor_name("nonexistent")
        assert len(filtered) == 0

    def test_from_fs(self, tmp_path: Path, minimal_config_dict: dict):
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml.dump(minimal_config_dict))

        group = ArcaneOsConfigGroup.from_fs(config_file)

        assert len(group) == 1

    def test_from_fs_file_not_found(self, tmp_path: Path):
        group = ArcaneOsConfigGroup.from_fs(tmp_path / "nonexistent.yaml")

        assert len(group) == 0

    def test_from_fs_invalid_yaml(self, tmp_path: Path):
        config_file = tmp_path / "bad.yaml"
        config_file.write_text(": invalid: yaml: {{{{")

        group = ArcaneOsConfigGroup.from_fs(config_file)

        assert len(group) == 0

    def test_add_nodes(self, minimal_config_dict: dict):
        group1 = ArcaneOsConfigGroup.from_dict(minimal_config_dict)
        group2 = ArcaneOsConfigGroup.from_dict(minimal_config_dict)

        group1.add_nodes(group2)

        assert len(group1) == 2

    def test_to_dict(self, minimal_config_dict: dict):
        group = ArcaneOsConfigGroup.from_dict(minimal_config_dict)
        result = group.to_dict()

        assert "nodes" in result
        assert len(result["nodes"]) == 1
