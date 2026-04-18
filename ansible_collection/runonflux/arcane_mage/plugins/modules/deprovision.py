#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, RunOnFlux
# License: MIT

DOCUMENTATION = r"""
---
module: deprovision
short_description: Remove a FluxNode VM from Proxmox
version_added: "1.0.0"
description:
  - Stops a running VM and deletes it along with all its disks.
  - Idempotent — succeeds if the VM does not exist.
  - Target VM by name or ID.
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
    description: Name of the VM to deprovision. Mutually exclusive with I(vm_id).
    type: str
  vm_id:
    description: ID of the VM to deprovision. Mutually exclusive with I(vm_name).
    type: int
requirements:
  - "python >= 3.13"
  - "arcane-mage >= 2.0.0"
author:
  - RunOnFlux
"""

EXAMPLES = r"""
- name: Remove a FluxNode VM by name
  runonflux.arcane_mage.deprovision:
    api_url: "https://65.109.86.37:8006"
    api_token: "{{ proxmox_token }}"
    node: tsa
    vm_name: tsa-vs1
  delegate_to: localhost

- name: Remove a FluxNode VM by ID
  runonflux.arcane_mage.deprovision:
    api_url: "https://65.109.86.37:8006"
    api_token: "{{ proxmox_token }}"
    node: tsa
    vm_id: 200
  delegate_to: localhost
"""

RETURN = r"""
deprovision_log:
  description: List of deprovision step messages.
  type: list
  elements: str
  returned: always
"""

import asyncio

from ansible.module_utils.basic import AnsibleModule


async def run_deprovision(params):
    """Execute the deprovisioning workflow."""
    from arcane_mage import Provisioner, ProxmoxApi

    token = ProxmoxApi.parse_token(params["api_token"])
    if not token:
        return False, ["Invalid API token format. Expected: user@realm!tokenname=tokenvalue"]

    api = ProxmoxApi.from_token(params["api_url"], token, verify_ssl=params["verify_ssl"])

    log_messages = []

    def callback(ok, msg):
        log_messages.append(f"{'OK' if ok else 'FAIL'}: {msg}")

    async with api:
        provisioner = Provisioner(api)

        # Check if VM exists first
        vms_res = await api.get_vms(params["node"])
        if not vms_res:
            return False, ["Unable to list VMs on node"]

        vm_name = params.get("vm_name")
        vm_id = params.get("vm_id")

        if vm_name:
            existing = next((v for v in vms_res.payload if v.get("name") == vm_name), None)
        else:
            existing = next((v for v in vms_res.payload if v.get("vmid") == vm_id), None)

        if not existing:
            identifier = vm_name or str(vm_id)
            return True, [f"VM '{identifier}' does not exist — nothing to do"]

        success = await provisioner.deprovision_vm(
            params["node"],
            callback=callback,
            vm_name=vm_name,
            vm_id=vm_id,
        )

    return success, log_messages


def main():
    module_args = dict(
        api_url=dict(type="str", required=True),
        api_token=dict(type="str", required=True, no_log=True),
        verify_ssl=dict(type="bool", default=False),
        node=dict(type="str", required=True),
        vm_name=dict(type="str"),
        vm_id=dict(type="int"),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        mutually_exclusive=[("vm_name", "vm_id")],
        required_one_of=[("vm_name", "vm_id")],
        supports_check_mode=True,
    )

    if module.check_mode:
        module.exit_json(changed=False, deprovision_log=["Check mode — no changes made"])

    try:
        success, log_messages = asyncio.run(run_deprovision(module.params))
    except ImportError:
        module.fail_json(msg="arcane-mage library is not installed. Install with: pip install arcane-mage")
    except Exception as e:
        module.fail_json(msg=f"Deprovision error: {e}")

    if not success:
        module.fail_json(
            msg="Failed to deprovision VM",
            deprovision_log=log_messages,
        )

    already_absent = any("does not exist" in m for m in log_messages)

    module.exit_json(
        changed=not already_absent,
        deprovision_log=log_messages,
    )


if __name__ == "__main__":
    main()
