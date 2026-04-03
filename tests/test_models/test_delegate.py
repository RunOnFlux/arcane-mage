from __future__ import annotations

import base64

import pyrage
import pytest
from pydantic import ValidationError

from arcane_mage.models import Delegate

VALID_COLLATERAL_PUBKEY = "02" + "a1" * 32
VALID_WIF_KEY = "L4yreKb7oFfok5i38Zi5DZo7vA7wdjrGhs8gdPqNNxdsuNBaywcR"


class TestDelegateValidation:
    def test_valid_collateral_pubkey(self):
        delegate = Delegate(collateral_pubkey=VALID_COLLATERAL_PUBKEY)
        assert delegate.collateral_pubkey == VALID_COLLATERAL_PUBKEY.lower()

    def test_invalid_collateral_pubkey(self):
        with pytest.raises(ValidationError, match="66 hex"):
            Delegate(collateral_pubkey="invalid")

    def test_invalid_pubkey_prefix(self):
        with pytest.raises(ValidationError):
            Delegate(collateral_pubkey="04" + "ab" * 32)

    def test_valid_wif_key(self):
        delegate = Delegate(
            delegate_private_key=VALID_WIF_KEY,
            delegate_passphrase="mypassphrase",
        )
        assert delegate.delegate_private_key == VALID_WIF_KEY

    def test_invalid_wif_key(self):
        with pytest.raises(ValidationError, match="valid WIF"):
            Delegate(delegate_private_key="not-a-wif", delegate_passphrase="pass")

    def test_raw_key_without_passphrase(self):
        with pytest.raises(ValidationError, match="delegate_passphrase is required"):
            Delegate(delegate_private_key=VALID_WIF_KEY)

    def test_both_encrypted_and_raw_raises(self):
        with pytest.raises(ValidationError, match="not both"):
            Delegate(
                delegate_private_key_encrypted="encrypted-data",
                delegate_private_key=VALID_WIF_KEY,
                delegate_passphrase="pass",
            )


class TestDelegateSerialization:
    def test_to_dict_with_encrypted_key(self):
        delegate = Delegate(delegate_private_key_encrypted="pre-encrypted-data")
        result = delegate.to_dict()

        assert result["delegate_private_key_encrypted"] == "pre-encrypted-data"
        assert "delegate_private_key" not in result
        assert "delegate_passphrase" not in result

    def test_to_dict_encrypts_raw_key(self):
        passphrase = "test-passphrase"
        delegate = Delegate(
            delegate_private_key=VALID_WIF_KEY,
            delegate_passphrase=passphrase,
        )
        result = delegate.to_dict()

        assert "delegate_private_key_encrypted" in result
        assert "delegate_private_key" not in result

        # Verify the encrypted data can be decrypted
        encrypted_bytes = base64.b64decode(result["delegate_private_key_encrypted"])
        decrypted = pyrage.passphrase.decrypt(encrypted_bytes, passphrase)
        assert decrypted.decode("utf-8") == VALID_WIF_KEY

    def test_to_dict_with_collateral(self):
        delegate = Delegate(collateral_pubkey=VALID_COLLATERAL_PUBKEY)
        result = delegate.to_dict()

        assert result["collateral_pubkey"] == VALID_COLLATERAL_PUBKEY.lower()

    def test_to_dict_empty(self):
        delegate = Delegate()
        result = delegate.to_dict()

        assert result == {}

    def test_from_dict(self):
        data = {"collateral_pubkey": VALID_COLLATERAL_PUBKEY}
        delegate = Delegate(**data)

        assert delegate.collateral_pubkey == VALID_COLLATERAL_PUBKEY.lower()
