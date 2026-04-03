from __future__ import annotations

from dataclasses import fields
from typing import Literal

from pydantic import TypeAdapter, field_validator
from pydantic.dataclasses import dataclass as py_dataclass


@py_dataclass
class Identifier:
    """Machine identifier for matching nodes by UUID or MAC address."""

    type: Literal["system-uuid", "mac-address"]
    value: str

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")


@py_dataclass
class Identity:
    """Fluxnode identity containing the keys and transaction used to start a node."""

    flux_id: str
    identity_key: str
    tx_id: str
    output_id: int

    @field_validator("flux_id", mode="after")
    @classmethod
    def validate_flux_id(cls, value: str) -> str:
        id_len = len(value)

        if id_len > 72 or id_len < 14:
            raise ValueError("FluxId must be between 14 and 72 characters")

        return value

    @field_validator("identity_key", mode="after")
    @classmethod
    def validate_identity_key(cls, value: str) -> str:
        key_len = len(value)

        if key_len < 51 or key_len > 52:
            raise ValueError("Identity key must be 51 or 52 characters")

        return value

    @field_validator("tx_id", mode="after")
    @classmethod
    def validate_txid(cls, value: str) -> str:
        if len(value) != 64:
            raise ValueError("Transaction Id must be 64 characters")

        return value

    @field_validator("output_id", mode="before")
    @classmethod
    def validate_output_id(cls, value: str | int) -> int:
        value = int(value)

        if value < 0 or value > 999:
            raise ValueError("OutputId must be between 0 and 999")

        return value

    @classmethod
    def from_dict(cls, data: dict) -> Identity:
        """Generates a fluxnode identity from a dict.

        Args:
            data (dict): The raw dict to convert to an Identity

        Raises:
            ValueError: If the dict doesn't pass validation

        Returns:
            Identity: The fluxnode Identity
        """

        items = []

        for _field in fields(cls):
            name = _field.name
            prop = data.get(name)

            if prop is None:
                raise ValueError(f"Property: {name} missing")

            items.append(prop)

        return cls(*items)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")
