from __future__ import annotations

import re
from ast import literal_eval
from configparser import ConfigParser
from dataclasses import asdict, fields
from ipaddress import (
    AddressValueError,
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Interface,
)
from pathlib import Path
from typing import Any, ClassVar, Iterator, Literal
from uuid import uuid4

import aiofiles
import keyring
import keyring.errors
import yaml
from pydantic import EmailStr, field_validator
from pydantic.dataclasses import Field
from pydantic.dataclasses import dataclass as py_dataclass
from pydantic.networks import HttpUrl
from pydantic.types import StringConstraints
from sshpubkeys import InvalidKeyError, SSHKey
from typing_extensions import Annotated


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
class HypervisorConfig:
    url: str
    auth_type: Literal["token", "userpass"]
    credential: str
    keychain: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> HypervisorConfig:
        items: dict[str, str | bool | None] = {}

        for _field in fields(cls):
            items[_field.name] = data.get(_field.name)

        return cls(**items)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HypervisorConfig):
            return False

        return (
            self.url == other.url
            and self.auth_type == other.auth_type
            and self.credential == other.credential
        )

    def real_credential(self) -> str | None:
        if not self.keychain:
            return self.credential

        stored_cred = keyring.get_password("arcane_mage", self.credential)

        return stored_cred


@py_dataclass
class ArcaneCreatorConfig:
    config_path: ClassVar[Path] = Path().home() / ".fluxnode_creator.yaml"

    hypervisors: list[HypervisorConfig] = Field(default_factory=list)
    use_keyring: bool = True
    default_page: str | None = None

    as_dict = asdict

    @classmethod
    def from_fs(cls) -> ArcaneCreatorConfig:
        try:
            with open(ArcaneCreatorConfig.config_path) as f:
                config_raw: str | None = f.read()
        except FileNotFoundError:
            config_raw = None

        if not config_raw:
            return cls()

        try:
            parsed: dict | None = yaml.safe_load(config_raw)
        except yaml.YAMLError:
            parsed = None

        if not parsed:
            return cls()

        hypervisors = [
            HypervisorConfig.from_dict(x) for x in parsed.get("hypervisors", [])
        ]

        return cls(
            hypervisors,
            parsed.get("use_keyring", True),
            parsed.get("default_page"),
        )

    @property
    def has_config(self) -> bool:
        return bool(self.hypervisors)

    def write(self) -> None:
        with open(ArcaneCreatorConfig.config_path, "w") as f:
            f.write(yaml.dump(self.as_dict()))

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
                keyring.set_password(
                    "arcane_mage", cred_uuid, hypervisor.credential
                )
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
            try:
                keyring.delete_password("arcane_mage", hypervisor.credential)
            except keyring.errors.PasswordDeleteError:
                pass


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
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props

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
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props

    @property
    def as_ip_interface(self) -> IPv4Interface | IPv6Interface:
        if self.family == "ipv4":
            return IPv4Interface(f"{self.address}/{self.prefixlen}")
        else:
            return IPv4Interface(f"{self.address}/{self.prefixlen}")


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
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            if isinstance(value, IPv4Address):
                props[_field.name] = str(value)
            else:
                props[_field.name] = value

        return props

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
class Identifier:
    type: Literal["system-uuid", "mac-address"]
    value: str

    @classmethod
    def from_dict(cls, data: dict) -> Identifier:
        filtered = {}

        for _field in fields(cls):
            filtered[_field.name] = data.get(_field.name)

        return cls(**filtered)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props


@py_dataclass
class AddressConfig:
    default_dns: ClassVar[set[IPv4Address]] = {
        IPv4Address("1.1.1.1"),
        IPv4Address("8.8.8.8"),
    }

    address: IPv4Interface
    gateway: IPv4Address
    dns: set[IPv4Address] = Field(default=default_dns)

    @classmethod
    def from_dict(cls, data: dict) -> AddressConfig:
        try:
            address = IPv4Interface(data.get("address"))
            gateway = IPv4Address(data.get("gateway"))
        except AddressValueError as e:
            raise ValueError(str(e))

        dns_raw = data.get("dns")

        if gateway not in address.network:
            raise ValueError(
                "Gateway must be within the same subnet as address"
            )

        dns = (
            set(IPv4Address(x) for x in dns_raw) if dns_raw else cls.default_dns
        )

        return cls(address, gateway, dns)

    def to_dict(self) -> dict:
        parsed: dict = {}

        for _field in fields(self):
            attr = getattr(self, _field.name)

            if isinstance(attr, set):
                parsed[_field.name] = [str(x) for x in attr]
            else:
                parsed[_field.name] = str(attr)

        return parsed

    def to_systemd_networkd_dict(self) -> dict:
        formatted = {
            "Address": str(self.address),
            "Gateway": str(self.gateway),
            "DNS": [str(x) for x in self.dns],
        }

        return formatted


