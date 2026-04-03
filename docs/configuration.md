# Configuration

Arcane Mage uses YAML configuration files to define Fluxnode deployments. The default file is `fluxnodes.yaml` in the current directory, overridable with `-c`/`--config`.

See the [examples](../examples/) directory for ready-to-use templates.

## Quick Start

A minimal configuration requires only `hypervisor`, `system`, and `fluxnode.identity`:

```yaml
nodes:
  - hypervisor:
      node: pve1
      storage_images: local-lvm
      storage_iso: local
      storage_import: local
      network: vmbr0
      iso_name: FluxLive-1775071308.iso
      vm_name: mynode
      node_tier: cumulus
    system:
      hostname: mynode
    fluxnode:
      identity:
        flux_id: 1ExampleFluxAddressxxxxxxxxxxxxxxxxxx
        identity_key: L1ExampleIdentityKeyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
        output_id: 0
        tx_id: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

## File Structure

The top-level key is `nodes`, a list of node configurations. Each node has these sections:

```yaml
nodes:
  - hypervisor: ...    # Required - Proxmox VM placement
    system: ...        # Required - OS-level settings
    fluxnode: ...      # Required - Fluxnode identity and network
    network: ...       # Optional - IP allocation (default: DHCP)
    installer: ...     # Optional - Post-install behavior
    metrics_app: ...   # Optional - Metrics display settings
    identifier: ...    # Optional - Machine identifier for matching
```

## Sections

### hypervisor (required)

Controls where and how the VM is created on Proxmox.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `node` | string | yes | | Proxmox cluster node name (e.g. `pve1`, `pve2`) |
| `vm_name` | string | yes | | VM name on the hypervisor |
| `node_tier` | string | yes | | Hardware tier: `cumulus`, `nimbus`, or `stratus` |
| `network` | string | yes | | Network bridge (e.g. `vmbr0`) |
| `iso_name` | string | yes | | FluxOS ISO filename. Must match pattern `FluxLive-XXXXXXXXXX.iso` |
| `storage_images` | string | yes | | Storage backend for VM disk images (e.g. `local-lvm`) |
| `storage_iso` | string | yes | | Storage backend for ISO files (e.g. `local`) |
| `storage_import` | string | yes | | Storage backend for import operations (e.g. `local`) |
| `vm_id` | integer | no | next available | Explicit VM ID |
| `start_on_creation` | boolean | no | `false` | Start the VM after creation |
| `startup_config` | string | no | | Proxmox startup order (e.g. `order=4,up=360`). Requires `Sys.Modify` permission |
| `disk_limit` | integer | no | | Disk I/O limit in MB/s |
| `cpu_limit` | float | no | | CPU limit (0.0-1.0) |
| `network_limit` | integer | no | | Network rate limit in MB/s |

Hardware tier specs:

| Tier | Memory | Disk | CPU Cores |
|---|---|---|---|
| cumulus | 8 GB | 220 GB | 4 |
| nimbus | 32 GB | 440 GB | 8 |
| stratus | 64 GB | 880 GB | 16 |

### system (required)

OS-level configuration for the installed system.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `hostname` | string | yes | | System hostname (2-253 characters) |
| `ssh_pubkey` | string | no | | SSH public key in OpenSSH format (e.g. `ssh-ed25519 AAAA...`) |
| `keyboard.layout` | string | no | `us` | Keyboard layout |
| `keyboard.variant` | string | no | `""` | Keyboard variant |

The console password is set interactively during provisioning (via TUI or prompted by the CLI). It is hashed with Yescrypt before being written to the VM.

### fluxnode (required)

Fluxnode identity, network, notifications, and delegate configuration.

#### fluxnode.identity (required)

| Field | Type | Required | Validation | Description |
|---|---|---|---|---|
| `flux_id` | string | yes | 14-72 characters | Flux address |
| `identity_key` | string | yes | 51-52 characters | Fluxnode identity key (WIF) |
| `tx_id` | string | yes | exactly 64 characters | Collateral transaction hash |
| `output_id` | integer | yes | 0-999 | Collateral output index |

#### fluxnode.network (optional)

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `upnp_port` | integer | no | | UPnP port for automatic port forwarding |
| `router_address` | string | no | | Router IP for UPnP |
| `private_chain_sources` | list[string] | no | `[]` | Private IP:port pairs for local chain sync (must be RFC1918 addresses) |

#### fluxnode.notifications (optional)

**Discord:**

| Field | Type | Validation | Description |
|---|---|---|---|
| `discord.webhook_url` | string | Must be HTTPS discord.com/discordapp.com webhook URL | Discord webhook URL |
| `discord.user_id` | string | 17-19 characters | Discord user ID for mentions |

**Telegram:**

| Field | Type | Validation | Description |
|---|---|---|---|
| `telegram.bot_token` | string | Pattern: `XXXXXXXXXX:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX` | Telegram bot token |
| `telegram.chat_id` | string | 6-1000 characters | Telegram chat ID |

**Other:**

| Field | Type | Description |
|---|---|---|
| `email` | string | Email address for notifications |
| `webhook` | string | Custom webhook URL (HTTP or HTTPS) |
| `node_name` | string | Display name in notifications |

#### fluxnode.delegate (optional)

Delegate node starting configuration. Provide either a pre-encrypted key or a raw key with passphrase.

| Field | Type | Description |
|---|---|---|
| `collateral_pubkey` | string | Compressed public key (02/03 prefix + 64 hex chars) |
| `delegate_private_key_encrypted` | string | Pre-encrypted delegate key (base64) |
| `delegate_private_key` | string | Raw delegate private key (WIF format) |
| `delegate_passphrase` | string | Passphrase for encrypting the delegate key |

You must provide either:
- `delegate_private_key_encrypted` (pre-encrypted), OR
- `delegate_private_key` + `delegate_passphrase` (arcane-mage encrypts on output)

Not both.

### network (optional)

VM network configuration. Defaults to DHCP if omitted.

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `ip_allocation` | string | no | `dhcp` | `dhcp` or `static` |
| `address_config` | object | if static | | Static IP configuration (see below) |
| `vlan` | integer | no | | VLAN tag (1-4095) |
| `rate_limit` | integer | no | | Network rate limit. Must be 35, 75, 135, or 250 |

**address_config** (required when `ip_allocation: static`):

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `address` | string | yes | | IPv4 address with prefix (e.g. `192.168.1.10/24`) |
| `gateway` | string | yes | | Default gateway (must be in same subnet as address) |
| `dns` | list[string] | no | `[1.1.1.1, 8.8.8.8]` | DNS servers |

### installer (optional)

Post-installation behavior.

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_reboot` | boolean | `true` | Automatically reboot after installation |
| `reboot_to_firmware` | boolean | `false` | Reboot into UEFI firmware settings |
| `reboot_to_boot_menu` | boolean | `false` | Reboot into systemd-boot menu |

