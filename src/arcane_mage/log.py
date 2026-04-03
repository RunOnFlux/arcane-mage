import logging
from logging.config import dictConfig

log = logging.getLogger("arcane_mage")

_LOG_FORMAT = "{asctime} [{levelname}] {filename} {lineno}: {message}"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

_BASE_HANDLERS: dict[str, dict[str, str | int]] = {
    "file_handler": {
        "level": "INFO",
        "formatter": "standard",
        "class": "logging.handlers.RotatingFileHandler",
        "filename": "arcane_mage.log",
        "mode": "a",
        "maxBytes": 1_048_576,
        "backupCount": 3,
    },
}


def _apply_config(handlers: dict[str, dict[str, str | int]], handler_names: list[str]) -> None:
    dictConfig({
        "version": 1,
        "disable_existing_loggers": True,
        "formatters": {
            "standard": {
                "format": _LOG_FORMAT,
                "datefmt": _LOG_DATEFMT,
                "style": "{",
            },
        },
        "handlers": _BASE_HANDLERS | handlers,
        "loggers": {
            "": {
                "handlers": [],
                "level": "WARNING",
                "propagate": False,
            },
            "arcane_mage": {
                "handlers": handler_names,
                "level": "INFO",
                "propagate": False,
            },
        },
    })


def configure_tui_logging() -> None:
    """Configure logging for the Textual TUI application.

    Sets up a TextualHandler for live console output and a
    RotatingFileHandler for persistent logs. Should only be called
    from the TUI entry point, not by library consumers.
    """
    _apply_config(
        handlers={
            "textual_handler": {
                "level": "INFO",
                "formatter": "standard",
                "class": "textual.logging.TextualHandler",
            },
        },
        handler_names=["textual_handler", "file_handler"],
    )


def configure_cli_logging() -> None:
    """Configure logging for CLI (non-TUI) usage.

    Sets up a StreamHandler for terminal output and a RotatingFileHandler
    for persistent logs.
    """
    _apply_config(
        handlers={
            "stream_handler": {
                "level": "INFO",
                "formatter": "standard",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
            },
        },
        handler_names=["stream_handler", "file_handler"],
    )
