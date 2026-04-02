from __future__ import annotations

import re
from ast import literal_eval
from configparser import ConfigParser
from dataclasses import fields
from ipaddress import (
    AddressValueError,
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Interface,
)
from pathlib import Path
from typing import ClassVar, Literal

import aiofiles
import yaml
from pydantic import TypeAdapter
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass


class ConfigParserDict(dict):
    def items(self):
        for k, v in super().items():
            if v.startswith("[") and v.endswith("]"):
                for i in literal_eval(v):
                    yield k, i
            else:
                yield k, v


class SystemdConfigParser(ConfigParser):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._dict = ConfigParserDict

    def optionxform(self, optionstr: str) -> str:
        # this stops the keys being lowercased
        return optionstr


@py_dataclass
class Link:
    state: Literal["up", "down"]
    address: str
    name: str
    kind: str | None
    index: int

    @classmethod
    def from_dict(cls, data: dict) -> Link:
        field_set = {f.name for f in fields(cls) if f.init}
        filtered = {k: v for k, v in data.items() if k in field_set}

        return cls(**filtered)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")

    @property
    def connected(self) -> bool:
        return self.state == "up"

    @property
    def ethernet(self) -> bool:
        # not sure how this applies for wlan interfaces as I don't have one to test
        return (
            self.kind is None
            and self.name != "lo"
            and bool(self.address)
            and self.address != "00:00:00:00:00:00"
        )


@py_dataclass
class Address:
    address: str
    prefixlen: int
    family: Literal["ipv4", "ipv6"]
    index: int

    @classmethod
    def from_dict(cls, data: dict) -> Address:
        field_set = {f.name for f in fields(cls) if f.init}
        filtered = {k: v for k, v in data.items() if k in field_set}

        filtered["family"] = "ipv4" if filtered["family"] == 2 else "ipv6"

        return cls(**filtered)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")

    @property
    def as_ip_interface(self) -> IPv4Interface | IPv6Interface:
        if self.family == "ipv4":
            return IPv4Interface(f"{self.address}/{self.prefixlen}")
        else:
            return IPv6Interface(f"{self.address}/{self.prefixlen}")


@py_dataclass
class Route:
    dst: IPv4Network
    gateway: str | None
    scope: Literal["universe", "link"]
    proto: Literal["static", "kernel", "boot", "dhcp"]
    link: str
    prefsrc: IPv4Address | None

    @classmethod
    def from_dict(cls, data: dict) -> Route:
        field_set = {f.name for f in fields(cls) if f.init}
        filtered = {k: v for k, v in data.items() if k in field_set}

        filtered["dst"] = IPv4Network(filtered["dst"])

        if filtered["prefsrc"]:
            filtered["prefsrc"] = IPv4Address(filtered["prefsrc"])

        return cls(**filtered)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")

    @property
    def is_default(self) -> bool:
        return self.dst.with_prefixlen == "0.0.0.0/0" and bool(self.gateway)

    def __hash__(self) -> int:
        return hash(self.dst) + hash(self.link) + hash(self.gateway)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Route):
            return False

        return (
            self.dst == other.dst
            and self.link == other.link
            and self.gateway == other.gateway
        )


@py_dataclass
class AddressConfig:
    """Static IPv4 address configuration with gateway and DNS servers."""

    default_dns: ClassVar[set[IPv4Address]] = {
        IPv4Address("1.1.1.1"),
        IPv4Address("8.8.8.8"),
    }

    address: IPv4Interface
    gateway: IPv4Address
    dns: set[IPv4Address] = Field(default=default_dns)

    @classmethod
    def from_dict(cls, data: dict) -> AddressConfig:
        """Create from a dict with 'address', 'gateway', and optional 'dns' keys.

        Raises:
            ValueError: If the address or gateway is invalid, or gateway is
                outside the address subnet.
        """
        try:
            address = IPv4Interface(data.get("address"))
            gateway = IPv4Address(data.get("gateway"))
        except AddressValueError as e:
            raise ValueError(str(e)) from e

        dns_raw = data.get("dns")

        if gateway not in address.network:
            raise ValueError("Gateway must be within the same subnet as address")

        dns = set(IPv4Address(x) for x in dns_raw) if dns_raw else cls.default_dns

        return cls(address, gateway, dns)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")

    def to_systemd_networkd_dict(self) -> dict:
        formatted = {
            "Address": str(self.address),
            "Gateway": str(self.gateway),
            "DNS": [str(x) for x in self.dns],
        }

        return formatted


