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


@py_dataclass
class SystemConfig:
    """OS-level system configuration including hostname, console password, and SSH key."""

    hostname: str = Field(min_length=2, max_length=253)
    hashed_console: str = "!"  # No password login
    ssh_pubkey: str | None = None
    keyboard: KeyboardConfig = Field(default_factory=KeyboardConfig)

    @field_validator("ssh_pubkey", mode="before")
    @classmethod
    def validate_ssh_pubkey(cls, value: str | None) -> str | None:
        if not value:
            return value

        try:
            load_ssh_public_key(value.encode())
        except Exception as e:
            raise ValueError("A public key in OpenSSH format is required") from e

        return value

    @classmethod
    def from_dict(cls, data: dict) -> SystemConfig:
        if not data.get("hostname"):
            raise ValueError("System config is missing hostname")

        if not data.get("hashed_console", "!"):
            raise ValueError("System config is missing hashed console password")

        return cls(**data)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")
