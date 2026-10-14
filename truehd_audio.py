"""Lossless TrueHD access units + separately encoded Blu-ray AC-3 companion.

Never decode/re-encode or filter the TrueHD stream. Frames (including Atmos
substreams) are opaque except for their length, timing and major-sync header.
See docs/TRUEHD.md for format references and limits of playback verification.
"""
import hashlib
from pathlib import Path


def label(stream):
    if stream.get('codec_name') == 'truehd':
        return 'TrueHD / Atmos' if 'Atmos' in stream.get('profile', '') else 'TrueHD'
    return str(stream.get('codec_name', '?')).upper()


def paths(target):
    target = Path(target)
    return target.with_suffix('.truehd'), target.with_suffix('.compat.ac3')


def frames(stream, cancel):
    rate = None
    while True:
        if cancel.is_set():
            raise RuntimeError('Обработка TrueHD отменена.')
        head = stream.read(4)
        if not head:
            return
        if len(head) != 4:
            raise ValueError('Оборван заголовок TrueHD.')
        size = (int.from_bytes(head[:2], 'big') & 0xfff) * 2
        if size < 8:
            raise ValueError('Неверная длина кадра TrueHD.')
        frame = head + stream.read(size - 4)
        if len(frame) != size:
            raise ValueError('Оборван кадр TrueHD.')
        if frame[4:8] == b'\xf8\x72\x6f\xba':
            if size < 32 or frame[12:14] != b'\xb7\x52':
                raise ValueError('Повреждён major sync TrueHD.')
            code = frame[8] >> 4
            current = {0: 48000, 1: 96000}.get(code)
            peak = (int.from_bytes(frame[18:20], 'big') & 0x7fff) * (current or 0) // 16
            if current is None or (rate is not None and rate != current) or peak > 18_640_000:
                raise ValueError('TrueHD: неподдерживаемая частота, её изменение или пик выше 18,64 Мбит/с.')
            rate = current
        if rate is None:
            raise ValueError('TrueHD должен начинаться с полного major sync.')
        # input_timing is a decoder-buffer arrival clock, NOT presentation time.
        # Container PTS continuity is checked separately by scan_audio.
        yield frame


def interleave(target, cancel):
    """Insert one 1536-sample AC-3 frame per 38.4 TrueHD access units.

    Integer arithmetic prevents cumulative sync drift on full-length movies.
    Only our generated 640 kb/s, 48 kHz AC-3 companion is accepted here.
    """
    raw, companion = paths(target)
    count = ac_count = 0
    checksum = hashlib.sha256()
    with raw.open('rb') as source, companion.open('rb') as ac3, Path(target).open('wb') as output:
        for frame in frames(source, cancel):
            if count * 5 >= ac_count * 192:
                core = ac3.read(2560)
                if len(core) != 2560 or core[:2] != b'\x0b\x77' or core[4] != 36 or core[5] >> 3 > 10:
                    raise ValueError('Совместимый слой AC-3 отсутствует или повреждён.')
                output.write(core)
                ac_count += 1
            output.write(frame)
            checksum.update(frame)
            count += 1
        # FFmpeg may pad at most one final AC-3 frame. It is not part of TrueHD.
        tail = ac3.read(2561)
        if len(tail) > 2560 or len(tail) not in (0, 2560):
            raise ValueError('Длительность слоя AC-3 не совпадает с TrueHD.')
        if tail:
            if tail[:2] != b'\x0b\x77' or tail[4] != 36 or tail[5] >> 3 > 10:
                raise ValueError('Повреждён завершающий кадр AC-3.')
            output.write(tail)
    if not count:
        raise ValueError('Пустая дорожка TrueHD.')
    return checksum.hexdigest()


def payload_hash(path, index, ffmpeg, cancel):
    """Hash the demuxed encoded bytes; decoding would discard Atmos metadata."""
    from smart_copy import stream_lines
    hashes = []
    def consume(line):
        if line.startswith('SHA256='):
            hashes.append(line[7:].lower())
    stream_lines([ffmpeg, '-v', 'error', '-nostdin', '-progress', 'pipe:1', '-i', str(path), '-map', f'0:{index}',
                  '-c:a', 'copy', '-f', 'hash', '-hash', 'sha256', '-'], cancel, consume)
    if len(hashes) != 1 or len(hashes[0]) != 64:
        raise ValueError('Не удалось проверить SHA-256 потока TrueHD.')
    return hashes[0]
