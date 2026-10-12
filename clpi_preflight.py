"""Bounded static CLPI preflight for DiscForge RU's unencrypted 2D BD25/BD50 output.

CLPI structure and source-packet counts are inspected; this is not a decoder,
seek-table validator, or proof of compliance with the Blu-ray specification.
"""
from __future__ import annotations

from pathlib import Path

MAX_CLPI_BYTES = 16 * 1024 * 1024
SUPPORTED_VERSIONS = (b"0100", b"0200")
SOURCE_PACKET_BYTES = 192


def validate_clip(path: Path, stream_path: Path) -> tuple[list[str], list[str]]:
    """Check CLPI section bounds and matching M2TS source-packet count.

    Only reads up to MAX_CLPI_BYTES+1 of metadata, never video payload. The
    BDMV caller has already enforced five-digit filenames and symlink policy.
    """
    errors: list[str] = []
    warnings: list[str] = []
    name = path.name
    if path.is_symlink() or not path.is_file():
        return [f"CLPI missing or symlink: {name}"], warnings
    try:
        with path.open("rb") as source:
            data = source.read(MAX_CLPI_BYTES + 1)
    except OSError as exc:
        return [f"Cannot read CLPI {name}: {exc}"], warnings

    if len(data) > MAX_CLPI_BYTES:
        return [f"CLPI too large for bounded preflight: {name}"], warnings
    if len(data) < 60:
        return [f"Truncated CLPI header/ClipInfo (60 bytes required): {name}"], warnings
    if data[:4] != b"HDMV" or data[4:8] not in SUPPORTED_VERSIONS:
        return [f"Invalid CLPI signature/version: {name}"], warnings

    def u32(offset: int) -> int:
        return int.from_bytes(data[offset:offset + 4], "big")

    intervals: list[tuple[int, int, str]] = []

    def section(offset: int, label: str, minimum: int) -> None:
        if offset < 40 or offset % 2 or offset + 4 > len(data):
            errors.append(f"CLPI invalid {label} start address: {name}")
            return
        length = u32(offset)
        end = offset + 4 + length
        if length < minimum or end > len(data):
            errors.append(f"CLPI truncated {label} section: {name}")
            return
        intervals.append((offset, end, label))

    # The mandatory ClipInfo block starts after the fixed 40-byte header.
    # Its 16-byte body contains the 32-bit source packet count at byte 56.
    section(40, "ClipInfo", 16)
    for location, name_part, minimum, mandatory in (
        (8, "SequenceInfo", 2, True),
        (12, "ProgramInfo", 2, True),
        (16, "CPI", 0, False),
        (20, "ClipMark", 0, False),
        (24, "ExtensionData", 0, False),
    ):
        address = u32(location)
        if address == 0 and not mandatory:
            continue
        if address == 0:
            errors.append(f"CLPI missing {name_part} start address: {name}")
            continue
        if address < 60:
            errors.append(f"CLPI invalid {name_part} start address: {name}")
            continue
        section(address, name_part, minimum)

    ordered = sorted(intervals)
    for left, right in zip(ordered, ordered[1:]):
        if left[1] > right[0]:
            errors.append(f"CLPI overlapping {left[2]} and {right[2]} sections: {name}")

    # Cross-check the advertised packet count against the *unmodified* stream.
    # File length must be an integer number of 192-byte M2TS source packets.
    packet_count = u32(56)
    if packet_count == 0:
        errors.append(f"CLPI zero source packet count: {name}")
    try:
        if stream_path.is_symlink() or not stream_path.is_file():
            errors.append(f"CLPI matching stream missing or symlink: {stream_path.name}")
        else:
            stream_bytes = stream_path.stat().st_size
            if stream_bytes != packet_count * SOURCE_PACKET_BYTES:
                errors.append(
                    f"CLPI source packet count mismatch for {stream_path.name}: "
                    f"declared {packet_count}, actual {stream_bytes // SOURCE_PACKET_BYTES}"
                )
    except OSError as exc:
        errors.append(f"Cannot inspect CLPI matching stream {stream_path.name}: {exc}")

    # The internal CPI seek map and STC/PCR/PID semantics remain unchecked.
    return errors, warnings
