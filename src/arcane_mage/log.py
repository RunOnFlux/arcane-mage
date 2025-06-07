from logging import getLogger
from logging.config import dictConfig

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "standard": {
            "format": "{asctime} [{levelname}] {filename} {lineno}: {message}",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "style": "{",
        },
    },
    "handlers": {
        "default": {
            "level": "INFO",
            "formatter": "standard",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",  # Default is stderr
        },
        "file_handler": {
            "level": "INFO",
            "formatter": "standard",
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "arcane_mage.log",
            "mode": "a",
            "maxBytes": 1048576,
            "backupCount": 3,
        },
        "textual_handler": {
            "level": "INFO",
            "formatter": "standard",
            "class": "textual.logging.TextualHandler",
        },
        "rich_handler": {
            "level": "INFO",
            "class": "rich.logging.RichHandler",
        },
    },
    "loggers": {
        "": {  # root logger
            "handlers": [],
            "level": "WARNING",
            "propagate": False,
        },
        "arcane_mage": {
            "handlers": ["textual_handler", "file_handler"],
            "level": "INFO",
            "propagate": False,
            "__main__": {
                "handlers": ["rich_handler"],
                "level": "DEBUG",
                "propagate": False,
            },
        },
    },
}

dictConfig(LOGGING_CONFIG)

log = getLogger(__name__)
