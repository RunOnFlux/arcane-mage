# Changelog

## 3.1.0

### Added

- **Console copy devices**: every VM is created with `vga: std,clipboard=vnc` and `serial0: socket`. The node's console offers its configure link to the viewer's clipboard (the noVNC clipboard panel) and writes it to the serial port (the xterm.js console). VNC clipboard needs PVE 8.2, below the 8.4.1 minimum.

## 3.0.0

### Breaking Changes

- **`ApiResponse`** is the union `ApiSuccess[T] | ApiFailure`. `ApiSuccess` always carries its `payload`, typed per endpoint (for example `ApiResponse[list[VmSummary]]` from `get_vms`); `ApiFailure` carries `status`, `error` and `timed_out`. `if not res:` narrows to `ApiFailure`. Code that constructs `ApiResponse(...)` constructs `ApiSuccess(payload=...)` or `ApiFailure(...)` instead.
- **Response validation**: each endpoint's answer is validated against its shape (`VmSummary`, `NetworkIface`, `NodeStorage`, `StorageVolume`, `NodeVersion`, `TaskStatus`, `HypervisorNode`, `StorageConfig`, all exported). An answer missing a required field, or with `data: null` where a value is due, is an `ApiFailure` naming the field. Fields the shapes do not name are kept.

### Changed

- **Build backend**: the package builds with `uv_build`. The wheel's contents are unchanged.
- **Discovery**: a hypervisor node whose VMs cannot be listed is left out of `discover_nodes`, rather than treated as running none.
- **`download_iso`** sends `verify-certificates` as `0`/`1`. The Ansible `iso_info` module downloads through it.

### Fixed

- **TUI**: adding a hypervisor whose connection times out reports the timeout instead of validating it.
- **CLI**: `arcane-mage ping` reports "Authentication failed" for a rejected token.

## 2.3.0

### Added

- **`fluxnode.network.params_source`**: an `ip:port` the node fetches fluxd's zk params from. Kept under the same address rule as chain sources; unset, the node downloads them from Flux.

## 2.2.1

### Fixed

- **Chain sources**: a source is kept only at a unicast address a node can stream from: a LAN address or, for a labnet install, a global address on labnet's internet. Loopback, link-local, multicast, unspecified, reserved and broadcast addresses are dropped.

## 2.2.0

### Added

- **`fluxnode.chain`**: the network a node installs onto. Absent, mainnet; for a lab, `network: labnet` with the lab's compressed public key as `key`.

### Changed

- **UPnP**: the installer config carries `upnp_port` alone. Setting the port enables UPnP.

### Removed

- **`fluxnode.gravity.testnet`**: a config that still sets it loads, and the key is ignored.

## 2.1.0

### Added

- **`FluxnodeConfig.dev_token`**: optional dev-channel enrolment token, written to the autoinstall config next to the identity it is bound to; the installer verifies it

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
