"""Limited structural preflight for Full HD BD25/BD50 BDMV directories.

A pass does not prove UDF 2.50, disc-menu behavior or hardware compatibility.
"""
from __future__ import annotations
import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from mpls_preflight import validate_playlist
from clpi_preflight import validate_clip
from backup_preflight import check_backup_metadata
from tools.bdmv_audio_check import AudioInspectionError, inspect as inspect_m2ts_audio

CAPACITIES = {"BD25": 25_000_000_000, "BD50": 50_000_000_000}
# This 2D Full HD authoring path accepts BD metadata versions 1 and 2 only.
SUPPORTED_VERSIONS = {b"0100", b"0200"}
HEADERS = {"index.bdmv": b"INDX", "MovieObject.bdmv": b"MOBJ"}
COLLECTIONS = {"PLAYLIST": ("*.mpls", b"MPLS"),
               "CLIPINF": ("*.clpi", b"HDMV"),
               "STREAM": ("*.m2ts", None)}

def _check_m2ts_adaptation(ts: bytes) -> str | None:
    """Check length-delimited adaptation fields in sampled 188-byte packets."""
    control = (ts[3] >> 4) & 3
    if control == 0:
        return "Reserved M2TS adaptation_field_control"
    if ts[3] & 0xC0:
        return "M2TS transport_scrambling_control unsupported in clear authoring"
    if control == 1:
        return None
    length = ts[4]
    if control == 2 and length != 183:
        return "M2TS adaptation_field_length must be 183 for adaptation-only packets"
    if control == 3 and length > 182:
        return "M2TS adaptation_field_length exceeds TS packet"
    if length == 0:
        return None
    end = 5 + length
    flags = ts[5]
    cursor = 6
    for flag, size in ((0x10, 6), (0x08, 6), (0x04, 1)):
        if flags & flag:
            if cursor + size > end:
                return "M2TS adaptation field truncated PCR/OPCR/splice data"
            cursor += size
    if flags & 0x02:
        if cursor >= end:
            return "M2TS adaptation field missing private_data_length"
        count = ts[cursor]
        cursor += 1
        if cursor + count > end:
            return "M2TS adaptation field truncated private data"
        cursor += count
    if flags & 0x01:
        if cursor >= end:
            return "M2TS adaptation field missing extension_length"
        count = ts[cursor]
        cursor += 1
        if cursor + count > end:
            return "M2TS adaptation field truncated extension"
        if count:
            extension_end = cursor + count
            extension_flags = ts[cursor]
            cursor += 1
            for flag, size in ((0x80, 2), (0x40, 3), (0x20, 5)):
                if extension_flags & flag:
                    if cursor + size > extension_end:
                        return "M2TS adaptation field truncated extension payload"
                    cursor += size
    return None

