"""Read-only UDF 2.50 metadata preflight for Full HD Blu-ray ISO images.

Checks VRS, anchor, LVD revision, descriptor tags/CRC. Does not traverse
file tree or verify BDMV, menus, disc burning or physical player compatibility.
"""
from __future__ import annotations

import argparse
import binascii
import json
from dataclasses import asdict, dataclass
from pathlib import Path

SECTOR_SIZE = 2048
CAPACITIES = {"BD25": 25_000_000_000, "BD50": 50_000_000_000}
UDF_DOMAIN = b"*OSTA UDF Compliant"
MAX_VDS_SECTORS = 256
UDF_TYPE2_PARTITION_IDS = frozenset((
    b"*UDF Virtual Partition",
    b"*UDF Sparable Partition",
    b"*UDF Metadata Partition",
))


@dataclass
class Report:
    path: str
    profile: str
    valid: bool
    size_bytes: int
    udf_revision: str | None
    errors: list[str]
    warnings: list[str]


def _sector(handle, lba: int, count: int) -> bytes:
    if not 0 <= lba < count:
        raise ValueError("Sector outside ISO image.")
    handle.seek(lba * SECTOR_SIZE)
    data = handle.read(SECTOR_SIZE)
    if len(data) != SECTOR_SIZE:
        raise ValueError("Truncated ISO sector.")
    return data


def _tag(block: bytes, lba: int) -> int:
    """Validate ECMA-167 descriptor tag, checksum, location and CRC-16."""
    tag_id = int.from_bytes(block[0:2], "little")
    version = int.from_bytes(block[2:4], "little")
    if tag_id == 0 or version not in (2, 3):
        raise ValueError("Invalid descriptor tag or version.")
    if (sum(block[0:4]) + sum(block[5:16])) % 256 != block[4]:
        raise ValueError("Descriptor tag checksum mismatch.")
    if int.from_bytes(block[12:16], "little") != lba:
        raise ValueError("Descriptor tag location mismatch.")
    crc_length = int.from_bytes(block[10:12], "little")
    if crc_length > SECTOR_SIZE - 16:
        raise ValueError("Descriptor CRC extends beyond inspected sector.")
    crc_expected = int.from_bytes(block[8:10], "little")
    if binascii.crc_hqx(block[16:16 + crc_length], 0) != crc_expected:
        raise ValueError("Descriptor CRC mismatch.")
    # The CRC must cover every descriptor field used by this preflight.
    # LVD's UDF domain identifier and revision end at byte offset 242.
    if tag_id == 6 and crc_length < 226:
        raise ValueError("LVD CRC does not cover the UDF domain revision.")
    # The AVDP Main VDS extent is bytes 16:24, Reserve is bytes 24:32.
    # An empty or short CRC would otherwise allow unchecked extent changes.
    if tag_id == 2:
        required = 16 if any(block[24:32]) else 8
        if crc_length < required:
            raise ValueError("Anchor CRC does not cover advertised VDS extents.")
    return tag_id


def _vrs(handle, count: int) -> bool:
    """Find ordered BEA01 / NSR03 / TEA01 recognition sequence."""
    state = 0
    for lba in range(16, min(count, 64)):
        block = _sector(handle, lba, count)
        identifier = block[1:6]
        # ECMA-167 Volume Structure Descriptors require type 0 and version 1.
        # Checking identifiers alone can mistake malformed VRS sectors for UDF.
        if identifier in (b"BEA01", b"NSR03", b"TEA01"):
            if block[0] != 0 or block[6] != 1:
                return False
        if state == 0 and identifier == b"BEA01":
            state = 1
        elif state == 1 and identifier == b"NSR03":
            state = 2
        elif state == 2 and identifier == b"TEA01":
            return True
        elif state in (1, 2) and identifier in (b"BEA01", b"NSR02"):
            return False
    return False


def _vds_extent(block: bytes, offset: int, count: int) -> tuple[int, int] | None:
    length = int.from_bytes(block[offset:offset + 4], "little")
    start = int.from_bytes(block[offset + 4:offset + 8], "little")
    # VDS extents must cover complete logical sectors. Rounding up a partial
    # extent could accept a descriptor outside the advertised byte range.
    if length == 0 or length % SECTOR_SIZE:
        return None
    sectors = length // SECTOR_SIZE
    if 1 <= sectors <= MAX_VDS_SECTORS and start < count and sectors <= count - start:
        return start, sectors
    return None


