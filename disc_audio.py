"""Primary audio inventory: source metadata for labels, MPLS order for controls."""
from dataclasses import replace
import re
from audio_selection import selected_tracks, language


def menu_tracks(info):
    return [dict(language=language(t), title=' '.join(''.join(c if ord(c) >= 32 else ' ' for c in str((t.get('tags') or {}).get('title', ''))).split())[:80])
            for t in selected_tracks(info)] if info else []


def track_label(track, number, locale):
    names = {'rus': ('Русский', 'Russian'), 'eng': ('Английский', 'English'),
             'fra': ('Французский', 'French'), 'fre': ('Французский', 'French'),
             'deu': ('Немецкий', 'German'), 'ger': ('Немецкий', 'German'),
             'jpn': ('Японский', 'Japanese'), 'spa': ('Испанский', 'Spanish'),
             'ita': ('Итальянский', 'Italian'), 'ukr': ('Украинский', 'Ukrainian'),
             'und': ('Без указания языка', 'Unspecified language')}
    lang = track['language']
    name = names.get(lang, (lang.upper(), lang.upper()))[locale == 'en']
    return f'{number:02} · {name}' + (' · ' + track['title'] if track['title'] else '')


def playlist_tracks(path, *, subtitles=False):
    """Read bounded primary audio STN entries, requiring consistent PlayItems.

    BD-J stream numbers are one-based positions, never ffprobe indices or PIDs.
    Unsupported angles/subpaths are rejected instead of guessing a voice.
    """
    with path.open('rb') as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    def require(condition):
        if not condition:
            raise ValueError('Не удалось проверить аудиодорожки в плейлисте Blu-ray.')
    def u16(at): return int.from_bytes(data[at:at+2], 'big')
    def u32(at): return int.from_bytes(data[at:at+4], 'big')
    require(40 <= len(data) <= 16 * 1024 * 1024 and data[:8] in (b'MPLS0100', b'MPLS0200'))
    start = u32(8)
    require(start >= 40 and start+10 <= len(data))
    end = start+4+u32(start)
    require(start+10 <= end <= len(data) and u16(start+6) > 0 and u16(start+8) == 0)
    cursor, inventory = start+10, None
    for _ in range(u16(start+6)):
        require(cursor+2 <= end)
        item_end = cursor+2+u16(cursor)
        item = cursor+2
        require(item+48 <= item_end <= end and not data[item+10] & 0x10)
        stn = item+32
        stn_end = stn+2+u16(stn)
        require(stn+16 <= stn_end <= item_end)
        videos, audios, captions = data[stn+4], data[stn+5], data[stn+6]
        require(audios <= 32 and captions <= 32)
        pos, tracks = stn+16, []
        for index in range(videos+audios+(captions if subtitles else 0)):
            require(pos < stn_end)
            entry_end = pos+1+data[pos]
            require(pos+4 <= entry_end < stn_end)
            stream_type = data[pos+1]
            pid = u16(pos+2)
            attr_end = entry_end+1+data[entry_end]
            require(entry_end+2 <= attr_end <= stn_end)
            if videos <= index < videos+audios and not subtitles:
                require(stream_type == 1 and entry_end+6 <= attr_end and
                        data[entry_end+1] in (0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0xa1, 0xa2))
                lang = data[entry_end+3:entry_end+6].decode('ascii', errors='replace').lower()
                require(bool(re.fullmatch('[a-z]{3}', lang)))
                tracks.append(dict(language=lang, title='', pid=pid))
            if subtitles and index >= videos+audios:
                require(stream_type == 1 and entry_end+5 <= attr_end and data[entry_end+1] == 0x90)
                lang = data[entry_end+2:entry_end+5].decode('ascii', errors='replace').lower()
                require(bool(re.fullmatch('[a-z]{3}', lang)))
                tracks.append(dict(language=lang, title='', pid=pid))
            pos = attr_end
        require(len({t['pid'] for t in tracks}) == len(tracks))
        if inventory is not None:
            require(inventory == tracks)
        inventory = tracks
        cursor = item_end
    return inventory


def bind_tracks(design, playlist, metadata=None):
    actual = playlist_tracks(playlist)
    labels = design.audio_tracks if metadata is None else metadata
    if metadata is not None and (len(labels) != len(actual) or
            any(a['language'] != b['language'] for a, b in zip(labels, actual))):
        raise ValueError('Аудиодорожки меню не совпадают с собранным плейлистом.')
    tracks = [dict(language=t['language'], title=(labels[i]['title'] if i < len(labels)
                   and labels[i]['language'] == t['language'] else '')) for i, t in enumerate(actual)]
    return replace(design, audio_tracks=tracks)


def playlist_subtitles(path):
    return playlist_tracks(path, subtitles=True)


def bind_subtitles(design, playlist, metadata=None):
    actual = playlist_subtitles(playlist)
    labels = design.subtitle_tracks if metadata is None else metadata
    if metadata is not None and (len(labels) != len(actual) or
            any(a['language'] != b['language'] for a, b in zip(labels, actual))):
        raise ValueError('Субтитры меню не совпадают с собранным плейлистом.')
    tracks = [dict(language=t['language'], title=(labels[i]['title'] if i < len(labels)
                   and labels[i]['language'] == t['language'] else '')) for i, t in enumerate(actual)]
    return replace(design, subtitle_tracks=tracks)