def _check_m2ts_packets(handle, count: int, filename: str, errors: list[str]) -> None:
    """Bounded, deterministic M2TS transport-header sampling.

    Every packet has a 4-byte arrival timestamp followed by 188 TS bytes.
    This does not prove that all packets, codecs or Blu-ray timing are valid.
    """
    centers = (0, count // 4, count // 2, 3 * count // 4, count - 1)
    indices = sorted({
        index for center in centers
        for index in range(max(0, center - 2), min(count, center + 3))
    })
    for index in indices:
        handle.seek(index * 192)
        packet = handle.read(192)
        ts = packet[4:]
        if len(packet) != 192 or ts[0] != 0x47:
            reason = "Missing M2TS sync/header"
        elif packet[0] & 0xC0:
            # First two TP_extra_header bits are copy-permission indicators.
            reason = "M2TS copy_permission_indicator nonzero in clear authoring"
        elif ts[1] & 0x80:
            reason = "M2TS transport_error_indicator set"
        else:
            reason = _check_m2ts_adaptation(ts)
            if reason is None:
                continue
        errors.append(f"{reason} at packet {index}: {filename}")
        return

@dataclass
class Report:
    root: str
    profile: str
    structurally_valid: bool
    size_bytes: int
    errors: list[str]
    warnings: list[str]
    counts: dict[str, int]

def inspect(root: Path, profile: str = "BD25", expected_ac3: int | None = None) -> Report:
    root = Path(root).expanduser()
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    collection_stems: dict[str, set[str]] = {}
    size = 0
    if profile not in CAPACITIES:
        errors.append("Only BD25/BD50 supported; UHD/BDXL deferred.")
    check_ac3 = expected_ac3 is not None and type(expected_ac3) is int and 0 <= expected_ac3 <= 8
    if expected_ac3 is not None and not check_ac3:
        errors.append("Expected AC-3 stream count must be an integer from 0 to 8.")
    if not root.is_dir() or root.is_symlink():
        errors.append("Output directory missing or symlink.")
    else:
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if path.is_symlink():
                errors.append(f"Symlink forbidden: {relative}")
            elif path.is_file():
                try:
                    size += path.stat().st_size
                except OSError as exc:
                    errors.append(f"Cannot stat {relative}: {exc}")
        bdmv = root / "BDMV"
        if not bdmv.is_dir() or bdmv.is_symlink():
            errors.append("Missing BDMV directory.")
        for name, magic in HEADERS.items():
            path = bdmv / name
            if not path.is_file() or path.is_symlink():
                errors.append(f"Missing BDMV/{name}")
                continue
            try:
                with path.open("rb") as handle:
                    header = handle.read(8)
                    if header[:4] != magic:
                        errors.append(f"Invalid signature: BDMV/{name}")
                    elif len(header) < 8:
                        errors.append(f"Truncated version: BDMV/{name}")
                    elif header[4:8] not in SUPPORTED_VERSIONS:
                        errors.append(f"Unsupported Full HD metadata version: BDMV/{name}")
            except OSError as exc:
                errors.append(f"Cannot read BDMV/{name}: {exc}")
        for name, (pattern, magic) in COLLECTIONS.items():
            directory = bdmv / name
            matches = (sorted(directory.glob(pattern))
                       if directory.is_dir() and not directory.is_symlink() else [])
            counts[name] = len(matches)
            collection_stems[name] = {
                path.stem for path in matches if path.is_file() and not path.is_symlink()
                and len(path.stem) == 5 and path.stem.isascii() and path.stem.isdigit()
            }
            if not matches:
                errors.append(f"Missing BDMV/{name}/{pattern}")
            for path in matches:
                if len(path.stem) != 5 or not path.stem.isascii() or not path.stem.isdigit():
                    errors.append(f"Invalid five-digit Blu-ray filename: BDMV/{name}/{path.name}")
                    continue
                if path.is_symlink() or not path.is_file():
                    errors.append(f"Invalid BDMV/{name}/{path.name}")
                    continue
                try:
                    length = path.stat().st_size
                    if length == 0:
                        errors.append(f"Empty BDMV/{name}/{path.name}")
                    elif magic is None:
                        if length % 192 != 0:
                            errors.append(f"M2TS not 192-byte aligned: {path.name}")
                        else:
                            # Inspect bounded short windows, including adjacent packets,
                            # rather than trusting only three isolated sync bytes.
                            count = length // 192
                            with path.open("rb") as handle:
                                _check_m2ts_packets(handle, count, path.name, errors)
                    elif magic is not None:
                        with path.open("rb") as handle:
                            header = handle.read(8)
                            if header[:4] != magic:
                                errors.append(f"Invalid signature: BDMV/{name}/{path.name}")
                            elif len(header) < 8:
                                errors.append(f"Truncated version: BDMV/{name}/{path.name}")
                            elif header[4:8] not in SUPPORTED_VERSIONS:
                                errors.append(f"Unsupported Full HD metadata version: BDMV/{name}/{path.name}")
                except OSError as exc:
                    errors.append(f"Cannot inspect {path.name}: {exc}")
        # Authored-output gate: count declared AC-3 streams in every M2TS.
        # Optional on standalone inspection of third-party BDMV folders.
        if check_ac3:
            stream_dir = bdmv / "STREAM"
            if stream_dir.is_dir() and not stream_dir.is_symlink():
                for stream in sorted(stream_dir.glob("*.m2ts")):
                    if not stream.is_file() or stream.is_symlink():
                        continue
                    try:
                        inspect_m2ts_audio(stream, expected_ac3)
                    except (AudioInspectionError, OSError) as exc:
                        errors.append(f"BDMV/STREAM/{stream.name}: AC-3 PAT/PMT check failed: {exc}")
        # In a 2D Full HD BDMV set, every stream needs its same-numbered clip info.
        clip_ids = collection_stems.get("CLIPINF", set())
        stream_ids = collection_stems.get("STREAM", set())
        for stem in sorted(stream_ids - clip_ids):
            errors.append(f"Missing matching CLIPINF/{stem}.clpi for STREAM/{stem}.m2ts")
        for stem in sorted(clip_ids - stream_ids):
            errors.append(f"Missing matching STREAM/{stem}.m2ts for CLIPINF/{stem}.clpi")
        # Cross-check the metadata and physical 192-byte packet count for each 2D clip.
        for stem in sorted(clip_ids & stream_ids):
            file_errors, file_warnings = validate_clip(
                bdmv / "CLIPINF" / f"{stem}.clpi",
                bdmv / "STREAM" / f"{stem}.m2ts")
            errors.extend(file_errors)
            warnings.extend(file_warnings)
        # Cross-check every MPLS PlayItem against the matching clips and streams.
        playlist_dir = bdmv / "PLAYLIST"
        if playlist_dir.is_dir() and not playlist_dir.is_symlink():
            for playlist_path in sorted(playlist_dir.glob("*.mpls")):
                stem = playlist_path.stem
                if (playlist_path.is_file() and not playlist_path.is_symlink()
                        and len(stem) == 5 and stem.isascii() and stem.isdigit()):
                    file_errors, file_warnings = validate_playlist(
                        playlist_path, clip_ids, stream_ids)
                    errors.extend(file_errors)
                    warnings.extend(file_warnings)
        backup_errors, backup_warnings = check_backup_metadata(bdmv, collection_stems)
        errors.extend(backup_errors)
        warnings.extend(backup_warnings)
        if not (root / "CERTIFICATE").is_dir():
            warnings.append("Missing CERTIFICATE directory.")
    if profile in CAPACITIES and size > CAPACITIES[profile]:
        errors.append(f"Output size {size} exceeds {profile}.")
    warnings.append("Static preflight only: menus, UDF 2.50, burning and player compatibility UNVERIFIED.")
    return Report(str(root), profile, not errors, size, errors, warnings, counts)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DiscForge RU BD25/BD50 BDMV preflight")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--profile", choices=tuple(CAPACITIES), default="BD25")
    parser.add_argument("--expected-ac3", type=int, default=None,
                        help="Reject if M2TS PMT does not report this many AC-3 streams (0..8)")
    args = parser.parse_args(argv)
    result = inspect(args.folder, args.profile, expected_ac3=args.expected_ac3)
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    return 0 if result.structurally_valid else 2

if __name__ == "__main__":
    raise SystemExit(main())
