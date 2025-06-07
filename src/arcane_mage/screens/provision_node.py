from __future__ import annotations

from itertools import groupby

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import var
from textual.screen import ModalScreen
from textual.validation import ValidationResult, Validator
from textual.widgets import Button, Input, Label, Markdown

from ..models import ArcaneOsConfig
from ..password import HashedPassword


class PasswordValidator(Validator):
    def __init__(self) -> None:
        super().__init__()

        self.previous: str | None = None

    def validate(self, password: str) -> ValidationResult:
        if self.previous is not None and len(password) < len(self.previous):
            self.previous = password
            return self.success()

        if len(password) < 12:
            self.previous = password
            return self.failure("Password must be a minimum of 12 characters.")

        groups = groupby(password)
        consecutives = [
            letter for letter, group in groups if len(list(group)) > 3
        ]
        if any(consecutives):
            self.previous = password
            return self.failure("Only 3 consecutive repeat characters allowed.")

        self.previous = password
        return self.success()


class ProvisionNodeScreen(ModalScreen[ArcaneOsConfig | None]):
    password_valid = var(False)

    def __init__(self, fluxnode: ArcaneOsConfig, all: bool = False) -> None:
        super().__init__()

        self.fluxnode = fluxnode
        self.all = all

    def on_screen_resume(self) -> None:
        self.query_one(Input).focus()

    def compose(self) -> ComposeResult:
        title = (
            "Provision: All nodes"
            if self.all
            else f"Provision: {self.fluxnode.hypervisor.vm_name}"
        )

        container = Container()
        container.border_title = title

        error_label = Label("", id="error-label")
        error_label.visible = False

        single_dialog = """Add console password and click Provision to configure this fluxnode.
The following will occur:
* Validate Hypervisor has required iso, network, and resources
* Get the next available vmid from the Hypervisor
* Create the Fluxnode on the Hypervisor
* Start the node (if configured)
"""
        all_dialog = """Add console password and click Provision to configure all fluxnodes.
The following will occur:
* Validate Hypervisor has required iso, network, and resources
* Get the next available vmid from the Hypervisor for each node
* Create the Fluxnodes on the Hypervisor
* Start each node syncronously (if configured)
* Wait an optional delay (in seconds) between each Fluxnode start"""

        dialog = all_dialog if self.all else single_dialog

        with container:
            yield Markdown(dialog)
            with Horizontal(id="console-container"):
                yield Label("Console Pass:", classes="text-label")
                yield Input(
                    placeholder="Enter Console Password",
                    password=True,
                    validators=[PasswordValidator()],
                )
            yield error_label
            with Horizontal(id="button-container"):
                yield Button("Cancel", id="cancel")
                yield Button("Reveal", id="reveal")
                yield Button("Provision", id="provision", disabled=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        input = self.query_one(Input)

        if event.button.id == "provision":
            hasher = HashedPassword(input.value)
            self.fluxnode.system.hashed_console = hasher.hash()  # thread?
            self.dismiss(self.fluxnode)
        elif event.button.id == "reveal":
            input.password = not input.password
            event.button.label = "Reveal" if input.password else "Hide"
        else:
            self.dismiss(None)

    def on_input_changed(self, event: Input.Changed) -> None:
        display = bool(event.validation_result.failures)

        label = self.query_one("#error-label", Label)

        label.visible = display

        msg = (
            "\n".join(event.validation_result.failure_descriptions)
            if display
            else ""
        )
        label.update(msg)

        provision_disabled = display or len(event.input.value) < 12

        self.query_one("#provision", Button).disabled = provision_disabled
