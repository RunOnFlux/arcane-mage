"""Minimal async FAT12/16 writer for single-file writes.

Based on pyfatfs (https://github.com/nathanhi/pyfatfs) but modernized for async I/O
and simplified for our specific use case: writing a single YAML config file.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from pathlib import Path

import aiofiles


@dataclass
class BootSector:
    """FAT12/16 boot sector information."""

    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    num_fats: int
    root_entries: int
    total_sectors: int
    sectors_per_fat: int
    fat_type: int  # 12 or 16

    @classmethod
    async def read(cls, image_path: Path) -> BootSector:
        """Read and parse boot sector from FAT image."""
        # Read first sector
        async with aiofiles.open(image_path, "rb") as f:
            data = await f.read(512)

        # Parse BPB (BIOS Parameter Block)
        bpb = struct.unpack_from("<3s8sHBHBHHBHHHLL", data, 0)

        bytes_per_sector = bpb[2]
        sectors_per_cluster = bpb[3]
        reserved_sectors = bpb[4]
        num_fats = bpb[5]
        root_entries = bpb[6]
        total_sectors_16 = bpb[7]
        sectors_per_fat = bpb[9]
        total_sectors_32 = bpb[13]

        total_sectors = total_sectors_16 if total_sectors_16 != 0 else total_sectors_32

        # Determine FAT type
        root_dir_sectors = ((root_entries * 32) + (bytes_per_sector - 1)) // bytes_per_sector
        data_sectors = total_sectors - (reserved_sectors + (num_fats * sectors_per_fat) + root_dir_sectors)
        total_clusters = data_sectors // sectors_per_cluster

        if total_clusters < 4085:
            fat_type = 12
        elif total_clusters < 65525:
            fat_type = 16
        else:
            fat_type = 32

        return cls(
            bytes_per_sector=bytes_per_sector,
            sectors_per_cluster=sectors_per_cluster,
            reserved_sectors=reserved_sectors,
            num_fats=num_fats,
            root_entries=root_entries,
            total_sectors=total_sectors,
            sectors_per_fat=sectors_per_fat,
            fat_type=fat_type,
        )

    @property
    def root_dir_sectors(self) -> int:
        """Calculate root directory sectors."""
        return ((self.root_entries * 32) + (self.bytes_per_sector - 1)) // self.bytes_per_sector

    @property
    def first_fat_sector(self) -> int:
        """First FAT sector offset."""
        return self.reserved_sectors

    @property
    def first_root_dir_sector(self) -> int:
        """First root directory sector offset."""
        return self.reserved_sectors + (self.num_fats * self.sectors_per_fat)

    @property
    def first_data_sector(self) -> int:
        """First data sector offset."""
        return self.first_root_dir_sector + self.root_dir_sectors


class FAT12Writer:
    """Async FAT12/16 writer for single files."""

    def __init__(self, image_path: Path):
        """Initialize writer for given FAT image."""
        self.image_path = image_path
        self.boot_sector: BootSector | None = None

    async def __aenter__(self):
        """Async context manager entry."""
        self.boot_sector = await BootSector.read(self.image_path)
        return self

    async def __aexit__(self, *args):
        """Async context manager exit."""
        pass

    async def write_file(self, filename: str, data: bytes) -> None:
        """Write a file to the FAT filesystem with VFAT long filename support.

        Args:
            filename: Any filename (long names supported via VFAT LFN)
            data: File contents
        """
        if not self.boot_sector:
            raise RuntimeError("BootSector not loaded")

        bs = self.boot_sector

        # Read entire image into memory (it's only 4MB)
        async with aiofiles.open(self.image_path, "rb") as f:
            image_data = bytearray(await f.read())

        # Calculate how many LFN entries we need
        lfn_entries_needed = (len(filename) + 12) // 13  # 13 chars per LFN entry
        total_entries_needed = lfn_entries_needed + 1  # +1 for short name entry

        # Find contiguous free directory entries
        root_dir_offset = bs.first_root_dir_sector * bs.bytes_per_sector
        dir_entry_offset = None

        for i in range(bs.root_entries - total_entries_needed + 1):
            offset = root_dir_offset + (i * 32)

            # Check if we have enough contiguous free entries
            all_free = True
            for j in range(total_entries_needed):
                check_offset = offset + (j * 32)
                first_byte = image_data[check_offset]
                if first_byte != 0x00 and first_byte != 0xE5:
                    all_free = False
                    break

            if all_free:
                dir_entry_offset = offset
                break

        if dir_entry_offset is None:
            raise RuntimeError("No free directory entries")

        # Find free clusters
        clusters_needed = (len(data) + (bs.bytes_per_sector * bs.sectors_per_cluster) - 1) // (
            bs.bytes_per_sector * bs.sectors_per_cluster
        )
        free_clusters = await self._find_free_clusters(image_data, clusters_needed)

        if len(free_clusters) < clusters_needed:
            raise RuntimeError("Not enough free space")

        # Write data to clusters
        bytes_written = 0
        for cluster_num in free_clusters:
            cluster_offset = (
                (bs.first_data_sector + ((cluster_num - 2) * bs.sectors_per_cluster))
                * bs.bytes_per_sector
            )
            cluster_size = bs.bytes_per_sector * bs.sectors_per_cluster
            chunk = data[bytes_written : bytes_written + cluster_size]

            image_data[cluster_offset : cluster_offset + len(chunk)] = chunk
            bytes_written += len(chunk)

        # Update FAT chain
        await self._update_fat_chain(image_data, free_clusters)

        # Create LFN entries + short name entry
        await self._create_lfn_entries(image_data, dir_entry_offset, filename, len(data), free_clusters[0])

        # Write modified image back
        async with aiofiles.open(self.image_path, "wb") as f:
            await f.write(bytes(image_data))

    async def _find_free_clusters(self, image_data: bytearray, count: int) -> list[int]:
        """Find free clusters in FAT."""
        bs = self.boot_sector
        fat_offset = bs.first_fat_sector * bs.bytes_per_sector
        free_clusters = []

        # Start from cluster 2 (first usable data cluster)
        for cluster in range(2, 4085 if bs.fat_type == 12 else 65525):
            value = self._read_fat_entry(image_data, fat_offset, cluster)

            if value == 0:  # Free cluster
                free_clusters.append(cluster)
                if len(free_clusters) >= count:
                    break

        return free_clusters

    def _read_fat_entry(self, image_data: bytearray, fat_offset: int, cluster: int) -> int:
        """Read FAT entry for given cluster."""
        bs = self.boot_sector

        if bs.fat_type == 12:
            # FAT12: 1.5 bytes per entry
            byte_offset = fat_offset + (cluster * 3) // 2
            if cluster % 2 == 0:
                value = struct.unpack_from("<H", image_data, byte_offset)[0] & 0xFFF
            else:
                value = struct.unpack_from("<H", image_data, byte_offset)[0] >> 4
        else:  # FAT16
            byte_offset = fat_offset + (cluster * 2)
            value = struct.unpack_from("<H", image_data, byte_offset)[0]

        return value

    def _write_fat_entry(self, image_data: bytearray, fat_offset: int, cluster: int, value: int) -> None:
        """Write FAT entry for given cluster."""
        bs = self.boot_sector

        if bs.fat_type == 12:
            byte_offset = fat_offset + (cluster * 3) // 2
            if cluster % 2 == 0:
                existing = struct.unpack_from("<H", image_data, byte_offset)[0]
                new_value = (existing & 0xF000) | (value & 0xFFF)
                struct.pack_into("<H", image_data, byte_offset, new_value)
            else:
                existing = struct.unpack_from("<H", image_data, byte_offset)[0]
                new_value = (existing & 0x000F) | ((value & 0xFFF) << 4)
                struct.pack_into("<H", image_data, byte_offset, new_value)
        else:  # FAT16
            byte_offset = fat_offset + (cluster * 2)
            struct.pack_into("<H", image_data, byte_offset, value)

    async def _update_fat_chain(self, image_data: bytearray, clusters: list[int]) -> None:
        """Update FAT chain for allocated clusters."""
        bs = self.boot_sector
        eoc = 0xFFF if bs.fat_type == 12 else 0xFFFF

        # Update all FAT copies
        for fat_num in range(bs.num_fats):
            fat_offset = (bs.first_fat_sector + (fat_num * bs.sectors_per_fat)) * bs.bytes_per_sector

            # Chain clusters together
            for i, cluster in enumerate(clusters):
                next_cluster = clusters[i + 1] if i + 1 < len(clusters) else eoc
                self._write_fat_entry(image_data, fat_offset, cluster, next_cluster)

    async def _create_lfn_entries(
        self, image_data: bytearray, offset: int, filename: str, file_size: int, start_cluster: int
    ) -> None:
        """Create VFAT Long File Name entries followed by short name entry."""
        # Generate short name from long name
        short_name = self._generate_short_name(filename)

        # Calculate checksum for short name
        checksum = self._calculate_lfn_checksum(short_name)

        # Split filename into 13-char chunks for LFN entries
        lfn_entries = []
        for i in range(0, len(filename), 13):
            lfn_entries.append(filename[i : i + 13])

        # Write LFN entries in reverse order
        current_offset = offset
        for i, chunk in enumerate(reversed(lfn_entries)):
            sequence = len(lfn_entries) - i
            is_last = i == 0

            # Pad chunk: filename + null terminator + 0xFFFF padding (VFAT requirement)
            if len(chunk) < 13:
                # Add null terminator after last char
                chunk_padded = chunk + "\x00"
                # Pad remaining with 0xFFFF
                chunk_padded += "\uffff" * (13 - len(chunk_padded))
            else:
                chunk_padded = chunk

            # Encode to UCS-2 (UTF-16LE)
            name1 = chunk_padded[0:5].encode("utf-16le")
            name2 = chunk_padded[5:11].encode("utf-16le")
            name3 = chunk_padded[11:13].encode("utf-16le")

            # LFN entry attributes
            attr = 0x0F  # LFN attribute
            seq = sequence | (0x40 if is_last else 0x00)

            # Pack LFN entry (32 bytes)
            # Structure: seq(1) + name1(10) + attr(1) + type(1) + checksum(1) + name2(12) + cluster(2) + name3(4)
            lfn_entry = struct.pack(
                "<B10sBBB12sH4s",
                seq,  # Sequence number (1 byte)
                name1,  # First 5 UCS-2 chars (10 bytes)
                attr,  # Attributes 0x0F (1 byte)
                0,  # Type, always 0 (1 byte)
                checksum,  # Checksum (1 byte)
                name2,  # Next 6 UCS-2 chars (12 bytes)
                0,  # First cluster, always 0 for LFN (2 bytes)
                name3,  # Last 2 UCS-2 chars (4 bytes)
            )

            image_data[current_offset : current_offset + 32] = lfn_entry
            current_offset += 32

        # Write short name entry
        await self._create_dir_entry(image_data, current_offset, short_name, file_size, start_cluster)

    def _generate_short_name(self, long_name: str) -> str:
        """Generate 8.3 short name from long filename."""
        # Remove extension
        if "." in long_name:
            base, ext = long_name.rsplit(".", 1)
        else:
            base, ext = long_name, ""

        # Convert to uppercase and remove invalid chars
        base = base.upper().replace(" ", "").replace(".", "")
        ext = ext.upper()[:3]

        # Truncate base to 6 chars and add ~1
        if len(base) > 6:
            base = base[:6] + "~1"
        else:
            base = base[:8]

        return base + "." + ext if ext else base

    def _calculate_lfn_checksum(self, short_name: str) -> int:
        """Calculate LFN checksum for short name."""
        # Convert to 8.3 format bytes
        name_part, ext_part = self._to_83_format(short_name)
        name_83 = name_part + ext_part

        checksum = 0
        for byte in name_83:
            checksum = ((checksum >> 1) | (checksum << 7)) & 0xFF
            checksum = (checksum + byte) & 0xFF

        return checksum

    async def _create_dir_entry(
        self, image_data: bytearray, offset: int, short_name: str, file_size: int, start_cluster: int
    ) -> None:
        """Create short name directory entry at given offset."""
        # Convert filename to 8.3 format
        name_part, ext_part = self._to_83_format(short_name)

        # Get current time for timestamps
        dos_date, dos_time = self._get_dos_datetime()

        # Pack directory entry (32 bytes)
        entry = struct.pack(
            "<11sBBBHHHHHHHL",
            name_part + ext_part,  # Filename (11 bytes)
            0x20,  # Attributes (archive bit set)
            0,  # Reserved
            0,  # Creation time tenth
            dos_time,  # Creation time
            dos_date,  # Creation date
            dos_date,  # Last access date
            0,  # High word of start cluster (FAT32)
            dos_time,  # Last write time
            dos_date,  # Last write date
            start_cluster,  # Low word of start cluster
            file_size,  # File size
        )

        image_data[offset : offset + 32] = entry

    def _to_83_format(self, filename: str) -> tuple[bytes, bytes]:
        """Convert filename to 8.3 format."""
        # Simple conversion - pad with spaces
        if "." in filename:
            name, ext = filename.upper().rsplit(".", 1)
        else:
            name, ext = filename.upper(), ""

        name = name[:8].ljust(8).encode("ascii")
        ext = ext[:3].ljust(3).encode("ascii")

        return name, ext

    def _get_dos_datetime(self) -> tuple[int, int]:
        """Get current time in DOS format."""
        now = time.localtime()

        dos_date = (
            ((now.tm_year - 1980) << 9) | (now.tm_mon << 5) | now.tm_mday
        )
        dos_time = (now.tm_hour << 11) | (now.tm_min << 5) | (now.tm_sec // 2)

        return dos_date, dos_time