@py_dataclass
class NetworkConfig:
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
            raise ValueError(
                "Network config missing Address Config and static selected"
            )

        address_config = AddressConfig.from_dict(address_config_raw)

        return cls("static", address_config, vlan, rate_limit)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            if hasattr(value, "to_dict"):
                props[_field.name] = value.to_dict()
            else:
                props[_field.name] = value

        return props

    def systemd_ini_configs(
        self, interface_name: str
    ) -> list[tuple[str, SystemdConfigParser]]:
        confs: list[tuple[str, SystemdConfigParser]] = []

        int_conf = SystemdConfigParser()

        confs.append((f"20-{interface_name}.network", int_conf))

        dhcp = {"DHCP": "yes"}
        static = {"DHCP": "no"}

        address_config = (
            self.address_config.to_systemd_networkd_dict()
            if self.address_config
            else {}
        )

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
class KeyboardConfig:
    # narrow these
    layout: str = "us"
    variant: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> KeyboardConfig:
        filtered = {}

        for _field in fields(cls):
            value = data.get(_field.name)
            if not isinstance(value, str):
                continue

            filtered[_field.name] = value

        return cls(**filtered)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props


@py_dataclass
class SystemConfig:
    hostname: str = Field(min_length=2, max_length=253)
    hashed_console: str = "!"  # No password login
    ssh_pubkey: str | None = None
    keyboard: KeyboardConfig = Field(default_factory=KeyboardConfig)

    @field_validator("ssh_pubkey", mode="before")
    @classmethod
    def validate_ssh_pubkey(cls, value: str | None) -> str | None:
        if not value:
            return value

        key = SSHKey(value, strict=True)

        try:
            key.parse()
        except InvalidKeyError:
            raise ValueError("A public key in OpenSSH format is required")

        return value

    @classmethod
    def from_dict(cls, data: dict) -> SystemConfig:
        hostname = data.get("hostname")
        console = data.get("hashed_console", "!")
        keyboard_raw = data.get("keyboard")
        ssh_pubkey = data.get("ssh_pubkey")

        if not hostname:
            raise ValueError("System config is missing hostname")

        if not console:
            raise ValueError("System config is missing hashed console password")

        keyboard = (
            KeyboardConfig.from_dict(keyboard_raw)
            if keyboard_raw
            else KeyboardConfig()
        )

        return cls(
            hostname,
            console,
            ssh_pubkey,
            keyboard,
        )

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            if hasattr(value, "to_dict"):
                props[_field.name] = value.to_dict()
            else:
                props[_field.name] = value

        return props


@py_dataclass
class Identity:
    ui_property_map: ClassVar[dict] = {
        "flux_id": "fluxId",
        "identity_key": "identityKey",
        "tx_id": "txId",
        "output_id": "outputId",
    }

    flux_id: str
    identity_key: str
    tx_id: str
    output_id: int

    asdict = asdict

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
    def from_dict(cls, data: dict, ui: bool = False) -> Identity:
        """Generates a fluxnode identity from either a UI dict, or standard dict

        Args:
            params (dict): The raw dict to convert to an Identity
            ui (bool, optional): If params are from the UI. Defaults to False.

        Raises:
            ValueError: If the dict doesn't pass validation

        Returns:
            Identity: The fluxnode Identity
        """

        items = []

        for _field in fields(cls):
            name = cls.ui_property_map[_field.name] if ui else _field.name
            prop = data.get(name, None)

            if prop is None:
                raise ValueError(f"Property: {name} missing")

            items.append(prop)

        return cls(*items)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props

    @property
    def ui_dict(self) -> dict:
        """Returns the Identity in dict format for the UI

        Returns:
            dict: UI formatted dictionariy
        """

        return {
            self.ui_property_map[_field.name]: getattr(self, _field.name)
            for _field in fields(self)
        }


