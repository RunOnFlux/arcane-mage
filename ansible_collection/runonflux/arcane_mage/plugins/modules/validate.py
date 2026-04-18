#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, RunOnFlux
# License: MIT

DOCUMENTATION = r"""
---
module: validate
short_description: Pre-flight validation for FluxNode provisioning
version_added: "1.0.0"
description:
  - Validates that a Proxmox hypervisor is ready for FluxNode provisioning.
  - Checks API version, storage backends, ISO availability, and network bridge.
  - Does not make any changes.
options:
  api_url:
    description: Proxmox API URL.
    required: true
    type: str
  api_token:
    description: Proxmox API token in format C(user@realm!tokenname=tokenvalue).
    required: true
    type: str
    no_log: true
  verify_ssl:
    description: Whether to verify SSL certificates.
    type: bool
    default: false
  node:
    description: Proxmox cluster node name.
    required: true
    type: str
  network:
    description: Network bridge to validate.
    required: true
    type: str
  iso_name:
    description: ISO filename to check for.
    required: true
    type: str
  storage_images:
    description: Storage backend for VM disks.
    type: str
    default: local-lvm
  storage_iso:
    description: Storage backend for ISOs.
    type: str
    default: local
  storage_import:
    description: Storage backend for temporary imports.
    type: str
    default: local
requirements:
  - "python >= 3.13"
  - "arcane-mage >= 2.0.0"
author:
  - RunOnFlux
"""

EXAMPLES = r"""
- name: Validate hypervisor is ready
  runonflux.arcane_mage.validate:
    api_url: "https://65.109.86.37:8006"
    api_token: "{{ proxmox_token }}"
    node: tsa
    network: vmbr4000
    iso_name: FluxLive-1775071308.iso
  delegate_to: localhost
"""

RETURN = r"""
api_version:
  description: Proxmox API version.
  type: str
  returned: success
checks:
  description: Results of each validation check.
  type: dict
  returned: always
  contains:
    api_version:
      description: Whether API version meets minimum.
      type: bool
    storage:
      description: Whether storage backends are valid.
      type: bool
    iso:
      description: Whether the ISO exists.
      type: bool
    network:
      description: Whether the network bridge exists.
      type: bool
"""

import asyncio

from ansible.module_utils.basic import AnsibleModule


async def run_validate(params):
    """Run pre-flight validation checks."""
    from arcane_mage import Provisioner, ProxmoxApi

    token = ProxmoxApi.parse_token(params["api_token"])
    if not token:
        return False, {}, "", "Invalid API token format"

    api = ProxmoxApi.from_token(params["api_url"], token, verify_ssl=params["verify_ssl"])

    checks = {}
    errors = []
    api_version = ""

    async with api:
        provisioner = Provisioner(api)

        # API version
        version_ok, version_msg = await provisioner.validate_api_version(params["node"])
        checks["api_version"] = version_ok
        if version_ok:
            api_version = version_msg
        else:
            errors.append(f"API version: {version_msg}")

        # Storage
        storage_ok, storage_msg = await provisioner.validate_storage(
            params["node"],
            params["storage_iso"],
            params["storage_images"],
            params["storage_import"],
        )
        checks["storage"] = storage_ok
        if not storage_ok:
            errors.append(f"Storage: {storage_msg}")

        # ISO
        iso_ok = await provisioner.validate_iso_version(
            params["node"], params["iso_name"], params["storage_iso"]
        )
        checks["iso"] = iso_ok
        if not iso_ok:
            errors.append(f"ISO '{params['iso_name']}' not found on storage '{params['storage_iso']}'")

        # Network
        network_ok = await provisioner.validate_network(params["node"], params["network"])
        checks["network"] = network_ok
        if not network_ok:
            errors.append(f"Network bridge '{params['network']}' not found")

    all_ok = all(checks.values())
    error_msg = "; ".join(errors) if errors else ""

    return all_ok, checks, api_version, error_msg


def main():
    module_args = dict(
        api_url=dict(type="str", required=True),
        api_token=dict(type="str", required=True, no_log=True),
        verify_ssl=dict(type="bool", default=False),
        node=dict(type="str", required=True),
        network=dict(type="str", required=True),
        iso_name=dict(type="str", required=True),
        storage_images=dict(type="str", default="local-lvm"),
        storage_iso=dict(type="str", default="local"),
        storage_import=dict(type="str", default="local"),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    try:
        success, checks, api_version, error = asyncio.run(run_validate(module.params))
    except ImportError:
        module.fail_json(msg="arcane-mage library is not installed. Install with: pip install arcane-mage")
    except Exception as e:
        module.fail_json(msg=f"Validation error: {e}")

    if not success:
        module.fail_json(msg=f"Validation failed: {error}", checks=checks, api_version=api_version)

    module.exit_json(changed=False, checks=checks, api_version=api_version)


if __name__ == "__main__":
    main()
