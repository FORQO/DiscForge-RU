"""Cross-check authored metadata, transport streams and generated menu actions."""
from pathlib import Path
import zipfile
from build_cache import digest
from disc_audio import playlist_tracks, playlist_subtitles
from disc_menu import validate_menu_chapters, validate_menu_navigation, pages, resolve_action


def verify_disc(root, info, audio, subtitles, chapters, menu, ffprobe, cancel, log, *, ffmpeg=None, verification=None):
    from core import _run_probe
    root = Path(root)
    playlist = root / 'BDMV/PLAYLIST/00000.mpls'
    actual_audio, actual_subs = playlist_tracks(playlist, coding=True), playlist_subtitles(playlist)
    if len(actual_audio) != len(audio) or len(actual_subs) != len(subtitles):
        raise ValueError('Количество дорожек MPLS не совпадает с проектом.')
    if chapters is not None:
        validate_menu_chapters(playlist, list(chapters))
    from audio_selection import language
    for expected, actual in zip([language(t) for _, t in audio], actual_audio):
        if expected != actual['language']:
            raise ValueError('Язык аудио MPLS не совпадает с проектом.')
    for (_, expected), actual in zip(subtitles, actual_subs):
        if expected['language'] != actual['language']:
            raise ValueError('Язык субтитров MPLS не совпадает с проектом.')
    media = sorted((root / 'BDMV/STREAM').glob('*.m2ts'))
    if len(media) != 1:
        raise ValueError('Ожидался один основной M2TS.')
    data = _run_probe(str(media[0]), ffprobe)
    streams = data.get('streams', [])
    sound = [s for s in streams if s.get('codec_type') == 'audio']
    captions = [s for s in streams if s.get('codec_type') == 'subtitle']
    video = [s for s in streams if s.get('codec_type') == 'video']
    if len(video) != 1 or video[0].get('codec_name') != 'h264':
        raise ValueError('Неверный видеопоток собранного диска.')
    lossless = []
    for observed, expected, plans in ((sound, actual_audio, audio), (captions, actual_subs, subtitles)):
        by_pid = {}
        for s in observed:
            by_pid.setdefault(int(str(s.get('id', '-1')), 0), []).append(s)
        if set(by_pid) != {t['pid'] for t in expected}:
            raise ValueError('Дорожки M2TS не совпадают с MPLS.')
        for track, (elementary, plan) in zip(expected, plans):
            candidates = by_pid[track['pid']]
            wanted = plan.get('output_codec') or {'A_AC3': 'ac3', 'A_DTS': 'dts', 'A_LPCM': 'pcm_bluray'}.get(plan.get('output_mux'), 'hdmv_pgs_subtitle')
            if sorted(s.get('codec_name', '') for s in candidates) != sorted(['truehd', 'ac3'] if wanted == 'truehd' else [wanted]):
                raise ValueError('Состав основного и совместимого звука M2TS не совпадает с проектом.')
            stream = next(s for s in candidates if s.get('codec_name') == wanted)
            codec = stream.get('codec_name')
            if codec != wanted:
                raise ValueError(f'Формат дорожки M2TS: ожидался {wanted}, получен {codec}.')
            if plan.get('output_mux'):
                expected_rate = int(plan.get('sample_rate') or 48000) if plan.get('output_copy') else 48000
                expected_channels = int(plan.get('channels') or 2) if plan.get('output_copy') else (6 if int(plan.get('channels') or 2) > 2 else 2)
                if int(stream.get('sample_rate') or 0) != expected_rate or int(stream.get('channels') or 0) != expected_channels:
                    raise ValueError('Частота или число каналов звука изменились при авторинге.')
            if wanted == 'truehd':
                if track.get('coding_type') != 0x83:
                    raise ValueError('MPLS не объявляет дорожку TrueHD.')
                companion = next(s for s in candidates if s.get('codec_name') == 'ac3')
                expected_core_channels = 6 if int(plan.get('channels') or 2) > 2 else 2
                if int(companion.get('sample_rate') or 0) != 48000 or int(companion.get('channels') or 0) != expected_core_channels:
                    raise ValueError('Совместимый слой AC-3 отсутствует или повреждён.')
                if not ffmpeg:
                    raise ValueError('FFmpeg нужен для побитной проверки TrueHD.')
                from truehd_audio import payload_hash
                log('Сверяем SHA-256 TrueHD/Atmos в исходнике и собранном M2TS…')
                original = payload_hash(info.path, plan['index'], ffmpeg, cancel)
                authored = payload_hash(media[0], stream['index'], ffmpeg, cancel)
                if original != authored:
                    raise ValueError('Поток TrueHD/Atmos изменился при авторинге. ISO не будет сохранён.')
                from truehd_audio import paths
                core_hash = payload_hash(media[0], companion['index'], ffmpeg, cancel)
                if core_hash != digest(paths(elementary)[1], cancel)['sha256']:
                    raise ValueError('Совместимый слой AC-3 изменился при авторинге.')
                lossless.append(dict(source_index=plan['index'], pid=track['pid'], sha256=original,
                                     profile=stream.get('profile', 'TrueHD'), bit_exact=True, ac3_companion_verified=True))
    duration = float(video[0].get('duration') or data.get('format', {}).get('duration') or 0)
    if abs(duration - info.duration) > max(.3, info.duration * .0001):
        raise ValueError('Длительность диска не совпадает с исходником.')
    button_count = 0
    if menu is not None:
        validate_menu_navigation(list(chapters), menu)
        from iso_menu_update import open_menu
        open_menu(root)  # bounded JAR, action ranges, chapter references and page bounds
        with zipfile.ZipFile(root / 'BDMV/JAR/00000.jar') as jar:
            props = dict(line.split('=', 1) for line in jar.read('menu.properties').decode('ascii').splitlines() if '=' in line)
            expected_pages = pages(list(chapters), menu)
            if (int(props['pages']) != len(expected_pages) or int(props['audio.count']) != len(audio)
                    or int(props['subtitle.count']) != len(subtitles)):
                raise ValueError('Инвентарь меню не совпадает с проектом.')
            for page, entries in enumerate(expected_pages):
                if int(props[f'page.{page}.count']) != len(entries) or f'page{page}.png' not in jar.namelist():
                    raise ValueError('Страница меню неполна.')
                for index, (_, action) in enumerate(entries):
                    prefix = f'page.{page}.{index}'
                    if props[prefix] != resolve_action(menu, action) or props.get(prefix+'.focus') not in jar.namelist():
                        raise ValueError('Кнопка меню ведёт не к объекту проекта или отсутствует её изображение.')
                    button_count += 1
    log('Связи меню, глав, MPLS и PID аудио/субтитров проверены. Считаем SHA-256 файлов диска…')
    from verification_progress import VerificationProgress
    paths = [p for p in sorted(root.rglob('*')) if p.is_file()]
    tracker = VerificationProgress(sum(p.stat().st_size for p in paths), len(paths), verification)
    files = {p.relative_to(root).as_posix(): tracker.digest(p, cancel, 'Контрольные суммы файлов Blu-ray', p.relative_to(root).as_posix()) for p in paths}
    tracker.finish()
    return {'passed': True, 'audio_tracks': len(audio), 'subtitle_tracks': len(subtitles), 'lossless_truehd': lossless,
            'chapters': len(chapters or []), 'buttons': button_count, 'duration': duration, 'files': files,
            'scope': 'BDMV, MPLS/CLPI, menu actions, M2TS codecs/PIDs/duration, SHA-256',
            'playback_tested': False, 'hardware_player_tested': False}


def verify_image_payload(image, expected, cancel, tracker=None):
    from iso_menu_update import mounted_iso
    if tracker:
        tracker.status('Подключение ISO · ожидание тома Windows до 15 с')
    with mounted_iso(image) as root:
        if tracker:
            tracker.status('Проверка списка файлов внутри ISO')
        actual_names = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        if actual_names != set(expected):
            raise ValueError('Состав ISO отличается от проверенной структуры диска.')
        for name, checksum in expected.items():
            actual = (tracker.digest(root / name, cancel, '1/2 · Сверка файлов внутри ISO', name)
                      if tracker else digest(root / name, cancel))
            if actual != checksum:
                raise ValueError('Данные внутри ISO повреждены: ' + name)
        if tracker:
            tracker.status('Отключение проверочного тома ISO')
