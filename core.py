"""DiscForge RU backend: media probing, BD budget and Blu-ray job pipeline.

This module has no GUI dependencies and is independently unit-testable.
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Sequence

from bdmv_preflight import inspect as inspect_bdmv
from tools.iso_udf_preflight import inspect as inspect_iso
from disc_menu import MenuDesign, install_menu
from windows_iso import create_iso, pending_image
from audio_selection import selected_tracks, select_tracks, audio_outputs, stream_map, language

SUPPORTED = {'.mkv', '.mp4', '.mov', '.m2ts', '.mts', '.ts', '.avi'}
CAPACITIES = {'BD25': 25_000_000_000, 'BD50': 50_000_000_000}
HDR_TRANSFERS = {'smpte2084', 'arib-std-b67'}


class DiscForgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration: float
    size: int
    video_codec: str
    width: int
    height: int
    video_fps: str
    video_color_transfer: str
    audio_codec: str
    audio_channels: int
    audio_tracks: int
    subtitle_tracks: int
    video_details: dict = field(default_factory=dict)
    audio_details: dict = field(default_factory=dict)
    copy_report: object = None
    audio_streams: tuple = ()
    selected_audio: tuple | None = None
    subtitle_streams: tuple = ()
    selected_subtitles: tuple = ()
    source_chapters: tuple = ()
    chapter_notices: tuple = ()

    @property
    def is_hdr(self) -> bool:
        return self.video_color_transfer in HDR_TRANSFERS

    @property
    def name(self) -> str:
        return Path(self.path).stem


def _run_probe(path: str, ffprobe: str = 'ffprobe') -> dict:
    cmd = [ffprobe, '-v', 'error', '-show_format', '-show_streams', '-show_chapters', '-of', 'json', path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except FileNotFoundError as e:
        raise DiscForgeError('Не найден ffprobe. Укажите путь к ffprobe в настройках.') from e
    except subprocess.TimeoutExpired as e:
        raise DiscForgeError('ffprobe не ответил за 45 секунд.') from e
    if p.returncode != 0:
        raise DiscForgeError('Ошибка анализа файла: ' + p.stderr[-400:])
    try:
        return json.loads(p.stdout)
    except ValueError as e:
        raise DiscForgeError('ffprobe вернул некорректный JSON.') from e


def parse_probe(path: str, data: dict) -> MediaInfo:
    if not isinstance(data, dict) or not isinstance(data.get('streams'), list):
        raise DiscForgeError('ffprobe вернул некорректные данные о потоках.')
    streams = [s for s in data['streams'] if isinstance(s, dict)]
    videos = [s for s in streams if s.get('codec_type') == 'video' and not (
        s.get('disposition', {}).get('attached_pic') if isinstance(s.get('disposition'), dict) else False
    )]
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    subs = [s for s in streams if s.get('codec_type') == 'subtitle']
    if not videos:
        raise DiscForgeError('В файле не обнаружен видеопоток.')
    v = videos[0]
    fmt = data.get('format') or {}
    if not isinstance(fmt, dict):
        raise DiscForgeError('ffprobe вернул некорректные данные о файле.')
    try:
        duration = float(fmt.get('duration') or v.get('duration') or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DiscForgeError('Не удалось определить длительность фильма.') from exc
    if not math.isfinite(duration) or duration <= 0:
        raise DiscForgeError('Не удалось определить длительность фильма.')
    a = audios[0] if audios else {}
    def nonnegative_int(value: object, label: str) -> int:
        try:
            result = int(value or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DiscForgeError(f'ffprobe вернул некорректное значение: {label}.') from exc
        if result < 0:
            raise DiscForgeError(f'ffprobe вернул некорректное значение: {label}.')
        return result

    from source_chapters import import_chapters
    source_chapters, chapter_notices = import_chapters(data.get('chapters'), duration)
    return MediaInfo(
        path=path, duration=duration,
        size=nonnegative_int(fmt.get('size') or (os.path.getsize(path) if os.path.isfile(path) else 0), 'размер'),
        video_codec=str(v.get('codec_name') or '?'),
        width=nonnegative_int(v.get('width'), 'ширина'), height=nonnegative_int(v.get('height'), 'высота'),
        video_fps=str(v.get('avg_frame_rate') or v.get('r_frame_rate') or '?'),
        video_color_transfer=str(v.get('color_transfer') or '').lower(),
        audio_codec=str(a.get('codec_name') or 'нет'),
        audio_channels=nonnegative_int(a.get('channels'), 'аудиоканалы'),
        audio_tracks=len(audios), subtitle_tracks=len(subs),
        video_details=dict(v), audio_details=dict(a),
        audio_streams=tuple(dict(s) for s in audios),
        subtitle_streams=tuple(dict(s) for s in subs),
        source_chapters=source_chapters, chapter_notices=chapter_notices,
    )


def probe_media(path: str, ffprobe: str = 'ffprobe') -> MediaInfo:
    return parse_probe(path, _run_probe(path, ffprobe))


def bitrate_plan(duration: float, profile: str, has_audio: bool) -> int:
    if profile not in CAPACITIES:
        raise DiscForgeError('Неизвестный профиль диска: ' + profile)
    if not math.isfinite(duration) or duration <= 0:
        raise DiscForgeError('Некорректная длительность.')
    # Leave 7% spare room for transport stream mux overhead, filesystem, menus and rate fluctuations.
    room = CAPACITIES[profile] * 0.93
    audio_bps = 640_000 * int(has_audio)
    video_bps = int(room * 8 / duration - audio_bps)
    video_bps = min(video_bps, 28_000_000, 44_000_000-audio_bps)  # keep within conservative BD encoding bounds
    if video_bps < 5_000_000:
        raise DiscForgeError('Фильм слишком длинный для выбранного диска при минимальном качестве 5 Мбит/с.')
    return video_bps


def make_video_command(ffmpeg: str, src: str, dst: str, bitrate: int, nvenc: bool, fast: bool = False) -> list[str]:
    # 1080p23.976 H.264/8-bit. 25/30/50/60 fps files have their frame rate converted.
    vf = ('fps=24000/1001,scale=1920:1080:force_original_aspect_ratio=decrease,'
          'pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1')
    cmd = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-progress', 'pipe:1',
           '-i', src, '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', vf, '-pix_fmt', 'yuv420p',
           '-r', '24000/1001', '-c:v', 'h264_nvenc' if nvenc else 'libx264',
           '-profile:v', 'high', '-level:v', '4.1', '-b:v', str(bitrate),
           '-maxrate', '30000000', '-bufsize', '30000000', '-g', '24',
           '-bf', '3']
    if not nvenc:
        cmd += ['-preset', 'fast' if fast else 'medium', '-x264-params', 'bluray-compat=1']
    else:
        cmd += ['-preset', 'p3' if fast else 'p5', '-bluray-compat', '1']
    cmd += ['-f', 'h264', dst]
    return cmd


def make_audio_command(ffmpeg: str, src: str, dst: str, channels: int) -> list[str]:
    return [ffmpeg, '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-progress', 'pipe:1',
            '-i', src, '-map', '0:a:0', '-vn', '-sn', '-dn', '-c:a', 'ac3', '-b:a', '640k',
            '-ar', '48000', '-ac', '6' if channels > 2 else '2', '-f', 'ac3', dst]


def make_encode_command(ffmpeg, info, video, audio, bitrate, nvenc, fast=False):
    cmd = make_video_command(ffmpeg, info.path, str(video), bitrate, nvenc, fast)
    tracks = audio_outputs(info, audio)
    cmd[cmd.index('-maxrate')+1] = str(min(30_000_000, 44_000_000-640_000*len(tracks)))
    for target, stream in tracks:
        # Both elementary streams from one demux pass; no second read of the movie.
        cmd += ['-map', stream_map(stream), '-vn', '-sn', '-dn', '-c:a', 'ac3', '-b:a', '640k',
                '-ar', '48000', '-ac', '6' if int(stream.get('channels', info.audio_channels)) > 2 else '2',
                '-af', 'aresample=async=1:first_pts=0', '-f', 'ac3', str(target)]
    return cmd


def prepare_processing(info, mode, profile, ffprobe, ffmpeg, cancel, log):
    from smart_copy import analyze_copy, choose_mode, signature, MODES
    if mode not in MODES:
        raise DiscForgeError('Неизвестный режим обработки.')
    if mode == 'encode':
        return info, 'encode', 'Выбрано полное перекодирование.'
    report = info.copy_report
    if report is not None and report.signature != signature(info.path):
        raise DiscForgeError('Исходник изменился после анализа. Удалите его из очереди и добавьте заново.')
    if report is None:
        log('Проверяем возможность копирования потоков без перекодирования…')
        info = replace(probe_media(info.path, ffprobe), selected_audio=info.selected_audio)
        report = analyze_copy(info, ffprobe, ffmpeg, cancel)
        info = replace(info, copy_report=report)
    selected, reason = choose_mode(mode, report, info, CAPACITIES[profile])
    return info, selected, reason


def signature_changed(info):
    from smart_copy import signature
    return info.copy_report.signature != signature(info.path)


def make_copy_command(ffmpeg, info, video, audio, convert_audio=False):
    cmd = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-progress', 'pipe:1',
           '-i', info.path, '-map', f"0:{info.video_details['index']}", '-an', '-sn', '-dn',
           '-c:v', 'copy', '-bsf:v', 'h264_mp4toannexb', '-f', 'h264', str(video)]
    for target, stream in audio_outputs(info, audio):
        cmd += ['-map', stream_map(stream), '-vn', '-sn', '-dn']
        checked = getattr(info.copy_report, 'audio_results', {}).get(stream.get('index'), {})
        if convert_audio and not checked.get('ok', False):
            cmd += ['-c:a', 'ac3', '-b:a', '640k', '-ar', '48000', '-ac', '6' if int(stream.get('channels', info.audio_channels))>2 else '2',
                    '-af', 'aresample=async=1:first_pts=0']
        else:
            cmd += ['-c:a', 'copy']
        cmd += ['-f', 'ac3', str(target)]
    return cmd


def nvenc_available(ffmpeg, cancel, log):
    """Test the actual driver/encoder, not just the list of compiled codecs."""
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
               'color=size=1920x1080:rate=24000/1001', '-frames:v', '2',
               '-c:v', 'h264_nvenc', '-preset', 'p3', '-bluray-compat', '1', '-f', 'null', '-']
    # Use a separate cancellation token so a failed probe never cancels the build.
    probe_cancel = threading.Event()
    def stop_probe():
        probe_cancel.set()
    timer = threading.Timer(15, stop_probe)
    timer.start()
    try:
        if cancel.is_set():
            raise DiscForgeError('Операция отменена пользователем.')
        class Cancellation:
            def is_set(self): return cancel.is_set() or probe_cancel.is_set()
            def wait(self, seconds):
                cancel.wait(seconds)
                return self.is_set()
        run_command(command, 'Проверка NVIDIA NVENC', 0, 1, 1, Cancellation(), log, lambda _: None)
        return True
    except DiscForgeError as exc:
        if cancel.is_set():
            raise
        log('NVENC недоступен; выбран CPU x264. ' + str(exc))
        return False
    finally:
        timer.cancel()


def validate_chapters(chapters: Sequence[float], duration: float | None = None) -> None:
    if not chapters:
        raise DiscForgeError('Главы должны начинаться с отметки 00:00:00.')
    try:
        valid = all(not isinstance(t, bool) and isinstance(t, (int, float)) and
                    math.isfinite(t) and t >= 0 for t in chapters)
    except OverflowError:
        valid = False
    if not valid:
        raise DiscForgeError('В списке глав есть недопустимая отметка.')
    if chapters[0] != 0:
        raise DiscForgeError('Главы должны начинаться с отметки 00:00:00.')
    if any(b <= a for a, b in zip(chapters, chapters[1:])):
        raise DiscForgeError('Отметки глав должны идти по возрастанию.')
    if duration is not None and chapters[-1] >= duration:
        raise DiscForgeError('Отметка главы должна быть меньше длительности фильма.')


def write_meta(path: Path, video: Path, audio: Path | None,
               chapters: Sequence[float] | None = None, fps: str = '23.976', *, subtitles=(), width=1920, height=1080) -> None:
    # Convert to slash separators; absolute paths are encoded as UTF-8 and quoted.
    def escaped(f: Path) -> str:
        return '"' + f.resolve().as_posix().replace('"', '') + '"'
    if chapters is None:
        opts = '--auto-chapters=10'
    else:
        validate_chapters(chapters)
        def fmt(sec: float) -> str:
            millis = round(sec * 1000)
            h, rest = divmod(millis, 3600000)
            m, rest = divmod(rest, 60000)
            s, ms = divmod(rest, 1000)
            return f'{h:02d}:{m:02d}:{s:02d}.{ms:03d}'
        opts = '--custom-chapters=' + ';'.join(fmt(sec) for sec in chapters)
    content = 'MUXOPT --blu-ray ' + opts + '\n'
    content += f'V_MPEG4/ISO/AVC, {escaped(video)}, fps={fps}\n'
    tracks = audio if isinstance(audio, list) else [(audio,{})] if audio is not None else []
    for target, stream in tracks:
        content += f'A_AC3, {escaped(target)}, lang={language(stream)}\n'
    from subtitles import meta_lines
    content += meta_lines(subtitles, fps, width, height)
    path.write_text(content, encoding='utf-8')


def safe_name(value: str) -> str:
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')
    return result[:90] or 'Movie'


def unique_target(root: Path, stem: str, iso: bool) -> Path:
    name = safe_name(stem)
    base = root / (name + '.iso' if iso else name)
    number = 2
    while base.exists():
        base = root / (f'{name}_{number}.iso' if iso else f'{name}_{number}')
        number += 1
    return base


def _require_binary(binary: str, label: str) -> None:
    if not binary:
        raise DiscForgeError('Не указан путь к ' + label)
    if Path(binary).is_file() or shutil.which(binary):
        return
    raise DiscForgeError(f'Не найден {label}: {binary}. Проверьте настройки.')


def run_command(cmd: list[str], stage: str, pct_start: int, pct_width: int,
                duration: float, cancel: threading.Event,
                log: Callable[[str], None], progress: Callable[[int], None]) -> None:
    log(f'{stage}...')
    if cancel.is_set():
        raise DiscForgeError('Операция отменена пользователем.')
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors='replace', bufsize=1, creationflags=creationflags)
    except OSError as e:
        raise DiscForgeError(f'Не удаётся запустить {Path(cmd[0]).name}: {e}') from e
    errors = []
    output: queue.SimpleQueue[str | None] = queue.SimpleQueue()
    def read_output() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                output.put(line)
        finally:
            output.put(None)
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        while True:
            if cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise DiscForgeError('Операция отменена пользователем.')
            try:
                raw = output.get(timeout=0.1)
            except queue.Empty:
                continue
            if raw is None:
                break
            line = raw.strip()
            if line.startswith('out_time='):
                try:
                    h, m, s = line.partition('=')[2].split(':')
                    seconds = int(h)*3600 + int(m)*60 + float(s)
                    progress(pct_start + max(0, min(pct_width-0.01, seconds/duration*pct_width)))
                except (ValueError, ZeroDivisionError):
                    pass
            elif line and not re.match(r'^(frame|fps|bitrate|total_size|out_time_\w+|speed|progress|dup_frames|drop_frames)=', line):
                errors.append(line)
                if len(errors) > 25:
                    errors.pop(0)
        while proc.poll() is None:
            if cancel.wait(0.1):
                raise DiscForgeError('Операция отменена пользователем.')
        returncode = proc.returncode
        if cancel.is_set():
            raise DiscForgeError('Операция отменена пользователем.')
        if returncode != 0:
            raise DiscForgeError(f'{stage} завершён с ошибкой (код {returncode}): ' + '\n'.join(errors[-5:]))
        progress(pct_start + pct_width)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(timeout=2)
        if proc.stdout is not None:
            proc.stdout.close()




def run_tsmuxer(tsmuxer: str, meta: Path, destination: Path, cancel: threading.Event,
                 log: Callable[[str], None], progress=None) -> None:
    """Run tsMuxer with cancellation and bounded stderr capture."""
    if cancel.is_set():
        raise DiscForgeError('Операция отменена пользователем.')
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    try:
        proc = subprocess.Popen([tsmuxer, str(meta), str(destination)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors='replace', creationflags=creationflags)
    except OSError as exc:
        raise DiscForgeError(f'Не удалось запустить tsMuxer: {exc}') from exc
    lines: list[str] = []
    def read_output() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if progress:
                match = re.search(r'(\d+(?:\.\d+)?)%', line)
                if match:
                    progress(min(0.999, float(match.group(1))/100))
            if line.strip():
                lines.append(line.strip())
                if len(lines) > 40:
                    del lines[:10]
    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        while proc.poll() is None:
            if cancel.wait(0.25):
                proc.terminate()
                try:
                    proc.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise DiscForgeError('Операция отменена пользователем.')
        reader.join(timeout=2)
        if cancel.is_set():
            raise DiscForgeError('Операция отменена пользователем.')
        if proc.returncode:
            raise DiscForgeError('tsMuxer завершился с ошибкой: ' + '\n'.join(lines[-12:]))
        if progress:
            progress(1.0)
        log('tsMuxer: структура создана.')
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        reader.join(timeout=2)
        if proc.stdout is not None:
            proc.stdout.close()


def create_bluray(info: MediaInfo, profile: str, output_root: str,
                   ffmpeg: str, tsmuxer: str, iso: bool, nvenc: bool,
                   cancel: threading.Event, log: Callable[[str], None],
                   progress: Callable[[int], None],
                   chapters: Sequence[float] | None = None,
                   menu: MenuDesign | None = None, *, fast: bool = False, observer=None, processing_mode='encode', ffprobe='ffprobe') -> Path:
    """Encode to BD-oriented H.264 + AC3, then author BDMV directory or image.

    This is the first compatible-oriented implementation, NOT independently verified BD spec compliance.
    """
    if not Path(info.path).is_file():
        raise DiscForgeError('Исходный файл не найден.')
    if info.selected_audio is not None:
        try:
            info = select_tracks(info, info.selected_audio)
        except ValueError as exc:
            raise DiscForgeError(str(exc)) from exc
    from subtitles import select_subtitles, prepare_subtitles, menu_subtitles
    info = select_subtitles(info, info.selected_subtitles)
    if menu is not None:
        if not isinstance(menu, MenuDesign):
            raise DiscForgeError('Некорректное меню проекта.')
        from disc_audio import menu_tracks
        menu = replace(menu, audio_tracks=menu_tracks(info), subtitle_tracks=menu_subtitles(info))
    info, selected_mode, recommendation = prepare_processing(info, processing_mode, profile, ffprobe, ffmpeg, cancel, log)
    log(recommendation)
    copying = selected_mode != 'encode'
    if info.is_hdr:
        raise DiscForgeError('HDR10/HLG пока не обрабатывается. Для этого файла нужен будущий UHD-модуль.')
    if cancel.is_set():
        raise DiscForgeError('Операция отменена пользователем.')
    if chapters is None and info.source_chapters:
        chapters = list(info.source_chapters)
    if chapters is not None:
        validate_chapters(chapters, info.duration)
    if menu is not None:
        if not isinstance(menu, MenuDesign):
            raise DiscForgeError('Некорректное меню проекта.')
        if menu.background:
            from disc_menu import background_image
            background_image(menu.background)
        if iso and os.name != 'nt':
            raise DiscForgeError('ISO с меню пока доступен на Windows. Выберите папку BDMV.')
        chapters = list(chapters) if chapters is not None else [0.0] + list(range(600, int(info.duration), 600))
        if len(chapters) > 999:
            raise DiscForgeError('Меню поддерживает не более 999 глав.')
        from disc_menu import validate_menu_navigation
        validate_menu_navigation(list(chapters), menu)
    _require_binary(ffmpeg, 'FFmpeg')
    _require_binary(tsmuxer, 'tsMuxeR')
    tracks = selected_tracks(info)
    if len(tracks) > 32:
        raise DiscForgeError('Blu-ray поддерживает не более 32 выбранных аудиодорожек.')
    bitrate = int(info.copy_report.video_bytes*8/info.duration) if copying else bitrate_plan(info.duration, profile, len(tracks))
    root = Path(output_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    estimated_stream = info.duration * (bitrate + 640_000 * len(tracks)) / 8 * 1.08
    required = int(estimated_stream * (3 if iso and menu is not None else 2)) + 256_000_000
    if shutil.disk_usage(root).free < required:
        raise DiscForgeError(f'Недостаточно места для сборки. Освободите примерно {required/1e9:.1f} ГБ в выходной папке.')
    target = unique_target(root, info.name, iso)
    log(f'Профиль: {profile}, видео: {bitrate/1e6:.1f} Мбит/с, выход: {target}')
    log('Меню BD-J: перед записью проверьте воспроизведение на вашем плеере.' if menu else 'Диск без интерактивного меню.')
    if not copying:
        log('Предупреждение: кадры будут приведены к 23,976 fps; конвертация может повлиять на плавность.')
    else:
        log('Видео копируется без изменения изображения и частоты кадров. ' + ('Звук преобразуется в AC-3.' if selected_mode == 'video_copy' else 'Звук копируется без перекодирования.'))
    with tempfile.TemporaryDirectory(prefix='DiscForge_work_', dir=root) as tmp, pending_image(target) as pending_iso:
        work = Path(tmp)
        subtitle_outputs = prepare_subtitles(info, work, ffmpeg, cancel, log, run_command)
        if subtitle_outputs:
            log(f'Субтитров: {len(subtitle_outputs)}. Отдельные дорожки PGS, без наложения на видео.')
        video = work / 'video.h264'
        audio = [(work / f'audio_{i}.ac3', stream) for i, stream in enumerate(tracks)]
        meta = work / 'project.meta'
        def report(stage, fraction):
            if observer:
                observer(stage, fraction)
        report('encode', 0)
        if not copying:
            log('Видеокодер: ' + ('NVIDIA NVENC' if nvenc else 'CPU x264') + (' · быстрый режим' if fast else ' · сбалансированный режим'))
        def encoded(value):
            progress(value)
            report('encode', value/90)
        command = make_copy_command(ffmpeg, info, video, audio, selected_mode == 'video_copy') if copying else make_encode_command(ffmpeg, info, video, audio, bitrate, nvenc, fast)
        run_command(command,
                    ('Копирование видео + AC-3' if selected_mode == 'video_copy' else 'Копирование потоков') if copying else ('Кодирование видео и звука' if audio else 'Кодирование видео'),
                    0, 90, info.duration, cancel, log, encoded)
        report('encode', 1)
        if copying and signature_changed(info):
            raise DiscForgeError('Файл изменился при копировании. Сборка остановлена.')
        write_meta(meta, video, audio, chapters, fps=info.copy_report.fps if copying else '23.976',
                   subtitles=subtitle_outputs, width=info.width if copying else 1920, height=info.height if copying else 1080)
        if cancel.is_set():
            raise DiscForgeError('Операция отменена пользователем.')
        # Author to an intermediate output first; move to destination only on success.
        author_target = pending_iso if iso and menu is None else work / 'disc'
        log('Формирование структуры Blu-ray через tsMuxer...')
        report('mux', 0)
        if observer:
            run_tsmuxer(tsmuxer, meta, author_target, cancel, log,
                        progress=lambda f: (progress(90+3*f), report('mux', f)))
        else:
            run_tsmuxer(tsmuxer, meta, author_target, cancel, log)
        report('mux', 1)
        report('menu', 0)
        if cancel.is_set():
            raise DiscForgeError('Операция отменена пользователем.')
        if not author_target.exists():
            raise DiscForgeError('tsMuxer не создал выходной файл/папку.')
        if menu is not None:
            log('Создаём интерактивное меню BD-J…')
            from disc_audio import bind_tracks, menu_tracks
            menu = bind_tracks(menu, author_target / 'BDMV/PLAYLIST/00000.mpls', menu_tracks(info))
            from disc_audio import bind_subtitles
            menu = bind_subtitles(menu, author_target / 'BDMV/PLAYLIST/00000.mpls', menu_subtitles(info))
            install_menu(author_target, menu, list(chapters))
            progress(94)
        if iso and menu is None:
            size = author_target.stat().st_size
        else:
            size = sum(f.stat().st_size for f in author_target.rglob('*') if f.is_file())
            if not (author_target / 'BDMV').is_dir():
                raise DiscForgeError('tsMuxer не создал каталог BDMV.')
        if size > CAPACITIES[profile]:
            raise DiscForgeError(f'Размер результата {size / 1e9:.2f} ГБ превышает {profile}. Файлы болванки не затронуты.')
        # Validate the authored folder before packaging and before publishing.
        if not iso or menu is not None:
            bdmv_report = inspect_bdmv(author_target, profile)
            if not bdmv_report.structurally_valid:
                raise DiscForgeError('Проверка BDMV не пройдена: ' + '; '.join(bdmv_report.errors[:8]))
            log('Структурная проверка BDMV пройдена; совместимость с плеером не подтверждена.')
        report('menu', 1)
        if menu is not None and iso:
            image = pending_iso
            report('iso', 0)
            create_iso(author_target, image, CAPACITIES[profile], cancel, log,
                       progress=lambda f: (progress(94+5*f), report('iso', f)))
            report('iso', 1)
            author_target = image
            size = image.stat().st_size
        report('verify', 0)
        if iso:
            iso_report = inspect_iso(author_target, profile)
            if not iso_report.valid:
                raise DiscForgeError('Проверка ISO UDF 2.50 не пройдена: ' + '; '.join(iso_report.errors[:8]))
            log('Метаданные UDF 2.50 проверены; воспроизведение требует отдельного теста.')
        if cancel.is_set():
            raise DiscForgeError('Операция отменена пользователем.')
        shutil.move(str(author_target), str(target))
        report('verify', 1)
        progress(100)
        log(f'Готово: {target} ({size/1e9:.2f} ГБ).')
        return target
