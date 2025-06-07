from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, action: str) -> None:
        super().__init__()

        self.action = action

    def compose(self) -> ComposeResult:
        with Container():
            yield Label(
                self.action,
                id="info-label",
            )

            with Horizontal(id="button-container"):
                yield Button("Cancel", id="cancel")
                yield Button("Ok", id="ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        confirmed = event.button.id == "ok"
        self.dismiss(confirmed)


if __name__ == "__main__":
    from textual.app import App
    from textual.screen import Screen

    class Dummy(Screen):
        def compose(self) -> ComposeResult:
            yield Label("background")

    class TestApp(App):
        CSS_PATH = "../frontend.tcss"

        def on_mount(self) -> None:
            self.push_screen(Dummy())
            self.push_screen(ConfirmScreen("Are you sure?"))

    app = TestApp()
    app.run()
