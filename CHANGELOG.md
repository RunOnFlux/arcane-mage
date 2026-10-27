# Changelog

## Unreleased

### Added

- **Cluster pre-flight**: on a Proxmox cluster, `provision_node` checks that the cluster has quorum, the target node is online, the VM name is unused across the cluster, and the import storage is shared when the target is not the node the connection reaches. Each check reads the cluster's current state, and a read that fails fails the check. `validate` (CLI and Ansible) reports it as a `cluster` check, and the TUI shows the cluster's state. `force_standalone` on a stored hypervisor, or on the Ansible modules, skips it. See `docs/proxmox-setup.md`.
- **VM tags, notes and memory**: the hypervisor config takes `tags` and `description`, set on the VM when it is created, and `memory_mb`, which overrides the tier's memory. The Ansible `provision` module takes all three.
- **`refresh-iso`**: stages the latest ArcaneOS ISO on a node unless it is there already (`Provisioner.refresh_iso`). The node downloads it and checks it against the release's sha256 sums. `ProxmoxApi.download_iso` takes `sha256`.
- **`provision --json` gives the VM id**: each node's result carries `vm_id`, and a single node's is also at the top level. `Provisioner.provision_node` writes an assigned id back to `fluxnode.hypervisor.vm_id`.

### Changed

- **Task outcomes**: `ProxmoxApi.wait_for_task` returns a `TaskOutcome`, truthy when the task ended OK, with Proxmox's exit status in `error` when it did not. `Provisioner.create_vm`, `start_vm`, `stop_vm`, `delete_vm`, `upload_arcane_config` and `upload_arcane_efi` return it too. `ApiFailure.reason` gives a failure in one line.
- **Per-VM EFI images**: each VM uploads, imports and deletes its own `<vmid>_arcane_efi.raw`. `Provisioner.provision_node` no longer takes `delete_efi`, and `upload_arcane_efi` takes the VM id.
- **Graceful teardown**: deprovisioning a running VM asks it to shut down (ACPI, `ProxmoxApi.shutdown_vm`, 120 s) so ArcaneOS stops its services, and hard-stops it only if it does not go down. The hard stop passes `overrule-shutdown`, so a hung shutdown task cannot block it.
- A failed provisioning or teardown step carries the reason: "Unable to create VM on hypervisor: unable to create VM 264 - cannot import from …".

### Fixed

- **TLS to public endpoints** (the release feed, the ISO checksums): verified against certifi's CA bundle, so it no longer depends on the Python install's certificate store. `certifi` is a new dependency.
- `refresh-iso` says when it cannot reach the release feed.
- **Task polling on a cluster**: `get_task` polls the node named in the UPID, which for an upload is the node that accepted the connection, not the target node.
- **Slow creates**: the create task is waited on for up to 600 s and uploads for up to 120 s. A create that outlasted the old 10 s wait had its install images deleted while it was still running.
- `wait_for_task` returns as soon as a task stops, so a failed task is reported at once.
- A failed config or EFI upload deletes the files it uploaded.
- Two provisioning runs sharing an import storage no longer delete each other's EFI image mid-create, and the TUI no longer leaves the shared EFI image behind on every hypervisor but the last node's.

## 3.2.1

### Fixed

- `validate` now accepts SDN VNets applied on the target node, via `/nodes/{node}/network?type=any_bridge`. The same call also rejects interfaces a VM cannot attach to (`eth0`, `bond0`, `vmbr0.50`).

### Changed

- `ProxmoxApi.get_networks` returns the bridges on the node a VM can attach to (Linux and OVS bridges, and SDN VNets applied there), not every interface.

## 3.2.0

### Added

- **SSH keys by tier**: `system.ssh_keys` takes `operate` keys (log in as `fluxtui` and `operator`) and `status` keys (log in as `fluxstatus`, read only), beside `ssh_pubkey`, which is one more operate key and the only key ISOs before tiered keys read. Documented in `docs/configuration.md` and `examples/all_settings.yaml`.
- **Ansible collection 1.1.0**: the `provision` module takes `ssh_keys` (`operate`, `status`) and passes it to `system.ssh_keys`.

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
