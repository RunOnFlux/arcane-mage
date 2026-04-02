from __future__ import annotations

import base64
import re
from typing import ClassVar

import pyrage
from pydantic import field_validator, model_validator
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass


@py_dataclass
class Delegate:
    """Delegate node starting configuration.

    Accepts either a pre-encrypted key (delegate_private_key_encrypted) or
    a raw WIF key + passphrase (delegate_private_key, delegate_passphrase)
    which will be encrypted with pyrage on output.
    """

    COMPRESSED_PUBKEY_PATTERN: ClassVar[str] = r"^(02|03)[0-9a-fA-F]{64}$"
    WIF_PATTERN: ClassVar[str] = r"^[5KLc9][1-9A-HJ-NP-Za-km-z]{50,51}$"

    collateral_pubkey: str | None = None
    # Pre-encrypted key (pass through directly)
    delegate_private_key_encrypted: str | None = Field(default=None, repr=False)
    # Raw key + passphrase (arcane-mage encrypts on output)
    delegate_private_key: str | None = Field(default=None, repr=False)
    delegate_passphrase: str | None = Field(default=None, repr=False)

    @field_validator("collateral_pubkey", mode="after")
    @classmethod
    def validate_collateral_pubkey(cls, value: str | None) -> str | None:
        if value is None:
            return value

        if not re.match(cls.COMPRESSED_PUBKEY_PATTERN, value):
            raise ValueError("Collateral pubkey must be 66 hex chars with 02/03 prefix")

        return value.lower()

    @field_validator("delegate_private_key", mode="after")
    @classmethod
    def validate_delegate_private_key(cls, value: str | None) -> str | None:
        if value is None:
            return value

        if not re.match(cls.WIF_PATTERN, value):
            raise ValueError("Delegate private key must be a valid WIF key")

        return value

    @model_validator(mode="after")
    def validate_key_config(self) -> Delegate:
        if self.delegate_private_key and not self.delegate_passphrase:
            raise ValueError("delegate_passphrase is required when delegate_private_key is provided")

        has_encrypted = self.delegate_private_key_encrypted is not None
        has_raw = self.delegate_private_key is not None
        if has_encrypted and has_raw:
            raise ValueError(
                "Provide either delegate_private_key_encrypted or "
                "delegate_private_key + delegate_passphrase, not both"
            )

        return self

    def to_dict(self) -> dict:
        """Produce the output dict matching flux_config_shared's Delegate schema.

        If a raw key + passphrase were provided, encrypts the key with pyrage.
        Only outputs delegate_private_key_encrypted and collateral_pubkey.
        """
        encrypted = self.delegate_private_key_encrypted

        if self.delegate_private_key and self.delegate_passphrase:
            encrypted_bytes = pyrage.passphrase.encrypt(
                self.delegate_private_key.encode("utf-8"),
                self.delegate_passphrase,
            )
            encrypted = base64.b64encode(encrypted_bytes).decode("utf-8")

        result = {}
        if encrypted is not None:
            result["delegate_private_key_encrypted"] = encrypted
        if self.collateral_pubkey is not None:
            result["collateral_pubkey"] = self.collateral_pubkey

        return result
