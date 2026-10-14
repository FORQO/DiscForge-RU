"""Conservative, independently verified per-track passthrough planning."""
import math
from pathlib import Path
from audio_selection import selected_tracks, stream_map


def copy_format(stream):
    codec = stream.get('codec_name')
    rate = int(stream.get('sample_rate') or 0)
    channels = int(stream.get('channels') or 0)
    bitrate = int(stream.get('bit_rate') or 0)
    if codec == 'ac3' and rate == 48000 and 1 <= channels <= 6 and 0 < bitrate <= 640000:
        return 'ac3', 'A_AC3', bitrate
    if codec == 'truehd' and rate in (48000, 96000) and 1 <= channels <= 8:
        # Reserve the format maximum plus its compatibility AC-3 companion.
        return 'thd+ac3', 'A_AC3', 19_280_000
    if (codec == 'dts' and stream.get('profile') == 'DTS' and rate == 48000
            and 1 <= channels <= 6 and 0 < bitrate <= 1536000):
        return 'dts', 'A_DTS', bitrate
    if codec in ('pcm_s16le', 'pcm_s24le') and rate in (48000, 96000) and channels in (1, 2, 6):
        bits = 16 if codec == 'pcm_s16le' else 24
        return 'wav', 'A_LPCM', bits * rate * channels
    return None


def scan_audio(info, ffprobe, cancel):
    """Check every selected audio packet, even when video must be transcoded."""
    from smart_copy import stream_lines
    tracks = {t['index']: t for t in selected_tracks(info)}
    state = {i: dict(start=None, end=None, bytes=0, packets=0, reason='') for i in tracks}
    first_video = None
    def feed(line):
        nonlocal first_video
        f = dict(x.split('=', 1) for x in line.split('|') if '=' in x)
        index = int(f.get('stream_index', -1))
        if index == info.video_details.get('index'):
            try:
                pts = float(f['pts_time'])
                if math.isfinite(pts):
                    first_video = pts if first_video is None else min(first_video, pts)
            except (KeyError, ValueError):
                pass
        if index not in state or state[index]['reason']:
            return
        s = state[index]
        try:
            pts, size = float(f['pts_time']), int(f['size'])
            truehd = tracks[index].get('codec_name') == 'truehd'
            # Matroska's millisecond time base cannot represent an 833 us AU.
            duration = 1/1200 if truehd and f.get('duration_time', 'N/A') == 'N/A' else float(f['duration_time'])
            if not math.isfinite(pts) or not math.isfinite(duration) or duration <= 0 or size <= 0 or 'C' in f.get('flags', ''):
                raise ValueError('неверный или повреждённый пакет')
            if s['end'] is not None and abs(pts - s['end']) > .002:
                raise ValueError('разрыв временных меток')
            if truehd and s['start'] is not None and abs(pts-s['start']-s['packets']/1200) > .002:
                raise ValueError('временные метки TrueHD не соответствуют числу кадров')
            if tracks[index].get('codec_name') == 'ac3' and (size > 2560 or abs(duration-.032) > .002):
                raise ValueError('пакет AC-3 превышает ограничения')
            if s['start'] is None:
                s['start'] = pts
            s['end'], s['bytes'] = pts + duration, s['bytes'] + size
            s['packets'] += 1
        except (ValueError, KeyError, OverflowError) as exc:
            s['reason'] = str(exc)
    if tracks:
        stream_lines([ffprobe, '-v', 'error', '-show_packets', '-show_entries',
                      'packet=stream_index,pts_time,duration_time,size,flags', '-of', 'compact=p=0', info.path], cancel, feed)
    for index, s in state.items():
        if not copy_format(tracks[index]):
            s['reason'] = 'формат не входит в проверенный набор AC-3 / DTS core / LPCM / TrueHD 48–96 кГц'
        elif (first_video is None or s['start'] is None or s['end'] is None or
              abs(s['start'] - first_video) > .05 or abs(s['end'] - first_video - info.duration) > .1):
            s['reason'] = s['reason'] or 'смещение или длительность звука требует коррекции'
        s['ok'] = not s['reason']
    return state


def plan_audio(info, checks, work, log):
    outputs = []
    for number, track in enumerate(selected_tracks(info)):
        index = track.get('index', 0)
        policy = info.audio_modes.get(str(index), 'auto')
        if policy not in ('auto', 'copy', 'ac3'):
            raise ValueError('Неизвестный режим аудиодорожки.')
        checked = checks.get(index, {})
        fmt = copy_format(track)
        copy = bool(fmt and checked.get('ok') and policy != 'ac3')
        if (policy == 'copy' or track.get('codec_name') == 'truehd' and policy == 'auto') and not copy:
            raise ValueError(f'Аудио #{index}: копирование не подтверждено. ' + checked.get('reason', 'Требуется проверка.'))
        ext, mux, bitrate = fmt if copy else ('ac3', 'A_AC3', 640000)
        stream = dict(track, output_copy=copy, output_format=ext, output_mux=mux, output_bitrate=bitrate)
        if copy and track.get('codec_name') == 'truehd':
            stream['output_codec'] = 'truehd'
            stream['output_average_bitrate'] = math.ceil(checked['bytes'] * 8 / info.duration) + 640000
        outputs.append((Path(work) / f'audio_{number}.{ext}', stream))
        log(f'Аудио #{index}: ' + (f'сохранение {track.get("codec_name")} без потерь' if copy else
            'AC-3 48 кГц, до 5.1' + (' · ' + checked.get('reason', '') if policy != 'ac3' else ' · выбрано вручную')))
        if stream.get('output_codec') == 'truehd':
            log('TrueHD/Atmos: основной поток копируется побитно; отдельно создаётся совместимый слой AC-3.')
    return outputs


def append_audio(command, audio, info):
    if any(stream.get('output_codec') == 'truehd' for _, stream in audio):
        # A decode error in the compatibility layer must not produce a broken
        # fallback track while reporting that authoring succeeded.
        command[1:1] = ['-xerror']
    for target, stream in audio:
        truehd = stream.get('output_codec') == 'truehd'
        if truehd:
            from truehd_audio import paths
            raw, companion = paths(target)
        command += ['-map', stream_map(stream), '-vn', '-sn', '-dn']
        if stream['output_copy']:
            command += ['-c:a', 'copy']
        else:
            command += ['-c:a', 'ac3', '-b:a', '640k', '-ar', '48000', '-ac',
                        '6' if int(stream.get('channels') or 2) > 2 else '2', '-af', 'aresample=async=1:first_pts=0']
        command += ['-f', 'truehd' if truehd else stream['output_format'], str(raw if truehd else target)]
        if truehd:
            command += ['-map', stream_map(stream), '-vn', '-sn', '-dn', '-c:a', 'ac3', '-b:a', '640k',
                        '-ar', '48000', '-ac', '6' if int(stream.get('channels') or 2) > 2 else '2',
                        '-f', 'ac3', str(companion)]
    return command
