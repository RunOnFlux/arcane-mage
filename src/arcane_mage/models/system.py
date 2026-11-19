from __future__ import annotations

from cryptography.hazmat.primitives.serialization import load_ssh_public_key
from pydantic import TypeAdapter, field_validator
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass

from ..password import sets_a_password

# What an ISO from before console_login reads as "no console login".
_NO_PASSWORD = "!"


@py_dataclass
class KeyboardConfig:
    """Keyboard layout configuration for the installed OS."""

    # narrow these
    layout: str = "us"
    variant: str = ""

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")


def _ssh_pubkey(value: str) -> str:
    try:
        load_ssh_public_key(value.strip().encode())
    except Exception as e:
        raise ValueError("A public key in OpenSSH format is required") from e
    return value.strip()


@py_dataclass
class SshKeysConfig:
    """The operator's SSH keys by tier: operate keys log in as fluxtui and operator, status
    keys as fluxstatus (read only). ISOs before tiered keys read only ssh_pubkey."""

    operate: list[str] = Field(default_factory=list)
    status: list[str] = Field(default_factory=list)

    @field_validator("operate", "status", mode="before")
    @classmethod
    def validate_keys(cls, value: list[str] | None) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            # pydantic turns a ValueError, not a TypeError, into a validation error
            raise ValueError("A list of SSH public keys is required")
        return [_ssh_pubkey(key) for key in value]


@py_dataclass
class SystemConfig:
    """OS-level system configuration including hostname, the console login, and SSH keys.

    A node is installed with a console password (hashed_console, set from the password given
    at provisioning) or, on purpose, with no console login (console_login false). The password
    cannot be set after the install."""

    hostname: str = Field(min_length=2, max_length=253)
    hashed_console: str | None = None
    # One operate key; ssh_keys holds keys by tier.
    ssh_pubkey: str | None = None
    keyboard: KeyboardConfig = Field(default_factory=KeyboardConfig)
    ssh_keys: SshKeysConfig = Field(default_factory=SshKeysConfig)
    console_login: bool = True

    @field_validator("ssh_pubkey", mode="before")
    @classmethod
    def validate_ssh_pubkey(cls, value: str | None) -> str | None:
        return _ssh_pubkey(value) if value else value

    @field_validator("hashed_console", mode="before")
    @classmethod
    def validate_hashed_console(cls, value: str | None) -> str | None:
        """A hash that sets no password is no password given."""
        return value if sets_a_password(value) else None

    @property
    def console_problem(self) -> str | None:
        """Why this node cannot be installed as its console login stands, or None."""
        if self.console_login and self.hashed_console is None:
            return (
                "No console password: give one, or set console_login to false for a node"
                " with no console login. It cannot be set after the install."
            )
        if not self.console_login and self.hashed_console is not None:
            return "console_login is false and a console password is given: choose one."
        return None

    @classmethod
    def from_dict(cls, data: dict) -> SystemConfig:
        if not data.get("hostname"):
            raise ValueError("System config is missing hostname")

        return cls(**data)

    def to_dict(self) -> dict:
        """The system section as an installer reads it. An ISO from before console_login
        reads hashed_console alone, so a node with no console login carries the locked
        entry there too."""
        data = TypeAdapter(type(self)).dump_python(self, mode="json")
        if not self.console_login:
            data["hashed_console"] = _NO_PASSWORD
        return data
