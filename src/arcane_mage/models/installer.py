from __future__ import annotations

from pathlib import Path

import aiofiles
import yaml
from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass as py_dataclass


@py_dataclass
class InstallerConfig:
    """Post-installation behavior settings (reboot mode, firmware options)."""

    auto_reboot: bool = True
    reboot_to_firmware: bool = False
    reboot_to_boot_menu: bool = False

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")


@py_dataclass
class MetricsAppConfig:
    """Configuration for the on-device metrics display application."""

    poweroff_screen: int = 0
    theme: str = "flexoki"

    def to_dict(self) -> dict:
        return TypeAdapter(type(self)).dump_python(self, mode="json")

    async def write_config(self, file_path: Path) -> bool:
        config = self.to_dict()

        writeable_config = yaml.dump(
            config,
            sort_keys=False,
            default_flow_style=False,
        )

        async with aiofiles.open(file_path, "w") as f:
            await f.write(writeable_config)

        return True
