"""Experimental, read-only UDF File Set Descriptor / root ICB preflight.

Type 1 and bounded Type 2 Metadata File partition maps can be resolved.
Virtual/sparable/mirror-only maps are unsupported and must not be guessed. This module is deliberately separate
from the ISO publication gate until real images have been validated.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import iso_udf_preflight as udf
from . import udf_metadata_partition as metadata


@dataclass
class FileSetReport:
    path: str
    profile: str
    valid: bool
    fsd_lba: int | None
    root_icb_lba: int | None
    errors: list[str]
    warnings: list[str]


def _long_ad(block: bytes, offset: int, description: str, minimum: int) -> tuple[int, int, int]:
    """Parse a recorded-and-allocated ECMA-167 long_ad (length, lbn, map index)."""
    extent = int.from_bytes(block[offset:offset + 4], "little")
    length = extent & 0x3FFFFFFF
    if extent >> 30 != 0 or length < minimum:
        raise ValueError(f"{description}: missing, short, or non-recorded extent.")
    lbn = int.from_bytes(block[offset + 4:offset + 8], "little")
    reference = int.from_bytes(block[offset + 8:offset + 10], "little")
    return length, lbn, reference


def _partition_maps(lvd: bytes) -> list[tuple[int, int, bytes]]:
    """Preserve map order: long_ad partition references are map *indices*."""
    table_length = int.from_bytes(lvd[264:268], "little")
    count = int.from_bytes(lvd[268:272], "little")
    table = lvd[440:440 + table_length]
    maps: list[tuple[int, int, bytes]] = []
    offset = 0
    for _ in range(count):
        if offset + 2 > len(table):
            raise ValueError("Truncated partition map table.")
        kind, length = table[offset:offset + 2]
        if kind not in (1, 2) or length != (6 if kind == 1 else 64):
            raise ValueError("Unsupported partition map layout.")
        if offset + length > len(table):
            raise ValueError("Truncated partition map entry.")
        number = int.from_bytes(table[offset + (4 if kind == 1 else 38):
                                      offset + (6 if kind == 1 else 40)], "little")
        maps.append((kind, number, bytes(table[offset:offset + length])))
        offset += length
    if offset != table_length:
        raise ValueError("Partition map table has trailing data.")
    return maps


def _physical_lba(handle, ad: tuple[int, int, int], maps: list[tuple[int, int | None]],
                  partitions: dict[int, tuple[int, int]], image_sectors: int,
                  description: str) -> int:
    length, lbn, reference = ad
    if reference >= len(maps):
        raise ValueError(f"{description}: partition map index is missing.")
    kind, number, record = maps[reference]
    if number not in partitions:
        raise ValueError(f"{description}: physical partition is missing.")
    start, size = partitions[number]
    if kind == 2:
        if record[5:28].rstrip(b"\x00") != b"*UDF Metadata Partition":
            raise ValueError(f"{description}: Type 2 virtual/sparable partition maps are unsupported.")
        try:
            return metadata.resolve_metadata_lba(
                handle, metadata_icb_lbn=int.from_bytes(record[40:44], "little"),
                physical_start=start, physical_sectors=size,
                metadata_lbn=lbn, extent_bytes=length, image_sectors=image_sectors)
        except ValueError as exc:
            raise ValueError(f"{description}: Type 2 partition map: {exc}") from exc
    required_sectors = (length + udf.SECTOR_SIZE - 1) // udf.SECTOR_SIZE
    if lbn >= size or required_sectors > size - lbn:
        raise ValueError(f"{description}: extent exceeds the partition.")
    absolute = start + lbn
    if absolute >= image_sectors or required_sectors > image_sectors - absolute:
        raise ValueError(f"{description}: extent exceeds the ISO.")
    return absolute


def _check_vds(handle, image_sectors: int, start: int, sectors: int) -> tuple[int, int]:
    partitions: dict[int, tuple[int, int]] = {}
    logical_volumes: list[bytes] = []
    for lba in range(start, start + sectors):
        block = udf._sector(handle, lba, image_sectors)
        if not any(block):
            continue
        tag = udf._tag(block, lba)
        if tag == 8:
            break
        if tag == 5:
            number = int.from_bytes(block[22:24], "little")
            physical = (int.from_bytes(block[188:192], "little"),
                        int.from_bytes(block[192:196], "little"))
            if number in partitions and partitions[number] != physical:
                raise ValueError("Conflicting Partition Descriptors for the same number.")
            partitions[number] = physical
        elif tag == 6:
            logical_volumes.append(block)
    if not logical_volumes:
        raise ValueError("No Logical Volume Descriptor.")
    found: list[tuple[int, int]] = []
    for lvd in logical_volumes:
        maps = _partition_maps(lvd)
        fsd_ad = _long_ad(lvd, 248, "File Set Descriptor", 512)
        fsd_lba = _physical_lba(handle, fsd_ad, maps, partitions, image_sectors,
                                "File Set Descriptor")
        # UDF file-system descriptor tag locations are partition-relative LBNs.
        fsd = udf._sector(handle, fsd_lba, image_sectors)
        if udf._tag(fsd, fsd_ad[1]) != 256:
            raise ValueError("File Set Descriptor tag ID is not 256.")
        if int.from_bytes(fsd[10:12], "little") < 432:
            raise ValueError("File Set Descriptor CRC does not cover root ICB and domain.")
        if fsd[417:440].rstrip(b"\x00") != udf.UDF_DOMAIN:
            raise ValueError("File Set Descriptor UDF domain identifier is missing.")
        if int.from_bytes(fsd[440:442], "little") != 0x0250:
            raise ValueError("File Set Descriptor UDF revision is not 2.50.")
        root_ad = _long_ad(fsd, 400, "Root directory ICB", 36)
        root_lba = _physical_lba(handle, root_ad, maps, partitions, image_sectors,
                                 "Root directory ICB")
        root = udf._sector(handle, root_lba, image_sectors)
        entry_tag = udf._tag(root, root_ad[1])
        if entry_tag not in (261, 266):
            raise ValueError("Root ICB does not reference a File Entry.")
        # FE and Extended FE have distinct fixed fields. In particular, the
        # extended-attribute and allocation-descriptor byte counts must be
        # covered by the descriptor CRC, not merely the ICB file-type byte.
        header_size = 176 if entry_tag == 261 else 216
        if root_ad[0] < header_size:
            raise ValueError("Root ICB extent is smaller than the File Entry header.")
        crc_end = 16 + int.from_bytes(root[10:12], "little")
        if crc_end < header_size:
            raise ValueError("Root File Entry CRC does not cover the full entry header.")
        if root[27] != 4:
            raise ValueError("Root File Entry is not a directory.")
        ea_offset, alloc_offset = ((168, 172) if entry_tag == 261 else (208, 212))
        ea_length = int.from_bytes(root[ea_offset:ea_offset + 4], "little")
        alloc_length = int.from_bytes(root[alloc_offset:alloc_offset + 4], "little")
        entry_end = header_size + ea_length + alloc_length
        if entry_end > udf.SECTOR_SIZE:
            raise ValueError("Root File Entry exceeds one sector; unsupported layout.")
        if entry_end > root_ad[0]:
            raise ValueError("Root File Entry exceeds the advertised ICB extent.")
        if crc_end < entry_end:
            raise ValueError("Root File Entry CRC does not cover allocation descriptors.")
        found.append((fsd_lba, root_lba))
    if len(set(found)) != 1:
        raise ValueError("Logical Volume Descriptors disagree about the root directory.")
    return found[0]


def inspect(path: str | Path, profile: str = "BD25") -> FileSetReport:
    """Inspect FSD + root ICB only; does not traverse directories or read BDMV."""
    base = udf.inspect(path, profile)
    warnings = [
        "Experimental Type 1/Type 2 FSD/root-ICB check; directory tree, BDMV, "
        "menus, physical burning and playback are NOT verified."
    ]
    if not base.valid:
        return FileSetReport(base.path, profile, False, None, None,
                             list(base.errors), warnings)
    image = Path(path).expanduser()
    last_error = "No usable File Set Descriptor."
    try:
        with image.open("rb") as handle:
            count = base.size_bytes // udf.SECTOR_SIZE
            for anchor_lba, main, reserve in udf._anchors(handle, count):
                for label, extent in (("Main", main), ("Reserve", reserve)):
                    if extent is None:
                        continue
                    try:
                        if udf._revision(handle, count, *extent) != 0x0250:
                            continue
                        fsd_lba, root_lba = _check_vds(handle, count, *extent)
                    except ValueError as exc:
                        last_error = str(exc)
                        continue
                    if anchor_lba != 256:
                        warnings.append("Used backup UDF anchor.")
                    if label == "Reserve":
                        warnings.append("Used Reserve VDS.")
                    return FileSetReport(str(image), profile, True, fsd_lba,
                                         root_lba, [], warnings)
    except (OSError, ValueError) as exc:
        last_error = str(exc)
    return FileSetReport(str(image), profile, False, None, None,
                         [f"FSD/root-ICB check failed: {last_error}"], warnings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Experimental UDF FSD/root-ICB check")
    parser.add_argument("iso", type=Path)
    parser.add_argument("--profile", choices=tuple(udf.CAPACITIES), default="BD25")
    args = parser.parse_args(argv)
    result = inspect(args.iso, args.profile)
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
