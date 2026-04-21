from __future__ import annotations

from collections.abc import Callable

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Grid, Horizontal, Vertical
from textual.css.query import NoMatches
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
from ..messages import UpdateDefaultPage
from ..models import ArcaneOsConfig, ArcaneOsConfigGroup, HypervisorConfig
from ..provisioner import Provisioner, get_latest_iso_version


class WelcomeScreenProxmox(Screen):
    class AddHypervisor(Message): ...

    class EditHypervisor(Message):
        def __init__(self, hypervisor: HypervisorConfig) -> None:
            super().__init__()
            self.hypervisor = hypervisor

    class DelHypervisor(Message):
        def __init__(self, hypervisor: HypervisorConfig) -> None:
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

    provisioner: Provisioner

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

    def on_screen_resume(self) -> None:
        self.app.set_focus(None)

    def on_load(self) -> None:
        self.fetch_latest_iso()

    def on_mount(self) -> None:
        self.validate_hypervisors()

    def validate_hypervisors(self, hypervisor: HypervisorConfig | None = None) -> None:
        self.first_time = not bool(self.hypervisors)
        self.hypervisor_populated = False

        select = self.query_one(Select)
        options = [(x.display_label, x.url) for x in self.hypervisors]

        if hypervisor:
            select.set_options(options)
            select.value = hypervisor.url
        elif options:
            select.set_options(options)
            select.value = options[0][1]
        else:
            select.set_options(options)
            select.value = Select.BLANK

    @work(name="populate_hypervisor")
    async def populate_hypervisor_api(self, hypervisor: HypervisorConfig) -> None:
        empty = ArcaneOsConfigGroup()
        empty_provisioned: dict[str, list[dict]] = {}

        provisioner = await Provisioner.from_hypervisor_config(hypervisor)

        if not provisioner:
            self.build_fluxnode_table(empty, empty_provisioned)
            self.hypervisor_populated = True
            self.notify("Unable to connect to Hypervisor", severity="error")
            return

        self.provisioner = provisioner

        # Update cluster info display
        cluster_label = self.query_one("#cluster-info", Label)
        if provisioner.cluster:
            c = provisioner.cluster
            quorum_str = "ok" if c.has_quorum else "LOST"
            node_count = len(c.nodes)
            cluster_label.update(
                f"Cluster: {c.cluster_name} | Nodes: {node_count} | Quorum: {quorum_str}"
            )
            cluster_label.display = True

            if not c.has_quorum:
                self.notify("Cluster has lost quorum — provisioning disabled", severity="warning")
                sync_btn = self.query_one("#sync-all", Button)
                sync_btn.disabled = True
        else:
            cluster_label.display = False

        discovery = await provisioner.discover_nodes(self.fluxnodes)

        if not discovery:
            self.build_fluxnode_table(empty, empty_provisioned)
            self.hypervisor_populated = True
            self.notify("Error discovering hypervisor nodes", severity="error")
            return

        self.build_fluxnode_table(discovery.nodes, discovery.provisioned_vms)
        self.hypervisor_populated = True

    def build_fluxnode_table(self, fluxnodes: ArcaneOsConfigGroup, provisioned_nodes: dict[str, list[dict]]) -> None:
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
            {"label": "Status", "key": "status"},
            {"label": "Provisioned", "key": "provisioned"},
        ]

        table.clear(columns=True)
        for column in columns:
            table.add_column(**column)

        cluster = getattr(self, "provisioner", None) and self.provisioner.cluster

        for fluxnode in fluxnodes:
            hyper = fluxnode.hypervisor
            row_key = f"{hyper.node}:{hyper.vm_name}"

            hypervisor_nodes = provisioned_nodes.get(hyper.node) or []

            is_provisioned = bool(
                next(
                    filter(
                        lambda x: x.get("name") == hyper.vm_name,
                        hypervisor_nodes,
                    ),
                    None,
                )
            )

            node_status = ""
            if cluster:
                node_status = "online" if cluster.is_node_online(hyper.node) else "offline"

            table.add_row(*fluxnode.as_row(), node_status, is_provisioned, key=row_key)

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
                    yield Button("\u21b0", id="back", classes="icon-button", tooltip="Back")
                    yield Button("Add", id="add-hypervisor", tooltip="Add Hypervisor")
                    yield Button("Edit", id="edit-hypervisor", tooltip="Edit Hypervisor")
                    yield Button("Del", id="del-hypervisor", tooltip="Delete Hypervisor")
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
                    yield Button("X", id="exit", classes="icon-button", tooltip="Exit")
            yield Rule()
            cluster_info = Label("", id="cluster-info")
            cluster_info.display = False
            yield cluster_info
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
        elif event.button.id == "edit-hypervisor":
            select = self.query_one(Select)
            if select.value == Select.BLANK:
                return
            if hyper := self.get_hypervisor_by_url(select.value):
                self.post_message(WelcomeScreenProxmox.EditHypervisor(hyper))
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

        self.post_message(UpdateDefaultPage("welcome-proxmox", event.switch.value))

    def get_provisionable_nodes(self) -> ArcaneOsConfigGroup:
        try:
            dt = self.query_one(DataTable)
        except NoMatches:
            return ArcaneOsConfigGroup()

        nodes: list[ArcaneOsConfig] = []

        for row_key in dt.rows:
            row = dt.get_row(row_key)

            if not row[-1]:  # not provisioned
                fluxnode = self.fluxnodes.get_node_by_vm_name(*row_key.value.split(":"))

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

        fluxnode = self.fluxnodes.get_node_by_vm_name(*event.row_key.value.split(":"))

        if not fluxnode:
            return

        self.post_message(WelcomeScreenProxmox.ProvisionNode(fluxnode))

    def get_hypervisor_by_url(self, url: str) -> HypervisorConfig | None:
        return next(filter(lambda x: x.url == url, self.hypervisors), None)

    def on_select_changed(self, event: Select.Changed) -> None:
        if (hyper_url := event.select.value) == Select.BLANK:
            return

        if hypervisor := self.get_hypervisor_by_url(hyper_url):
            self.populate_hypervisor_api(hypervisor)

    @work(name="get_latest_iso_version")
    async def fetch_latest_iso(self) -> None:
        self.latest_iso = await get_latest_iso_version()

    @work(name="provision_node")
    async def provision_node(
        self,
        fluxnode: ArcaneOsConfig,
        callback: Callable[[bool, str], None],
        delete_efi: bool = True,
        skip_efi_upload: bool = False,
    ) -> bool:
        result = await self.provisioner.provision_node(
            fluxnode, callback, delete_efi, skip_efi_upload=skip_efi_upload
        )

        # Update UI after successful provisioning
        if result and fluxnode.hypervisor:
            hv = fluxnode.hypervisor
            row_key = f"{hv.node}:{hv.vm_name}"
            table = self.query_one(DataTable)
            table.update_cell(row_key, "provisioned", True)

        return result

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
