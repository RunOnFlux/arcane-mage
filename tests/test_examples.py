"""Every example config's system section is a valid SystemConfig, SSH keys included."""

from pathlib import Path

import pytest
import yaml

from arcane_mage.models import SystemConfig

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.yaml"))


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_example_systems_validate(example):
    nodes = yaml.safe_load(example.read_text())["nodes"]
    for node in nodes:
        SystemConfig.from_dict(node["system"])
