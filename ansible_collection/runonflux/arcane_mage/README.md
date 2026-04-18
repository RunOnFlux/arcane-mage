# Ansible Collection: runonflux.arcane_mage

Ansible collection for provisioning ArcaneOS FluxNodes on Proxmox hypervisors using the [arcane-mage](https://github.com/RunOnFlux/arcane-mage) library.

## Requirements

- Python >= 3.13 on the Ansible controller
- `arcane-mage >= 2.0.0` (`pip install arcane-mage`)
- Proxmox VE >= 8.4.1
- Proxmox API token with appropriate permissions

## Installation

### From source (development)

```bash
cd arcane-mage/ansible_collection/runonflux/arcane_mage
ansible-galaxy collection build
ansible-galaxy collection install runonflux-arcane_mage-*.tar.gz
```

### Using collections path

Add the path to your `ansible.cfg`:

```ini
[defaults]
collections_path = /path/to/arcane-mage/ansible_collection
```

## Modules

| Module | Description |
|--------|-------------|
| `runonflux.arcane_mage.provision` | Provision an ArcaneOS FluxNode VM (UEFI, TPM 2.0, config disk) |
| `runonflux.arcane_mage.deprovision` | Stop and delete a FluxNode VM |
| `runonflux.arcane_mage.status` | Check VM status on a Proxmox node |
| `runonflux.arcane_mage.validate` | Pre-flight validation (API, storage, ISO, network) |
| `runonflux.arcane_mage.iso_info` | Get latest ArcaneOS ISO version, optionally download it |

All modules use `delegate_to: localhost` since they communicate with Proxmox via its REST API.

## Quick Start

```yaml
- name: Provision FluxNodes
  hosts: proxmox_hosts
  gather_facts: no
  tasks:
    - name: Get latest ISO
      runonflux.arcane_mage.iso_info:
      delegate_to: localhost
      register: iso
      run_once: true

    - name: Download ISO to host
      runonflux.arcane_mage.iso_info:
        api_url: "https://{{ ansible_host }}:8006"
        api_token: "{{ proxmox_token }}"
        node: "{{ inventory_hostname | lower }}"
        download: true
      delegate_to: localhost

    - name: Validate host is ready
      runonflux.arcane_mage.validate:
        api_url: "https://{{ ansible_host }}:8006"
        api_token: "{{ proxmox_token }}"
        node: "{{ inventory_hostname | lower }}"
        network: vmbr4000
        iso_name: "{{ iso.iso_name }}"
      delegate_to: localhost

    - name: Provision FluxNode
      runonflux.arcane_mage.provision:
        api_url: "https://{{ ansible_host }}:8006"
        api_token: "{{ proxmox_token }}"
        node: "{{ inventory_hostname | lower }}"
        vm_name: "{{ inventory_hostname | lower }}-vs{{ idx + 1 }}"
        node_tier: stratus
        network: vmbr4000
        iso_name: "{{ iso.iso_name }}"
        hostname: "{{ inventory_hostname | lower }}-vs{{ idx + 1 }}"
        console_password: "{{ console_password }}"
        flux_id: "{{ flux_id }}"
        identity_key: "{{ identity_key }}"
        tx_id: "{{ tx_id }}"
        output_id: "{{ output_id }}"
        ip_allocation: static
        ip_address: "{{ item }}/27"
        gateway: "49.12.156.225"
        start: true
      delegate_to: localhost
      loop: "{{ vswitch_vm_ips }}"
      loop_control:
        index_var: idx
```

## Node Tiers

| Tier | Cores | RAM | Disk |
|------|-------|-----|------|
| cumulus | 4 | 8 GB | 220 GB |
| nimbus | 8 | 32 GB | 440 GB |
| stratus | 16 | 64 GB | 880 GB |

## License

MIT