@py_dataclass
class NetworkConfig:
    """Network configuration for a fluxnode VM, supporting DHCP or static addressing."""

    ip_allocation: Literal["dhcp", "static"] = "dhcp"
    address_config: AddressConfig | None = None
    vlan: int | None = Field(default=None, lt=4095, gt=0)
    rate_limit: Literal[35, 75, 135, 250] | None = None

    @classmethod
    def from_dict(cls, data: dict) -> NetworkConfig:
        ip_allocation: str = data.get("ip_allocation", "")
        vlan: int | None = data.get("vlan")
        rate_limit: int | None = data.get("rate_limit")

        if ip_allocation not in ("dhcp", "static"):
            raise ValueError(f"Invalid ip allocation: {ip_allocation}")
        elif ip_allocation == "dhcp":
            return NetworkConfig(rate_limit=rate_limit)

        address_config_raw = data.get("address_config")

        if not address_config_raw:
            raise ValueError("Network config missing Address Config and static selected")

        address_config = AddressConfig.from_dict(address_config_raw)

        return cls("static", address_config, vlan, rate_limit)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_none=True)

    def systemd_ini_configs(self, interface_name: str) -> list[tuple[str, SystemdConfigParser]]:
        confs: list[tuple[str, SystemdConfigParser]] = []

        int_conf = SystemdConfigParser()

        confs.append((f"20-{interface_name}.network", int_conf))

        dhcp = {"DHCP": "yes"}
        static = {"DHCP": "no"}

        address_config = self.address_config.to_systemd_networkd_dict() if self.address_config else {}

        network_config = static if self.ip_allocation == "static" else dhcp
        network_config |= address_config

        int_conf["Match"] = {"Name": interface_name}

        if self.vlan:
            vlan_interface_name = f"{interface_name}.{self.vlan}"

            vlan_netdev_conf = SystemdConfigParser()
            vlan_int_conf = SystemdConfigParser()

            vlan_netdev_conf["NetDev"] = {
                "Name": vlan_interface_name,
                "Kind": "vlan",
            }
            vlan_netdev_conf["VLAN"] = {"Id": str(self.vlan)}
            vlan_int_conf["Network"] = network_config
            int_conf["Network"] = {"DHCP": "no", "VLAN": vlan_interface_name}

            confs.append((f"20-{vlan_interface_name}.netdev", vlan_netdev_conf))
            confs.append((f"20-{vlan_interface_name}.network", vlan_int_conf))
        else:
            int_conf["Network"] = network_config

        return confs


@py_dataclass
class FluxnodeNetworkConfig:
    """Fluxnode-specific network settings such as UPnP and private chain sources."""

    upnp_port: int | None = None
    router_address: str | None = None
    private_chain_sources: list[str] = Field(default_factory=list)

    @property
    def upnp_enabled(self) -> bool:
        return bool(self.upnp_port)

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json", exclude_defaults=True)

    async def write_installer_config(self, file_path: Path | str) -> bool:
        # we let the configure app set the local chain sources
        ip_pattern = r"^((25[0-5]|(2[0-4]|1\d|[1-9]|)\d)\.?\b){4}$"
        port_pattern = (
            r"^(?:6553[0-5]|655[0-2][0-9]|65[0-4][0-9]{2}|6[0-4][0-9]{3}|[1-5][0-9]{4}|[1-9][0-9]{0,3})"
            r"(?:\s?,\s?(6553[0-5]|655[0-2][0-9]|65[0-4][0-9]{2}|6[0-4][0-9]{3}|[1-5][0-9]{4}|[1-9][0-9]{0,3}))*$"
        )

        filtered_chain_sources = []

        for chain_source in self.private_chain_sources:
            try:
                ip, port = chain_source.split(":")
            except ValueError:
                continue

            if not re.match(ip_pattern, ip) or not re.match(port_pattern, port):
                continue

            if not IPv4Address(ip).is_private:
                continue

            filtered_chain_sources.append(chain_source)

        config = {
            "network": {
                "upnp_enabled": self.upnp_enabled,
                "upnp_port": self.upnp_port,
                "private_chain_sources": filtered_chain_sources,
                "router_address": self.router_address,
            }
        }

        writeable_config = yaml.dump(
            config,
            sort_keys=False,
            default_flow_style=False,
        )

        async with aiofiles.open(file_path, "w") as f:
            await f.write(writeable_config)

        return True
