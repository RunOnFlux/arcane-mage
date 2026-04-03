from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import yaml
from pydantic import TypeAdapter
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass

from .fluxnode import FluxnodeConfig
from .hypervisor import Hypervisor
from .identity import Identifier
from .installer import InstallerConfig, MetricsAppConfig
from .network import NetworkConfig
from .system import SystemConfig

log = logging.getLogger(__name__)


@py_dataclass
class ArcaneOsConfig:
    """Top-level configuration for a single ArcaneOS fluxnode deployment."""

    fluxnode: FluxnodeConfig
    system: SystemConfig
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    installer: InstallerConfig = Field(default_factory=InstallerConfig)
    metrics_app: MetricsAppConfig = Field(default_factory=MetricsAppConfig)
    hypervisor: Hypervisor | None = None
    identifier: Identifier | None = None

    @classmethod
    def from_dict(cls, data: dict) -> ArcaneOsConfig:
        if not data.get("fluxnode"):
            raise ValueError("fluxnode config missing")

        if not data.get("system"):
            raise ValueError("system config missing")

        return cls(**data)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_none=True)

    def as_row(self) -> list[str]:
        if self.hypervisor is None:
            raise ValueError("as_row() requires a hypervisor configuration")

        address = "dhcp" if not self.network.address_config else str(self.network.address_config.address)
        return [
            self.hypervisor.node,
            self.system.hostname,
            self.hypervisor.node_tier,
            self.hypervisor.network,
            address,
        ]

    async def write_installer_config(self, file_path: Path) -> bool:
        res = await self.fluxnode.network.write_installer_config(file_path)

        return res

    async def write_user_config(self, file_path: Path) -> bool:
        ssh_pubkey = self.system.ssh_pubkey

        # It makes more sense to move the pubkey to global system config, not as
        # as misc item on the fluxnode config. Will eventually move it off here
        res = await self.fluxnode.write_user_config(file_path, ssh_pubkey=ssh_pubkey)

        return res

    async def write_metrics_config(self, file_path: Path) -> bool:
        res = await self.metrics_app.write_config(file_path)

        return res


@py_dataclass
class ArcaneOsConfigGroup:
    """An iterable collection of ArcaneOsConfig nodes, loadable from a YAML file."""

    default_path: ClassVar[Path] = Path("fluxnodes.yaml")

    nodes: list[ArcaneOsConfig] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> ArcaneOsConfigGroup:
        nodes: list[dict] | None = data.get("nodes")

        if not nodes:
            raise ValueError("ArcaneOsConfigGroup must contain nodes key")

        parsed = [ArcaneOsConfig.from_dict(x) for x in nodes]

        return cls(parsed)

    @classmethod
    def from_fs(cls, config_path: Path | None = None) -> ArcaneOsConfigGroup:
        """Load a config group from a YAML file, returning an empty group on failure."""
        file_path = config_path or ArcaneOsConfigGroup.default_path

        try:
            with open(file_path) as f:
                data = f.read()
        except (FileNotFoundError, PermissionError):
            return cls()

        try:
            config_raw = yaml.safe_load(data)
        except yaml.YAMLError:
            log.warning("Invalid YAML in config file: %s", file_path)
            return cls()

        return cls.from_dict(config_raw)

    @property
    def first(self) -> ArcaneOsConfig | None:
        return self.nodes[0] if self.nodes else None

    @property
    def rest(self) -> list[ArcaneOsConfig]:
        return self.nodes[1:] if self.nodes else []

    @property
    def last(self) -> ArcaneOsConfig | None:
        return self.nodes[-1] if self.nodes else None

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self) -> Iterator[ArcaneOsConfig]:
        yield from self.nodes

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_none=True)

    def get_node_by_vm_name(self, node_name: str, vm_name: str) -> ArcaneOsConfig | None:
        node = next(
            filter(
                lambda x: x.hypervisor and x.hypervisor.vm_name == vm_name and x.hypervisor.node == node_name,
                self.nodes,
            ),
            None,
        )

        return node

    def get_nodes_by_hypervisor_name(self, hyper_name: str) -> ArcaneOsConfigGroup:
        return ArcaneOsConfigGroup(
            list(
                filter(
                    lambda x: x.hypervisor and x.hypervisor.node == hyper_name,
                    self.nodes,
                )
            )
        )

    def add_nodes(self, other: ArcaneOsConfigGroup) -> None:
        self.nodes.extend(other.nodes)
