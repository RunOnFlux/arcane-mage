import asyncio
import gzip
import importlib.resources as resources
import logging
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, NotRequired, Self, TypedDict

import yaml
from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config

from .fat_writer import FAT12Writer
from .helpers import do_http, do_http_to_file
from .models import ArcaneOsConfig, ArcaneOsConfigGroup, HypervisorConfig
from .proxmox import ApiResponse, HypervisorNode, ProxmoxApi, TaskOutcome, VmSummary

log = logging.getLogger(__name__)

MIN_API_VERSION = "8.4.1"
MIN_IMPORT_STORAGE_BYTES = 10 * 1024 * 1024  # 10 MiB

# provision_node deletes the install images once the create's wait ends, so the wait must
# outlast a create on a busy host: allocating the disks and importing the images can take
# minutes.
CREATE_TASK_MAX_WAIT_S = 600
UPLOAD_TASK_MAX_WAIT_S = 120

# A 3+ GB download onto the hypervisor.
ISO_DOWNLOAD_MAX_WAIT_S = 1800

_RELEASE_FEED = "https://images.runonflux.io/arcane/api/latest_release"
_RELEASE_BASE = "https://images.runonflux.io/arcane/releases"

# How long a running VM gets to shut itself down before teardown hard-stops it.
SHUTDOWN_TIMEOUT_S = 120

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
    vga: str
    serial0: str
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


# Each VM imports its own EFI image, so runs that share an import storage never delete
# an image another run is still importing.
def _get_vm_efi_file_name(vm_id: int) -> str:
    return f"{vm_id}_arcane_efi.raw"


async def get_latest_iso_version() -> str | None:
    """Fetch the latest FluxOS ISO version from the release API."""
    res = await do_http("https://images.runonflux.io/api/latest_release", total_timeout=3)

    if not res or not isinstance(res, dict):
        return None

    return res.get("iso")


@with_config(ConfigDict(extra="allow"))
class ArcaneRelease(TypedDict):
    """The latest ArcaneOS release, as the release feed gives it."""

    iso: str
    build: str
    checksums: str
    release: NotRequired[str]
    severity: NotRequired[str]


_ARCANE_RELEASE = TypeAdapter(ArcaneRelease)


def _find_sha256(sums: str, file_name: str) -> str | None:
    """`file_name`'s digest in a `sha256sum`-format checksum file."""
    for line in sums.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*") == file_name:
            return parts[0]

    return None


@dataclass
class IsoRefreshResult:
    """The release a refresh found, and whether it staged its ISO. `error` is set when
    the refresh failed."""

    iso: str | None = None
    build: str | None = None
    release: str | None = None
    severity: str | None = None
    changed: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class HypervisorDiscovery:
    """Result of discovering nodes and their provisioned VMs on a hypervisor."""

    nodes: ArcaneOsConfigGroup
    provisioned_vms: dict[str, list[VmSummary]]


