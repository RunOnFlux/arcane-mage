#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2025, RunOnFlux
# License: MIT

DOCUMENTATION = r"""
---
module: iso_info
short_description: Get the latest ArcaneOS ISO version
version_added: "1.0.0"
description:
  - Queries the Flux release API for the latest ArcaneOS ISO filename.
  - Optionally downloads the ISO to a Proxmox node via the Proxmox API.
  - Useful for ensuring all hosts have the latest ISO before provisioning.
options:
  api_url:
    description: >
      Proxmox API URL. Required only when I(download=true).
    type: str
  api_token:
    description: >
      Proxmox API token. Required only when I(download=true).
    type: str
    no_log: true
  verify_ssl:
    description: Whether to verify SSL certificates.
    type: bool
    default: false
  node:
    description: >
      Proxmox cluster node name. Required when I(download=true).
    type: str
  storage_iso:
    description: Storage backend for ISO files.
    type: str
    default: local
  download:
    description: >
      Whether to download the latest ISO to the Proxmox node.
      Uses the Proxmox API download-url endpoint.
    type: bool
    default: false
requirements:
  - "python >= 3.13"
  - "arcane-mage >= 2.0.0"
author:
  - RunOnFlux
"""

EXAMPLES = r"""
- name: Get latest ISO version
  runonflux.arcane_mage.iso_info:
  delegate_to: localhost
  register: iso_result

- name: Download latest ISO to Proxmox node
  runonflux.arcane_mage.iso_info:
    api_url: "https://65.109.86.37:8006"
    api_token: "{{ proxmox_token }}"
    node: tsa
    download: true
  delegate_to: localhost

- name: Use ISO name in provisioning
  runonflux.arcane_mage.provision:
    iso_name: "{{ iso_result.iso_name }}"
    # ... other params
  delegate_to: localhost
"""

RETURN = r"""
iso_name:
  description: The latest ISO filename (e.g., FluxLive-1775071308.iso).
  type: str
  returned: success
downloaded:
  description: Whether the ISO was downloaded to the node.
  type: bool
  returned: always
"""

import asyncio

from ansible.module_utils.basic import AnsibleModule


async def run_iso_info(params):
    """Fetch latest ISO info and optionally download."""
    from arcane_mage import ProxmoxApi, get_latest_iso_version

    iso_name = await get_latest_iso_version()

    if not iso_name:
        return False, None, False, "Unable to fetch latest ISO version from release API"

    if not params.get("download"):
        return True, iso_name, False, ""

    # Download to Proxmox node
    if not params.get("api_url") or not params.get("api_token") or not params.get("node"):
        return False, iso_name, False, "api_url, api_token, and node are required for download"

    token = ProxmoxApi.parse_token(params["api_token"])
    if not token:
        return False, iso_name, False, "Invalid API token format"

    api = ProxmoxApi.from_token(params["api_url"], token, verify_ssl=params["verify_ssl"])

    async with api:
        # Check if ISO already exists
        content_res = await api.get_storage_content(params["node"], params["storage_iso"])
        if content_res:
            existing = next(
                (c for c in content_res.payload if c.get("volid", "").endswith(iso_name)),
                None,
            )
            if existing:
                return True, iso_name, False, ""

        # Download via Proxmox API (direct POST without verify-certificates param)
        # URL format: https://images.runonflux.io/arcane/releases/<build>/FluxLive-<build>.iso
        build_id = iso_name.replace("FluxLive-", "").replace(".iso", "")
        download_url = f"https://images.runonflux.io/arcane/releases/{build_id}/{iso_name}"

        endpoint = f"nodes/{params['node']}/storage/{params['storage_iso']}/download-url"
        data = {"content": "iso", "filename": iso_name, "url": download_url}

        res = await api._do_post(endpoint, data=data)

        if not res:
            error_detail = f"status={res.status} error={res.error}" if res else "no response"
            return False, iso_name, False, f"Failed to initiate ISO download: {error_detail}"

        # Wait for download task
        ok = await api.wait_for_task(res.payload, params["node"], max_wait_s=600)
        if not ok:
            return False, iso_name, False, "ISO download task did not complete in time"

    return True, iso_name, True, ""


def main():
    module_args = dict(
        api_url=dict(type="str"),
        api_token=dict(type="str", no_log=True),
        verify_ssl=dict(type="bool", default=False),
        node=dict(type="str"),
        storage_iso=dict(type="str", default="local"),
        download=dict(type="bool", default=False),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        required_if=[
            ("download", True, ["api_url", "api_token", "node"]),
        ],
        supports_check_mode=True,
    )

    if module.check_mode:
        module.exit_json(changed=False, iso_name="", downloaded=False)

    try:
        success, iso_name, downloaded, error = asyncio.run(run_iso_info(module.params))
    except ImportError:
        module.fail_json(msg="arcane-mage library is not installed. Install with: pip install arcane-mage")
    except Exception as e:
        module.fail_json(msg=f"ISO info error: {e}")

    if not success:
        module.fail_json(msg=error, iso_name=iso_name or "")

    module.exit_json(changed=downloaded, iso_name=iso_name, downloaded=downloaded)


if __name__ == "__main__":
    main()
