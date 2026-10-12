"""Verify optional BDMV/BACKUP navigation metadata mirrors the primary copies.

This limited consistency check does not prove BD-ROM compliance or playback.
M2TS video data is intentionally never read here.
"""
from __future__ import annotations

from pathlib import Path

_CHUNK = 64 * 1024


def _identical(primary: Path, redundant: Path) -> bool:
    """Byte-compare two metadata files with constant memory usage."""
    if primary.stat().st_size != redundant.stat().st_size:
        return False
    with primary.open("rb") as original, redundant.open("rb") as copy:
        while True:
            block = original.read(_CHUNK)
            if block != copy.read(len(block)):
                return False
            if not block:
                return True


def check_backup_metadata(
    bdmv: Path, collection_stems: dict[str, set[str]],
) -> tuple[list[str], list[str]]:
    """Return errors for inconsistent present copies, warnings for absent copies.

    Some generators omit BACKUP entirely, so missing redundancy does not turn
    an otherwise structurally valid BDMV into a false hard failure. A present
    redundant copy that differs from the primary is an authoring error.
    """
    errors: list[str] = []
    warnings: list[str] = []
    backup_root = bdmv / "BACKUP"
    if backup_root.is_symlink():
        return ["Symlink forbidden: BDMV/BACKUP"], warnings
    if not backup_root.is_dir():
        return errors, ["Missing BDMV/BACKUP; check redundant metadata."]

    names = ["index.bdmv", "MovieObject.bdmv"]
    for folder, extension in (("PLAYLIST", ".mpls"), ("CLIPINF", ".clpi")):
        for stem in sorted(collection_stems.get(folder, set())):
            names.append(f"{folder}/{stem}{extension}")

    for name in names:
        primary = bdmv / name
        duplicate = backup_root / name
        # The caller rejects bad originals; do not inspect their duplicates.
        if primary.is_symlink() or not primary.is_file():
            continue
        # Reject symlinked backup parents too, before following any files.
        if duplicate.parent.is_symlink() or duplicate.is_symlink():
            errors.append(f"Symlink forbidden: BDMV/BACKUP/{name}")
            continue
        if not duplicate.exists():
            warnings.append(f"Missing BDMV/BACKUP/{name}")
            continue
        if not duplicate.is_file():
            errors.append(f"Invalid BDMV/BACKUP/{name}")
            continue
        try:
            if not _identical(primary, duplicate):
                errors.append(f"BDMV/BACKUP metadata differs from primary: {name}")
        except OSError as exc:
            errors.append(f"Cannot compare BDMV/BACKUP/{name}: {exc}")
    return errors, warnings
