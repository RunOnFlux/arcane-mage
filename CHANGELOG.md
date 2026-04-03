# Changelog

## 2.0.0

### Breaking Changes

- **Library refactor**: arcane-mage is now a full library with a public API via `__init__.py` (`__all__` with 22 exports)
- **Optional extras**: TUI requires `arcane-mage[tui]`, CLI requires `arcane-mage[cli]`. The base package has no UI dependencies.
- **CLI**: Replaced Click with Typer. `arcane-mage` no longer launches the TUI by default — use `arcane-mage tui`. `provision-proxmox` renamed to `provision`.
- **ProxmoxApi**: HTTP methods (`do_get`, `do_post`, etc.) are now private (`_do_get`, `_do_post`)
- **ProxmoxApi**: `.type` property renamed to `.auth_type`
- **ProxmoxApi.parse_token()**: returns `ParsedToken` dataclass instead of `tuple[str, str, str]`
- **ProxmoxApi.parse_user_pass()**: returns `ParsedUserPass` dataclass instead of `tuple[str, str]`
- **ProxmoxApi.from_token()**: takes `(url, ParsedToken)` instead of `(url, user, token_name, token_value)`
- **ProxmoxApi.from_user_pass()**: takes `(url, ParsedUserPass)` instead of `(url, user, password)`
- **ProxmoxApi.download_iso()**: now requires a `node` parameter
- **TIER_CONFIG**: values are now `int` instead of `str`
- **ArcaneOsConfig.to_dict()**: uses Pydantic TypeAdapter with `mode='json'`
- **Models split**: `models.py` split into 9 modules under `models/` (imports via `arcane_mage.models` still work)

### Added

- **Provisioner class**: standalone orchestrator extracted from TUI screens, usable programmatically
- **Provisioner.from_hypervisor_config()**: factory method with credential resolution
- **Provisioner.discover_nodes()**: match configurations against hypervisor VMs
- **VmConfig dataclass**: typed replacement for untyped VM config dicts
- **HypervisorDiscovery dataclass**: typed node discovery results
- **get_latest_iso_version()**: standalone async function to fetch latest FluxOS ISO version
- **CLI rebuilt with Typer**: rich-formatted help output, explicit `tui`, `provision`, `validate`, `status`, `ping`, and `deprovision` commands
- **`validate` command**: pre-flight check of API version, storage, ISO, and network without provisioning
- **`status` command**: show which configured nodes are already provisioned on the hypervisor
- **`ping` command**: test connectivity and authentication to a Proxmox hypervisor without a config file
- **`deprovision` command**: stop and delete VMs by config file, VM name (`--vm-name`), or VM ID (`--vm-id`)
- **`--hypervisor` / `-H` flag**: resolve URL and credentials from stored hypervisors in `~/.fluxnode_creator.yaml` + keyring, with auto-backfill of hypervisor names from the API
- **`--json` flag**: machine-readable JSON output on all Proxmox commands for Ansible/automation
- **Environment variables**: `ARCANE_MAGE_URL` and `ARCANE_MAGE_TOKEN` for credential-free CLI usage; token also accepted via stdin
- **`ParsedToken` and `ParsedUserPass` dataclasses**: typed replacements for raw tuples from `parse_token()` / `parse_user_pass()`, with `.username` property
- **`ResolvedConnection` dataclass**: typed container for resolved URL + token
- **TPM 2.0 keyring backend**: stores credentials in TPM NV indices on headless Linux systems via `libtss2-esys`; auto-detected as a `keyring.backends` entry point
- **TUI**: edit hypervisor support, dropdown shows `name (user)` instead of raw URL
- **172 unit tests** across 14 test modules covering all core library code
- **Zero `Any` usage** across the entire codebase - all types are concrete

### Changed

- Replaced Click with Typer for CLI
- Replaced `pyfatfs` with custom async FAT12 writer (`fat_writer.py`)
- Replaced `sshpubkeys` with `cryptography` for SSH key validation
- Bumped `keyring` to `>=25.7.0`
- Credential fields masked in `repr` with `Field(repr=False)`
- Config files written with `0o600` permissions
- `BasicAuth` password redacted in repr

### Fixed

- VFAT LFN padding now correctly uses `0xFFFF`

### Security

- Bumped `aiohttp` to `>=3.13.5` (10 advisories: header injection, SSRF, DoS)
- Bumped `cryptography` to `>=46.0.6` (DNS name constraint enforcement)
- Replaced `ecdsa` (Minerva timing attack, no patch) with `cryptography`
- Bumped dependencies to fix high severity vulnerabilities

## 1.x

Initial releases. TUI-only application for Proxmox Fluxnode provisioning.
