from __future__ import annotations

from cryptography.hazmat.primitives.serialization import load_ssh_public_key
from pydantic import TypeAdapter, field_validator
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass


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
    """OS-level system configuration including hostname, console password, and SSH keys."""

    hostname: str = Field(min_length=2, max_length=253)
    hashed_console: str = "!"  # No password login
    # One operate key; ssh_keys holds keys by tier.
    ssh_pubkey: str | None = None
    keyboard: KeyboardConfig = Field(default_factory=KeyboardConfig)
    ssh_keys: SshKeysConfig = Field(default_factory=SshKeysConfig)

    @field_validator("ssh_pubkey", mode="before")
    @classmethod
    def validate_ssh_pubkey(cls, value: str | None) -> str | None:
        return _ssh_pubkey(value) if value else value

    @classmethod
    def from_dict(cls, data: dict) -> SystemConfig:
        if not data.get("hostname"):
            raise ValueError("System config is missing hostname")

        if not data.get("hashed_console", "!"):
            raise ValueError("System config is missing hashed console password")

        return cls(**data)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")
