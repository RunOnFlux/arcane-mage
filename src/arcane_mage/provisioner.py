import asyncio
import gzip
import hashlib
import importlib.resources as resources
import logging
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Self

import yaml

from .fat_writer import FAT12Writer
from .helpers import do_http, do_http_to_file
from .models import ArcaneOsConfig, ArcaneOsConfigGroup, HypervisorConfig
from .models.cluster import ClusterContext
from .proxmox import ApiResponse, ProxmoxApi

log = logging.getLogger(__name__)

MIN_API_VERSION = "8.4.1"
MIN_IMPORT_STORAGE_BYTES = 10 * 1024 * 1024  # 10 MiB

TIER_CONFIG: dict[str, dict[str, int]] = {
    "cumulus": {"memory_mb": 8192, "scsi_gb": 220, "cpu_cores": 4},
    "nimbus": {"memory_mb": 32768, "scsi_gb": 440, "cpu_cores": 8},
    "stratus": {"memory_mb": 65536, "scsi_gb": 880, "cpu_cores": 16},
}

_images_ref = resources.files("arcane_mage.images")
_efi_gz_resource = _images_ref / "arcane_efi.raw.gz"
_config_gz_resource = _images_ref / "arcane_config.raw.gz"
_config_image_base = "arcane_config"


@dataclass
class VmConfig:
    """Proxmox QEMU VM configuration for node provisioning."""

    efidisk0: str
    cpu: str
    ostype: str
    sockets: int
    vmid: int
    agent: str
    onboot: int
    name: str
    smbios1: str
    boot: str
    numa: int
    memory: int
    tpmstate0: str
    cores: int
    cpulimit: float
    bios: str
    scsi0: str
    scsi1: str
    ide2: str
    net0: str
    scsihw: str
    startup: str | None = None
    tags: str | None = None
    description: str | None = None

    def to_proxmox_dict(self) -> dict:
        """Convert to the dict format Proxmox API expects."""
        result = asdict(self)
        return {k: v for k, v in result.items() if v is not None}


def is_api_min_version(version: str) -> bool:
    """Check if a Proxmox API version meets the minimum requirement (8.4.1)."""
    min_version = [8, 4, 1]

    parts = version.split(".")

    if len(parts) != 3:
        return False

    for actual_str, required in zip(parts, min_version, strict=True):
        try:
            actual = int(actual_str)
        except ValueError:
            return False

        if actual > required:
            return True
        elif actual < required:
            return False

    return True


def _get_vm_config_file_name(vm_id: int) -> str:
    return f"{vm_id}_{_config_image_base}.raw"


async def get_latest_iso_version() -> str | None:
    """Fetch the latest FluxOS ISO version from the release API."""
    res = await do_http("https://images.runonflux.io/api/latest_release", total_timeout=3)

    if not res or not isinstance(res, dict):
        return None

    return res.get("iso")


_ARCANE_RELEASE_API = "https://images.runonflux.io/arcane/api/latest_release"
_ARCANE_RELEASE_BASE = "https://images.runonflux.io/arcane/releases"


