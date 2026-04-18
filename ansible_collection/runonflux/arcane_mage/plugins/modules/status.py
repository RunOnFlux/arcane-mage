#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, RunOnFlux
# License: MIT

DOCUMENTATION = r"""
---
module: status
short_description: Check FluxNode VM status on Proxmox
version_added: "1.0.0"
description:
  - Lists all VMs on a Proxmox node and their current status.
  - Optionally filter by VM name to check a specific node.
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
  vm_name:
    description: Filter results to a specific VM name.
    type: str
  vm_id:
    description: Filter results to a specific VM ID.
    type: int
requirements:
  - "python >= 3.13"
  - "arcane-mage >= 2.0.0"
author:
  - RunOnFlux
"""

EXAMPLES = r"""
- name: List all VMs on a node
  runonflux.arcane_mage.status:
    api_url: "https://65.109.86.37:8006"
    api_token: "{{ proxmox_token }}"
    node: tsa
  delegate_to: localhost
  register: vm_status

- name: Check specific VM status
  runonflux.arcane_mage.status:
    api_url: "https://65.109.86.37:8006"
    api_token: "{{ proxmox_token }}"
    node: tsa
    vm_name: tsa-vs1
  delegate_to: localhost
"""

RETURN = r"""
vms:
  description: List of VMs with their status.
  type: list
  elements: dict
  returned: always
  contains:
    vmid:
      description: VM ID.
      type: int
    name:
      description: VM name.
      type: str
    status:
      description: VM status (running, stopped, etc.).
      type: str
    cpu:
      description: CPU usage.
      type: float
    mem:
      description: Memory usage in bytes.
      type: int
    maxmem:
      description: Maximum memory in bytes.
      type: int
    disk:
      description: Disk usage in bytes.
      type: int
    maxdisk:
      description: Maximum disk in bytes.
      type: int
"""

import asyncio

from ansible.module_utils.basic import AnsibleModule


async def run_status(params):
    """Fetch VM status from Proxmox."""
    from arcane_mage import ProxmoxApi

    token = ProxmoxApi.parse_token(params["api_token"])
    if not token:
        return False, [], "Invalid API token format"

    api = ProxmoxApi.from_token(params["api_url"], token, verify_ssl=params["verify_ssl"])

    async with api:
        vms_res = await api.get_vms(params["node"])
        if not vms_res:
            return False, [], f"Unable to list VMs on {params['node']}"

        vms = vms_res.payload

        if params.get("vm_name"):
            vms = [v for v in vms if v.get("name") == params["vm_name"]]
        elif params.get("vm_id") is not None:
            vms = [v for v in vms if v.get("vmid") == params["vm_id"]]

    return True, vms, ""


def main():
    module_args = dict(
        api_url=dict(type="str", required=True),
        api_token=dict(type="str", required=True, no_log=True),
        verify_ssl=dict(type="bool", default=False),
        node=dict(type="str", required=True),
        vm_name=dict(type="str"),
        vm_id=dict(type="int"),
    )

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    try:
        success, vms, error = asyncio.run(run_status(module.params))
    except ImportError:
        module.fail_json(msg="arcane-mage library is not installed. Install with: pip install arcane-mage")
    except Exception as e:
        module.fail_json(msg=f"Status check error: {e}")

    if not success:
        module.fail_json(msg=error)

    module.exit_json(changed=False, vms=vms)


if __name__ == "__main__":
    main()
