from __future__ import annotations

from ipaddress import IPv4Address, IPv4Interface

import pytest
from pydantic import ValidationError

from arcane_mage.models import AddressConfig, Identifier, NetworkConfig


class TestAddressConfig:
    def test_from_dict_valid(self):
        data = {
            "address": "192.168.1.10/24",
            "gateway": "192.168.1.1",
            "dns": ["8.8.8.8", "1.1.1.1"],
        }
        config = AddressConfig.from_dict(data)

        assert config.address == IPv4Interface("192.168.1.10/24")
        assert config.gateway == IPv4Address("192.168.1.1")
        assert config.dns == {IPv4Address("8.8.8.8"), IPv4Address("1.1.1.1")}

    def test_gateway_outside_subnet_raises(self):
        data = {
            "address": "192.168.1.10/24",
            "gateway": "10.0.0.1",
        }

        with pytest.raises(ValueError, match="same subnet"):
            AddressConfig.from_dict(data)

    def test_default_dns(self):
        data = {
            "address": "192.168.1.10/24",
            "gateway": "192.168.1.1",
        }
        config = AddressConfig.from_dict(data)

        assert IPv4Address("1.1.1.1") in config.dns
        assert IPv4Address("8.8.8.8") in config.dns

    def test_to_dict_roundtrip(self):
        data = {
            "address": "192.168.1.10/24",
            "gateway": "192.168.1.1",
            "dns": ["8.8.8.8"],
        }
        config = AddressConfig.from_dict(data)
        result = config.to_dict()

        assert result["address"] == "192.168.1.10/24"
        assert result["gateway"] == "192.168.1.1"

    def test_to_systemd_networkd_dict(self):
        data = {
            "address": "192.168.1.10/24",
            "gateway": "192.168.1.1",
            "dns": ["8.8.8.8"],
        }
        config = AddressConfig.from_dict(data)
        result = config.to_systemd_networkd_dict()

        assert result["Address"] == "192.168.1.10/24"
        assert result["Gateway"] == "192.168.1.1"
        assert "8.8.8.8" in result["DNS"]


class TestNetworkConfig:
    def test_dhcp(self, network_dhcp_dict: dict):
        config = NetworkConfig.from_dict(network_dhcp_dict)

        assert config.ip_allocation == "dhcp"
        assert config.address_config is None

    def test_static(self, network_static_dict: dict):
        config = NetworkConfig.from_dict(network_static_dict)

        assert config.ip_allocation == "static"
        assert config.address_config is not None
        assert config.address_config.gateway == IPv4Address("192.168.44.1")

    def test_invalid_ip_allocation(self):
        with pytest.raises(ValueError, match="Invalid ip allocation"):
            NetworkConfig.from_dict({"ip_allocation": "auto"})

    def test_static_without_address_config(self):
        with pytest.raises(ValueError, match="missing Address Config"):
            NetworkConfig.from_dict({"ip_allocation": "static"})

    def test_vlan_range(self):
        with pytest.raises(ValidationError):
            NetworkConfig(ip_allocation="dhcp", vlan=5000)

    def test_to_dict_roundtrip(self, network_static_dict: dict):
        config = NetworkConfig.from_dict(network_static_dict)
        result = config.to_dict()

        assert result["ip_allocation"] == "static"
        assert result["address_config"]["address"] == "192.168.44.13/24"

    def test_systemd_dhcp_config(self):
        config = NetworkConfig(ip_allocation="dhcp")
        confs = config.systemd_ini_configs("eth0")

        assert len(confs) == 1
        filename, parser = confs[0]
        assert filename == "20-eth0.network"
        assert parser["Network"]["DHCP"] == "yes"

    def test_systemd_vlan_config(self):
        config = NetworkConfig(ip_allocation="dhcp", vlan=100)
        confs = config.systemd_ini_configs("eth0")

        assert len(confs) == 3
        filenames = [c[0] for c in confs]
        assert "20-eth0.100.netdev" in filenames
        assert "20-eth0.100.network" in filenames


class TestIdentifier:
    def test_from_dict(self):
        data = {"type": "system-uuid", "value": "abc-123"}
        ident = Identifier(**data)

        assert ident.type == "system-uuid"
        assert ident.value == "abc-123"

    def test_to_dict_roundtrip(self):
        data = {"type": "mac-address", "value": "aa:bb:cc:dd:ee:ff"}
        ident = Identifier(**data)

        assert ident.to_dict() == data
