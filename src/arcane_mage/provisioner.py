import asyncio
import gzip
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
from .helpers import do_http
from .models import ArcaneOsConfig, ArcaneOsConfigGroup, HypervisorConfig
from .proxmox import ProxmoxApi

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


@dataclass
class HypervisorDiscovery:
    """Result of discovering nodes and their provisioned VMs on a hypervisor."""

    nodes: ArcaneOsConfigGroup
    provisioned_vms: dict[str, list[dict]]


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
            api = ProxmoxApi.from_token(config.url, *token)
        elif config.auth_type == "userpass" and (user_pass := ProxmoxApi.parse_user_pass(credential)):
            api = await ProxmoxApi.from_user_pass(config.url, *user_pass)

        if not api:
            return None

        return cls(api)

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
                provisioned[name] = vm_res.payload
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

        return True, ""

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
                msg += " Free up space, reduce reserved blocks, or run as root"
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

    async def validate_network(self, node: str, network: str) -> bool:
        """Validate that the specified network bridge exists on the hypervisor."""
        res = await self.api.get_networks(node)

        if not res:
            return False

        network_exists = next(filter(lambda x: x.get("iface") == network, res.payload), None)

        return bool(network_exists)

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
        )

    async def provision_node(
        self,
        fluxnode: ArcaneOsConfig,
        callback: Callable[[bool, str], None] | None = None,
        delete_efi: bool = True,
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
        )

        if not vm_config:
            _cb(False, "Unable to generate vm config")
            return False

        vm_id = vm_config.vmid

        config_upload = yaml.dump({"nodes": [fluxnode.to_dict()]})

        config_ok = await self.upload_arcane_config(config_upload.encode("utf-8"), vm_id, hv.node, hv.storage_import)

        if not config_ok:
            _cb(False, "Unable to upload Config image to hypervisor")
            return False

        _cb(True, "Config image uploaded")

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