### metrics_app (optional)

On-device metrics display configuration.

| Field | Type | Default | Description |
|---|---|---|---|
| `poweroff_screen` | integer | `0` | Screen off timeout in minutes (0 = never) |
| `theme` | string | `flexoki` | Display theme (e.g. `flexoki`, `gruvbox`) |

### identifier (optional)

Machine identifier for matching physical hardware to configurations (used for USB/multicast provisioning).

| Field | Type | Description |
|---|---|---|
| `type` | string | `system-uuid` or `mac-address` |
| `value` | string | The UUID or MAC address value |

## Multiple Nodes

Define multiple nodes in a single file:

```yaml
nodes:
  - hypervisor:
      node: pve1
      vm_name: node1
      # ...
    system:
      hostname: node1
    fluxnode:
      identity:
        # ...

  - hypervisor:
      node: pve1
      vm_name: node2
      # ...
    system:
      hostname: node2
    fluxnode:
      identity:
        # ...
```

## YAML Merge Anchors

For large deployments with shared settings, use YAML merge anchors to avoid repetition. Define shared values under a `global` key and reference them with `<<: *anchor`:

```yaml
global:
  hypervisor: &hypervisor
    node: pve1
    storage_images: local-lvm
    storage_iso: local
    storage_import: local
    network: vmbr0
    iso_name: FluxLive-1775071308.iso
    start_on_creation: true
  system: &system
    ssh_pubkey: ssh-ed25519 AAAA... operator@fluxnode

nodes:
  - hypervisor:
      <<: *hypervisor
      vm_name: node1
      node_tier: cumulus
    system:
      <<: *system
      hostname: node1
    fluxnode:
      identity:
        # ...

  - hypervisor:
      <<: *hypervisor
      vm_name: node2
      node_tier: nimbus
    system:
      <<: *system
      hostname: node2
    fluxnode:
      identity:
        # ...
```

Anchor values can be overridden per-node. See [examples/merge_anchors.yaml](../examples/merge_anchors.yaml) for a complete example.

## Examples

| File | Description |
|---|---|
| [minimal.yaml](../examples/minimal.yaml) | Bare minimum — DHCP, no autostart, no notifications |
| [typical.yaml](../examples/typical.yaml) | Common setup — static IP, autostart, UPnP, Discord notifications |
| [all_settings.yaml](../examples/all_settings.yaml) | Every available option with inline documentation |
| [merge_anchors.yaml](../examples/merge_anchors.yaml) | Multi-node DRY configuration using YAML anchors |
