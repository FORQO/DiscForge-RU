"""Bounded structural checks for 2D Full HD Blu-ray MPLS playlists.

This is not a complete BD-ROM parser or a compatibility certification. In
particular, STN tables, SubPaths, menu objects and transport timing are not
fully interpreted here.
"""
from __future__ import annotations

from pathlib import Path

MAX_PLAYLIST_BYTES = 16 * 1024 * 1024


def validate_playlist(path: Path, clip_ids: set[str], stream_ids: set[str]) -> tuple[list[str], list[str]]:
    """Validate MPLS section boundaries, primary PlayItems and chapter refs.

    Returns (errors, warnings); callers decide whether failures block output.
    Limits reading to 16 MiB per playlist and never follows playlist references
    outside the BDMV directory: only five-digit clip identifiers are accepted.
    """
    errors: list[str] = []
    warnings: list[str] = []
    name = path.name
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_PLAYLIST_BYTES + 1)
    except OSError as exc:
        return [f"Cannot read MPLS {name}: {exc}"], warnings
    if len(data) > MAX_PLAYLIST_BYTES:
        return [f"MPLS too large for bounded preflight: {name}"], warnings

    if len(data) < 40:
        return [f"Truncated MPLS header (40 bytes required): {name}"], warnings
    if data[:4] != b"MPLS":
        return [f"Invalid MPLS signature: {name}"], warnings

    def u16(offset: int) -> int:
        return int.from_bytes(data[offset:offset + 2], "big")

    def u32(offset: int) -> int:
        return int.from_bytes(data[offset:offset + 4], "big")

    sections: list[tuple[int, int, str]] = []

    def section(address: int, label: str, minimum: int) -> tuple[int, int] | None:
        if address < 40 or address % 2 or address + 4 > len(data):
            errors.append(f"MPLS invalid {label} start address: {name}")
            return None
        length = u32(address)
        end = address + 4 + length
        if length < minimum or end > len(data):
            errors.append(f"MPLS truncated {label} section: {name}")
            return None
        sections.append((address, end, label))
        return address + 4, end

    playlist = section(u32(8), "PlayList", 6)
    marks_address = u32(12)
    marks = section(marks_address, "PlayListMark", 2) if marks_address else None
    extension_address = u32(16)
    if extension_address:
        section(extension_address, "ExtensionData", 0)

    for left, right in zip(sorted(sections), sorted(sections)[1:]):
        if left[1] > right[0]:
            errors.append(f"MPLS overlapping {left[2]} and {right[2]} sections: {name}")

    if playlist is None:
        return errors, warnings
    start, end = playlist
    play_item_count = u16(start + 2)
    subpath_count = u16(start + 4)
    if play_item_count == 0:
        errors.append(f"MPLS playlist has no PlayItems: {name}")
    cursor = start + 6
    # PlayListMark timestamps use the referenced PlayItem's 45-kHz clock,
    # not an offset on the concatenated playlist timeline.
    item_intervals: list[tuple[int, int]] = []

    def check_clip(raw: bytes, item: int) -> None:
        if len(raw) != 5 or not raw.isascii() or not raw.isdigit():
            errors.append(f"MPLS PlayItem {item} has invalid five-digit clip ID: {name}")
            return
        clip = raw.decode("ascii")
        if clip not in clip_ids:
            errors.append(f"MPLS PlayItem {item} references missing CLIPINF/{clip}.clpi: {name}")
        if clip not in stream_ids:
            errors.append(f"MPLS PlayItem {item} references missing STREAM/{clip}.m2ts: {name}")

    for item_index in range(play_item_count):
        if cursor + 2 > end:
            errors.append(f"MPLS missing PlayItem {item_index} length: {name}")
            return errors, warnings
        item_length = u16(cursor)
        item_end = cursor + 2 + item_length
        if item_length < 32 or item_end > end:
            errors.append(f"MPLS truncated PlayItem {item_index}: {name}")
            return errors, warnings
        item = data[cursor + 2:item_end]
        check_clip(item[:5], item_index)
        if item[5:9] != b"M2TS":
            errors.append(f"MPLS PlayItem {item_index} has unsupported codec identifier: {name}")
        in_time = int.from_bytes(item[12:16], "big")
        out_time = int.from_bytes(item[16:20], "big")
        item_intervals.append((in_time, out_time))
        if in_time >= out_time:
            errors.append(f"MPLS PlayItem {item_index} has non-positive IN/OUT interval: {name}")
        if item[10] & 0x10:  # multi-angle flag
            if item_length < 34:
                errors.append(f"MPLS truncated multi-angle PlayItem {item_index}: {name}")
                return errors, warnings
            angles = item[32]
            if not 1 <= angles <= 9 or 34 + (angles - 1) * 10 > item_length:
                errors.append(f"MPLS invalid multi-angle record count in PlayItem {item_index}: {name}")
                return errors, warnings
            for angle in range(1, angles):
                position = 34 + (angle - 1) * 10
                check_clip(item[position:position + 5], item_index)
            warnings.append(f"MPLS multi-angle semantics not fully validated: {name}")
        cursor = item_end
    # Parse only SubPath length envelopes. SubPath semantics (PID, STC, angles)
    # remain unvalidated, but a declared record cannot extend beyond PlayList.
    for subpath_index in range(subpath_count):
        if cursor + 4 > end:
            errors.append(f"MPLS missing SubPath {subpath_index} length: {name}")
            return errors, warnings
        subpath_length = u32(cursor)
        subpath_end = cursor + 4 + subpath_length
        if subpath_length < 4 or subpath_end > end:
            errors.append(f"MPLS truncated SubPath {subpath_index}: {name}")
            return errors, warnings
        cursor = subpath_end
    if subpath_count:
        warnings.append(f"MPLS contains {subpath_count} bounded but unvalidated SubPaths: {name}")

    if marks:
        mark_start, mark_end = marks
        mark_count = u16(mark_start)
        if mark_start + 2 + 14 * mark_count > mark_end:
            errors.append(f"MPLS truncated PlayListMark records: {name}")
        else:
            for index in range(mark_count):
                record = mark_start + 2 + 14 * index
                ref_item = u16(record + 2)
                if ref_item >= play_item_count:
                    errors.append(f"MPLS PlayListMark {index} references invalid PlayItem {ref_item}: {name}")
                    continue
                mark_type = data[record + 1]
                if mark_type == 1:  # Entry mark: a chapter in a PlayItem.
                    mark_time = u32(record + 4)
                    in_time, out_time = item_intervals[ref_item]
                    if not in_time <= mark_time < out_time:
                        errors.append(
                            f"MPLS chapter mark {index} timestamp {mark_time} outside "
                            f"PlayItem {ref_item} interval [{in_time}, {out_time}): {name}"
                        )
                elif mark_type != 2:
                    errors.append(f"MPLS PlayListMark {index} has invalid mark type {mark_type}: {name}")
                else:
                    warnings.append(f"MPLS link-point mark {index} timing not validated: {name}")
    return errors, warnings
