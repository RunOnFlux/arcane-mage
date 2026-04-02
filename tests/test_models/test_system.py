from __future__ import annotations

import pytest
from pydantic import ValidationError

from arcane_mage.models import KeyboardConfig, SystemConfig

from ..conftest import VALID_SSH_PUBKEY


class TestKeyboardConfig:
    def test_defaults(self):
        config = KeyboardConfig()

        assert config.layout == "us"
        assert config.variant == ""

    def test_from_dict(self):
        config = KeyboardConfig(**{"layout": "gb", "variant": "dvorak"})

        assert config.layout == "gb"
        assert config.variant == "dvorak"

    def test_from_dict_coerces_non_string(self):
        config = KeyboardConfig(**{"layout": "123"})

        assert config.layout == "123"


class TestSystemConfig:
    def test_from_dict_minimal(self, system_dict: dict):
        config = SystemConfig.from_dict(system_dict)

        assert config.hostname == "test-node"
        assert config.hashed_console == "!"
        assert config.ssh_pubkey is None
        assert config.keyboard.layout == "us"

    def test_from_dict_with_ssh_pubkey(self, system_dict: dict):
        system_dict["ssh_pubkey"] = VALID_SSH_PUBKEY

        config = SystemConfig.from_dict(system_dict)
        assert config.ssh_pubkey == VALID_SSH_PUBKEY

    def test_from_dict_missing_hostname(self):
        with pytest.raises(ValueError, match="missing hostname"):
            SystemConfig.from_dict({})

    def test_hostname_too_short(self):
        with pytest.raises(ValidationError):
            SystemConfig(hostname="a")

    def test_hostname_max_length(self):
        with pytest.raises(ValidationError):
            SystemConfig(hostname="a" * 254)

    def test_invalid_ssh_pubkey(self, system_dict: dict):
        system_dict["ssh_pubkey"] = "not-a-valid-key"

        with pytest.raises(ValidationError, match="OpenSSH format"):
            SystemConfig.from_dict(system_dict)

    def test_to_dict_roundtrip(self, system_dict: dict):
        config = SystemConfig.from_dict(system_dict)
        result = config.to_dict()

        assert result["hostname"] == "test-node"
        assert result["hashed_console"] == "!"
        assert "keyboard" in result
