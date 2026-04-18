#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, RunOnFlux
# License: MIT

DOCUMENTATION = r"""
---
module: provision
short_description: Provision an ArcaneOS FluxNode VM on Proxmox
version_added: "1.0.0"
description:
  - Creates and optionally starts an ArcaneOS FluxNode VM on a Proxmox hypervisor.
  - Uses the arcane-mage library to handle EFI disk, config disk, TPM 2.0, and VM creation.
  - The VM is fully configured for unattended ArcaneOS installation.
  - Idempotent — skips provisioning if a VM with the same name already exists.
options:
  api_url:
    description: Proxmox API URL (e.g., https://192.168.1.100:8006).
    required: true
    type: str
  api_token:
    description: >
      Proxmox API token in format C(user@realm!tokenname=tokenvalue).
    required: true
    type: str
    no_log: true
  verify_ssl:
    description: Whether to verify SSL certificates.
    type: bool
    default: false
  node:
    description: Proxmox cluster node name (e.g., pve1).
    required: true
    type: str
  vm_name:
    description: Name for the VM on the hypervisor.
    required: true
    type: str
  vm_id:
    description: Explicit VM ID. If omitted, Proxmox assigns the next available ID.
    type: int
  node_tier:
    description: FluxNode tier determining CPU, RAM, and disk allocation.
    required: true
    type: str
    choices: [cumulus, nimbus, stratus]
  network:
    description: Proxmox network bridge name (e.g., vmbr0, vmbr4000).
    required: true
    type: str
  iso_name:
    description: >
      FluxOS ISO filename on the hypervisor (e.g., FluxLive-1775071308.iso).
      Must already be uploaded to the ISO storage.
    required: true
    type: str
  storage_images:
    description: Proxmox storage backend for VM disks.
    type: str
    default: local-lvm
  storage_iso:
    description: Proxmox storage backend where the ISO is stored.
    type: str
    default: local
  storage_import:
    description: Proxmox storage backend for temporary import images (needs 10+ MiB free).
    type: str
    default: local
  start:
    description: Whether to start the VM after creation.
    type: bool
    default: false
  startup_config:
    description: Proxmox startup order config (e.g., order=4,up=360).
    type: str
  disk_limit:
    description: Disk I/O limit in MB/s.
    type: int
  cpu_limit:
    description: CPU limit (0.0-1.0).
    type: float
  network_limit:
    description: Network rate limit in MB/s.
    type: int
  hostname:
    description: System hostname for the ArcaneOS installation.
    required: true
    type: str
  console_password:
    description: >
      Password for the console user. Will be hashed with Yescrypt.
      If omitted, console login is disabled (SSH key only).
    type: str
    no_log: true
  ssh_pubkey:
    description: SSH public key for the operator account (OpenSSH format).
    type: str
  keyboard_layout:
    description: Keyboard layout for the installation.
    type: str
    default: us
  keyboard_variant:
    description: Keyboard variant.
    type: str
    default: ""
  flux_id:
    description: Flux address (14-72 characters).
    required: true
    type: str
  identity_key:
    description: FluxNode identity key in WIF format (51-52 characters).
    required: true
    type: str
    no_log: true
  tx_id:
    description: Collateral transaction hash (exactly 64 hex characters).
    required: true
    type: str
  output_id:
    description: Collateral output index (0-999).
    required: true
    type: int
  ip_allocation:
    description: IP allocation method for the VM.
    type: str
    default: dhcp
    choices: [dhcp, static]
  ip_address:
    description: >
      Static IP address with prefix (e.g., 49.12.156.226/27).
      Required when I(ip_allocation=static).
    type: str
  gateway:
    description: >
      Default gateway IP. Required when I(ip_allocation=static).
      Must be in the same subnet as I(ip_address).
    type: str
  dns:
    description: List of DNS server IPs.
    type: list
    elements: str
    default: ["1.1.1.1", "8.8.8.8"]
  upnp_port:
    description: UPnP port for FluxNode communication.
    type: int
  router_address:
    description: >
      Router IP for UPnP. Pass an empty string to explicitly clear the field
      when UPnP is not used — some daemon versions require the key to be
      present as a string.
    type: str
  notifications:
    description: >
      Notification configuration dict with optional keys:
      discord (webhook_url, user_id), telegram (bot_token, chat_id),
      email, webhook, node_name.
    type: dict
    default: {}
  delegate:
    description: >
      Delegate configuration dict with optional keys:
      collateral_pubkey, delegate_private_key_encrypted,
      delegate_private_key, delegate_passphrase.
    type: dict
    no_log: true
requirements:
  - "python >= 3.13"
  - "arcane-mage >= 2.0.0"
author:
  - RunOnFlux
"""

