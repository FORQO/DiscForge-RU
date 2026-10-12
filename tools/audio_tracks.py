"""Pure, dependency-free source audio-stream discovery for Full HD authoring.

Stream numbers refer to FFmpeg audio-only indices (0:a:N), not global
container stream IDs. This module does not select or mux audio on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class AudioTrackError(ValueError):
    """Source audio metadata or requested audio index is invalid."""


@dataclass(frozen=True)
class AudioTrack:
    index: int
    codec: str
    channels: int
    language: str

    @property
    def ffmpeg_map(self) -> str:
        return f"0:a:{self.index}"


def parse_audio_tracks(probe_data: dict[str, Any]) -> tuple[AudioTrack, ...]:
    """Return audio tracks in ffprobe stream order; fail closed on bad metadata."""
    if not isinstance(probe_data, dict) or not isinstance(probe_data.get("streams"), list):
        raise AudioTrackError("ffprobe streams must be a list")
    found: list[AudioTrack] = []
    for stream in probe_data["streams"]:
        if not isinstance(stream, dict):
            continue
        if stream.get("codec_type") != "audio":
            continue
        channels = stream.get("channels")
        if type(channels) is not int or not 1 <= channels <= 32:
            raise AudioTrackError("audio channel count must be an integer from 1 to 32")
        tags = stream.get("tags")
        language = tags.get("language", "und") if isinstance(tags, dict) else "und"
        if not isinstance(language, str):
            language = "und"
        found.append(AudioTrack(len(found), str(stream.get("codec_name") or "unknown"),
                                channels, language))
    return tuple(found)


def select_audio_track(tracks: tuple[AudioTrack, ...], index: int) -> AudioTrack:
    """Select exactly one existing audio-only stream, rejecting bool indices."""
    if type(index) is not int or not 0 <= index < len(tracks):
        raise AudioTrackError(f"audio index {index!r} is unavailable (tracks: {len(tracks)})")
    return tracks[index]
