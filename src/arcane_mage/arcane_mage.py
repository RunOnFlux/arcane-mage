from __future__ import annotations

from functools import partial
from pathlib import Path

from textual import on, work
from textual.app import App
from textual.worker import Worker, WorkerCancelled

from .messages import ScreenRequested, UpdateDefaultPage
from .models import (
    ArcaneCreatorConfig,
    ArcaneOsConfig,
    ArcaneOsConfigGroup,
    HypervisorConfig,
)
from .screens import (
    AddHypervisorScreen,
    ConfirmScreen,
    ProvisioningInfoScreen,
    ProvisionNodeScreen,
    WelcomeScreen,
    WelcomeScreenProxmox,
)


class ArcaneMage(App):
    CSS_PATH = "arcane_mage.tcss"
    AUTO_FOCUS = None
    SCREENS = {
        "welcome": WelcomeScreen,
    }

    def __init__(self, fluxnode_config: str) -> None:
        super().__init__()

        self.config = ArcaneCreatorConfig.from_fs()
        self.fluxnodes = ArcaneOsConfigGroup.from_fs(Path(fluxnode_config))

    def on_mount(self) -> None:
        self.push_screen("welcome")

        if self.config.default_page == "welcome-proxmox":
            screen = WelcomeScreenProxmox(
                self.config.hypervisors, self.fluxnodes, is_homepage=True
            )
            self.install_screen(screen, name="welcome-proxmox")
            self.push_screen("welcome-proxmox")

    def hypervisor_callback(self, hypervisor: HypervisorConfig | None) -> None:
        if not hypervisor:
            return

        # the welcome-proxmox screen has the same hypervisor list
        added = self.config.add_hypervisor(hypervisor)

        if not added:
            self.notify("Unable to add Hypervisor (keyring)")
            return

        screen = self.get_screen("welcome-proxmox", WelcomeScreenProxmox)
        screen.validate_hypervisors(hypervisor)

    def del_hypervisor_callback(
        self, hypervisor: HypervisorConfig, delete: bool
    ) -> None:
        if not delete:
            return

        self.config.remove_hypervisor(hypervisor)
        screen = self.get_screen("welcome-proxmox", WelcomeScreenProxmox)
        screen.validate_hypervisors()

    async def provision_node_callback(
        self, fluxnode: ArcaneOsConfig | None, delete_efi: bool = True
    ) -> Worker | None:
        if not fluxnode or not fluxnode.hypervisor:
            return None

        vm_name = fluxnode.hypervisor.vm_name
        total_steps = 9 if fluxnode.hypervisor.start_on_creation else 8
        info_screen = ProvisioningInfoScreen(
            vm_name=vm_name, total_steps=total_steps
        )

        # this is a bit rugged
        mounted = self.push_screen(info_screen)
        await mounted

        screen = self.get_screen("welcome-proxmox", WelcomeScreenProxmox)
        worker = screen.provision_node(
            fluxnode,
            callback=info_screen.update_callback,
            delete_efi=delete_efi,
        )

        return worker

    @on(ScreenRequested)
    def on_screen_requested(self, event: ScreenRequested) -> None:
        if event.screen == "welcome-proxmox" and not self.is_screen_installed(
            "welcome-proxmox"
        ):
            screen = WelcomeScreenProxmox(
                self.config.hypervisors, self.fluxnodes
            )
            self.install_screen(screen, name="welcome-proxmox")
        else:
            self.notify("Not implemented yet")
            return

        self.push_screen(event.screen)

    @on(UpdateDefaultPage)
    def on_update_default_page(self, event: UpdateDefaultPage) -> None:
        default = event.page if event.value else None

        # this is blocking but meh
        self.config.update_default_page(default)

    @on(WelcomeScreenProxmox.AddHypervisor)
    def on_add_hypervisor(self) -> None:
        self.push_screen(
            AddHypervisorScreen(self.config.use_keyring),
            self.hypervisor_callback,
        )

    @on(WelcomeScreenProxmox.DelHypervisor)
    def on_del_hypervisor(
        self, event: WelcomeScreenProxmox.DelHypervisor
    ) -> None:
        dialog = f"Are you sure?\n\n This will delete hypervisor: {event.hypervisor.url}"
        self.push_screen(
            ConfirmScreen(dialog),
            callback=partial(self.del_hypervisor_callback, event.hypervisor),
        )

    @on(WelcomeScreenProxmox.ProvisionNode)
    def on_provision_node(
        self, event: WelcomeScreenProxmox.ProvisionNode
    ) -> None:
        self.push_screen(
            ProvisionNodeScreen(event.fluxnode),
            self.provision_node_callback,
        )

    @on(WelcomeScreenProxmox.ProvisionAllNodes)
    def on_provision_all_nodes(
        self, event: WelcomeScreenProxmox.ProvisionAllNodes
    ) -> None:
        self.provision_all_nodes(event.delay)

    @work(name="provision_all_nodes")
    async def provision_all_nodes(self, delay: int) -> None:
        screen = self.get_screen("welcome-proxmox", WelcomeScreenProxmox)

        provisionable_nodes = screen.get_provisionable_nodes()

        if not provisionable_nodes.first:
            return

        configured_node = await self.push_screen(
            ProvisionNodeScreen(provisionable_nodes.first, all=True),
            wait_for_dismiss=True,
        )

        if not configured_node:
            return

        hashed_password = configured_node.system.hashed_console

        worker = await self.provision_node_callback(configured_node)

        if not worker:
            return

        result = await worker.wait()

        if not result:
            return

        needs_pop = len(provisionable_nodes) > 1

        if delay and not configured_node == provisionable_nodes.last:
            worker = self.screen.show_delay(delay)

            try:
                await worker.wait()
            except WorkerCancelled:
                # if the user clicks okay... the screen will be dismissed
                needs_pop = False

        if needs_pop:
            # this is the info screen for the first node
            self.pop_screen()

        # we leave the info screen to display on error, otherwise pop
        for fluxnode in provisionable_nodes.rest:
            needs_pop = True
            fluxnode.system.hashed_console = hashed_password

            last = fluxnode == provisionable_nodes.last

            worker = await self.provision_node_callback(
                fluxnode, delete_efi=last
            )

            if not worker:
                break

            result = await worker.wait()

            if not result:
                break

            if delay and not last:
                worker = self.screen.show_delay(delay)

                try:
                    await worker.wait()
                except WorkerCancelled:
                    needs_pop = True

            if needs_pop:
                self.pop_screen()