async def _sha256_file(path: Path) -> str:
    """Compute the sha256 hex digest of a file without blocking the event loop."""

    def _hash() -> str:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(16 * 1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    return await asyncio.to_thread(_hash)


def _parse_sha256sum_line(sums_text: str, file_name: str) -> str | None:
    """Find `file_name`'s expected hash in a `sha256sum`-format checksum file."""
    for line in sums_text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip().lstrip("*") == file_name:
            return parts[0]
    return None


@dataclass
class IsoRefreshResult:
    """Result of checking for (and possibly staging) a newer ArcaneOS/FluxLive ISO."""

    ok: bool
    changed: bool = False
    iso: str | None = None
    previous: str | None = None
    build: str | None = None
    severity: str | None = None
    release: str | None = None
    error: str | None = None


@dataclass
class HypervisorDiscovery:
    """Result of discovering nodes and their provisioned VMs on a hypervisor."""

    nodes: ArcaneOsConfigGroup
    provisioned_vms: dict[str, list[dict]]


class Provisioner:
    """Orchestrates Proxmox VM provisioning for Fluxnodes."""

    def __init__(self, api: ProxmoxApi, cluster: ClusterContext | None = None) -> None:
        self.api = api
        self.cluster = cluster
        # Set when cluster detection could not complete. A standalone host and a
        # cluster we failed to read both leave ``cluster`` as None, and they must
        # not be treated the same way — see ``detect_cluster``.
        self.cluster_detection_error: str | None = None

    @classmethod
    async def from_hypervisor_config(cls, config: HypervisorConfig) -> Self | None:
        """Create a Provisioner from a HypervisorConfig, resolving credentials.

        Returns None if credentials are invalid or the API is unreachable.
        """
        credential = config.real_credential()
        if not credential:
            return None

        api: ProxmoxApi | None = None

        if config.auth_type == "token" and (token := ProxmoxApi.parse_token(credential)):
            api = ProxmoxApi.from_token(config.url, token)
        elif config.auth_type == "userpass" and (user_pass := ProxmoxApi.parse_user_pass(credential)):
            api = await ProxmoxApi.from_user_pass(config.url, user_pass)

        if not api:
            return None

        provisioner = cls(api)

        if not config.force_standalone:
            await provisioner.detect_cluster()

        return provisioner

    @staticmethod
    def _detection_failure(endpoint: str, res: ApiResponse) -> str:
        """Describe a cluster-detection read that did not come back usable."""
        cause = res.error or (f"HTTP {res.status}" if res.status else "no response")

        return (
            f"Unable to read {endpoint} ({cause}), so cluster membership is unknown. "
            "A standalone host and a cluster this token cannot read look identical "
            "from here, and provisioning without the cluster pre-flight checks is "
            "not safe. Grant the token read access, or set force_standalone on this "
            "hypervisor if it really is standalone."
        )

    async def detect_cluster(self) -> None:
        """Detect cluster topology and set ``self.cluster``.

        Called automatically by ``from_hypervisor_config()``. Call this
        explicitly when constructing a ``Provisioner`` directly via
        ``Provisioner(api)``.

        A standalone host leaves ``self.cluster`` as ``None`` and clears
        ``self.cluster_detection_error``. A read that fails — a 403 on a
        least-privilege token, a timeout — leaves the error set instead, because
        the two are indistinguishable by their result and only one of them is
        safe to provision on. ``provision_node`` refuses when it is set.

        ``/storage`` is only consulted once the host is known to be a cluster:
        it exists to classify shared-vs-local storage for EFI dedup, and a
        standalone host has no use for the answer.
        """
        self.cluster_detection_error = None

        status_res = await self.api.get_cluster_status()

        if not status_res:
            self.cluster_detection_error = self._detection_failure(
                "/cluster/status", status_res
            )
            return

        status_payload = status_res.payload if isinstance(status_res.payload, list) else []

        if not any(item.get("type") == "cluster" for item in status_payload):
            self.cluster = None
            return

        storage_res = await self.api.get_storage_config()

        if not storage_res:
            self.cluster_detection_error = self._detection_failure("/storage", storage_res)
            return

        storage_payload = storage_res.payload if isinstance(storage_res.payload, list) else []

        ctx = ClusterContext.from_api_responses(status_payload, storage_payload)

        if ctx.is_cluster:
            self.cluster = ctx

    async def discover_nodes(
        self, all_configs: ArcaneOsConfigGroup
    ) -> HypervisorDiscovery | None:
        """Discover hypervisor nodes and match against known configurations.

        Returns the usable nodes and their provisioned VMs, or None on failure.
        """
        hyper_nodes = await self.api.get_hypervisor_nodes()

        if not hyper_nodes:
            return None

        useable_nodes = ArcaneOsConfigGroup()
        provisioned: dict[str, list[dict]] = {}

        async def handle_node(node: dict) -> None:
            if name := node.get("node"):
                vm_res = await self.api.get_vms(name)
                provisioned[name] = vm_res.payload or []
                useable_nodes.add_nodes(all_configs.get_nodes_by_hypervisor_name(name))

        await asyncio.gather(*(handle_node(n) for n in hyper_nodes.payload))

        return HypervisorDiscovery(nodes=useable_nodes, provisioned_vms=provisioned)

    async def validate_api_version(self, node: str) -> tuple[bool, str]:
        """Validate that the Proxmox API version meets minimum requirements."""
        res = await self.api.get_api_version(node)

        if not res:
            return False, "Unable to get Proxmox api version"

        version = res.payload.get("version")

        if not version:
            return False, "Api payload missing version info"

        if not is_api_min_version(version):
            return False, f"Api version too old. Got: {version}, Want: {MIN_API_VERSION}"

        return True, version

    async def validate_storage(
        self,
        node: str,
        storage_iso: str,
        storage_images: str,
        storage_import: str,
    ) -> tuple[bool, str]:
        """Validate that required storage backends exist and have correct content types."""
        res = await self.api.get_storage_state(node)

        if not res:
            return False, "Unable to get Proxmox storage state"

        if not res.payload:
            return False, "No Storage state available, did you forget API permissions?"

        node_storage_iso = next(filter(lambda x: x.get("storage") == storage_iso, res.payload), None)
        node_storage_images = next(filter(lambda x: x.get("storage") == storage_images, res.payload), None)
        node_storage_import = next(filter(lambda x: x.get("storage") == storage_import, res.payload), None)

        if not all([node_storage_iso, node_storage_images, node_storage_import]):
            return False, "Missing storage config item"

        iso_content = node_storage_iso.get("content")
        images_content = node_storage_images.get("content")
        import_content = node_storage_import.get("content")

        if "iso" not in iso_content or "images" not in images_content or "import" not in import_content:
            return False, "Storage type missing on hypervisor"

        import_available = node_storage_import.get("avail", 0)
        import_total = node_storage_import.get("total", 0)
        import_used = node_storage_import.get("used", 0)
        used_pct = (import_used / import_total * 100) if import_total else 0

        # We need 4MiB + 4MiB for the EFI image and the config image. So we check for 10MiB
        if import_available < MIN_IMPORT_STORAGE_BYTES:
            msg = f"Storage '{storage_import}' has less than 10MiB available ({used_pct:.1f}% used)."
            if used_pct < 100:
                msg += " Free up space, reduce reserved blocks, or use a root@pam API token"
            return False, msg

        return True, ""

    async def validate_iso_version(self, node: str, iso_name: str, storage_iso: str) -> bool:
        """Validate that the specified ISO exists on the hypervisor."""
        res = await self.api.get_storage_content(node, storage_iso)

        if not res:
            return False

        iso_exists = next(
            filter(
                lambda x: x.get("content") == "iso" and x.get("volid", "").endswith(iso_name),
                res.payload,
            ),
            None,
        )

        return bool(iso_exists)

    async def refresh_iso(
        self, node: str, storage_iso: str, current_iso: str | None = None
    ) -> IsoRefreshResult:
        """Check the RunOnFlux release feed for a newer ArcaneOS/FluxLive ISO and,
        if the hypervisor doesn't already have it staged, download + checksum-verify
        + upload it to `storage_iso`. No-ops (changed=False) if already current.
        """
        release = await do_http(_ARCANE_RELEASE_API, total_timeout=10)

        if not release or not isinstance(release, dict):
            return IsoRefreshResult(ok=False, error="Unable to fetch latest release info")

        iso_name = release.get("iso")
        build = release.get("build")
        severity = release.get("severity")
        release_name = release.get("release")
        checksums_name = release.get("checksums")

        if not iso_name or not build or not checksums_name:
            return IsoRefreshResult(ok=False, error=f"Malformed release response: {release}")

        if await self.validate_iso_version(node, iso_name, storage_iso):
            return IsoRefreshResult(
                ok=True, changed=False, iso=iso_name, build=build, severity=severity, release=release_name
            )

        base = f"{_ARCANE_RELEASE_BASE}/{build}"

        with tempfile.TemporaryDirectory(prefix="arcane_mage_iso_") as tmpdir:
            iso_path = Path(tmpdir) / iso_name
            sums_path = Path(tmpdir) / checksums_name

            if not await do_http_to_file(f"{base}/{iso_name}", iso_path, read_timeout=1200):
                return IsoRefreshResult(ok=False, error=f"Failed to download {iso_name}", build=build)

            if not await do_http_to_file(f"{base}/{checksums_name}", sums_path, read_timeout=60):
                return IsoRefreshResult(ok=False, error=f"Failed to download {checksums_name}", build=build)

            expected_hash = _parse_sha256sum_line(sums_path.read_text(), iso_name)

            if not expected_hash:
                return IsoRefreshResult(
                    ok=False, error=f"{iso_name} not listed in {checksums_name}", build=build
                )

            actual_hash = await _sha256_file(iso_path)

            if actual_hash != expected_hash:
                return IsoRefreshResult(ok=False, error=f"Checksum mismatch for {iso_name}", build=build)

            upload_res = await self.api.upload_file(
                iso_path, node=node, storage=storage_iso, file_name=iso_name, content="iso"
            )

            if not upload_res:
                return IsoRefreshResult(ok=False, error=f"Upload of {iso_name} failed", build=build)

            if not await self.api.wait_for_task(upload_res.payload, node, max_wait_s=120):
                return IsoRefreshResult(ok=False, error=f"Upload of {iso_name} did not complete", build=build)

        return IsoRefreshResult(
            ok=True,
            changed=True,
            iso=iso_name,
            previous=current_iso,
            build=build,
            severity=severity,
            release=release_name,
        )

    async def validate_network(self, node: str, network: str) -> bool:
        """Validate that the specified network bridge exists on the hypervisor."""
        res = await self.api.get_networks(node)

        if not res:
            return False

        network_exists = next(filter(lambda x: x.get("iface") == network, res.payload), None)

        return bool(network_exists)

    async def stop_vm(self, vm_id: int, node: str) -> bool:
        """Stop a VM and wait for the task to complete."""
        res = await self.api.stop_vm(vm_id, node)

        if not res:
            return False

        return await self.api.wait_for_task(res.payload, node, 30)

    async def delete_vm(self, vm_id: int, node: str) -> bool:
        """Delete a VM (with disk purge) and wait for the task to complete."""
        res = await self.api.delete_vm(vm_id, node)

        if not res:
            return False

        return await self.api.wait_for_task(res.payload, node, 30)

    async def _stop_and_delete_vm(
        self,
        vm_id: int,
        vm_name: str,
        vm_status: str,
        node: str,
        callback: Callable[[bool, str], None] | None = None,
    ) -> bool:
        """Stop a VM (if running) then delete it with its disks."""
        if callback:
            callback(True, f"Found VM {vm_name} (id={vm_id}, status={vm_status})")

        if vm_status == "running":
            if callback:
                callback(True, "Stopping VM...")
            stopped = await self.stop_vm(vm_id, node)
            if not stopped:
                if callback:
                    callback(False, "Failed to stop VM")
                return False
            if callback:
                callback(True, "VM stopped")

        if callback:
            callback(True, "Deleting VM and disks...")
        deleted = await self.delete_vm(vm_id, node)
        if not deleted:
            if callback:
                callback(False, "Failed to delete VM")
            return False

        if callback:
            callback(True, "VM deleted")

        return True

    async def deprovision_node(
        self,
        fluxnode: "ArcaneOsConfig",
        callback: Callable[[bool, str], None] | None = None,
    ) -> bool:
        """Deprovision a single Fluxnode VM from a Proxmox hypervisor.

        Stops the VM (if running), then deletes it along with its disks.

        Args:
            fluxnode: The node configuration to deprovision.
            callback: Optional progress callback receiving (success, message).

        Returns:
            True if deprovisioning succeeded, False otherwise.
        """
        hyper = fluxnode.hypervisor

        if not hyper:
            if callback:
                callback(False, "No hypervisor config")
            return False

        vms_res = await self.api.get_vms(hyper.node)
        if not vms_res:
            if callback:
                callback(False, "Unable to list VMs")
            return False

        vm = next(
            (v for v in vms_res.payload if v.get("name") == hyper.vm_name),
            None,
        )

        if not vm:
            if callback:
                callback(False, f"VM '{hyper.vm_name}' not found on {hyper.node}")
            return False

        return await self._stop_and_delete_vm(
            vm["vmid"], hyper.vm_name, vm.get("status", "unknown"), hyper.node, callback,
        )

    async def deprovision_vm(
        self,
        node: str,
        callback: Callable[[bool, str], None] | None = None,
        *,
        vm_name: str | None = None,
        vm_id: int | None = None,
    ) -> bool:
        """Deprovision a VM by name or ID directly, without a config file.

        Exactly one of vm_name or vm_id must be provided.
        """
        vms_res = await self.api.get_vms(node)
        if not vms_res:
            if callback:
                callback(False, f"Unable to list VMs on {node}")
            return False

        vm: dict | None = None
        if vm_id is not None:
            vm = next((v for v in vms_res.payload if v.get("vmid") == vm_id), None)
            if not vm:
                if callback:
                    callback(False, f"VM id={vm_id} not found on {node}")
                return False
        elif vm_name is not None:
            vm = next((v for v in vms_res.payload if v.get("name") == vm_name), None)
            if not vm:
                if callback:
                    callback(False, f"VM '{vm_name}' not found on {node}")
                return False

        if not vm:
            return False

        return await self._stop_and_delete_vm(
            vm["vmid"], vm.get("name", str(vm["vmid"])), vm.get("status", "unknown"), node, callback,
        )

    async def start_vm(self, vm_id: int, node: str) -> bool:
        """Start a VM and wait for the task to complete."""
        res = await self.api.start_vm(vm_id, node)

        if not res:
            return False

        return await self.api.wait_for_task(res.payload, node, 20)

    async def create_vm(self, config: VmConfig, node: str) -> bool:
        """Create a VM and wait for the task to complete."""
        create_res = await self.api.create_vm(config.to_proxmox_dict(), node)

        if not create_res:
            log.error("VM creation failed: status=%s error=%s", create_res.status, create_res.error)
            return False

        return await self.api.wait_for_task(create_res.payload, node)

    async def delete_install_disks(self, vm_id: int, node: str, storage: str, delete_efi: bool = True) -> bool:
        """Delete the EFI and config disk images used during provisioning."""
        efi_file = "arcane_efi.raw"
        config_file = f"{vm_id}_arcane_config.raw"

        if delete_efi:
            efi_res = await self.api.delete_file(efi_file, node, storage, content="import")
        else:
            efi_res = True

        config_res = await self.api.delete_file(config_file, node, storage, content="import")

        if not efi_res or not config_res:
            return False

        if delete_efi:
            efi_ok = await self.api.wait_for_task(efi_res.payload, node)
        else:
            efi_ok = True

        if not efi_ok:
            return False

        return await self.api.wait_for_task(config_res.payload, node)

    async def upload_arcane_efi(self, node: str, storage: str) -> bool:
        """Upload the EFI bootloader image to the hypervisor."""
        with _efi_gz_resource.open("rb") as f:
            efi_disk = gzip.decompress(f.read())

        upload_res = await self.api.upload_file(
            efi_disk,
            node=node,
            storage=storage,
            file_name="arcane_efi.raw",
        )

        if not upload_res:
            return False

        return await self.api.wait_for_task(upload_res.payload, node)

    async def upload_arcane_config(self, config: bytes, vm_id: int, node: str, storage: str) -> bool:
        """Write node config into a FAT image and upload to the hypervisor."""
        with tempfile.TemporaryDirectory(prefix="arcane_mage_") as tmpdir:
            config_image_name = _get_vm_config_file_name(vm_id)
            config_image_path = Path(tmpdir) / config_image_name

            # Extract and decompress the config image template
            with config_image_path.open("wb") as img_fh, _config_gz_resource.open("rb") as img_gz_fh:
                img_fh.write(gzip.decompress(img_gz_fh.read()))

            # Modify the FAT filesystem to add the config
            async with FAT12Writer(config_image_path) as fat_writer:
                await fat_writer.write_file("arcane_config.yaml", config)

            # Upload the modified image
            upload_res = await self.api.upload_file(
                config_image_path,
                node=node,
                storage=storage,
            )

        if not upload_res:
            return False

        return await self.api.wait_for_task(upload_res.payload, node)

    async def create_vm_config(
        self,
        vm_name: str,
        tier: Literal["cumulus", "nimbus", "stratus"],
        network_bridge: str,
        storage_images: str = "local-lvm",
        storage_iso: str = "local",
        storage_import: str = "local",
        vm_id: int | None = None,
        iso_name: str | None = None,
        startup_config: str | None = None,
        tags: str | None = None,
        description: str | None = None,
        disk_limit: int | None = None,
        cpu_limit: float | None = None,
        network_limit: int | None = None,
    ) -> VmConfig | None:
        """Generate the Proxmox VM configuration for a given tier."""
        tier_config = TIER_CONFIG.get(tier)

        if not tier_config:
            return None

        if vm_id is None:
            vm_id_res = await self.api.get_next_id()

            if not vm_id_res:
                return None

            vm_id = vm_id_res.payload

            assert vm_id

        disk_rate = f"mbps_rd={disk_limit},mbps_wr={disk_limit}," if disk_limit else ""
        network_rate = f",rate={network_limit}" if network_limit else ""
        cpu_limit = cpu_limit or 0

        smbios_uuid = str(uuid.uuid4())
        config_img = _get_vm_config_file_name(vm_id)

        return VmConfig(
            efidisk0=(
                f"{storage_images}:0,efitype=4m,pre-enrolled-keys=0,"
                f"import-from={storage_import}:import/arcane_efi.raw"
            ),
            cpu="host",
            ostype="l26",
            sockets=1,
            vmid=vm_id,
            agent="1",
            onboot=1,
            name=vm_name,
            smbios1=f"uuid={smbios_uuid}",
            boot="order=scsi0;ide2;net0",
            numa=0,
            memory=tier_config["memory_mb"],
            tpmstate0=f"{storage_images}:4,version=v2.0",
            cores=tier_config["cpu_cores"],
            cpulimit=cpu_limit,
            bios="ovmf",
            scsi0=f"{storage_images}:{tier_config['scsi_gb']},{disk_rate}discard=on,iothread=1,ssd=1",
            scsi1=f"{storage_images}:0,import-from={storage_import}:import/{config_img}",
            ide2=f"{storage_iso}:iso/{iso_name},media=cdrom",
            net0=f"model=virtio,bridge={network_bridge}{network_rate}",
            scsihw="virtio-scsi-single",
            startup=startup_config,
            tags=tags,
            description=description,
        )

    async def provision_node(
        self,
        fluxnode: ArcaneOsConfig,
        callback: Callable[[bool, str], None] | None = None,
        delete_efi: bool = True,
        skip_efi_upload: bool = False,
    ) -> bool:
        """Provision a single Fluxnode VM on a Proxmox hypervisor.

        Args:
            fluxnode: The node configuration to provision.
            callback: Optional progress callback receiving (success, message).
            delete_efi: Whether to delete the EFI image after provisioning.

        Returns:
            True if provisioning succeeded, False otherwise.
        """

        def _cb(ok: bool, msg: str) -> None:
            if callback:
                callback(ok, msg)

        hv = fluxnode.hypervisor

        if not hv:
            return False

        if hv.node_tier not in TIER_CONFIG:
            _cb(False, f"Node tier: {hv.node_tier} does not exist")
            return False

        if self.cluster_detection_error:
            _cb(False, self.cluster_detection_error)
            return False

        if self.cluster:
            if not self.cluster.has_quorum:
                _cb(False, "Cluster has lost quorum, refusing to provision")
                return False

            if not self.cluster.is_node_online(hv.node):
                _cb(False, f"Node '{hv.node}' is offline in cluster")
                return False

            resources_res = await self.api.get_cluster_resources(resource_type="vm")
            if resources_res and isinstance(resources_res.payload, list):
                duplicate = next(
                    (r for r in resources_res.payload if r.get("name") == hv.vm_name),
                    None,
                )
                if duplicate:
                    existing_node = duplicate.get("node", "unknown")
                    _cb(
                        False,
                        f"VM name '{hv.vm_name}' already exists on node '{existing_node}'",
                    )
                    return False

            _cb(True, "Cluster pre-flight checks passed")

        version_valid, version_error = await self.validate_api_version(hv.node)

        if not version_valid:
            _cb(False, version_error)
            return False

        _cb(True, "Api version validated")

        storage_valid, storage_error = await self.validate_storage(
            hv.node, hv.storage_iso, hv.storage_images, hv.storage_import
        )

        if not storage_valid:
            _cb(False, storage_error)
            return False

        _cb(True, "Storage validated")

        iso_valid = await self.validate_iso_version(hv.node, hv.iso_name, hv.storage_iso)

        if not iso_valid:
            _cb(False, "Unable to find ISO image on hypervisor")
            return False

        _cb(True, "ISO image validated")

        network_valid = await self.validate_network(hv.node, hv.network)

        if not network_valid:
            _cb(False, "Network not present on hypervisor")
            return False

        _cb(True, "Network validated")

        vm_config = await self.create_vm_config(
            vm_name=hv.vm_name,
            vm_id=hv.vm_id,
            tier=hv.node_tier,
            network_bridge=hv.network,
            storage_images=hv.storage_images,
            storage_iso=hv.storage_iso,
            storage_import=hv.storage_import,
            iso_name=hv.iso_name,
            disk_limit=hv.disk_limit,
            cpu_limit=hv.cpu_limit,
            network_limit=hv.network_limit,
            startup_config=hv.startup_config,
            tags=hv.tags,
            description=hv.description,
        )

        if not vm_config:
            _cb(False, "Unable to generate vm config")
            return False

        vm_id = vm_config.vmid
        # Surface the resolved vmid back onto the config so callers (CLI --json,
        # downstream automation) can capture it even when it was auto-assigned.
        hv.vm_id = vm_id

        config_upload = yaml.dump({"nodes": [fluxnode.to_dict()]})

        config_ok = await self.upload_arcane_config(config_upload.encode("utf-8"), vm_id, hv.node, hv.storage_import)

        if not config_ok:
            _cb(False, "Unable to upload Config image to hypervisor")
            return False

        _cb(True, "Config image uploaded")

        if skip_efi_upload:
            _cb(True, "EFI image upload skipped (shared storage)")
        else:
            efi_ok = await self.upload_arcane_efi(hv.node, hv.storage_import)

            if not efi_ok:
                _cb(False, "Unable to upload EFI image to hypervisor")
                return False

            _cb(True, "EFI image uploaded")

        created_ok = await self.create_vm(vm_config, node=hv.node)

        if not created_ok:
            await self.delete_install_disks(vm_id, hv.node, hv.storage_import, delete_efi)
            _cb(False, "Unable to create VM on hypervisor")
            return False

        _cb(True, "VM Created")

        deleted_ok = await self.delete_install_disks(vm_id, hv.node, hv.storage_import, delete_efi)

        if not deleted_ok:
            _cb(False, "Unable to clean up disk images on hypervisor")
            return False

        _cb(True, "Disk images cleaned")

        if not hv.start_on_creation:
            return True

        started_ok = await self.start_vm(vm_id, hv.node)

        if not started_ok:
            _cb(False, "Unable to start VM on hypervisor")
            return False

        _cb(True, "VM started")
        return True
