from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import var
from textual.screen import ModalScreen
from textual.validation import ValidationResult, Validator
from textual.widgets import Button, Input, Label, Markdown

from ..models import ArcaneOsConfig
from ..password import MIN_PASSWORD_LENGTH, HashedPassword, password_problem


class PasswordValidator(Validator):
    def __init__(self) -> None:
        super().__init__()

        self.previous: str | None = None

    def validate(self, value: str) -> ValidationResult:
        if self.previous is not None and len(value) < len(self.previous):
            self.previous = value
            return self.success()

        self.previous = value
        problem = password_problem(value)
        return self.failure(problem) if problem else self.success()


class ProvisionNodeScreen(ModalScreen[ArcaneOsConfig | None]):
    password_valid = var(False)

    def __init__(self, fluxnode: ArcaneOsConfig, all: bool = False, needs_password: bool | None = None) -> None:
        """needs_password: whether a node this screen provisions takes a console password
        (by default, whether `fluxnode` has a console login)."""
        super().__init__()

        self.fluxnode = fluxnode
        self.all = all
        self.needs_password = fluxnode.system.console_login if needs_password is None else needs_password
        # the password given, hashed: for every node of this provisioning that has a console login
        self.hashed_password: str | None = None

    def on_screen_resume(self) -> None:
        if self.needs_password:
            self.query_one(Input).focus()

    def compose(self) -> ComposeResult:
        hyper = self.fluxnode.hypervisor
        name = hyper.vm_name if hyper else self.fluxnode.system.hostname
        title = "Provision: All nodes" if self.all else f"Provision: {name}"

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
        if not self.needs_password:
            dialog = dialog.replace("Add console password and click", "Click", 1)
            dialog += "\n\nNo console login is configured (`console_login: false`): no password is asked for."

        with container:
            yield Markdown(dialog)
            if self.needs_password:
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
                if self.needs_password:
                    yield Button("Reveal", id="reveal")
                yield Button("Provision", id="provision", disabled=self.needs_password)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "provision":
            if self.needs_password:
                self.hashed_password = HashedPassword(self.query_one(Input).value).hash()  # thread?
            if self.fluxnode.system.console_login:
                self.fluxnode.system.hashed_console = self.hashed_password
            self.dismiss(self.fluxnode)
        elif event.button.id == "reveal":
            input = self.query_one(Input)
            input.password = not input.password
            event.button.label = "Reveal" if input.password else "Hide"
        else:
            self.dismiss(None)

    def on_input_changed(self, event: Input.Changed) -> None:
        result = event.validation_result
        display = bool(result and result.failures)

        label = self.query_one("#error-label", Label)

        label.visible = display

        msg = "\n".join(result.failure_descriptions) if result and display else ""
        label.update(msg)

        provision_disabled = display or len(event.input.value) < MIN_PASSWORD_LENGTH

        self.query_one("#provision", Button).disabled = provision_disabled