@py_dataclass
class DiscordNotification:
    ui_property_map: ClassVar[dict] = {
        "webhook_url": "discordWebhookUrl",
        "user_id": "discordUserId",
    }

    watchdog_property_map: ClassVar[dict] = {
        "webhook_url": "web_hook_url",
        "user_id": "ping",
    }

    webhook_url: str | None = None
    user_id: str | None = None

    @field_validator("webhook_url", mode="after")
    @classmethod
    def validate_webhook_url(cls, value: str | None) -> str | None:
        if not value:
            return value

        # this will raise Validation error (and be caught)
        url = HttpUrl(value)
        # discordapp.com is the deprecated endpoint
        valid_hosts = ["discordapp.com", "discord.com"]

        if url.host not in valid_hosts:
            raise ValueError(
                "Discord webhook url must have discord as the host"
            )

        if not url.scheme == "https":
            raise ValueError("discord webhook url scheme must be https")

        if not url.path or not url.path.startswith("/api/webhooks"):
            raise ValueError(
                "discord webhook path must start with /api/webhooks"
            )

        return value

    @field_validator("user_id", mode="before")
    @classmethod
    def validate_user_id(cls, value: str | int) -> str:
        if not value and isinstance(value, str):
            return value
        elif value is None:
            return value

        as_str = str(value)

        len_user_id = len(as_str)

        if len_user_id < 17 or len_user_id > 19:
            raise ValueError(
                "Discord user id must be between 17 and 19 characters"
            )

        return as_str

    @classmethod
    def from_dict(cls, data: dict, ui: bool = False) -> DiscordNotification:
        items = []

        for _field in fields(cls):
            name = cls.ui_property_map[_field.name] if ui else _field.name
            prop = data.get(name, None)

            items.append(prop)

        return cls(*items)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props

    @property
    def watchdog_dict(self) -> dict:
        return {
            self.watchdog_property_map[field.name]: getattr(self, field.name)
            or "0"
            for field in fields(self)
        }

    @property
    def ui_dict(self) -> dict:
        return {
            self.ui_property_map[field.name]: getattr(self, field.name)
            for field in fields(self)
        }


@py_dataclass
class TelegramNotification:
    ui_property_map: ClassVar[dict] = {
        "bot_token": "telegramBotToken",
        "chat_id": "telegramChatId",
    }

    watchdog_property_map: ClassVar[dict] = {
        "bot_token": "telegram_bot_token",
        "chat_id": "telegram_chat_id",
        "telegram_alert": "telegram_alert",
    }

    bot_token: str | None = Field(
        None, pattern=r"^[0-9]{8,10}:[a-zA-Z0-9_-]{35}$"
    )
    chat_id: str | None = Field(None, min_length=6, max_length=1000)

    @classmethod
    def from_dict(cls, data: dict, ui: bool = False) -> TelegramNotification:
        items = []

        for _field in fields(cls):
            name = cls.ui_property_map[_field.name] if ui else _field.name
            prop = data.get(name, None)

            items.append(prop)

        return cls(*items)

    @property
    def telegram_alert(self) -> str:
        return "1" if self.bot_token and self.chat_id else "0"

    @property
    def watchdog_dict(self) -> dict:
        # we use the watchdog_property_map so we can include telegram_alert
        return {
            self.watchdog_property_map[key]: getattr(self, key) or "0"
            for key in self.watchdog_property_map
        }

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props

    @property
    def ui_dict(self) -> dict:
        # ToDo: figure out how to use InitVar with telegram_alert
        return {
            self.ui_property_map[field.name]: getattr(self, field.name)
            for field in fields(self)
        }