def _anchors(handle, count: int) -> list[tuple[int, tuple[int, int] | None, tuple[int, int] | None]]:
    """Collect bounded VDS extents from primary and backup anchors.

    An intact anchor can still reference an unusable VDS, so retain backup
    candidates until a valid LVD has been found.
    """
    candidates = []
    for lba in dict.fromkeys((256, count - 1, count - 257)):
        if not 0 <= lba < count:
            continue
        try:
            block = _sector(handle, lba, count)
            if _tag(block, lba) != 2:
                continue
            main = _vds_extent(block, 16, count)
            reserve = _vds_extent(block, 24, count)
            if main is not None or reserve is not None:
                candidates.append((lba, main, reserve))
        except ValueError:
            continue
    if not candidates:
        raise ValueError("No valid UDF anchor or bounded Main/Reserve VDS extent.")
    return candidates



def _partition_maps(block: bytes) -> set[int]:
    """Check bounded LVD partition maps, not filesystem or metadata contents."""
    table_length = int.from_bytes(block[264:268], "little")
    map_count = int.from_bytes(block[268:272], "little")
    if not table_length or not map_count:
        raise ValueError("LVD has no partition maps.")
    if table_length > SECTOR_SIZE - 440 or map_count > table_length // 6:
        raise ValueError("LVD partition map table exceeds descriptor bounds.")
    if int.from_bytes(block[10:12], "little") < 424 + table_length:
        raise ValueError("LVD CRC does not cover partition maps.")
    table = block[440:440 + table_length]
    offset = 0
    references: set[int] = set()
    for _ in range(map_count):
        if offset + 2 > table_length:
            raise ValueError("LVD partition map count/length mismatch.")
        map_type, length = table[offset], table[offset + 1]
        expected_length = {1: 6, 2: 64}.get(map_type)
        if expected_length is None or length != expected_length or offset + length > table_length:
            raise ValueError("LVD has invalid partition map type or length.")
        part_map = table[offset:offset + length]
        if map_type == 1:
            volume_sequence = int.from_bytes(part_map[2:4], "little")
            partition_number = int.from_bytes(part_map[4:6], "little")
        else:
            # Type 2 mappings may be virtual, sparable or metadata partitions;
            # only their structure and physical partition reference are checked.
            identifier = part_map[5:28].rstrip(b"\x00")
            if identifier not in UDF_TYPE2_PARTITION_IDS:
                raise ValueError("LVD has unsupported Type 2 partition identifier.")
            volume_sequence = int.from_bytes(part_map[36:38], "little")
            partition_number = int.from_bytes(part_map[38:40], "little")
        if volume_sequence != 1:
            raise ValueError("LVD partition map references unsupported volume sequence (expected 1).")
        references.add(partition_number)
        offset += length
    if offset != table_length:
        raise ValueError("LVD partition map count/length mismatch.")
    return references

