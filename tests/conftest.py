from __future__ import annotations

import pytest

VALID_FLUX_ID = "122Q5f5dJdiaoNP7iLbBgEt5fVF3g3DDeA"
VALID_IDENTITY_KEY = "L4yreKb7oFfok5i38Zi5DZo7vA7wdjrGhs8gdPqNNxdsuNBaywcR"
VALID_TX_ID = "657e17cd88d2e7993b62dfc957baedf7b026b0ae31083d30eb7c8851a2dd91ba"
VALID_OUTPUT_ID = 0
VALID_SSH_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICSorp1RcdDK0qqDm/jr/5EXtkny/s9H+2vq1fgbqU9+ operator@fluxnode"


@pytest.fixture
def identity_dict() -> dict:
    return {
        "flux_id": VALID_FLUX_ID,
        "identity_key": VALID_IDENTITY_KEY,
        "tx_id": VALID_TX_ID,
        "output_id": VALID_OUTPUT_ID,
    }


@pytest.fixture
def system_dict() -> dict:
    return {
        "hostname": "test-node",
        "hashed_console": "!",
    }


@pytest.fixture
def fluxnode_dict(identity_dict: dict) -> dict:
    return {
        "identity": identity_dict,
    }


@pytest.fixture
def network_dhcp_dict() -> dict:
    return {
        "ip_allocation": "dhcp",
    }


@pytest.fixture
def network_static_dict() -> dict:
    return {
        "ip_allocation": "static",
        "address_config": {
            "address": "192.168.44.13/24",
            "gateway": "192.168.44.1",
            "dns": ["8.8.8.8", "1.1.1.1"],
        },
    }


@pytest.fixture
def hypervisor_dict() -> dict:
    return {
        "node": "bigchug",
        "vm_name": "graham",
        "node_tier": "cumulus",
        "network": "vmbr0",
        "iso_name": "FluxLive-1749291196.iso",
        "storage_images": "local-lvm",
        "storage_iso": "local",
        "storage_import": "local",
    }


@pytest.fixture
def minimal_node_dict(fluxnode_dict: dict, system_dict: dict, hypervisor_dict: dict) -> dict:
    return {
        "fluxnode": fluxnode_dict,
        "system": system_dict,
        "hypervisor": hypervisor_dict,
    }


@pytest.fixture
def minimal_config_dict(minimal_node_dict: dict) -> dict:
    return {"nodes": [minimal_node_dict]}