@py_dataclass
class Notifications:
    ui_property_map: ClassVar[dict] = {
        "email": "emailAddress",
        "webhook": "genericWebhookUrl",
        "node_name": "nodeName",
    }

    discord: DiscordNotification = Field(default_factory=DiscordNotification)
    telegram: TelegramNotification = Field(default_factory=TelegramNotification)
    email: (
        Annotated[
            EmailStr, StringConstraints(strip_whitespace=True, to_lower=True)
        ]
        | None
    ) = None
    webhook: str | None = None
    node_name: str | None = None

    asdict = asdict

    @field_validator("webhook", mode="after")
    @classmethod
    def validate_webhook(cls, value: str | None) -> str | None:
        if not value:
            return value

        # this will raise ValidationError for us
        HttpUrl(value)

        return value

    @classmethod
    def from_dict(cls, data: dict, ui: bool = False):
        discord_raw = data.get("discord")
        telegram_raw = data.get("telegram")

        discord = (
            DiscordNotification.from_dict(discord_raw, ui)
            if discord_raw
            else DiscordNotification()
        )

        telegram = (
            TelegramNotification.from_dict(telegram_raw, ui)
            if telegram_raw
            else TelegramNotification()
        )

        other_items = (
            {k: data.get(v) for k, v in cls.ui_property_map.items()}
            if ui
            else {k: data.get(k) for k in cls.ui_property_map.keys()}
        )

        return cls(discord=discord, telegram=telegram, **other_items)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            if hasattr(value, "to_dict"):
                props[_field.name] = value.to_dict()
            elif value:
                props[_field.name] = value

        return props

    def to_ui_dict(self) -> dict:
        discord = self.discord.ui_dict if self.discord else {}
        telegram = self.telegram.ui_dict if self.telegram else {}

        rest = {
            self.ui_property_map[field.name]: getattr(self, field.name)
            for field in fields(self)
            if field.name in self.ui_property_map and getattr(self, field.name)
        }

        return discord | telegram | rest


@py_dataclass
class GravityConfig:
    ui_property_map: ClassVar[dict] = {
        "blocked_ports": "blockedPorts",
        "blocked_repositories": "blockedRepositories",
    }

    debug: bool = False
    development: bool = False
    testnet: bool = False
    blocked_ports: list[int] = Field(default_factory=list)
    blocked_repositories: list[str] = Field(default_factory=list)

    asdict

    @field_validator("blocked_ports", mode="before")
    @classmethod
    def validate_blocked_ports(cls, value: list[int | str]) -> list[int]:
        # ToDo: figure how to do this as a field

        as_int = []

        pattern = r"^([1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])$"

        for port in value:
            if not re.search(pattern, str(port)):
                raise ValueError(f"Port: {port} must be in the range 0-65535")

            as_int.append(int(port))

        return as_int

    @field_validator("blocked_repositories", mode="before")
    @classmethod
    def validate_blocked_repositories(cls, value: list) -> list:
        # ToDo: figure how to do this as a field

        pattern = r"^(?:(?:(?:(?:[\w-]+(?:\.[\w-]+)+)(?::\d+)?)|[\w]+:\d+)\/)?\/?(?:(?:(?:[a-z0-9]+(?:(?:[._]|__|[-]*)[a-z0-9]+)*)\/){0,2})(?:[a-z0-9-_.]+\/{0,1}[a-z0-9-_.]+)[:]?(?:[\w][\w.-]{0,127})?$"

        for repo in value:
            if not re.search(pattern, repo):
                raise ValueError(f"Repository: {repo} must be a valid format")

        return value

    @classmethod
    def from_dict(cls, data: dict, ui: bool = False) -> GravityConfig:
        items: dict = {}

        for prop, ui_prop in cls.ui_property_map.items():
            key = ui_prop if ui else prop
            value = data.get(key, None)

            if value is not None:
                items[prop] = value

        return cls(**items)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            if value:
                props[_field.name] = value

        return props

    def to_ui_dict(self) -> dict:
        return {
            self.ui_property_map[field.name]: getattr(self, field.name)
            for field in fields(self)
            if field.name in self.ui_property_map
        }


@py_dataclass
class FluxnodeNetworkConfig:
    upnp_port: int | None = None
    router_address: str | None = None
    private_chain_sources: list[str] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> FluxnodeNetworkConfig:
        props = {}
        for _field in fields(cls):
            value = data.get(_field.name, None)

            if value is not None:
                props[_field.name] = value

        return cls(**props)

    @property
    def upnp_enabled(self) -> bool:
        return bool(self.upnp_port)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            if value:
                props[_field.name] = value

        return props

    async def write_installer_config(self, file_path: Path) -> bool:
        # we let the configure app set the local chain sources
        ip_pattern = r"^((25[0-5]|(2[0-4]|1\d|[1-9]|)\d)\.?\b){4}$"
        port_pattern = r"^(?:6553[0-5]|655[0-2][0-9]|65[0-4][0-9]{2}|6[0-4][0-9]{3}|[1-5][0-9]{4}|[1-9][0-9]{0,3})(?:\s?,\s?(6553[0-5]|655[0-2][0-9]|65[0-4][0-9]{2}|6[0-4][0-9]{3}|[1-5][0-9]{4}|[1-9][0-9]{0,3}))*$"

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