def _revision(handle, count: int, start: int, sectors: int) -> int:
    """Find a valid LVD and read its UDF revision (EntityID suffix, LE)."""
    revision = None
    partition_numbers: set[int] = set()
    map_references: set[int] = set()
    seen_primary = seen_partition = terminated = False
    for lba in range(start, start + sectors):
        block = _sector(handle, lba, count)
        if not any(block):
            continue
        try:
            tag_id = _tag(block, lba)
        except ValueError as exc:
            tag_names = {1: "Primary Volume Descriptor", 5: "Partition Descriptor",
                         6: "Logical Volume Descriptor", 8: "Terminating Descriptor"}
            name = tag_names.get(int.from_bytes(block[:2], "little"), "VDS descriptor")
            raise ValueError(f"Invalid {name} at sector {lba}: {exc}") from exc
        if tag_id == 3:
            raise ValueError("VDS continuation via Volume Descriptor Pointer is unsupported.")
        if tag_id not in (1, 4, 5, 6, 7, 8):
            raise ValueError(f"Unexpected descriptor type {tag_id} in VDS.")
        if tag_id == 8:  # Terminating Descriptor
            terminated = True
            break
        if tag_id == 1:  # Primary Volume Descriptor
            seen_primary = True
        elif tag_id == 5:  # Partition Descriptor
            # ECMA-167 3/10.5: partition start/length at bytes 188/192.
            # Reject unprotected or out-of-bounds partition extents.
            if int.from_bytes(block[10:12], "little") < 180:
                raise ValueError("Partition Descriptor CRC does not cover partition bounds.")
            # ECMA-167 3/10.5: regid Partition Contents at bytes 24:56.
            # UDF 2.50 partitions must identify the NSR03 layout.
            if block[25:48].rstrip(b"\x00") != b"+NSR03":
                raise ValueError("Partition Descriptor contents must identify +NSR03.")
            part_start = int.from_bytes(block[188:192], "little")
            part_length = int.from_bytes(block[192:196], "little")
            if part_length == 0 or part_start >= count or part_length > count - part_start:
                raise ValueError("Partition Descriptor has empty or out-of-bounds partition.")
            seen_partition = True
            partition_numbers.add(int.from_bytes(block[22:24], "little"))
        elif tag_id == 6:  # Logical Volume Descriptor
            if int.from_bytes(block[212:216], "little") != SECTOR_SIZE:
                raise ValueError("LVD logical block size is not 2048.")
            if block[217:240].rstrip(b"\x00") != UDF_DOMAIN:
                raise ValueError("LVD has no OSTA UDF domain identifier.")
            candidate_revision = int.from_bytes(block[240:242], "little")
            if revision is not None and revision != candidate_revision:
                raise ValueError("Conflicting UDF revisions in Logical Volume Descriptors.")
            revision = candidate_revision
            map_references.update(_partition_maps(block))
    if revision is None:
        raise ValueError("No valid Logical Volume Descriptor in VDS.")
    missing = []
    if not seen_primary:
        missing.append("Primary Volume Descriptor")
    if not seen_partition:
        missing.append("Partition Descriptor")
    if not terminated:
        missing.append("Terminating Descriptor")
    if missing:
        raise ValueError("Incomplete VDS; missing " + ", ".join(missing) + ".")
    if not map_references.issubset(partition_numbers):
        raise ValueError("LVD partition map references a missing Partition Descriptor.")
    return revision


def inspect(path: str | Path, profile: str = "BD25") -> Report:
    image = Path(path).expanduser()
    errors: list[str] = []
    warnings = [
        "Metadata-only preflight: UDF directory tree, BDMV contents, menus, "
        "physical burning and player compatibility are NOT verified."
    ]
    size = 0
    revision: str | None = None
    if profile not in CAPACITIES:
        errors.append("Only BD25/BD50 supported; UHD/BDXL deferred.")
    if image.is_symlink() or not image.is_file():
        errors.append("ISO missing, not a regular file, or a symlink.")
    elif image.suffix.lower() != ".iso":
        errors.append("Expected an .iso file.")
    else:
        try:
            size = image.stat().st_size
            if size < 258 * SECTOR_SIZE or size % SECTOR_SIZE:
                errors.append("ISO is too small or not aligned to 2048-byte sectors.")
            elif profile in CAPACITIES and size > CAPACITIES[profile]:
                errors.append(f"ISO size {size} exceeds {profile}.")
            else:
                with image.open("rb") as handle:
                    count = size // SECTOR_SIZE
                    if not _vrs(handle, count):
                        errors.append("Missing ordered BEA01/NSR03/TEA01 UDF VRS.")
                    else:
                        last_error = None
                        value = None
                        for anchor_lba, main_vds, reserve_vds in _anchors(handle, count):
                            for label, extent in (("Main", main_vds), ("Reserve", reserve_vds)):
                                if extent is None:
                                    continue
                                try:
                                    value = _revision(handle, count, *extent)
                                except ValueError as exc:
                                    last_error = exc
                                    continue
                                if anchor_lba != 256:
                                    warnings.append("Primary anchor/VDS unusable; used backup anchor.")
                                if label == "Reserve":
                                    warnings.append("Main VDS invalid or missing; used Reserve VDS.")
                                break
                            if value is not None:
                                break
                        if value is None:
                            raise ValueError(f"No valid Logical Volume Descriptor in Main/Reserve VDS: {last_error}")
                        revision = f"0x{value:04x}"
                        if value != 0x0250:
                            errors.append(f"UDF revision {revision} is not 2.50 (0x0250).")
        except (OSError, ValueError) as exc:
            errors.append(f"Cannot validate UDF descriptors: {exc}")
    return Report(str(image), profile, not errors, size, revision, errors, warnings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DiscForge RU ISO UDF 2.50 metadata preflight")
    parser.add_argument("iso", type=Path)
    parser.add_argument("--profile", choices=tuple(CAPACITIES), default="BD25")
    args = parser.parse_args(argv)
    report = inspect(args.iso, args.profile)
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    return 0 if report.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
