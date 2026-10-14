"""Explainable, conservative bitrate estimates, not perceptual quality scores.

Packet sampling measures compressed video only. The heuristic is intentionally
independent of free disc space: it never shrinks compatible streams to fit.
"""
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import math

MIN_RATE = 5_000_000
MAX_RATE = 28_000_000


def positive(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and 0 < value <= 1_000_000_000 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def source_rate(info):
    if info.copy_report and info.copy_report.video_bytes > 0 and info.duration > 0:
        return dict(bps=round(info.copy_report.video_bytes*8/info.duration), method='packets')
    measured = getattr(info, 'video_analysis', {})
    if positive(measured.get('bps')) and measured.get('method') in ('samples', 'metadata'):
        return measured
    rate = positive(info.video_details.get('bit_rate'))
    tags = info.video_details.get('tags', {})
    if not rate and isinstance(tags, dict):
        rate = next((positive(value) for key, value in tags.items()
                     if key.upper() in ('BPS', 'BPS-ENG') and positive(value)), 0)
    return dict(bps=round(rate), method='metadata' if rate else 'unknown')


def analyze_bitrate(info, ffprobe, cancel):
    if cancel.is_set():
        raise RuntimeError('Анализ отменён.')
    known = source_rate(info)
    if known['bps']:
        return known
    from smart_copy import stream_lines
    try:
        before = Path(info.path).stat()
        intervals = f'0%+{info.duration:.3f}' if info.duration <= 30 else ','.join(f'{info.duration*f:.3f}%+8' for f in (.15, .5, .85))
        count = size = 0
        seconds = 0.
        try:
            step = 1/float(Fraction(info.video_fps))
        except (ValueError, ZeroDivisionError):
            step = 0
        def consume(line):
            nonlocal count, size, seconds
            fields = dict(p.split('=', 1) for p in line.split('|') if '=' in p)
            packet_size = positive(fields.get('size'))
            duration = positive(fields.get('duration_time')) or step
            if packet_size and 0 < duration <= 10:
                count += 1
                if count > 100000:
                    raise ValueError('Слишком много пакетов в пробе.')
                size += packet_size
                seconds += duration
        stream_lines([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-read_intervals', intervals,
                      '-show_packets', '-show_entries', 'packet=size,duration_time', '-of', 'compact=p=0', info.path], cancel, consume)
        after = Path(info.path).stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError('Исходник изменился во время анализа битрейта.')
        if count >= 2 and seconds > 0:
            return dict(bps=round(size*8/seconds), method='samples', seconds=round(seconds, 2))
    except Exception as exc:
        if cancel.is_set():
            raise RuntimeError('Анализ отменён.') from exc
        return dict(bps=0, method='unknown', reason=str(exc)[:300])
    return dict(bps=0, method='unknown')


@dataclass(frozen=True)
class Advice:
    mode: str
    bitrate: int
    source_bps: int
    source_method: str
    explanation: str


def recommend(info, capacity=10**18):
    measured = source_rate(info)
    rate, method = measured['bps'], measured['method']
    mode, reason = info.copy_report.recommendation(info, capacity) if info.copy_report else ('encode', 'Совместимость для копирования не подтверждена.')
    pixels = info.width*info.height
    floor = 5_000_000 if pixels <= 700000 else 6_000_000 if pixels <= 1100000 else 8_000_000 if pixels <= 2300000 else 10_000_000
    factor = 2.0 if info.video_codec in ('hevc', 'av1', 'vp9') else 1.25 if info.video_codec in ('h264', 'vp8') else 1.0
    raw = max(floor, rate*factor) if rate else max(floor, 8_000_000 if pixels <= 1100000 else 12_000_000)
    target = min(MAX_RATE, math.ceil(raw/500000)*500000)
    origin = {'packets': 'по всем пакетам видео', 'metadata': 'по метаданным видеопотока',
              'samples': 'приблизительно по коротким пробам', 'unknown': 'битрейт видеопотока неизвестен'}[method]
    text = f'Исходник: {info.width}×{info.height}, {info.video_codec.upper()}; ' + (f'видео ≈ {rate/1e6:.2f} Мбит/с, {origin}.\n' if rate else origin+'.\n')
    text += reason + '\n'
    if info.is_hdr:
        text += 'Сборка HDR пока недоступна: подбор битрейта не заменяет тонмаппинг.'
    elif mode != 'encode':
        text += 'Лучший способ сохранить качество видео — копировать его. Битрейт исходного видео не меняется.'
    else:
        text += f'Экономный ориентир для H.264 1080p: {target/1e6:g} Мбит/с. '
        text += 'Расчёт по разрешению, кодеку и битрейту исходника; это не измерение визуального качества. '
        if method == 'unknown':
            text += 'Данных мало: выбран более осторожный ориентир. '
        if method == 'samples':
            text += 'Короткие пробы могут не захватить самые сложные сцены. '
        if raw > MAX_RATE:
            text += 'Расчёт выше предела 28 Мбит/с: исходное качество при таком ограничении особенно важно проверить. '
        if info.width != 1920 or info.height != 1080:
            text += 'Изменение размера кадра не восстановит отсутствующие детали. '
        text += 'При перекодировании возможна потеря качества; проверьте динамичные и тёмные сцены. Минимум этой версии — 5 Мбит/с.'
    return Advice(mode, target, rate, method, text)


def normalize_rates(raw, paths):
    if not isinstance(raw, dict):
        raise ValueError('Неверные индивидуальные битрейты.')
    known = {str(Path(p).resolve()) for p in paths}
    result = {}
    for path, rate in raw.items():
        if not isinstance(path, str) or type(rate) is not int or not MIN_RATE <= rate <= MAX_RATE:
            raise ValueError('Битрейт фильма должен быть от 5 до 28 Мбит/с.')
        normalized = str(Path(path).resolve())
        if normalized in known:
            result[normalized] = rate
    return result


def planned_rate(info, policy='auto', overrides=None, common_rate=12_000_000):
    rate = (overrides or {}).get(str(Path(info.path).resolve()))
    if rate is not None:
        return rate
    return recommend(info).bitrate if policy == 'auto' else common_rate