@py_dataclass
class FluxnodeConfig:
    config_path: ClassVar[Path] = Path("/mnt/root/config/flux_user_config.yaml")

    identity: Identity
    gravity: GravityConfig = Field(default_factory=GravityConfig)
    network: FluxnodeNetworkConfig = Field(
        default_factory=FluxnodeNetworkConfig
    )
    notifications: Notifications = Field(default_factory=Notifications)

    @staticmethod
    def remove_none_factory(data: list[tuple[str, Any]]):
        return {field: value for field, value in data if value is not None}

    @classmethod
    def from_dict(cls, params: dict) -> FluxnodeConfig:
        identity_raw: dict | None = params.get("identity")
        notifications_raw: dict | None = params.get("notifications")
        gravity_raw: dict | None = params.get("gravity")
        network_raw: dict | None = params.get("network")

        if not identity_raw:
            raise ValueError("Fluxnode identity missing")

        identity = Identity.from_dict(identity_raw)
        notifications = (
            Notifications.from_dict(notifications_raw)
            if notifications_raw
            else Notifications()
        )
        gravity = (
            GravityConfig.from_dict(gravity_raw)
            if gravity_raw
            else GravityConfig()
        )
        network = (
            FluxnodeNetworkConfig.from_dict(network_raw)
            if network_raw
            else FluxnodeNetworkConfig()
        )

        return cls(identity, gravity, network, notifications)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            if hasattr(value, "to_dict"):
                props[_field.name] = value.to_dict()
            elif value:
                props[_field.name] = value

        return props

    @classmethod
    async def from_config_file(cls) -> FluxnodeConfig | None:
        try:
            async with aiofiles.open(cls.config_path, "r") as f:
                data = await f.read()
        except FileNotFoundError:
            return None

        try:
            conf: dict = yaml.safe_load(data)
        except yaml.YAMLError:
            return None

        return cls.from_dict(conf)

    async def write_user_config(
        self, file_path: Path, ssh_pubkey: str | None = None
    ) -> bool:
        config = {
            "identity": self.identity.to_dict(),
            "notifications": self.notifications.to_dict(),
            "miscellaneous": {"ssh_pubkey": ssh_pubkey}
            | self.gravity.to_dict(),
        }

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

    @property
    def gravity_properties(self) -> dict:
        return {
            "flux_id": self.identity.flux_id,
            "debug": self.gravity.debug if self.gravity else False,
            "development": self.gravity.development if self.gravity else False,
            "testnet": self.gravity.testnet if self.gravity else False,
            "blocked_ports": self.gravity.blocked_ports if self.gravity else [],
            "blocked_repositories": self.gravity.blocked_repositories
            if self.gravity
            else [],
        }


@py_dataclass
class Hypervisor:
    node: str
    vm_name: str
    node_tier: Literal["cumulus", "nimbus", "stratus"]
    network: str
    iso_name: str = Field(pattern=r"^FluxLive-\d{10}\.iso$")
    storage_images: str = "local-lvm"
    storage_iso: str = "local"
    storage_import: str = "local"
    startup_config: str | None = None
    disk_limit: int | None = None
    cpu_limit: float | None = None
    network_limit: int | None = None
    start_on_creation: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> Hypervisor:
        props = {}
        for _field in fields(cls):
            value = data.get(_field.name, None)

            if value is not None:
                props[_field.name] = value

        return cls(**props)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props


@py_dataclass
class InstallerConfig:
    auto_reboot: bool = True
    reboot_to_firmware: bool = False
    reboot_to_boot_menu: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> InstallerConfig:
        props = {}
        for _field in fields(cls):
            value = data.get(_field.name, None)

            if value is not None:
                props[_field.name] = value

        return cls(**props)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props


@py_dataclass
class MetricsAppConfig:
    poweroff_screen: int = 0
    theme: str = "flexoki"

    @classmethod
    def from_dict(cls, data: dict) -> MetricsAppConfig:
        props = {}
        for _field in fields(cls):
            value = data.get(_field.name, None)

            if value is not None:
                props[_field.name] = value

        return cls(**props)

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name)

            props[_field.name] = value

        return props

    async def write_config(self, file_path: Path) -> bool:
        config = self.to_dict()

        writeable_config = yaml.dump(
            config,
            sort_keys=False,
            default_flow_style=False,
        )

        async with aiofiles.open(file_path, "w") as f:
            await f.write(writeable_config)

        return True


