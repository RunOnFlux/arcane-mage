from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from ansible_collections.runonflux.arcane_mage.plugins.module_utils import library
from ansible_collections.runonflux.arcane_mage.plugins.modules.provision import build_config_dict
from arcane_mage import ArcaneOsConfig

ROOT = Path(__file__).parent.parent
COLLECTION = ROOT / "ansible_collections/runonflux/arcane_mage"
MODULES = sorted((COLLECTION / "plugins/modules").glob("*.py"))
LAB_KEY = "02" + "ab" * 32


class FailJson(Exception):
    pass


@pytest.fixture
def module():
    mock = MagicMock()
    mock.fail_json.side_effect = FailJson
    return mock


def params(**overrides):
    base = {
        "api_url": "https://pve:8006",
        "node": "pve1",
        "vm_name": "node-1",
        "node_tier": "cumulus",
        "network": "vmbr0",
        "iso_name": "FluxLive-1775071308.iso",
        "storage_images": "local-lvm",
        "storage_iso": "local",
        "storage_import": "local",
        "start": False,
        "hostname": "node-1",
        "keyboard_layout": "us",
        "keyboard_variant": "",
        "flux_id": "1" * 34,
        "identity_key": "K" * 52,
        "tx_id": "a" * 64,
        "output_id": 0,
        "ip_allocation": "dhcp",
    }
    return base | overrides


def lab_chain():
    return {"network": "labnet", "key": LAB_KEY, "update_url": "http://cdn.lab/arcane"}


def test_mainnet_config_has_no_chain():
    config = ArcaneOsConfig.from_dict(build_config_dict(params()))

    assert config.fluxnode.chain is None


def test_labnet_config_reaches_the_library():
    config = ArcaneOsConfig.from_dict(build_config_dict(params(chain=lab_chain(), params_source="25.47.0.1:16127")))

    chain = config.fluxnode.chain
    assert chain is not None
    assert chain.network == "labnet"
    assert chain.update_url == "http://cdn.lab/arcane"
    assert config.fluxnode.network.params_source == "25.47.0.1:16127"


def test_labnet_config_needs_the_donor():
    with pytest.raises(ValueError, match="params_source"):
        ArcaneOsConfig.from_dict(build_config_dict(params(chain=lab_chain())))


def test_missing_library_fails_the_task(module):
    with pytest.raises(FailJson):
        library.require_arcane_mage(module, "ImportError traceback")

    assert library.MINIMUM_VERSION in module.fail_json.call_args.kwargs["msg"]


def test_older_library_fails_the_task(module):
    with patch.object(library, "version", return_value="5.0.2"), pytest.raises(FailJson):
        library.require_arcane_mage(module, None)

    assert "found 5.0.2" in module.fail_json.call_args.kwargs["msg"]


@pytest.mark.parametrize("installed", ["6.0.0", "6.0.10", "7.0.0"])
def test_current_library_passes(module, installed):
    with patch.object(library, "version", return_value=installed):
        library.require_arcane_mage(module, None)

    module.fail_json.assert_not_called()


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.stem)
def test_module_documents_and_enforces_the_library_floor(path):
    source = path.read_text()

    assert f'"arcane-mage >= {library.MINIMUM_VERSION}"' in source
    assert "require_arcane_mage(module, IMPORT_ERROR)" in source


def test_requirements_declare_the_library_floor():
    assert (COLLECTION / "requirements.txt").read_text().strip() == f"arcane-mage>={library.MINIMUM_VERSION}"


def test_ansible_floor_is_locked_and_lint_supported():
    runtime = yaml.safe_load((COLLECTION / "meta/runtime.yml").read_text())
    floor = runtime["requires_ansible"].removeprefix(">=")
    major, minor, _ = floor.split(".")
    lint = yaml.safe_load((COLLECTION / ".ansible-lint").read_text())

    assert f'"ansible-core>={floor}"' in (ROOT / "pyproject.toml").read_text()
    assert f"{major}.{minor}." in lint["supported_ansible_also"]


def test_collection_ships_the_repository_license():
    assert (COLLECTION / "LICENSE").read_text() == (ROOT / "LICENSE").read_text()
