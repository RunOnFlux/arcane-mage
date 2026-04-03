from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from arcane_mage.models import ArcaneCreatorConfig, Hypervisor, HypervisorConfig


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

    def test_display_label_name_with_token(self):
        config = HypervisorConfig(
            url="https://pve.local:8006",
            auth_type="token",
            credential="davew@pam!mytoken=secret",
            keychain=False,
            name="bigchug",
        )
        assert config.display_label == "bigchug (davew)"

    def test_display_label_name_with_userpass(self):
        config = HypervisorConfig(
            url="https://pve.local:8006",
            auth_type="userpass",
            credential="admin:password123",
            keychain=False,
            name="bigchug",
        )
        assert config.display_label == "bigchug (admin)"

    def test_display_label_no_name_falls_back_to_hostname(self):
        config = HypervisorConfig(
            url="https://pve.local:8006",
            auth_type="token",
            credential="davew@pam!mytoken=secret",
            keychain=False,
        )
        assert config.display_label == "pve.local (davew)"

    def test_display_label_no_parseable_credential(self):
        config = HypervisorConfig(
            url="https://pve.local:8006",
            auth_type="token",
            credential="not-a-valid-token",
            keychain=False,
            name="bigchug",
        )
        assert config.display_label == "bigchug"

    @patch("arcane_mage.models.hypervisor.keyring")
    def test_update_hypervisor(self, mock_keyring: MagicMock):
        mock_keyring.get_password.return_value = None
        mock_keyring.set_password.return_value = None
        mock_keyring.delete_password.return_value = None

        old = HypervisorConfig(
            url="https://pve.local:8006",
            auth_type="token",
            credential="old-cred",
            keychain=False,
            name="old-name",
        )
        new = HypervisorConfig(
            url="https://pve.local:8006",
            auth_type="token",
            credential="new-cred",
            keychain=False,
            name="new-name",
        )

        creator_config = ArcaneCreatorConfig(hypervisors=[old], use_keyring=False)

        with patch.object(ArcaneCreatorConfig, "write"):
            result = creator_config.update_hypervisor(old, new)

        assert result is True
        assert len(creator_config.hypervisors) == 1
        assert creator_config.hypervisors[0].credential == "new-cred"
        assert creator_config.hypervisors[0].name == "new-name"


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
