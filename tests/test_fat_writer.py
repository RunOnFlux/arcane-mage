from __future__ import annotations

import gzip
import importlib.resources as resources
from pathlib import Path

import pytest

from arcane_mage.fat_writer import BootSector, FAT12Writer


@pytest.fixture
def fat_image_path(tmp_path: Path) -> Path:
    """Extract the bundled FAT12 config image to a temp file."""
    images_ref = resources.files("arcane_mage.images")
    config_gz = images_ref / "arcane_config.raw.gz"

    image_path = tmp_path / "test_fat.raw"
    with config_gz.open("rb") as gz_fh:
        image_path.write_bytes(gzip.decompress(gz_fh.read()))

    return image_path


class TestBootSector:
    async def test_read_from_image(self, fat_image_path: Path):
        bs = await BootSector.read(fat_image_path)

        assert bs.bytes_per_sector == 512
        assert bs.sectors_per_cluster >= 1
        assert bs.root_entries > 0
        assert bs.fat_type in (12, 16)

    async def test_computed_properties(self, fat_image_path: Path):
        bs = await BootSector.read(fat_image_path)

        assert bs.root_dir_sectors > 0
        assert bs.first_fat_sector >= 1
        assert bs.first_root_dir_sector > bs.first_fat_sector
        assert bs.first_data_sector > bs.first_root_dir_sector


class TestFAT12Writer:
    async def test_write_and_verify_file_exists(self, fat_image_path: Path):
        test_data = b"hello flux world"

        async with FAT12Writer(fat_image_path) as writer:
            await writer.write_file("test.txt", test_data)

        # Read the image back and verify the file data is present
        raw = fat_image_path.read_bytes()
        assert test_data in raw

    async def test_write_yaml_config(self, fat_image_path: Path):
        config_data = b"nodes:\n  - system:\n      hostname: test\n"

        async with FAT12Writer(fat_image_path) as writer:
            await writer.write_file("arcane_config.yaml", config_data)

        raw = fat_image_path.read_bytes()
        assert config_data in raw

    async def test_write_creates_directory_entry(self, fat_image_path: Path):
        test_data = b"some content"

        async with FAT12Writer(fat_image_path) as writer:
            bs = writer.boot_sector
            assert bs is not None
            await writer.write_file("myfile.txt", test_data)

        # Verify short name exists in root directory
        raw = fat_image_path.read_bytes()
        # 8.3 format: "MYFILE  TXT"
        assert b"MYFILE" in raw

    async def test_context_manager_required(self):
        writer = FAT12Writer(Path("/nonexistent"))

        with pytest.raises(RuntimeError, match="async context manager"):
            await writer.write_file("test.txt", b"data")

    async def test_empty_file(self, fat_image_path: Path):
        async with FAT12Writer(fat_image_path) as writer:
            await writer.write_file("empty.txt", b"")

        # Should not raise


class TestShortNameGeneration:
    def test_short_name_truncation(self):
        writer = FAT12Writer(Path("/dummy"))
        result = writer._generate_short_name("verylongfilename.txt")

        assert len(result.split(".")[0]) <= 8
        assert result.endswith(".TXT")

    def test_short_name_simple(self):
        writer = FAT12Writer(Path("/dummy"))
        result = writer._generate_short_name("file.txt")

        assert result == "FILE.TXT"

    def test_short_name_no_extension(self):
        writer = FAT12Writer(Path("/dummy"))
        result = writer._generate_short_name("readme")

        assert result == "README"

    def test_lfn_checksum_deterministic(self):
        writer = FAT12Writer(Path("/dummy"))
        cs1 = writer._calculate_lfn_checksum("FILE.TXT")
        cs2 = writer._calculate_lfn_checksum("FILE.TXT")

        assert cs1 == cs2
        assert 0 <= cs1 <= 255
