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
        assert config.hashed_console is None
        assert config.console_login is True
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
        assert result["hashed_console"] is None
        assert result["console_login"] is True
        assert "keyboard" in result


HASH = "$y$j9T$abcdefghijklmnop$0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"


class TestConsoleLogin:
    def test_a_node_with_no_password_cannot_be_installed(self, system_dict: dict):
        config = SystemConfig.from_dict(system_dict)

        assert "No console password" in (config.console_problem or "")

    def test_a_password_hash_is_a_console_login(self, system_dict: dict):
        config = SystemConfig.from_dict(system_dict | {"hashed_console": HASH})

        assert config.console_problem is None
        assert config.to_dict()["hashed_console"] == HASH

    def test_no_console_login_is_said_on_purpose(self, system_dict: dict):
        config = SystemConfig.from_dict(system_dict | {"console_login": False})

        assert config.console_problem is None

    def test_no_console_login_reaches_an_older_iso_as_a_locked_entry(self, system_dict: dict):
        result = SystemConfig.from_dict(system_dict | {"console_login": False}).to_dict()

        assert result["console_login"] is False
        assert result["hashed_console"] == "!"

    def test_no_console_login_takes_no_password(self, system_dict: dict):
        config = SystemConfig.from_dict(system_dict | {"console_login": False, "hashed_console": HASH})

        assert "choose one" in (config.console_problem or "")

    @pytest.mark.parametrize("value", ["!", "*", "", "!locked", "plain-text-password", "$y$bad hash"])
    def test_a_value_that_sets_no_password_is_no_password(self, system_dict: dict, value: str):
        config = SystemConfig.from_dict(system_dict | {"hashed_console": value})

        assert config.hashed_console is None
        assert "No console password" in (config.console_problem or "")
