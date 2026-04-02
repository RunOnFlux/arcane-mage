from __future__ import annotations

import pytest
from pydantic import ValidationError

from arcane_mage.models import Identity

from ..conftest import VALID_FLUX_ID, VALID_IDENTITY_KEY, VALID_OUTPUT_ID, VALID_TX_ID


class TestIdentityFromDict:
    def test_valid(self, identity_dict: dict):
        identity = Identity.from_dict(identity_dict)

        assert identity.flux_id == VALID_FLUX_ID
        assert identity.identity_key == VALID_IDENTITY_KEY
        assert identity.tx_id == VALID_TX_ID
        assert identity.output_id == VALID_OUTPUT_ID

    def test_missing_field_raises(self, identity_dict: dict):
        del identity_dict["flux_id"]

        with pytest.raises(ValueError, match="flux_id missing"):
            Identity.from_dict(identity_dict)


class TestIdentityValidation:
    def test_flux_id_too_short(self, identity_dict: dict):
        identity_dict["flux_id"] = "short"

        with pytest.raises(ValidationError):
            Identity.from_dict(identity_dict)

    def test_flux_id_too_long(self, identity_dict: dict):
        identity_dict["flux_id"] = "a" * 73

        with pytest.raises(ValidationError):
            Identity.from_dict(identity_dict)

    def test_identity_key_wrong_length(self, identity_dict: dict):
        identity_dict["identity_key"] = "short"

        with pytest.raises(ValidationError):
            Identity.from_dict(identity_dict)

    def test_tx_id_wrong_length(self, identity_dict: dict):
        identity_dict["tx_id"] = "abc123"

        with pytest.raises(ValidationError):
            Identity.from_dict(identity_dict)

    def test_output_id_negative(self, identity_dict: dict):
        identity_dict["output_id"] = -1

        with pytest.raises(ValidationError):
            Identity.from_dict(identity_dict)

    def test_output_id_too_large(self, identity_dict: dict):
        identity_dict["output_id"] = 1000

        with pytest.raises(ValidationError):
            Identity.from_dict(identity_dict)

    def test_output_id_string_coercion(self, identity_dict: dict):
        identity_dict["output_id"] = "5"

        identity = Identity.from_dict(identity_dict)
        assert identity.output_id == 5


class TestIdentityRoundtrip:
    def test_to_dict(self, identity_dict: dict):
        identity = Identity.from_dict(identity_dict)
        result = identity.to_dict()

        assert result == identity_dict

