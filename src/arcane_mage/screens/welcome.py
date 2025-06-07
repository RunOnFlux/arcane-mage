from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label

from arcane_mage.messages import ScreenRequested


class WelcomeScreen(Screen):
    TITLE = "Arcane Creator"

    def __init__(
        self,
    ) -> None:
        super().__init__()

    def on_screen_resume(self):
        self.app.set_focus(None)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container():
            with Vertical():
                yield Label(
                    "Welcome to the Arcane Creator. Select your operation mode."
                )
            with Horizontal():
                yield Button(
                    "Proxmox Provisioner",
                    id="welcome-proxmox",
                    tooltip="Provision a Proxmox Arcane Fluxnode",
                    classes="action-button",
                )
                yield Button(
                    "Multicast Provisioner",
                    id="welcome-multicast",
                    tooltip="Configure an Arcane Fluxnode via Multicast",
                    classes="action-button",
                )
                yield Button(
                    "Usb Stick Creator",
                    id="welcome-usb",
                    tooltip="Provision a Bare Metal Arcane Fluxnode",
                    classes="action-button",
                )
            with Horizontal():
                yield Button("Exit", id="exit")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not event.button.id:
            return

        if event.button.id == "exit":
            self.app.exit()

        self.post_message(ScreenRequested(event.button.id))