class Provisioner:
    """Orchestrates Proxmox VM provisioning for Fluxnodes."""

    def __init__(self, api: ProxmoxApi) -> None:
        self.api = api

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

        return cls(api)

    async def discover_nodes(self, all_configs: ArcaneOsConfigGroup) -> HypervisorDiscovery | None:
        """Discover hypervisor nodes and match against known configurations.

        Returns the usable nodes and their provisioned VMs, or None on failure.
        """
        hyper_nodes = await self.api.get_hypervisor_nodes()

        if not hyper_nodes:
            return None

        useable_nodes = ArcaneOsConfigGroup()
        provisioned: dict[str, list[VmSummary]] = {}

        # A node whose VMs cannot be listed is left out: its configs cannot be
        # checked against what it already runs.
        async def handle_node(node: HypervisorNode) -> None:
            name = node["node"]
            vm_res = await self.api.get_vms(name)
            if vm_res:
                provisioned[name] = vm_res.payload
                useable_nodes.add_nodes(all_configs.get_nodes_by_hypervisor_name(name))

        await asyncio.gather(*(handle_node(n) for n in hyper_nodes.payload))

        return HypervisorDiscovery(nodes=useable_nodes, provisioned_vms=provisioned)

    async def validate_api_version(self, node: str) -> tuple[bool, str]:
        """Validate that the Proxmox API version meets minimum requirements."""
        res = await self.api.get_api_version(node)

        if not res:
            return False, "Unable to get Proxmox api version"

        version = res.payload["version"]

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

        by_name = {x["storage"]: x for x in res.payload}
        node_storage_iso = by_name.get(storage_iso)
        node_storage_images = by_name.get(storage_images)
        node_storage_import = by_name.get(storage_import)

        if not node_storage_iso or not node_storage_images or not node_storage_import:
            return False, "Missing storage config item"

        if (
            "iso" not in node_storage_iso["content"]
            or "images" not in node_storage_images["content"]
            or "import" not in node_storage_import["content"]
        ):
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

        return any(x["content"] == "iso" and x["volid"].endswith(iso_name) for x in res.payload)

    async def refresh_iso(self, node: str, storage_iso: str) -> IsoRefreshResult:
        """Stage the latest release's ISO in `node`'s `storage_iso`, unless it is there
        already. The node downloads it, and checks it against the release's sha256 sums."""
        feed = await do_http(_RELEASE_FEED, total_timeout=10)

        try:
            release = _ARCANE_RELEASE.validate_python(feed)
        except ValidationError:
            return IsoRefreshResult(error=f"Unexpected release feed answer: {feed}")

        iso = release["iso"]
        found = IsoRefreshResult(
            iso=iso,
            build=release["build"],
            release=release.get("release"),
            severity=release.get("severity"),
        )

        if await self.validate_iso_version(node, iso, storage_iso):
            return found

        base = f"{_RELEASE_BASE}/{release['build']}"

        with tempfile.TemporaryDirectory(prefix="arcane_mage_") as tmpdir:
            sums_path = Path(tmpdir) / release["checksums"]

            if not await do_http_to_file(f"{base}/{release['checksums']}", sums_path, read_timeout=60):
                return replace(found, error=f"Unable to fetch {release['checksums']}")

            sha256 = _find_sha256(sums_path.read_text(), iso)

        if not sha256:
            return replace(found, error=f"{iso} is not listed in {release['checksums']}")

        res = await self.api.download_iso(node, f"{base}/{iso}", iso, storage_iso, sha256=sha256)
        outcome = await self._await_task(res, node, ISO_DOWNLOAD_MAX_WAIT_S)

        if not outcome:
            return replace(found, error=f"Download of {iso} failed: {outcome.error}")

        return replace(found, changed=True)

    async def validate_network(self, node: str, network: str) -> bool:
        """Whether `network` is a bridge a VM on `node` can attach to: a Linux or
        OVS bridge, or an SDN VNet applied on that node."""
        res = await self.api.get_networks(node)

        if not res:
            return False

        return any(x["iface"] == network for x in res.payload)

    async def _await_task(self, res: ApiResponse[str], node: str, max_wait_s: int) -> TaskOutcome:
        """Wait on the task `res` started. A request that failed is a failed task."""
        if not res:
            return TaskOutcome(error=res.reason)

        return await self.api.wait_for_task(res.payload, node, max_wait_s)

    async def shutdown_vm(self, vm_id: int, node: str) -> TaskOutcome:
        """Ask the guest to shut down and wait for it to go down."""
        res = await self.api.shutdown_vm(vm_id, node, SHUTDOWN_TIMEOUT_S)

        return await self._await_task(res, node, SHUTDOWN_TIMEOUT_S + 10)

    async def stop_vm(self, vm_id: int, node: str) -> TaskOutcome:
        """Stop a VM and wait for the task to complete."""
        res = await self.api.stop_vm(vm_id, node)

        return await self._await_task(res, node, 30)

    async def delete_vm(self, vm_id: int, node: str) -> TaskOutcome:
        """Delete a VM (with disk purge) and wait for the task to complete."""
        res = await self.api.delete_vm(vm_id, node)

        return await self._await_task(res, node, 30)

    async def _stop_and_delete_vm(
        self,
        vm_id: int,
        vm_name: str,
        vm_status: str,
        node: str,
        callback: Callable[[bool, str], None] | None = None,
    ) -> bool:
        """Shut a running VM down, hard-stopping it if it does not go down, then delete it
        with its disks."""
        if callback:
            callback(True, f"Found VM {vm_name} (id={vm_id}, status={vm_status})")

        if vm_status == "running":
            if callback:
                callback(True, "Shutting down VM...")
            shut_down = await self.shutdown_vm(vm_id, node)
            if shut_down:
                if callback:
                    callback(True, "VM shut down")
            else:
                if callback:
                    callback(True, f"VM did not shut down ({shut_down.error}), stopping...")
                stopped = await self.stop_vm(vm_id, node)
                if not stopped:
                    if callback:
                        callback(False, f"Failed to stop VM: {stopped.error}")
                    return False
                if callback:
                    callback(True, "VM stopped")

        if callback:
            callback(True, "Deleting VM and disks...")
        deleted = await self.delete_vm(vm_id, node)
        if not deleted:
            if callback:
                callback(False, f"Failed to delete VM: {deleted.error}")
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
            vm["vmid"],
            hyper.vm_name,
            vm["status"],
            hyper.node,
            callback,
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

        vm: VmSummary | None = None
        if vm_id is not None:
            vm = next((v for v in vms_res.payload if v["vmid"] == vm_id), None)
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
            vm["vmid"],
            vm.get("name", str(vm["vmid"])),
            vm["status"],
            node,
            callback,
        )

    async def start_vm(self, vm_id: int, node: str) -> TaskOutcome:
        """Start a VM and wait for the task to complete."""
        res = await self.api.start_vm(vm_id, node)

        return await self._await_task(res, node, 20)

    async def create_vm(self, config: VmConfig, node: str) -> TaskOutcome:
        """Create a VM and wait for the task to complete."""
        create_res = await self.api.create_vm(config.to_proxmox_dict(), node)

        if not create_res:
            log.error("VM creation failed: status=%s error=%s", create_res.status, create_res.error)

        return await self._await_task(create_res, node, CREATE_TASK_MAX_WAIT_S)

    async def delete_install_disks(self, vm_id: int, node: str, storage: str, delete_efi: bool = True) -> bool:
        """Delete the VM's config image, and its EFI image unless `delete_efi` is False."""
        efi_file = _get_vm_efi_file_name(vm_id)
        config_file = _get_vm_config_file_name(vm_id)

        efi_res: ApiResponse[str] | None = None

        if delete_efi:
            efi_res = await self.api.delete_file(efi_file, node, storage, content="import")

        config_res = await self.api.delete_file(config_file, node, storage, content="import")

        if efi_res is not None and (not efi_res or not await self.api.wait_for_task(efi_res.payload, node)):
            return False

        if not config_res:
            return False

        return bool(await self.api.wait_for_task(config_res.payload, node))

    async def upload_arcane_efi(self, vm_id: int, node: str, storage: str) -> TaskOutcome:
        """Upload the VM's EFI bootloader image to the hypervisor."""
        with _efi_gz_resource.open("rb") as f:
            efi_disk = gzip.decompress(f.read())

        upload_res = await self.api.upload_file(
            efi_disk,
            node=node,
            storage=storage,
            file_name=_get_vm_efi_file_name(vm_id),
        )

        return await self._await_task(upload_res, node, UPLOAD_TASK_MAX_WAIT_S)

    async def upload_arcane_config(self, config: bytes, vm_id: int, node: str, storage: str) -> TaskOutcome:
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

        return await self._await_task(upload_res, node, UPLOAD_TASK_MAX_WAIT_S)

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
        memory_mb: int | None = None,
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

        disk_rate = f"mbps_rd={disk_limit},mbps_wr={disk_limit}," if disk_limit else ""
        network_rate = f",rate={network_limit}" if network_limit else ""
        cpu_limit = cpu_limit or 0

        smbios_uuid = str(uuid.uuid4())
        config_img = _get_vm_config_file_name(vm_id)
        efi_img = _get_vm_efi_file_name(vm_id)

        return VmConfig(
            efidisk0=(
                f"{storage_images}:0,efitype=4m,pre-enrolled-keys=0,import-from={storage_import}:import/{efi_img}"
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
            memory=memory_mb or tier_config["memory_mb"],
            tpmstate0=f"{storage_images}:4,version=v2.0",
            cores=tier_config["cpu_cores"],
            cpulimit=cpu_limit,
            bios="ovmf",
            scsi0=f"{storage_images}:{tier_config['scsi_gb']},{disk_rate}discard=on,iothread=1,ssd=1",
            scsi1=f"{storage_images}:0,import-from={storage_import}:import/{config_img}",
            ide2=f"{storage_iso}:iso/{iso_name},media=cdrom",
            net0=f"model=virtio,bridge={network_bridge}{network_rate}",
            scsihw="virtio-scsi-single",
            # The node's console offers text to the viewer's clipboard (Proxmox's
            # "Clipboard: VNC", PVE 8.2+, below MIN_API_VERSION) and writes it to its
            # first serial port (the xterm.js console).
            vga="std,clipboard=vnc",
            serial0="socket",
            startup=startup_config,
            tags=tags,
            description=description,
        )

    async def provision_node(
        self,
        fluxnode: ArcaneOsConfig,
        callback: Callable[[bool, str], None] | None = None,
    ) -> bool:
        """Provision a single Fluxnode VM on a Proxmox hypervisor.

        Args:
            fluxnode: The node configuration to provision.
            callback: Optional progress callback receiving (success, message).

        Returns:
            True if provisioning succeeded, False otherwise. Once the VM's id is
            known, `fluxnode.hypervisor.vm_id` holds it, an assigned id included.
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
            memory_mb=hv.memory_mb,
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
        hv.vm_id = vm_id

        config_upload = yaml.dump({"nodes": [fluxnode.to_dict()]})

        config_ok = await self.upload_arcane_config(config_upload.encode("utf-8"), vm_id, hv.node, hv.storage_import)

        # An upload whose wait failed can still have landed its file.
        if not config_ok:
            await self.delete_install_disks(vm_id, hv.node, hv.storage_import, delete_efi=False)
            _cb(False, f"Unable to upload Config image to hypervisor: {config_ok.error}")
            return False

        _cb(True, "Config image uploaded")

        efi_ok = await self.upload_arcane_efi(vm_id, hv.node, hv.storage_import)

        if not efi_ok:
            await self.delete_install_disks(vm_id, hv.node, hv.storage_import)
            _cb(False, f"Unable to upload EFI image to hypervisor: {efi_ok.error}")
            return False

        _cb(True, "EFI image uploaded")

        created_ok = await self.create_vm(vm_config, node=hv.node)

        if not created_ok:
            await self.delete_install_disks(vm_id, hv.node, hv.storage_import)
            _cb(False, f"Unable to create VM on hypervisor: {created_ok.error}")
            return False

        _cb(True, "VM Created")

        deleted_ok = await self.delete_install_disks(vm_id, hv.node, hv.storage_import)

        if not deleted_ok:
            _cb(False, "Unable to clean up disk images on hypervisor")
            return False

        _cb(True, "Disk images cleaned")

        if not hv.start_on_creation:
            return True

        started_ok = await self.start_vm(vm_id, hv.node)

        if not started_ok:
            _cb(False, f"Unable to start VM on hypervisor: {started_ok.error}")
            return False

        _cb(True, "VM started")
        return True
