"""SSH keys by tier in the install config's system section."""

import pytest
from pydantic import ValidationError

from arcane_mage.models import SshKeysConfig, SystemConfig

KEY_A = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl a"
KEY_B = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGbFcF1cJ6YtEa3oG3i4b9cVY9YjzW4V0t3sR5yQxP2n b"


def test_keys_by_tier_round_trip_beside_the_single_key():
    system = SystemConfig.from_dict({"hostname": "node1", "ssh_pubkey": KEY_A, "ssh_keys": {"status": [KEY_B]}})
    out = system.to_dict()
    assert out["ssh_pubkey"] == KEY_A
    assert out["ssh_keys"] == {"operate": [], "status": [KEY_B]}


def test_a_config_without_tiers_still_loads():
    assert SystemConfig.from_dict({"hostname": "node1", "ssh_pubkey": KEY_A}).ssh_keys == (SshKeysConfig())


@pytest.mark.parametrize("bad", [{"status": ["not a key"]}, {"operate": KEY_A}])
def test_keys_are_validated(bad):
    with pytest.raises(ValidationError):
        SystemConfig.from_dict({"hostname": "node1", "ssh_keys": bad})
