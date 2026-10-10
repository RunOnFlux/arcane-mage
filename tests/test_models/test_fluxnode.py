from __future__ import annotations

import pytest

from arcane_mage.models import FluxnodeConfig, GravityConfig

from ..conftest import VALID_FLUX_ID, VALID_IDENTITY_KEY, VALID_TX_ID


class TestGravityConfig:
    def test_defaults(self):
        config = GravityConfig()
        assert config.debug is False
        assert config.development is False

    def test_from_dict(self):
        config = GravityConfig(**{"debug": True, "development": True})
        assert config.debug is True
        assert config.development is True

    def test_has_no_network_switch(self):
        assert "testnet" not in GravityConfig(**{"testnet": True}).to_dict()

    def test_to_dict_excludes_false(self):
        config = GravityConfig()
        assert config.to_dict() == {}

    def test_to_dict_includes_true(self):
        config = GravityConfig(debug=True)
        assert config.to_dict() == {"debug": True}


class TestFluxnodeConfig:
    def test_from_dict_minimal(self, fluxnode_dict: dict):
        config = FluxnodeConfig.from_dict(fluxnode_dict)

        assert config.identity.flux_id == VALID_FLUX_ID
        assert config.delegate is None

    def test_from_dict_missing_identity(self):
        with pytest.raises(ValueError, match="identity missing"):
            FluxnodeConfig.from_dict({})

    def test_from_dict_with_gravity(self, fluxnode_dict: dict):
        fluxnode_dict["gravity"] = {"debug": True}
        config = FluxnodeConfig.from_dict(fluxnode_dict)

        assert config.gravity.debug is True

    def test_from_dict_with_notifications(self, fluxnode_dict: dict):
        fluxnode_dict["notifications"] = {
            "discord": {
                "webhook_url": "https://discord.com/api/webhooks/123456789/abcdefg",
            }
        }
        config = FluxnodeConfig.from_dict(fluxnode_dict)

        assert config.notifications.discord.webhook_url is not None

    def test_dev_token_roundtrip(self, fluxnode_dict: dict):
        fluxnode_dict["dev_token"] = "eyJ2IjoxfQ.c2ln"
        config = FluxnodeConfig.from_dict(fluxnode_dict)

        assert config.dev_token == "eyJ2IjoxfQ.c2ln"
        assert config.to_dict()["dev_token"] == "eyJ2IjoxfQ.c2ln"

    def test_dev_token_absent_when_unset(self, fluxnode_dict: dict):
        config = FluxnodeConfig.from_dict(fluxnode_dict)

        assert config.dev_token is None
        assert "dev_token" not in config.to_dict()

    def test_to_dict_roundtrip(self, fluxnode_dict: dict):
        config = FluxnodeConfig.from_dict(fluxnode_dict)
        result = config.to_dict()

        assert result["identity"]["flux_id"] == VALID_FLUX_ID
        assert result["identity"]["identity_key"] == VALID_IDENTITY_KEY
        assert result["identity"]["tx_id"] == VALID_TX_ID

    def test_fluxd_properties(self, fluxnode_dict: dict):
        config = FluxnodeConfig.from_dict(fluxnode_dict)
        props = config.fluxd_properties

        assert props["zelnodeprivkey"] == VALID_IDENTITY_KEY
        assert props["zelnodeoutpoint"] == VALID_TX_ID
        assert props["zelnodeindex"] == 0


LAB_KEY = "036fb1722c93444bc06daffaacc58c38e9e9c17b7fb2758486c26d13160e4eb4aa"


class TestFluxnodeChain:
    def test_absent_chain_is_not_written(self, fluxnode_dict: dict):
        assert "chain" not in FluxnodeConfig.from_dict(fluxnode_dict).to_dict()

    def test_labnet_chain_round_trips(self, fluxnode_dict: dict):
        config = FluxnodeConfig.from_dict({**fluxnode_dict, "chain": {"network": "labnet", "key": LAB_KEY.upper()}})
        assert config.to_dict()["chain"] == {"network": "labnet", "key": LAB_KEY}

    @pytest.mark.parametrize("chain", [
        {"network": "labnet"},
        {"network": "labnet", "key": "04" + LAB_KEY[2:]},
        {"network": "mainnet", "key": LAB_KEY},
        {"network": "testnet"},
    ])
    def test_bad_chains_are_refused(self, fluxnode_dict: dict, chain: dict):
        with pytest.raises(ValueError):
            FluxnodeConfig.from_dict({**fluxnode_dict, "chain": chain})
