# Cluster Support — Test Plan

Pre-PR verification checklist for the cluster-architecture branch.

## Running the TUI

The TUI is an HTTP client — it connects to Proxmox via API and does not need to run on a Proxmox node. Run from any machine with network access to the Proxmox API:

```
cd ~/Git/arcane-mage_flux
.venv/bin/arcane-mage
```

Requires Python 3.13+ and `pip install -e ".[tui]"` in the venv. A `fluxnodes.yaml` with hypervisor config is needed.

**Read-only tests (no VMs needed):** 2, 5, 8, 14, 15, 16 — just connect and view the UI.
**Provisioning tests (create/delete VMs):** 1, 3, 6, 7, 9, 10, 11, 12, 13, 17.

## Unit Tests (automated)

- [x] `pytest tests/` — 211 passed

## Integration Tests (manual)

### 1. Standalone Server (pve20 or pve40)
Connect to an R820 (not in the cluster). Verify `cluster` is `None`, all provisioning behavior identical to before.

### 2. Cluster Detection (moltentech)
Connect to any node in the `moltentech` cluster. Verify the TUI shows cluster name, node count (6), and quorum: ok.

### 3. Node Offline Check
Shut down or fence a non-critical cluster node (e.g. pve65). Provision a VM targeting that node. Should block with `"Node 'pve65' is offline in cluster"`.

### 4. Quorum Loss
Hard to test safely with a 6-node cluster (need 4 nodes down). Could simulate by adding `force_standalone` to skip this in production and unit-test coverage handles the logic. Or test in a throwaway 3-node cluster where killing 2 breaks quorum.

### 5. VM Name Uniqueness
Create a VM on one cluster node, then try to provision a second VM with the same name on a different node. Should block with `"VM name '...' already exists on node '...'"`.

### 6. Shared Storage EFI Dedup
Requires shared storage (NFS/Ceph) on the cluster. Provision 2+ VMs on different nodes using the same shared storage. Watch the callbacks — first should say "EFI image uploaded", subsequent should say "EFI image upload skipped (shared storage)".

### 7. Local Storage (unchanged behavior)
Provision 2+ VMs on local storage. Each should upload EFI independently, only the last should delete it.

### 8. `force_standalone: true`
Add `force_standalone: true` to a hypervisor config YAML entry pointing at a cluster node. Verify no cluster info is shown, no pre-flight cluster checks run.

### 9. CLI Provision
Run `arcane-mage provision` against the cluster. Verify output shows cluster pre-flight checks and correct EFI skip/delete behavior.

### 9a. CLI Provision JSON Output (Moltentech provisioner compat)
Run `arcane-mage provision --json -c <yaml> --url <url> --token <token>` (the exact invocation the Moltentech provisioner container uses). Verify:
- JSON output is `{"ok": true/false, "nodes": [{"hostname": "...", "ok": true/false, "steps": [...]}]}`
- The extra "Cluster pre-flight checks passed" step message doesn't break anything
- Exit code 0 on success, 1 on failure (unchanged)

### 10. Single-Node TUI Provision (regression)
Click a single row in the TUI DataTable and provision one VM. Verify it still works identically — no cluster-related regressions in the single-node path.

### 11. TUI Deprovision (regression)
Deprovision a VM through the TUI. Verify deprovisioning is unaffected (it should be — no cluster changes touch deprovision).

### 12. CLI Deprovision (regression)
Run `arcane-mage deprovision` against a cluster node. Verify it works unchanged.

### 13. Ansible Module (regression)
Run the Ansible `provision` module. It creates `Provisioner(api)` without cluster, so it should behave identically to before. Verify no errors.

### 14. DataTable Column Alignment
Open the TUI against both a standalone server and a cluster. Verify the new "Status" column renders correctly — shows "online"/"offline" for cluster, empty string for standalone. Check that no columns are misaligned.

### 15. Cluster Info Label Hide/Show
Switch between hypervisors in the TUI dropdown — from a cluster node to a standalone (or vice versa). Verify the cluster info label appears/disappears correctly and doesn't leave stale text.

### 16. API Auth Failure Graceful Handling
Connect with invalid credentials to a cluster. Verify the error path still works cleanly — `from_hypervisor_config` returns `None`, no crash from cluster detection on a failed connection.

### 17. `total_steps` Count in TUI
When `skip_efi_upload=True`, the provisioning step count shows "EFI image upload skipped" instead of "EFI image uploaded", but it's still counted as a step. Verify the progress indicator (e.g. "Step 7/9") still tracks correctly and doesn't show the wrong total.

### 18. Moltentech Provisioner Container
The provisioner container (`apps/provisioner/`) calls arcane-mage via CLI (`execFile`). It is affected by our changes because:
- `arcane-mage provision` now calls `detect_cluster()` (2 extra API calls per provision)
- Cluster pre-flight checks run if the Proxmox host is in a cluster

**What to verify:**
- Rebuild the provisioner Docker image (it copies `arcane-mage/` into the container)
- Trigger a provision job and verify it succeeds with unchanged JSON output
- Trigger a deprovision job and verify it succeeds (deprovision path is untouched)
- Check watchdog.py still runs cleanly (it only imports `HypervisorConfig`, `ProxmoxApi` — no Provisioner)

**Note:** The provisioner Dockerfile copies `arcane-mage/` from the repo root. If deploying, the updated arcane-mage source must be in `~/Git/moltentech/arcane-mage/` (currently it's a separate copy — check if it's a symlink or needs manual sync).
