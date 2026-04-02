from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import aiofiles
import yaml
from pydantic import TypeAdapter
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass

from .delegate import Delegate
from .identity import Identity
from .network import FluxnodeNetworkConfig
from .notifications import Notifications


@py_dataclass
class GravityConfig:
    """Runtime flags for the Gravity daemon (debug, development, testnet modes)."""

    debug: bool = False
    development: bool = False
    testnet: bool = False

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_defaults=True)


@py_dataclass
class FluxnodeConfig:
    """Complete fluxnode configuration including identity, network, notifications, and delegate."""

    config_path: ClassVar[Path] = Path("/mnt/root/config/flux_user_config.yaml")

    identity: Identity
    gravity: GravityConfig = Field(default_factory=GravityConfig)
    network: FluxnodeNetworkConfig = Field(default_factory=FluxnodeNetworkConfig)
    notifications: Notifications = Field(default_factory=Notifications)
    delegate: Delegate | None = None

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
