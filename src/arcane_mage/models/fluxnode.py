from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar, Literal
from urllib.parse import urlsplit

import aiofiles
import yaml
from pydantic import TypeAdapter, model_validator
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass

from .delegate import Delegate
from .identity import Identity
from .network import FluxnodeNetworkConfig
from .notifications import Notifications


@py_dataclass
class GravityConfig:
    """Runtime flags for the Gravity daemon (debug, development modes)."""

    debug: bool = False
    development: bool = False

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_defaults=True)


COMPRESSED_PUBKEY = re.compile(r"0[23][0-9a-f]{64}")


@py_dataclass
class FluxnodeChainConfig:
    """The network the node installs onto, fixed at install: absent, mainnet;
    for a lab, labnet, the lab's compressed public key and `update_url`, the lab's
    CDN its updater reads (a lab image carries no lab of its own). The installer
    checks the identity key belongs to it; a release image refuses any lab."""

    network: Literal["mainnet", "labnet"] = "mainnet"
    key: str | None = None
    update_url: str | None = None

    @model_validator(mode="after")
    def check_lab(self) -> FluxnodeChainConfig:
        if self.network == "labnet":
            key = (self.key or "").lower()
            if not COMPRESSED_PUBKEY.fullmatch(key):
                raise ValueError("labnet needs key: the lab's compressed public key (66 hex, 02/03)")
            self.key = key
            url = urlsplit(self.update_url or "")
            if url.scheme not in ("http", "https") or not url.hostname:
                raise ValueError("labnet needs update_url: the lab's CDN, e.g. http://cdn.lab/arcane")
        elif self.key is not None or self.update_url is not None:
            raise ValueError("key and update_url are for labnet only")
        return self


@py_dataclass
class FluxnodeConfig:
    """Complete fluxnode configuration including identity, network, notifications, and delegate."""

    config_path: ClassVar[Path] = Path("/mnt/root/config/flux_user_config.yaml")

    identity: Identity
    gravity: GravityConfig = Field(default_factory=GravityConfig)
    network: FluxnodeNetworkConfig = Field(default_factory=FluxnodeNetworkConfig)
    notifications: Notifications = Field(default_factory=Notifications)
    delegate: Delegate | None = None
    # Dev-channel enrolment token, one line, bound to `identity`; the installer
    # verifies it against that identity. Nothing here reads it.
    dev_token: str | None = None
    chain: FluxnodeChainConfig | None = None

    @model_validator(mode="after")
    def check_lab_donor(self) -> FluxnodeConfig:
        if self.chain is not None and self.chain.network == "labnet" and not self.network.params_source:
            raise ValueError("a labnet install names the lab's donor as network.params_source")
        return self

    @classmethod
    def from_dict(cls, params: dict) -> FluxnodeConfig:
        if not params.get("identity"):
            raise ValueError("Fluxnode identity missing")

        return cls(**params)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_none=True)

    @classmethod
    async def from_config_file(cls) -> FluxnodeConfig | None:
        try:
            async with aiofiles.open(cls.config_path) as f:
                data = await f.read()
        except FileNotFoundError:
            return None

        try:
            conf: dict = yaml.safe_load(data)
        except yaml.YAMLError:
            return None

        return cls.from_dict(conf)

    async def write_user_config(self, file_path: Path, ssh_pubkey: str | None = None) -> bool:
        config = {
            "identity": self.identity.to_dict(),
            "notifications": self.notifications.to_dict(),
            "miscellaneous": {"ssh_pubkey": ssh_pubkey} | self.gravity.to_dict(),
        }

        if self.delegate:
            delegate_output = self.delegate.to_dict()
            if delegate_output:
                config["delegate"] = delegate_output

        writeable_config = yaml.dump(
            config,
            sort_keys=False,
            default_flow_style=False,
        )

        async with aiofiles.open(file_path, "w") as f:
            await f.write(writeable_config)

        return True

    @property
    def fluxd_properties(self) -> dict:
        identity = self.identity

        return {
            "zelnodeprivkey": identity.identity_key,
            "zelnodeoutpoint": identity.tx_id,
            "zelnodeindex": identity.output_id,
        }
