## Arcane Mage - Fluxnode Provisioning Library and Tools

### Introduction

This is experimental software. There may be rough edges. If you spot something that is not quite right - please open an issue.

Arcane Mage is a Python library and suite of tools for automating Fluxnode installation and configuration. It can be used as a standalone CLI/TUI application or imported as a library for custom automation workflows.

**Features:**

* Fully automated A to Z Proxmox installs, including Secure Boot key enrollment
* Async Proxmox VE API client with token and password authentication
* Pydantic-validated configuration models loadable from YAML
* Custom async FAT12 writer for config disk image creation
* Yescrypt password hashing for Linux shadow-compatible hashes
* TUI (Textual) and CLI (Typer) interfaces with rich-formatted output
* TPM 2.0 keyring backend for headless Linux credential storage

### Installation

Install `uv` - https://docs.astral.sh/uv/getting-started/installation/

```bash
# Library only (for use as a dependency)
uv add arcane-mage

# With CLI support
uv add "arcane-mage[cli]"

# With TUI support (includes CLI)
uv add "arcane-mage[tui]"

# Install as a standalone tool
uv tool install "arcane-mage[tui]"

# Run once without installing
uvx --with "arcane-mage[tui]" arcane-mage
```

### Library Usage

arcane-mage exposes a public API for programmatic Fluxnode provisioning:

```python
from arcane_mage import (
    ArcaneOsConfigGroup,
    Provisioner,
    ProxmoxApi,
    TIER_CONFIG,
    get_latest_iso_version,
)
```

#### Loading Configuration

Node configurations are defined in YAML and loaded into Pydantic-validated models:

```python
from arcane_mage import ArcaneOsConfigGroup

# Load from the default fluxnodes.yaml
group = ArcaneOsConfigGroup.from_fs()

# Load from a specific file
group = ArcaneOsConfigGroup.from_fs(Path("my_nodes.yaml"))

for node in group:
    print(node.fluxnode.identity.flux_id)
```

See the `examples` directory for sample YAML configurations.

#### Connecting to Proxmox

```python
from arcane_mage import ProxmoxApi

# Parse and connect with a token string
token = ProxmoxApi.parse_token("automation@pam!arcane=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
if token:
    api = ProxmoxApi.from_token("https://pve.local:8006", token)
    print(token.user)      # "automation@pam"
    print(token.username)   # "automation"

# Password authentication
creds = ProxmoxApi.parse_user_pass("automation:secret")
if creds:
    api = await ProxmoxApi.from_user_pass("https://pve.local:8006", creds)

# Use as an async context manager
async with api:
    version = await api.get_api_version("pve1")
    vms = await api.get_vms("pve1")
```

#### Provisioning Nodes

```python
from arcane_mage import ArcaneOsConfigGroup, Provisioner, ProxmoxApi

token = ProxmoxApi.parse_token("automation@pam!arcane=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
api = ProxmoxApi.from_token(url, token)

async with api:
    provisioner = Provisioner(api)

    group = ArcaneOsConfigGroup.from_fs()
    for node in group:
        success = await provisioner.provision_node(
            node,
            callback=lambda ok, msg: print(f"{'OK' if ok else 'FAIL'}: {msg}"),
        )
```

The provisioner handles the full workflow: API validation, storage checks, VM config
generation, FAT12 config disk creation, EFI upload, VM creation, and cleanup.

#### Discovery

Match existing VMs on a hypervisor against your configuration:

```python
discovery = await provisioner.discover_nodes(group)
if discovery:
    for node in discovery.nodes:
        print(node.fluxnode.identity.flux_id)
```

#### Other Utilities

```python
from arcane_mage import HashedPassword, TIER_CONFIG, get_latest_iso_version

# Yescrypt password hashing (Linux shadow-compatible)
hashed = HashedPassword("my_password").hash()

# Hardware tier specs (cumulus, nimbus, stratus)
print(TIER_CONFIG["cumulus"])  # {'memory_mb': 8192, 'scsi_gb': 220, 'cpu_cores': 4}

# Fetch latest FluxOS ISO version
version = await get_latest_iso_version()
```

### CLI Usage

Launch the interactive TUI:

```bash
arcane-mage tui
arcane-mage tui -c my_nodes.yaml
```

#### Stored Hypervisors

Hypervisors added via the TUI are stored in `~/.fluxnode_creator.yaml` with credentials in your system keyring. Use `--hypervisor` / `-H` to reference them by name instead of passing `--url` and `--token`:

```bash
arcane-mage ping -H pve1
arcane-mage validate -H pve1 -c my_nodes.yaml
arcane-mage provision -H pve1 -c my_nodes.yaml --start
```

#### Commands

Test connectivity and authentication:

```bash
arcane-mage ping -H pve1
```

Provision nodes:

```bash
arcane-mage provision -H pve1 -c my_nodes.yaml --start
```

Pre-flight validation (checks API, storage, ISO, network without provisioning):

```bash
arcane-mage validate -H pve1 -c my_nodes.yaml
```

Check which configured nodes are already provisioned:

```bash
arcane-mage status -H pve1 -c my_nodes.yaml
```

Deprovision (stop and delete) VMs:

```bash
# From config file
arcane-mage deprovision -H pve1 -c my_nodes.yaml

# Target a specific VM by name or ID
arcane-mage deprovision -H pve1 --vm-name mynode
arcane-mage deprovision -H pve1 --vm-id 105

# Skip confirmation
arcane-mage deprovision -H pve1 --vm-name mynode --force
```

#### Authentication

All commands that connect to Proxmox also accept explicit credentials:

```bash
arcane-mage ping \
    --url https://pve.local:8006 \
    --token 'automation@pam!arcane=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
```

Or via environment variables:

```bash
export ARCANE_MAGE_URL=https://pve.local:8006
export ARCANE_MAGE_TOKEN='automation@pam!arcane=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
arcane-mage validate -c my_nodes.yaml
```

Or pipe the token via stdin:

```bash
cat /path/to/token | arcane-mage provision -c my_nodes.yaml
```

Credential resolution order: `--token` > stdin > `ARCANE_MAGE_TOKEN` env var > `--hypervisor` stored credential.

#### JSON Output

All Proxmox commands support `--json` for machine-readable output (useful for Ansible):

```bash
arcane-mage validate -H pve1 -c my_nodes.yaml --json
arcane-mage status -H pve1 -c my_nodes.yaml --json
```

Run the following for help:

```bash
arcane-mage --help
arcane-mage provision --help
```

### Configuration

Node configurations are defined in YAML. See [docs/configuration.md](docs/configuration.md) for the full schema reference and [examples/](examples/) for ready-to-use templates.

### Proxmox Setup

See [docs/proxmox-setup.md](docs/proxmox-setup.md) for hypervisor setup instructions (user, API token, storage, permissions).

### Credential Storage

Arcane Mage uses your system's secure keyring to store API credentials, with a built-in TPM 2.0 backend for headless Linux servers. See [docs/keyring.md](docs/keyring.md) for details and troubleshooting.
