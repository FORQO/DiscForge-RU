"""Source audio selection shared by probing, UI, bitrate planning and muxing."""
from dataclasses import replace


def available_tracks(info):
    if info.audio_streams:
        return list(info.audio_streams)
    if info.audio_tracks:
        return [dict(info.audio_details, codec_name=info.audio_codec, channels=info.audio_channels)]
    return []


def selected_tracks(info):
    tracks = available_tracks(info)
    if info.selected_audio is None:
        return tracks[:1]
    return [t for t in tracks if t.get('index', 0) in info.selected_audio]


def select_tracks(info, indices):
    if (not isinstance(indices, (list, tuple)) or len(indices) > 32 or
            any(type(i) is not int for i in indices) or len(indices) != len(set(indices))):
        raise ValueError('Выберите не более 32 разных аудиодорожек.')
    available = {t.get('index', 0) for t in available_tracks(info)}
    if not set(indices) <= available:
        raise ValueError('Выбранная аудиодорожка отсутствует в файле. Выберите дорожки заново.')
    return replace(info, selected_audio=tuple(indices))


def audio_outputs(info, audio):
    if audio is None:
        return []
    if isinstance(audio, list):
        return audio
    return [(audio, (selected_tracks(info) or [{}])[0])]


def language(stream):
    tags = stream.get('tags') or {}
    raw = tags.get('language', 'und') if isinstance(tags, dict) else 'und'
    code = raw.lower().strip() if isinstance(raw, str) else 'und'
    code = {'en':'eng','ru':'rus','de':'deu','fr':'fra','es':'spa','ja':'jpn','it':'ita','pt':'por','uk':'ukr'}.get(code, code)
    return code if len(code) == 3 and code.isascii() and code.isalpha() else 'und'


def stream_map(stream):
    return f"0:{stream['index']}" if 'index' in stream else '0:a:0'