@py_dataclass
class ArcaneOsConfig:
    fluxnode: FluxnodeConfig
    system: SystemConfig
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    installer: InstallerConfig = Field(default_factory=InstallerConfig)
    metrics_app: MetricsAppConfig = Field(default_factory=MetricsAppConfig)
    hypervisor: Hypervisor | None = None
    identifier: Identifier | None = None

    @classmethod
    def from_dict(cls, data: dict) -> ArcaneOsConfig:
        fluxnode_raw = data.get("fluxnode")
        system_raw = data.get("system")
        network_raw = data.get("network")
        hypervisor_raw = data.get("hypervisor")
        identifier_raw = data.get("identifier")
        installer_raw = data.get("installer")
        metrics_app_raw = data.get("metrics_app")

        if not fluxnode_raw:
            raise ValueError("fluxnode config missing")

        if not system_raw:
            raise ValueError("system config missing")

        fluxnode = FluxnodeConfig.from_dict(fluxnode_raw)
        system = SystemConfig.from_dict(system_raw)

        network = (
            NetworkConfig.from_dict(network_raw)
            if network_raw
            else NetworkConfig()
        )

        hypervisor = (
            Hypervisor.from_dict(hypervisor_raw) if hypervisor_raw else None
        )

        identifier = (
            Identifier.from_dict(identifier_raw) if identifier_raw else None
        )

        installer = (
            InstallerConfig.from_dict(installer_raw)
            if installer_raw
            else InstallerConfig()
        )

        metrics_app = (
            MetricsAppConfig.from_dict(metrics_app_raw)
            if metrics_app_raw
            else MetricsAppConfig()
        )

        return cls(
            fluxnode,
            system,
            network,
            installer,
            metrics_app,
            hypervisor,
            identifier,
        )

    def to_dict(self) -> dict:
        props = {}

        for _field in fields(self):
            value = getattr(self, _field.name, None)

            if value is not None:
                props[_field.name] = value.to_dict()

        return props

    def as_row(self) -> list[str]:
        address = (
            "dhcp"
            if not self.network.address_config
            else str(self.network.address_config.address)
        )
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
        res = await self.fluxnode.write_user_config(
            file_path, ssh_pubkey=ssh_pubkey
        )

        return res

    async def write_metrics_config(self, file_path: Path) -> bool:
        res = await self.metrics_app.write_config(file_path)

        return res


@py_dataclass
class ArcaneOsConfigGroup:
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
        file_path = config_path or ArcaneOsConfigGroup.default_path

        try:
            with open(file_path, "r") as f:
                data = f.read()
        except (FileNotFoundError, PermissionError):
            # log
            return cls()

        try:
            config_raw = yaml.safe_load(data)
        except yaml.YAMLError:
            # log
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
        return {"nodes": [x.to_dict() for x in self.nodes]}

    def get_node_by_vm_name(
        self, node_name: str, vm_name: str
    ) -> ArcaneOsConfig | None:
        node = next(
            filter(
                lambda x: x.hypervisor
                and x.hypervisor.vm_name == vm_name
                and x.hypervisor.node == node_name,
                self.nodes,
            ),
            None,
        )

        return node

    def get_nodes_by_hypervisor_name(
        self, hyper_name: str
    ) -> ArcaneOsConfigGroup:
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


if __name__ == "__main__":
    import asyncio
    from rich.pretty import pprint
    # address_config: AddressConfig | None = None
    # conf = NetworkConfig(
    #     ip_allocation="static",
    #     address_config=AddressConfig(
    #         address=IPv4Interface("192.168.44.14/24"),
    #         gateway=IPv4Address("192.16.44.1"),
    #         dns=["1.1.1.1", "5.5.5.5"],
    #     ),
    # )

    # configs: list[tuple[str, SystemdConfigParser]] = conf.systemd_ini_configs(
    #     "ens44"
    # )

    # for filename, config in configs:
    #     with open(filename, "w") as fd:
    #         config.write(fd, space_around_delimiters=False)

    async def main():
        config = ArcaneOsConfigGroup.from_fs(Path("dev/minimal.yaml"))

        pprint(config)

        await config.nodes[0].write_user_config("testing123.yaml")

    asyncio.run(main())
