from __future__ import annotations

import contextlib
from pathlib import Path
from typing import ClassVar, Literal
from uuid import uuid4

import keyring
import keyring.errors
import yaml
from pydantic import TypeAdapter
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass


@py_dataclass
class HypervisorConfig:
    """Connection configuration for a Proxmox hypervisor (URL, auth type, credential)."""

    url: str
    auth_type: Literal["token", "userpass"]
    credential: str = Field(repr=False)
    keychain: bool = True

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HypervisorConfig):
            return False

        return self.url == other.url and self.auth_type == other.auth_type and self.credential == other.credential

    def real_credential(self) -> str | None:
        if not self.keychain:
            return self.credential

        stored_cred = keyring.get_password("arcane_mage", self.credential)

        return stored_cred


@py_dataclass
class ArcaneCreatorConfig:
    """Persisted application configuration for Arcane Creator, stored in ~/.fluxnode_creator.yaml."""

    config_path: ClassVar[Path] = Path().home() / ".fluxnode_creator.yaml"

    hypervisors: list[HypervisorConfig] = Field(default_factory=list)
    use_keyring: bool = True
    default_page: str | None = None

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")

    @classmethod
    def from_fs(cls) -> ArcaneCreatorConfig:
        """Load configuration from disk, returning defaults if the file is missing or invalid."""
        try:
            with open(ArcaneCreatorConfig.config_path) as f:
                config_raw: str | None = f.read()
        except FileNotFoundError:
            config_raw = None

        if not config_raw:
            return cls()

        try:
            parsed = yaml.safe_load(config_raw)
        except yaml.YAMLError:
            parsed = None

        if not parsed:
            return cls()

        hypervisors = [HypervisorConfig(**x) for x in parsed.get("hypervisors", [])]

        return cls(
            hypervisors,
            parsed.get("use_keyring", True),
            parsed.get("default_page"),
        )

    @property
    def has_config(self) -> bool:
        return bool(self.hypervisors)

    def write(self) -> None:
        config_path = ArcaneCreatorConfig.config_path
        config_path.touch(mode=0o600, exist_ok=True)
        config_path.write_text(yaml.dump(self.to_dict()))

    def update_default_page(self, page: str | None) -> None:
        self.default_page = page

        self.write()

    def add_hypervisor(self, hypervisor: HypervisorConfig) -> bool:
        if next(filter(lambda x: x == hypervisor, self.hypervisors), None):
            return True

        self.hypervisors.append(hypervisor)

        if self.use_keyring:
            cred_uuid = str(uuid4())
            try:
                keyring.set_password("arcane_mage", cred_uuid, hypervisor.credential)
            except keyring.errors.PasswordSetError:
                return False

            hypervisor.credential = cred_uuid
        else:
            hypervisor.keychain = False

        self.write()

        return True

    def remove_hypervisor(self, hypervisor: HypervisorConfig) -> None:
        try:
            self.hypervisors.remove(hypervisor)
        except ValueError:
            return

        if hypervisor.keychain:
            with contextlib.suppress(keyring.errors.PasswordDeleteError):
                keyring.delete_password("arcane_mage", hypervisor.credential)


@py_dataclass
class Hypervisor:
    """VM placement and resource configuration for a fluxnode on a Proxmox hypervisor."""

    node: str
    vm_name: str
    node_tier: Literal["cumulus", "nimbus", "stratus"]
    network: str
    iso_name: str = Field(pattern=r"^FluxLive-\d{10}\.iso$")
    storage_images: str = "local-lvm"
    storage_iso: str = "local"
    storage_import: str = "local"
    vm_id: int | None = None
    startup_config: str | None = None
    disk_limit: int | None = None
    cpu_limit: float | None = None
    network_limit: int | None = None
    start_on_creation: bool = False

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")
