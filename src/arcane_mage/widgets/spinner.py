from rich.spinner import Spinner as RichSpinner
from textual.app import RenderableType
from textual.widgets import Static


class IntervalUpdater(Static):
    def __init__(self, renderable_object: RenderableType) -> None:
        super().__init__(renderable_object)

    def on_mount(self) -> None:
        self.interval_update = self.set_interval(1 / 15, self.refresh)

    def stop(self) -> None:
        self.interval_update.stop()


class Spinner(IntervalUpdater):
    def __init__(self, style: str) -> None:
        super().__init__(RichSpinner(style))
