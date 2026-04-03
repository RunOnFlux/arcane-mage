from __future__ import annotations

import arcane_mage


class TestPublicApi:
    def test_all_exports_are_importable(self):
        for name in arcane_mage.__all__:
            obj = getattr(arcane_mage, name)
            assert obj is not None, f"{name} is None"

    def test_all_matches_dir(self):
        """Ensure __all__ covers every public name we export."""
        exported = set(arcane_mage.__all__)
        public_attrs = {name for name in dir(arcane_mage) if not name.startswith("_")}

        # __all__ should be a subset of public attrs
        missing_from_module = exported - public_attrs
        assert not missing_from_module, f"In __all__ but not importable: {missing_from_module}"

    def test_key_classes_present(self):
        assert hasattr(arcane_mage, "ProxmoxApi")
        assert hasattr(arcane_mage, "Provisioner")
        assert hasattr(arcane_mage, "ArcaneOsConfig")
        assert hasattr(arcane_mage, "ArcaneOsConfigGroup")
        assert hasattr(arcane_mage, "Identity")
        assert hasattr(arcane_mage, "HashedPassword")
        assert hasattr(arcane_mage, "ApiResponse")
        assert hasattr(arcane_mage, "ParsedToken")
        assert hasattr(arcane_mage, "ParsedUserPass")
        assert hasattr(arcane_mage, "ResolvedConnection")

    def test_internal_types_not_exported(self):
        """Helpers and internal types should not be in the top-level API."""
        assert "do_http" not in arcane_mage.__all__
        assert "exec_binary" not in arcane_mage.__all__
        assert "FAT12Writer" not in arcane_mage.__all__
        assert "ConfigParserDict" not in arcane_mage.__all__

    def test_helpers_importable_from_submodule(self):
        """Internal utilities remain importable from their submodules."""
        from arcane_mage.fat_writer import FAT12Writer
        from arcane_mage.helpers import do_http, exec_binary

        assert callable(do_http)
        assert callable(exec_binary)
        assert FAT12Writer is not None
