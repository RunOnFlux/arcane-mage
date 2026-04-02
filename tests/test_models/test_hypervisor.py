from __future__ import annotations

import pytest
from pydantic import ValidationError

from arcane_mage.models import Hypervisor, HypervisorConfig


class TestHypervisorConfig:
    def test_from_dict(self):
        data = {
            "url": "https://pve.local:8006",
            "auth_type": "token",
            "credential": "user@pam!token=secret",
            "keychain": False,
        }
        config = HypervisorConfig(**data)

        assert config.url == "https://pve.local:8006"
        assert config.auth_type == "token"
        assert config.credential == "user@pam!token=secret"
        assert config.keychain is False

    def test_equality(self):
        a = HypervisorConfig(url="https://a", auth_type="token", credential="cred1")
        b = HypervisorConfig(url="https://a", auth_type="token", credential="cred1")

        assert a == b

    def test_inequality_different_url(self):
        a = HypervisorConfig(url="https://a", auth_type="token", credential="cred1")
        b = HypervisorConfig(url="https://b", auth_type="token", credential="cred1")

        assert a != b

    def test_equality_with_non_hypervisor(self):
        a = HypervisorConfig(url="https://a", auth_type="token", credential="cred1")

        assert a != "not a hypervisor"

    def test_real_credential_no_keychain(self):
        config = HypervisorConfig(
            url="https://a",
            auth_type="token",
            credential="raw-credential",
            keychain=False,
        )
        assert config.real_credential() == "raw-credential"


class TestHypervisor:
    def test_from_dict(self, hypervisor_dict: dict):
        hyper = Hypervisor(**hypervisor_dict)

        assert hyper.node == "bigchug"
        assert hyper.vm_name == "graham"
        assert hyper.node_tier == "cumulus"
        assert hyper.network == "vmbr0"
        assert hyper.storage_images == "local-lvm"
        assert hyper.start_on_creation is False

    def test_iso_name_pattern_valid(self, hypervisor_dict: dict):
        hyper = Hypervisor(**hypervisor_dict)
        assert hyper.iso_name == "FluxLive-1749291196.iso"

    def test_iso_name_pattern_invalid(self, hypervisor_dict: dict):
        hypervisor_dict["iso_name"] = "bad-name.iso"

        with pytest.raises(ValidationError):
            Hypervisor(**hypervisor_dict)

    def test_to_dict_roundtrip(self, hypervisor_dict: dict):
        hyper = Hypervisor(**hypervisor_dict)
        result = hyper.to_dict()

        assert result["node"] == "bigchug"
        assert result["vm_name"] == "graham"
        # Defaults should be present
        assert result["start_on_creation"] is False
        assert result["vm_id"] is None

    def test_with_optional_fields(self, hypervisor_dict: dict):
        hypervisor_dict["vm_id"] = 100
        hypervisor_dict["startup_config"] = "order=1"
        hypervisor_dict["disk_limit"] = 50
        hypervisor_dict["cpu_limit"] = 2.0
        hypervisor_dict["network_limit"] = 100
        hypervisor_dict["start_on_creation"] = True

        hyper = Hypervisor(**hypervisor_dict)

        assert hyper.vm_id == 100
        assert hyper.startup_config == "order=1"
        assert hyper.disk_limit == 50
        assert hyper.cpu_limit == 2.0
        assert hyper.network_limit == 100
        assert hyper.start_on_creation is True
