from __future__ import annotations

from typing import Literal

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Grid, Horizontal, Vertical
from textual.dom import NoMatches
from textual.reactive import var
from textual.screen import ModalScreen
from textual.validation import URL
from textual.widgets import Button, Footer, Input, Label, Select, Switch

from ..models import HypervisorConfig
from ..proxmox import ProxmoxApi


class AddHypervisorScreen(ModalScreen[HypervisorConfig | None]):
    url_valid = var(False)
    creds_valid = var(False)
    form_valid = var(False)
    hypervisor_valid = var(False)

    has_message = False

    url = var("")
    creds = var("")

    auth_type: var[Literal["token", "userpass"]] = var("token")

    def __init__(self, use_keyring: bool, existing: HypervisorConfig | None = None) -> None:
        super().__init__()

        self.use_keyring = use_keyring
        self.existing = existing
        self._resolved_name: str | None = existing.name if existing else None

    def on_screen_resume(self):
        self.app.set_focus(None)

    def compose(self) -> ComposeResult:
        title = "Edit Hypervisor" if self.existing else "Add Hypervisor"
        container = Container()
        container.border_title = title

        info_label = Label("", id="info-label")
        info_label.visible = False

        initial_auth = self.existing.auth_type if self.existing else "token"
        initial_url = self.existing.url if self.existing else ""
        initial_creds = ""
        if self.existing:
            initial_creds = self.existing.real_credential() or ""

        auth_labels = {"token": "API Token:", "userpass": "User / Pass:"}
        auth_placeholders = {
            "token": "USER@REALM!TOKENID=UUID",
            "userpass": "username:password",
        }

        with container:
            with Grid():
                yield Label("Auth Type:", classes="text-label")
                yield Select(
                    options=[("Token", "token"), ("User / Pass", "userpass")],
                    value=initial_auth,
                    allow_blank=False,
                    id="auth-type",
                )
                yield Label("API URL:", classes="text-label")
                yield Input(
                    value=initial_url,
                    placeholder="https://your.server:8006",
                    validators=[URL()],
                    id="url-input",
                )
                yield Label(auth_labels[initial_auth], classes="text-label", id="auth-label")
                yield Input(
                    value=initial_creds,
                    placeholder=auth_placeholders[initial_auth],
                    password=True,
                    id="auth-input",
                )
                with Horizontal():
                    yield Button("Reveal", id="reveal")
                    yield Label("Store in Keychain:", classes="text-label")
                    yield Switch(value=self.use_keyring)
            with Vertical():
                yield info_label
                with Horizontal():
                    yield Button("Cancel", id="cancel")
                    yield Button("Validate", id="validate", disabled=True)
                    yield Button("Save", id="save", disabled=True)

        yield Footer()

    def on_mount(self) -> None:
        if self.existing:
            self.auth_type = self.existing.auth_type
            self.url = self.existing.url
            self.creds = self.existing.real_credential() or ""

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "url-input":
            self.url = event.value
        elif event.input.id == "auth-input":
            self.creds = event.value

        if self.has_message:
            self.clear_message()

        # Reset validation when fields change
        if self.hypervisor_valid:
            self.hypervisor_valid = False

    def compute_url_valid(self) -> bool:
        try:
            valid = self.query_one("#url-input", Input).is_valid
        except NoMatches:
            return False

        return bool(self.url) and valid

    def compute_creds_valid(self) -> bool:
        try:
            valid = self.query_one("#auth-input", Input).is_valid
        except NoMatches:
            return False

        return bool(self.creds) and valid

    def compute_form_valid(self) -> bool:
        return self.url_valid and self.creds_valid

    def watch_form_valid(self, old: bool, new: bool) -> None:
        if old == new:
            return

        try:
            button = self.query_one("#validate", Button)
        except NoMatches:
            return

        button.disabled = not self.form_valid

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "validate":
            self.validate_hypervisor()
        elif event.button.id == "save":
            config = HypervisorConfig(self.url, self.auth_type, self.creds, name=self._resolved_name)
            self.dismiss(config)
        elif event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "reveal":
            input = self.query_one("#auth-input", Input)
            input.password = not input.password

    @work(name="validate_hypervisor")
    async def validate_hypervisor(self) -> None:
        client: ProxmoxApi | None = None

        if self.auth_type == "token":
            token = ProxmoxApi.parse_token(self.creds)

            if token:
                client = ProxmoxApi.from_token(self.url, token)
            else:
                self.set_message(
                    msg_type="error", value="Unable to parse token"
                )

        elif self.auth_type == "userpass":
            user_pass = ProxmoxApi.parse_user_pass(self.creds)

            if user_pass:
                client = await ProxmoxApi.from_user_pass(self.url, user_pass)
            else:
                self.set_message(
                    msg_type="error", value="Unable to parse user / pass"
                )

        if not client:
            self.hypervisor_valid = False
            return

        async with client.session():
            res = await client.get_hypervisor_nodes()

        if res.unauthorized:
            self.set_message(msg_type="error", value="Unauthorized")
            self.hypervisor_valid = False
        elif res.error:
            self.set_message(msg_type="error", value=res.error)
            self.hypervisor_valid = False
        else:
            # Extract node name from API response
            if isinstance(res.payload, list):
                names = [n.get("node", "") for n in res.payload if n.get("node")]
                if names:
                    self._resolved_name = names[0] if len(names) == 1 else ",".join(sorted(names))

            self.set_message(msg_type="info", value="Validated")
            self.hypervisor_valid = True

    def clear_message(self) -> None:
        try:
            label = self.query_one("#info-label", Label)
        except NoMatches:
            return

        label.visible = False
        label.update("")
        label.set_class(False, "--info-error")
        label.set_class(False, "--info-success")

        self.has_message = False

    def set_message(
        self, msg_type: Literal["error", "info"], value: str
    ) -> None:
        try:
            label = self.query_one("#info-label", Label)
        except NoMatches:
            return

        label_cls_add = (
            "--info-error" if msg_type == "error" else "--info-success"
        )
        label_cls_remove = (
            "--info-success" if msg_type == "error" else "--info-error"
        )

        label.update(value)
        label.set_class(True, label_cls_add)
        label.set_class(False, label_cls_remove)
        label.visible = True

        self.has_message = True

    def on_select_changed(self, event: Select.Changed) -> None:
        self.clear_message()

        select = event.select

        if select.id != "auth-type" or select.value == self.auth_type:
            return

        self.auth_type = select.value

    def watch_auth_type(self, new: Literal["token", "userpass"]) -> None:
        labels = {"token": "API Token:", "userpass": "User / Pass:"}
        placeholders = {
            "token": "USER@REALM!TOKENID=UUID",
            "userpass": "username:password",
        }

        try:
            label = self.query_one("#auth-label", Label)
            input = self.query_one("#auth-input", Input)
        except NoMatches:
            return

        label.update(labels[new])
        input.placeholder = placeholders[new]

    def watch_hypervisor_valid(self, old: bool, new: bool) -> None:
        if old == new:
            return

        try:
            save = self.query_one("#save", Button)
            validate = self.query_one("#validate", Button)
        except NoMatches:
            return

        save.disabled = not new
        validate.disabled = new