EXAMPLES = r"""
- name: Provision a Stratus FluxNode with static IP
  runonflux.arcane_mage.provision:
    api_url: "https://65.109.86.37:8006"
    api_token: "automation@pam!ansible=secret-token-value"
    node: tsa
    vm_name: tsa-vs1
    node_tier: stratus
    network: vmbr4000
    iso_name: FluxLive-1775071308.iso
    hostname: tsa-vs1
    console_password: "MySecurePassword123!"
    ssh_pubkey: "ssh-ed25519 AAAAC3..."
    flux_id: "1ExampleFluxAddress"
    identity_key: "L1ExampleIdentityKey"
    tx_id: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
    output_id: 0
    ip_allocation: static
    ip_address: "49.12.156.226/27"
    gateway: "49.12.156.225"
    start: true
  delegate_to: localhost

- name: Provision with DHCP (minimal config)
  runonflux.arcane_mage.provision:
    api_url: "https://192.168.1.100:8006"
    api_token: "{{ proxmox_token }}"
    node: pve1
    vm_name: flux-cumulus-01
    node_tier: cumulus
    network: vmbr0
    iso_name: "{{ flux_iso }}"
    hostname: flux-cumulus-01
    flux_id: "{{ flux_id }}"
    identity_key: "{{ identity_key }}"
    tx_id: "{{ tx_id }}"
    output_id: 0
  delegate_to: localhost
"""

RETURN = r"""
vm_id:
  description: The VM ID assigned by Proxmox.
  type: int
  returned: success
vm_name:
  description: The VM name on the hypervisor.
  type: str
  returned: always
provisioning_log:
  description: List of provisioning step messages.
  type: list
  elements: str
  returned: always
"""

import asyncio

from ansible.module_utils.basic import AnsibleModule


def build_config_dict(params):
    """Build an ArcaneOsConfig-compatible dict from module parameters."""
    # Fluxnode identity
    fluxnode = {
        "identity": {
            "flux_id": params["flux_id"],
            "identity_key": params["identity_key"],
            "tx_id": params["tx_id"],
            "output_id": params["output_id"],
        },
    }

    # Fluxnode network config — upnp_port and router_address are independent.
    # Use `is not None` so empty-string router_address (the "UPnP disabled"
    # sentinel for public-IP nodes) is preserved instead of being dropped.
    network = {}
    if params.get("upnp_port"):
        network["upnp_port"] = params["upnp_port"]
    if params.get("router_address") is not None:
        network["router_address"] = params["router_address"]
    if network:
        fluxnode["network"] = network

    # Notifications
    if params.get("notifications"):
        fluxnode["notifications"] = params["notifications"]

    # Delegate
    if params.get("delegate"):
        fluxnode["delegate"] = params["delegate"]

    # System
    system = {
        "hostname": params["hostname"],
        "keyboard": {
            "layout": params["keyboard_layout"],
            "variant": params["keyboard_variant"],
        },
    }

    if params.get("ssh_pubkey"):
        system["ssh_pubkey"] = params["ssh_pubkey"]

    # Network
    network = {"ip_allocation": params["ip_allocation"]}

    if params["ip_allocation"] == "static":
        network["address_config"] = {
            "address": params["ip_address"],
            "gateway": params["gateway"],
        }
        if params.get("dns"):
            network["address_config"]["dns"] = params["dns"]

    # Hypervisor
    hypervisor = {
        "node": params["node"],
        "vm_name": params["vm_name"],
        "node_tier": params["node_tier"],
        "network": params["network"],
        "iso_name": params["iso_name"],
        "storage_images": params["storage_images"],
        "storage_iso": params["storage_iso"],
        "storage_import": params["storage_import"],
        "start_on_creation": params["start"],
    }

    if params.get("vm_id") is not None:
        hypervisor["vm_id"] = params["vm_id"]
    if params.get("startup_config"):
        hypervisor["startup_config"] = params["startup_config"]
    if params.get("disk_limit") is not None:
        hypervisor["disk_limit"] = params["disk_limit"]
    if params.get("cpu_limit") is not None:
        hypervisor["cpu_limit"] = params["cpu_limit"]
    if params.get("network_limit") is not None:
        hypervisor["network_limit"] = params["network_limit"]

    return {
        "fluxnode": fluxnode,
        "system": system,
        "network": network,
        "hypervisor": hypervisor,
    }


