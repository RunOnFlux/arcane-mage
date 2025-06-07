from __future__ import annotations

import asyncio

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.dom import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ProgressBar
from textual.worker import Worker

from ..widgets import Spinner


class ProvisioningInfoScreen(ModalScreen):
    @property
    def finished(self) -> bool:
        return self.completed_steps >= self.total_steps

    @property
    def last_step(self) -> bool:
        return self.completed_steps + 1 == self.total_steps

    def __init__(self, vm_name: str, total_steps: int) -> None:
        super().__init__()

        self.vm_name = vm_name
        self.completed_steps = 0
        self.total_steps = total_steps

        self.failed = False

    def compose(self) -> ComposeResult:
        ok_button = Button("Ok")
        ok_button.visible = False
        error_label = Label("", id="error-label")
        error_label.visible = False
        main_container = Container()
        main_container.border_title = f"Provisioining: {self.vm_name}"

        wait_container = Horizontal(id="wait-container")
        wait_container.visible = False

        with main_container:
            with Container():
                with Vertical():
                    yield Label("", id="task-label")
                    with Horizontal():
                        yield Spinner("line")
                        yield ProgressBar(
                            show_eta=False,
                            id="task-progress",
                            total=self.total_steps,
                        )
            yield error_label
            with Horizontal(id="button-container"):
                yield ok_button
            with wait_container:
                yield Label("Countdown: 0", id="wait-label")

    def show_delay(self, delay: int) -> Worker:
        return self.wait_delay(delay)

    @work(name="wait_delay")
    async def wait_delay(self, delay: int) -> None:
        try:
            container = self.query_one("#wait-container", Horizontal)
        except NoMatches:
            return

        label = self.query_one("#wait-label", Label)
        label.update(f"Countdown: {delay}")

        container.visible = True

        while delay > 0:
            await asyncio.sleep(1)
            delay -= 1
            label.update(f"Countdown: {delay}")

    def update_callback(self, success: bool, message: str) -> None:
        if self.finished or self.failed:
            return

        try:
            progress = self.query_one(ProgressBar)
            label = self.query_one("#task-label", Label)
            spinner = self.query_one(Spinner)
            ok = self.query_one(Button)
        except NoMatches:
            return

        if success:
            progress.advance()
            label.update(message)
        else:
            self.failed = True
            label.update("Error")
            error = self.query_one("#error-label", Label)
            error.update(message)
            error.visible = True

        if not success or self.last_step:
            spinner.stop()
            spinner.visible = False
            ok.visible = True

        self.completed_steps += 1

    def on_button_pressed(self) -> None:
        self.dismiss()
