from __future__ import annotations

from pathlib import Path

import yaml

from arcane_mage.models import InstallerConfig, MetricsAppConfig


class TestInstallerConfig:
    def test_defaults(self):
        config = InstallerConfig()

        assert config.auto_reboot is True
        assert config.reboot_to_firmware is False
        assert config.reboot_to_boot_menu is False

    def test_from_dict(self):
        data = {"auto_reboot": False, "reboot_to_firmware": True}
        config = InstallerConfig(**data)

        assert config.auto_reboot is False
        assert config.reboot_to_firmware is True
        assert config.reboot_to_boot_menu is False

    def test_from_dict_ignores_unknown_keys(self):
        data = {"auto_reboot": True, "unknown_key": "value"}
        config = InstallerConfig(**data)

        assert config.auto_reboot is True

    def test_to_dict_roundtrip(self):
        config = InstallerConfig(auto_reboot=False, reboot_to_firmware=True)
        result = config.to_dict()

        assert result == {
            "auto_reboot": False,
            "reboot_to_firmware": True,
            "reboot_to_boot_menu": False,
        }


class TestMetricsAppConfig:
    def test_defaults(self):
        config = MetricsAppConfig()

        assert config.poweroff_screen == 0
        assert config.theme == "flexoki"

    def test_from_dict(self):
        data = {"poweroff_screen": 30, "theme": "dark"}
        config = MetricsAppConfig(**data)

        assert config.poweroff_screen == 30
        assert config.theme == "dark"

    def test_to_dict_roundtrip(self):
        config = MetricsAppConfig(poweroff_screen=60, theme="light")
        result = config.to_dict()

        assert result == {"poweroff_screen": 60, "theme": "light"}

    async def test_write_config(self, tmp_path: Path):
        config = MetricsAppConfig(poweroff_screen=30, theme="dark")
        file_path = tmp_path / "metrics.yaml"

        result = await config.write_config(file_path)

        assert result is True
        assert file_path.exists()

        written = yaml.safe_load(file_path.read_text())
        assert written["poweroff_screen"] == 30
        assert written["theme"] == "dark"