async def run_provision(params):
    """Execute the provisioning workflow."""
    from arcane_mage import ArcaneOsConfig, HashedPassword, Provisioner, ProxmoxApi

    token = ProxmoxApi.parse_token(params["api_token"])
    if not token:
        return False, None, ["Invalid API token format. Expected: user@realm!tokenname=tokenvalue"]

    api = ProxmoxApi.from_token(params["api_url"], token, verify_ssl=params["verify_ssl"])

    log_messages = []
    vm_id = None

    def callback(ok, msg):
        log_messages.append(f"{'OK' if ok else 'FAIL'}: {msg}")

    config_dict = build_config_dict(params)

    # Hash the console password if provided
    if params.get("console_password"):
        hp = HashedPassword(password=params["console_password"])
        config_dict["system"]["hashed_console"] = hp.hash()

    try:
        config = ArcaneOsConfig.from_dict(config_dict)
    except (ValueError, Exception) as e:
        return False, None, [f"Configuration validation failed: {e}"]

    async with api:
        # Check if VM already exists
        vms_res = await api.get_vms(params["node"])
        if vms_res:
            existing = next(
                (v for v in vms_res.payload if v.get("name") == params["vm_name"]),
                None,
            )
            if existing:
                return True, existing.get("vmid"), [f"VM '{params['vm_name']}' already exists (id={existing['vmid']})"]

        provisioner = Provisioner(api)
        success = await provisioner.provision_node(config, callback=callback)

        if success and config.hypervisor:
            # Get the VM ID from Proxmox
            vms_res = await api.get_vms(params["node"])
            if vms_res:
                vm = next(
                    (v for v in vms_res.payload if v.get("name") == params["vm_name"]),
                    None,
                )
                if vm:
                    vm_id = vm.get("vmid")

    return success, vm_id, log_messages


def main():
    module_args = dict(
        api_url=dict(type="str", required=True),
        api_token=dict(type="str", required=True, no_log=True),
        verify_ssl=dict(type="bool", default=False),
        node=dict(type="str", required=True),
        vm_name=dict(type="str", required=True),
        vm_id=dict(type="int"),
        node_tier=dict(type="str", required=True, choices=["cumulus", "nimbus", "stratus"]),
        network=dict(type="str", required=True),
        iso_name=dict(type="str", required=True),
        storage_images=dict(type="str", default="local-lvm"),
        storage_iso=dict(type="str", default="local"),
        storage_import=dict(type="str", default="local"),
        start=dict(type="bool", default=False),
        startup_config=dict(type="str"),
        disk_limit=dict(type="int"),
        cpu_limit=dict(type="float"),
        network_limit=dict(type="int"),
        hostname=dict(type="str", required=True),
        console_password=dict(type="str", no_log=True),
        ssh_pubkey=dict(type="str"),
        keyboard_layout=dict(type="str", default="us"),
        keyboard_variant=dict(type="str", default=""),
        flux_id=dict(type="str", required=True),
        identity_key=dict(type="str", required=True, no_log=True),
        tx_id=dict(type="str", required=True),
        output_id=dict(type="int", required=True),
        ip_allocation=dict(type="str", default="dhcp", choices=["dhcp", "static"]),
        ip_address=dict(type="str"),
        gateway=dict(type="str"),
        dns=dict(type="list", elements="str", default=["1.1.1.1", "8.8.8.8"]),
        upnp_port=dict(type="int"),
        router_address=dict(type="str"),
        notifications=dict(type="dict", default={}),
        delegate=dict(type="dict", no_log=True),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        required_if=[
            ("ip_allocation", "static", ["ip_address", "gateway"]),
        ],
        supports_check_mode=True,
    )

    if module.check_mode:
        module.exit_json(
            changed=False,
            vm_name=module.params["vm_name"],
            provisioning_log=["Check mode — no changes made"],
        )

    try:
        success, vm_id, log_messages = asyncio.run(run_provision(module.params))
    except ImportError:
        module.fail_json(msg="arcane-mage library is not installed. Install with: pip install arcane-mage")
    except Exception as e:
        module.fail_json(msg=f"Provisioning error: {e}")

    if not success:
        module.fail_json(
            msg=f"Failed to provision VM '{module.params['vm_name']}'",
            vm_name=module.params["vm_name"],
            provisioning_log=log_messages,
        )

    # VM already existed = no change
    already_existed = any("already exists" in m for m in log_messages)

    module.exit_json(
        changed=not already_existed,
        vm_id=vm_id,
        vm_name=module.params["vm_name"],
        provisioning_log=log_messages,
    )


if __name__ == "__main__":
    main()
