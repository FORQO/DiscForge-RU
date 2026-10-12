"""Validated logical-to-physical navigation target map for a *future* BD-J Xlet.

This module DOES NOT compile a BD-J application or prove that MPLS PlayMarks,
audio/subtitle PIDs, or bonus playlists actually exist in an authored BDMV.
A later authoring stage must read back and validate those physical objects.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bdj_menu import MenuAction, MenuSpec

_PLAYLIST = re.compile(r"[0-9]{5}", re.ASCII)


def _playlist_id(value: object) -> str:
    if not isinstance(value, str) or _PLAYLIST.fullmatch(value) is None:
        raise ValueError("Playlist ID must contain exactly five ASCII digits")
    return value


def _ordinal(value: object, label: str, limit: int) -> int:
    if type(value) is not int or not 0 <= value <= limit:
        raise ValueError(f"{label} must be an integer in 0..{limit}")
    return value


def _pid(value: object, label: str) -> int:
    # MPEG-TS PIDs below 0x20 are reserved, 0x1FFF is the null packet.
    # Range checking does NOT establish that a PID belongs to an audio/PGS
    # stream or that it was muxed into a referenced playlist.
    if type(value) is not int or not 0x20 <= value <= 0x1FFE:
        raise ValueError(f"{label} must be a non-reserved transport PID")
    return value


@dataclass(frozen=True)
class PlaylistMark:
    playlist_id: str
    mark_index: int

    def __post_init__(self) -> None:
        _playlist_id(self.playlist_id)
        _ordinal(self.mark_index, "PlayMark index", 65535)


@dataclass(frozen=True)
class MenuBindings:
    """Strict, immutable mapping from visible choices to proposed BDMV IDs.

    Mapping is only a manifest until a separate BDMV/M2TS validator verifies
    referenced playlist marks and transport stream PIDs against actual output.
    """

    spec: MenuSpec
    chapters: tuple[PlaylistMark, ...]
    audio_pids: tuple[int, ...] = ()
    subtitle_pids: tuple[int, ...] = ()
    bonus_playlists: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.spec, MenuSpec):
            raise ValueError("spec must be a MenuSpec")
        categories: tuple[tuple[str, tuple[Any, ...], int], ...] = (
            ("chapters", self.chapters, len(self.spec.chapters)),
            ("audio_pids", self.audio_pids, len(self.spec.audio_tracks)),
            ("subtitle_pids", self.subtitle_pids, len(self.spec.subtitle_tracks)),
            ("bonus_playlists", self.bonus_playlists, len(self.spec.bonus_titles)),
        )
        for name, values, expected in categories:
            if not isinstance(values, tuple) or len(values) != expected:
                raise ValueError(f"{name} must be a tuple of {expected} entries")
        if any(not isinstance(mark, PlaylistMark) for mark in self.chapters):
            raise ValueError("chapters must contain PlaylistMark objects")
        if len(set(self.chapters)) != len(self.chapters):
            raise ValueError("Chapter targets must be unique")
        for pid in self.audio_pids:
            _pid(pid, "audio PID")
        for pid in self.subtitle_pids:
            _pid(pid, "subtitle PID")
        if len(set(self.audio_pids + self.subtitle_pids)) != len(self.audio_pids) + len(self.subtitle_pids):
            raise ValueError("Audio/subtitle PID assignments must be unique")
        for playlist in self.bonus_playlists:
            _playlist_id(playlist)
        if len(set(self.bonus_playlists)) != len(self.bonus_playlists):
            raise ValueError("Bonus playlists must be unique")

    def resolve(self, action: MenuAction) -> dict[str, Any]:
        """Resolve a logical action into an *unverified* proposed target.

        Subtitle index -1 maps to disable, not a reserved transport PID.
        Out-of-range actions are rejected before any player command is emitted.
        """
        if not isinstance(action, MenuAction):
            raise ValueError("Expected MenuAction")
        index = action.index
        if type(index) is not int:
            raise ValueError("Action index must be an integer")
        if action.kind == "play":
            _ordinal(index, "chapter index", len(self.chapters) - 1)
            mark = self.chapters[index]
            return {"operation": "jump_chapter", "playlist_id": mark.playlist_id,
                    "mark_index": mark.mark_index}
        if action.kind == "audio":
            _ordinal(index, "audio index", len(self.audio_pids) - 1)
            return {"operation": "select_audio", "pid": self.audio_pids[index]}
        if action.kind == "subtitle":
            if index == -1 and self.subtitle_pids:
                return {"operation": "disable_subtitles"}
            _ordinal(index, "subtitle index", len(self.subtitle_pids) - 1)
            return {"operation": "select_subtitles", "pid": self.subtitle_pids[index]}
        if action.kind == "bonus":
            _ordinal(index, "bonus index", len(self.bonus_playlists) - 1)
            return {"operation": "jump_bonus", "playlist_id": self.bonus_playlists[index]}
        raise ValueError(f"Unsupported menu action: {action.kind}")

    def manifest(self) -> dict[str, Any]:
        """JSON-compatible proposed targets; not compiled BDJO/JAR/HDMV."""
        result = self.spec.manifest()
        result["target_schema_version"] = 1
        result["target_verification"] = "unverified"
        for i, mark in enumerate(self.chapters):
            result["chapters"][i].update(self.resolve(MenuAction("play", i)))
        for i in range(len(self.audio_pids)):
            result["audio_tracks"][i].update(self.resolve(MenuAction("audio", i)))
        for i in range(len(self.subtitle_pids)):
            result["subtitle_tracks"][i].update(self.resolve(MenuAction("subtitle", i)))
        for i in range(len(self.bonus_playlists)):
            result["bonus_titles"][i].update(self.resolve(MenuAction("bonus", i)))
        return result
