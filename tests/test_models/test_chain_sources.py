from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arcane_mage.models.config import ArcaneOsConfig

LAB_KEY = "020483178bc32818eb9922b68ac0771fe95136a6edf746016125270cfc2111fd5a"


async def written_sources(tmp_path: Path, node: dict, sources: list[str], chain: dict | None) -> list[str]:
    node["fluxnode"]["network"] = {"private_chain_sources": sources}
    if chain is not None:
        node["fluxnode"]["chain"] = chain
    path = tmp_path / "installer.yaml"
    assert await ArcaneOsConfig.from_dict(node).write_installer_config(path)
    return yaml.safe_load(path.read_text())["network"]["private_chain_sources"]


async def test_a_mainnet_install_keeps_only_private_chain_sources(tmp_path: Path, minimal_node_dict: dict):
    sources = ["192.168.1.20:16127", "10.0.0.5:16137", "25.47.0.2:16127", "8.8.8.8:16127"]

    kept = await written_sources(tmp_path, minimal_node_dict, sources, None)

    assert kept == ["192.168.1.20:16127", "10.0.0.5:16137"]


async def test_a_labnet_install_keeps_the_labs_donor_on_its_internet(tmp_path: Path, minimal_node_dict: dict):
    sources = ["25.47.0.2:16127", "192.168.147.10:16127"]

    kept = await written_sources(tmp_path, minimal_node_dict, sources, {"network": "labnet", "key": LAB_KEY})

    assert kept == ["25.47.0.2:16127", "192.168.147.10:16127"]


@pytest.mark.parametrize(
    "source",
    [
        "127.0.0.1:16127",
        "0.0.0.0:16127",
        "169.254.10.1:16127",
        "224.0.0.1:16127",
        "255.255.255.255:16127",
        "240.0.0.1:16127",
        "25.47.0.2:99999",
        "25.47.0.2",
        "host.example:16127",
    ],
)
async def test_a_labnet_install_still_drops_what_is_no_unicast_source(
    tmp_path: Path, minimal_node_dict: dict, source: str
):
    kept = await written_sources(tmp_path, minimal_node_dict, [source], {"network": "labnet", "key": LAB_KEY})

    assert kept == []


async def test_an_explicit_mainnet_chain_keeps_only_private_chain_sources(tmp_path: Path, minimal_node_dict: dict):
    kept = await written_sources(tmp_path, minimal_node_dict, ["25.47.0.2:16127"], {"network": "mainnet"})

    assert kept == []


@pytest.mark.parametrize(
    "source",
    [
        "127.0.0.1:16127",
        "0.0.0.0:16127",
        "169.254.10.1:16127",
        "255.255.255.255:16127",
        "240.0.0.1:16127",
    ],
)
async def test_a_mainnet_install_drops_what_is_no_lan_source(tmp_path: Path, minimal_node_dict: dict, source: str):
    kept = await written_sources(tmp_path, minimal_node_dict, [source], None)

    assert kept == []
