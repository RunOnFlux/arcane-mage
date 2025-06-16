from __future__ import annotations

import asyncio
import gzip
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

import warnings

# fix this before it becomes an error (11/25)
# https://github.com/PyFilesystem/pyfilesystem2/pull/590
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=UserWarning)
    import fs

import yaml
from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Grid, Horizontal, Vertical
from textual.dom import NoMatches
from textual.message import Message
from textual.reactive import var
from textual.screen import Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Rule,
    Select,
    Switch,
)
from yarl import URL

from ..helpers import do_http
from ..messages import (
    UpdateDefaultPage,
)
from ..models import ArcaneOsConfig, ArcaneOsConfigGroup, HypervisorConfig
from ..proxmox import ProxmoxApi


class WelcomeScreenProxmox(Screen):
    class AddHypervisor(Message): ...

    class DelHypervisor(Message):
        def __init__(self, hypervisor: HypervisorConfig):
            super().__init__()
            self.hypervisor = hypervisor

    class ProvisionNode(Message):
        def __init__(self, fluxnode: ArcaneOsConfig) -> None:
            super().__init__()

            self.fluxnode = fluxnode

    class ProvisionAllNodes(Message):
        def __init__(self, delay: int) -> None:
            super().__init__()

            self.delay = delay

    TITLE = "Proxmox Arcane Fluxnode Creator"

    first_time = var(False)
    hypervisor_populated = var(False)
    display_table = var(False)

    base_image_path = Path("images")

    efi_gz_path = base_image_path / "arcane_efi.raw.gz"
    efi_path = efi_gz_path.with_suffix("")

    config_image_gz_path = base_image_path / "arcane_config.raw.gz"
    config_image_base = "arcane_config"

    # The config location on the fluxnode
    config_file_path = Path("/arcane_config.yaml")

    config_tier_map = {
        "cumulus": {"memory_mb": "8192", "scsi_gb": "220", "cpu_cores": 4},
        "nimbus": {"memory_mb": "32768", "scsi_gb": "440", "cpu_cores": 8},
        "stratus": {"memory_mb": "65536", "scsi_gb": "880", "cpu_cores": 16},
    }

    proxmox_api: ProxmoxApi

    @staticmethod
    def is_api_min_version(version: str) -> bool:
        MIN_VERSION = [8, 4, 1]

        parts = version.split(".")

        if not len(parts) == 3:
            return False

        for minimum_str, actual in zip(parts, MIN_VERSION):
            try:
                minimum = int(minimum_str)
            except ValueError:
                return False

            if actual < minimum:
                return False

        return True

    def __init__(
        self,
        hypervisors: list[HypervisorConfig],
        fluxnodes: ArcaneOsConfigGroup,
        is_homepage: bool = False,
    ) -> None:
        super().__init__()

        self.hypervisors = hypervisors
        self.fluxnodes = fluxnodes
        self.is_homepage = is_homepage

        self.latest_iso: str | None = None

    def on_screen_resume(self):
        self.app.set_focus(None)

    def on_load(self) -> None:
        self.get_latest_iso_version()

    def on_mount(self) -> None:
        print(self.fluxnodes)

        self.validate_hypervisors()

    def validate_hypervisors(
        self, hypervisor: HypervisorConfig | None = None
    ) -> None:
        self.first_time = not bool(self.hypervisors)
        self.hypervisor_populated = False

        select = self.query_one(Select)

        options = [(URL(x.url).host, x.url) for x in self.hypervisors]

        if hypervisor:
            value = hypervisor.url
        else:
            value = options[0][1] if options else Select.BLANK

        select.set_options(options)
        select.value = value

    @work(name="populate_hypervisor")
    async def populate_hypervisor_api(
        self, hypervisor: HypervisorConfig
    ) -> None:
        url = hypervisor.url
        api = None

        useable_nodes = ArcaneOsConfigGroup()
        provisioned: dict[str, list] = {}

        credential = hypervisor.real_credential()

        if not credential:
            self.build_fluxnode_table(useable_nodes, provisioned)
            self.hypervisor_populated = True
            self.notify("Unable to find credential for Hypervisor")
            return

        if hypervisor.auth_type == "token":
            if token := ProxmoxApi.parse_token(credential):
                api = ProxmoxApi.from_token(url, *token)
        elif hypervisor.auth_type == "userpass":
            if user_pass := ProxmoxApi.parse_user_pass(credential):
                api = await ProxmoxApi.from_user_pass(url, *user_pass)

        if not api:
            self.build_fluxnode_table(useable_nodes, provisioned)
            self.hypervisor_populated = True
            self.notify("No Proxmox API available", severity="error")
            return

        self.proxmox_api = api

        hyper_nodes = await self.proxmox_api.get_hypervisor_nodes()

        if not hyper_nodes:
            self.build_fluxnode_table(useable_nodes, provisioned)
            self.hypervisor_populated = True
            self.notify(
                "Error getting Hypervisor nodes via api", severity="error"
            )
            return

        get_vms_coros = []

        async def handle_node(node: dict):
            if name := node.get("node", None):
                vm_res = await self.proxmox_api.get_vms(name)
                provisioned[name] = vm_res.payload
                useable_nodes.add_nodes(
                    self.fluxnodes.get_nodes_by_hypervisor_name(name)
                )

        for node in hyper_nodes.payload:
            get_vms_coros.append(handle_node(node))

        await asyncio.gather(*get_vms_coros)

        self.build_fluxnode_table(useable_nodes, provisioned)
        self.hypervisor_populated = True

    def build_fluxnode_table(
        self, fluxnodes: ArcaneOsConfigGroup, provisioned_nodes: dict[str, Any]
    ):
        try:
            table = self.query_one(DataTable)
        except NoMatches:
            return

        columns = [
            {"label": "Node", "key": "node"},
            {"label": "Hostname", "key": "hostname"},
            {"label": "Tier", "key": "tier"},
            {"label": "Network", "key": "network"},
            {"label": "Address", "key": "address"},
            {"label": "Provisioned", "key": "provisioned"},
        ]

        table.clear(columns=True)
        for column in columns:
            table.add_column(**column)

        # this is feral
        for fluxnode in fluxnodes:
            hyper = fluxnode.hypervisor
            row_key = f"{hyper.node}:{hyper.vm_name}"

            hypervisor_nodes = provisioned_nodes.get(hyper.node, [])

            is_provisioned = bool(
                next(
                    filter(
                        lambda x: x.get("name") == hyper.vm_name,
                        hypervisor_nodes,
                    ),
                    None,
                )
            )
            table.add_row(*fluxnode.as_row(), is_provisioned, key=row_key)

    def compose(self) -> ComposeResult:
        first_time_dialog = Label(
            'It looks like this is your first time here, click "Add" to get started.',
            id="dialog",
        )
        first_time_dialog.display = False

        fluxnode_dt: DataTable = DataTable(cursor_type="row")
        fluxnode_dt.border_title = "Fluxnodes"
        fluxnode_dt.display = False

        yield Header(show_clock=True)
        with Container():
            with Grid():
                with Horizontal(id="first-column"):
                    yield Button(
                        "\u21b0",
                        id="back",
                        classes="icon-button",
                        tooltip="Back",
                    )
                    yield Button(
                        "Add", id="add-hypervisor", tooltip="Add Hypervisor"
                    )
                    yield Button(
                        "Del", id="del-hypervisor", tooltip="Delete Hypervisor"
                    )
                with Horizontal(id="second-column"):
                    yield Label("Selected:", classes="text-label")
                    yield Select([])
                with Horizontal(id="third-column"):
                    yield Label("Homepage:", classes="text-label")
                    yield Switch(
                        id="homepage",
                        value=self.is_homepage,
                        tooltip="Set as default Homepage",
                    )
                    yield Button(
                        "X", id="exit", classes="icon-button", tooltip="Exit"
                    )
            yield Rule()
            with Vertical():
                yield first_time_dialog
                with Vertical(id="dt-container"):
                    yield fluxnode_dt
                    with Horizontal():
                        yield Label("Delay:", classes="text-label")
                        yield Input(
                            "300",
                            tooltip="Time between configuring",
                            restrict=r"^\d{0,4}",
                        )
                        yield Button("Sync All", id="sync-all")

        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not event.button.id:
            return

        if event.button.id == "add-hypervisor":
            self.post_message(WelcomeScreenProxmox.AddHypervisor())
        elif event.button.id == "del-hypervisor":
            select = self.query_one(Select)
            if select.value == Select.BLANK:
                return

            if hyper := self.get_hypervisor_by_url(select.value):
                self.post_message(WelcomeScreenProxmox.DelHypervisor(hyper))

        elif event.button.id == "back":
            self.dismiss()
        elif event.button.id == "exit":
            self.app.exit()
        elif event.button.id == "sync-all":
            input = self.query_one(Input)
            delay = int(input.value) if input.value else 0
            self.post_message(WelcomeScreenProxmox.ProvisionAllNodes(delay))

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id != "homepage":
            return

        self.post_message(
            UpdateDefaultPage("welcome-proxmox", event.switch.value)
        )

    def get_provisionable_nodes(self) -> ArcaneOsConfigGroup:
        # stop doing this. Store the data, so we don't have to get it from the dt
        try:
            dt = self.query_one(DataTable)
        except NoMatches:
            return ArcaneOsConfigGroup()

        nodes: list[ArcaneOsConfig] = []

        for row_key in dt.rows:
            row = dt.get_row(row_key)

            if not row[-1]:  # not provisioned
                fluxnode = self.fluxnodes.get_node_by_vm_name(
                    *row_key.value.split(":")
                )

                if fluxnode:
                    nodes.append(fluxnode)

        return ArcaneOsConfigGroup(nodes)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row = event.data_table.get_row(event.row_key)

        provisioned = row.pop()

        if provisioned:
            name = row[1]
            self.notify(f"Fluxnode: {name} already provisioned")
            return

        # fix this
        fluxnode = self.fluxnodes.get_node_by_vm_name(
            *event.row_key.value.split(":")
        )

        if not fluxnode:
            return

        self.post_message(WelcomeScreenProxmox.ProvisionNode(fluxnode))

    async def validate_api_version(self, node: str) -> tuple[bool, str]:
        res = await self.proxmox_api.get_api_version(node)

        if not res:
            return False, "Unable to get Proxmox api version"

        version = res.payload.get("version")

        if not version:
            return False, "Api payload missing version info"

        if not self.is_api_min_version(version):
            return False, f"Api version too old. Got: {version}, Want: 8.4.1"

        return True, ""

    async def validate_storage(
        self,
        node: str,
        storage_iso: str,
        storage_images: str,
        storage_import: str,
    ) -> tuple[bool, str]:
        res = await self.proxmox_api.get_storage_state(node)

        if not res:
            return False, "Unable to get Proxmox storage state"

        node_storage_iso = next(
            filter(lambda x: x.get("storage") == storage_iso, res.payload), None
        )

        node_storage_images = next(
            filter(lambda x: x.get("storage") == storage_images, res.payload),
            None,
        )

        node_storage_import = next(
            filter(lambda x: x.get("storage") == storage_import, res.payload),
            None,
        )

        if not all(
            [node_storage_iso, node_storage_images, node_storage_import]
        ):
            return False, "Missing storage config item"

        iso_content = node_storage_iso.get("content")
        images_content = node_storage_images.get("content")
        import_content = node_storage_import.get("content")

        if (
            "iso" not in iso_content
            or "images" not in images_content
            or "import" not in import_content
        ):
            return False, "Storage type missing on hypervisor"

        import_available = node_storage_import.get("avail", 0)

        # We need 4MiB + 4MiB for the EFI image and the config image. So we check for 10MiB
        if import_available < 10485760:
            return False, "Storage space less than 10MiB on hypervisor"

        # Should really parse these into storage objects and do stuff with them

        # {
        #     "data": [
        #         {
        #         "used": 1561132007424,
        #         "total": 3651770777600,
        #         "content": "images,rootdir",
        #         "enabled": 1,
        #         "avail": 2090638770176,
        #         "storage": "local-lvm",
        #         "used_fraction": 0.4275,
        #         "type": "lvmthin",
        #         "active": 1,
        #         "shared": 0
        #         },
        #         {
        #         "type": "dir",
        #         "active": 1,
        #         "shared": 0,
        #         "used_fraction": 0.941026375512876,
        #         "storage": "local",
        #         "avail": 2715500544,
        #         "enabled": 1,
        #         "total": 206551633920,
        #         "content": "import,backup,iso,vztmpl",
        #         "used": 194370535424
        #         }
        #     ]
        # }

        return True, ""

    async def validate_iso_version(
        self, node: str, iso_name: str, storage_iso: str
    ) -> bool:
        res = await self.proxmox_api.get_storage_content(node, storage_iso)

        if not res:
            return False

        iso_exists = next(
            filter(
                lambda x: x.get("content") == "iso"
                and x.get("volid", "").endswith(iso_name),
                res.payload,
            ),
            None,
        )

        return bool(iso_exists)

    async def validate_network(self, node: str, network: str) -> bool:
        res = await self.proxmox_api.get_networks(node)

        if not res:
            return False

        network_exists = next(
            filter(lambda x: x.get("iface") == network, res.payload), None
        )

        return bool(network_exists)

    def get_hypervisor_by_url(self, url: str) -> HypervisorConfig | None:
        return next(filter(lambda x: x.url == url, self.hypervisors), None)

    def on_select_changed(self, event: Select.Changed) -> None:
        if (hyper_url := event.select.value) == Select.BLANK:
            return

        if hypervisor := self.get_hypervisor_by_url(hyper_url):
            self.populate_hypervisor_api(hypervisor)

    @work(name="get_latest_iso_version")
    async def get_latest_iso_version(self) -> None:
        url = "https://images.runonflux.io/api/latest_release"

        res = await do_http(url, total_timeout=3)

        if not res:
            return None

        self.latest_iso = res.get("iso", None)

    @work(name="provision_node")
    async def provision_node(
        self,
        fluxnode: ArcaneOsConfig,
        callback: Callable[[bool, str], None],
        delete_efi: bool = True,
    ) -> bool:
        hv = fluxnode.hypervisor

        if not hv:
            return False

        if hv.node_tier not in WelcomeScreenProxmox.config_tier_map:
            callback(False, f"Node tier: {hv.node_tier} does not exist")
            return False

        version_valid, version_error = await self.validate_api_version(hv.node)

        if not version_valid:
            callback(False, version_error)
            return False

        callback(True, "Api version validated")

        storage_valid, storage_error = await self.validate_storage(
            hv.node, hv.storage_iso, hv.storage_images, hv.storage_import
        )

        if not storage_valid:
            callback(False, storage_error)
            return False

        callback(True, "Storage validated")

        iso_valid = await self.validate_iso_version(
            hv.node, hv.iso_name, hv.storage_iso
        )

        if not iso_valid:
            callback(False, "Unable to find ISO image on hypervisor")
            return False

        callback(True, "ISO image validated")

        network_valid = await self.validate_network(hv.node, hv.network)

        if not network_valid:
            callback(False, "Network not present on hypervisor")
            return False

        callback(True, "Network validated")

        vm_config = await self.create_vm_config(
            vm_name=hv.vm_name,
            tier=hv.node_tier,
            network_bridge=hv.network,
            iso_name=hv.iso_name,
            disk_limit=hv.disk_limit,
            cpu_limit=hv.cpu_limit,
            startup_config=hv.startup_config,
        )

        if not vm_config:
            callback(False, "Unable to generate vm config")
            return False

        vm_id: int = vm_config["vmid"]

        config_upload = yaml.dump({"nodes": [fluxnode.to_dict()]})

        config_ok = await self.upload_arcane_config(
            config_upload.encode("utf-8"), vm_id, hv.node, hv.storage_import
        )

        print("CONFIG OK:", config_ok)

        if not config_ok:
            callback(False, "Unable to upload Config image to hypervisor")
            return False

        callback(True, "Config image uploaded")

        efi_ok = await self.upload_arcane_efi(hv.node, hv.storage_import)

        print("EFI OK:", efi_ok)

        if not efi_ok:
            callback(False, "Unable to upload EFI image to hypervisor")
            return False

        callback(True, "EFI image uploaded")

        created_ok = await self.create_vm(vm_config, node=hv.node)

        print("CREATED OK", created_ok)

        if not created_ok:
            callback(False, "Unable to create VM on hypervisor")
            return False

        callback(True, "VM Created")

        deleted_ok = await self.delete_install_disks(
            vm_id, hv.node, hv.storage_import, delete_efi
        )

        print("DELETED OK", created_ok)

        if not deleted_ok:
            callback(False, "Unable to clean up disk images on hypervisor")
            return False

        callback(True, "Disk images cleaned")

        row_key = f"{hv.node}:{hv.vm_name}"
        table = self.query_one(DataTable)
        table.update_cell(row_key, "provisioned", True)

        if not hv.start_on_creation:
            return True

        started_ok = await self.start_vm(vm_id, hv.node)

        print("STARTED OK", created_ok)

        if not started_ok:
            callback(False, "Unable to start VM on hypervisor")
            return False

        callback(True, "VM started")
        return True

    def get_vm_config_file_name(self, vm_id: int) -> str:
        return f"{vm_id}_{self.config_image_base}.raw"

    def create_config_image(self, config: bytes, vm_id: int) -> Path:
        config_image_name = self.get_vm_config_file_name(vm_id)
        config_image_path = self.base_image_path / config_image_name

        with open(config_image_path, "wb") as img_fh:
            with open(self.config_image_gz_path, "rb") as img_gz_fh:
                img_fh.write(gzip.decompress(img_gz_fh.read()))

        fat_fs = fs.open_fs(f"fat://{config_image_path}")

        with fat_fs.openbin(str(self.config_file_path), mode="wb") as conf_fh:
            conf_fh.write(config)

        return config_image_path

    async def start_vm(self, vm_id: int, node: str) -> bool:
        res = await self.proxmox_api.start_vm(vm_id, node)

        if not res:
            return False

        ok = await self.proxmox_api.wait_for_task(res.payload, node, 20)

        return ok

    async def create_vm(self, config: dict, node: str) -> bool:
        create_res = await self.proxmox_api.create_vm(config, node)

        if not create_res:
            return False

        ok = await self.proxmox_api.wait_for_task(create_res.payload, node)

        return ok

    async def delete_install_disks(
        self, vm_id: int, node: str, storage: str, delete_efi: bool = True
    ) -> bool:
        efi_file = "arcane_efi.raw"
        config_file = f"{vm_id}_arcane_config.raw"

        if delete_efi:
            efi_res = await self.proxmox_api.delete_file(
                efi_file, node, storage, content="import"
            )
        else:
            efi_res = True

        config_res = await self.proxmox_api.delete_file(
            config_file, node, storage, content="import"
        )

        if not efi_res or not config_res:
            return False

        if delete_efi:
            efi_ok = await self.proxmox_api.wait_for_task(efi_res.payload, node)
        else:
            efi_ok = True

        if not efi_ok:
            return False

        config_ok = await self.proxmox_api.wait_for_task(
            config_res.payload, node
        )

        return config_ok

    async def upload_arcane_efi(self, node: str, storage: str) -> bool:
        with open(self.efi_gz_path, "rb") as f:
            efi_disk = gzip.decompress(f.read())

        upload_res = await self.proxmox_api.upload_file(
            efi_disk,
            node=node,
            storage=storage,
            file_name=str(self.efi_path.name),
        )

        if not upload_res:
            return False

        ok = await self.proxmox_api.wait_for_task(upload_res.payload, node)

        return ok

    async def upload_arcane_config(
        self, config: bytes, vm_id: int, node: str, storage: str
    ) -> bool:
        config_image_path = self.create_config_image(config, vm_id)

        try:
            upload_res = await self.proxmox_api.upload_file(
                config_image_path,
                node=node,
                storage=storage,
            )
        finally:
            config_image_path.unlink()

        if not upload_res:
            return False

        ok = await self.proxmox_api.wait_for_task(upload_res.payload, node)

        return ok

    async def create_vm_config(
        self,
        vm_name: str,
        tier: Literal["cumulus", "nimbus", "stratus"],
        network_bridge: str,
        storage_images: str = "local-lvm",
        storage_iso: str = "local",
        storage_import: str = "local",
        iso_name: str | None = None,
        startup_config: str | None = None,
        disk_limit: int | None = None,
        cpu_limit: float | None = None,
    ) -> dict | None:
        tier_config = WelcomeScreenProxmox.config_tier_map.get(tier)

        if not tier_config:
            return None

        vm_id_res = await self.proxmox_api.get_next_id()

        if not vm_id_res:
            return None

        vm_id: int = vm_id_res.payload

        disk_rate = (
            f"mbps_rd={disk_limit},mbps_wr={disk_limit}," if disk_limit else ""
        )
        cpu_limit = cpu_limit or 0

        smbios_uuid = str(uuid.uuid4())

        config_img = self.get_vm_config_file_name(vm_id)

        config = {
            "efidisk0": f"{storage_images}:0,efitype=4m,pre-enrolled-keys=0,import-from={storage_import}:import/arcane_efi.raw",
            "cpu": "host",
            "ostype": "l26",
            "sockets": 1,
            "vmid": vm_id,
            "agent": "1",
            "onboot": 1,
            "name": vm_name,
            "smbios1": f"uuid={smbios_uuid}",
            "boot": "order=scsi0;ide2;net0",
            "numa": 0,
            "memory": tier_config["memory_mb"],
            "tpmstate0": f"{storage_images}:4,version=v2.0",
            "cores": tier_config["cpu_cores"],
            "cpulimit": cpu_limit,
            "bios": "ovmf",
            "scsi0": f"{storage_images}:{tier_config['scsi_gb']},{disk_rate}discard=on,iothread=1,ssd=1",
            "scsi1": f"{storage_images}:0,import-from={storage_import}:import/{config_img}",
            "ide2": f"{storage_iso}:iso/{iso_name},media=cdrom",
            "net0": f"model=virtio,bridge={network_bridge}",
            "scsihw": "virtio-scsi-single",
        }

        if startup_config:
            config["starup"] = startup_config

        return config

    def compute_display_table(self) -> bool:
        return not self.first_time and self.hypervisor_populated

    def watch_display_table(self, old: bool, new: bool) -> None:
        if old == new:
            return

        try:
            table = self.query_one(DataTable)
        except NoMatches:
            return

        table.display = new

    def watch_first_time(self, old: bool, new: bool) -> None:
        if old == new:
            return

        try:
            label = self.query_one("#dialog", Label)
        except NoMatches:
            return

        label.display = new
