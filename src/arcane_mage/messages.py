from textual.messages import Message


class ScreenRequested(Message):
    def __init__(self, screen: str) -> None:
        super().__init__()

        self.screen = screen


class UpdateDefaultPage(Message):
    def __init__(self, page: str, value: bool) -> None:
        super().__init__()

        self.page = page
        self.value = value
