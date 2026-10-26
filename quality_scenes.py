"""Bounded content sampling and conservative, all-scenes bitrate recommendations.

The small grayscale scan selects candidates, never measures final quality. Full
1080p frame metrics are calculated by the same trial pipeline used manually.
"""
from dataclasses import replace
import math
from pathlib import Path
import shutil
from statistics import median

from core import DiscForgeError
from quality_trial import (FPS, WIDTH, HEIGHT, capture, source_stamp, ensure_source,
                           run_trial, video_peak)
from quality_metrics import recommend, _valid

SCAN_WIDTH, SCAN_HEIGHT, SCAN_FPS = 160, 90, 4
MAX_CANDIDATES = 24


def timecode(seconds):
    value = max(0, int(seconds))
    return f'{value//3600:02}:{value//60%60:02}:{value%60:02}'


def scan_starts(duration, seconds):
    if not math.isfinite(duration) or duration < 1:
        raise ValueError('Для автоматического выбора нужен фильм длительностью не менее секунды.')
    length = min(seconds, duration)
    last = max(0., duration-length)
    count = min(MAX_CANDIDATES, max(1, math.ceil(duration/length)*2)) if last else 1
    return [last*i/(count-1) for i in range(count)] if count > 1 else [0.]


def complexity(raw):
    """Robust spatial/temporal proxies. Median motion avoids a single cut spike."""
    size = SCAN_WIDTH*SCAN_HEIGHT
    if len(raw) < 2*size or len(raw) % size:
        raise ValueError('Недостаточно полных кадров для анализа сложности сцены.')
    frames = [raw[i:i+size] for i in range(0, len(raw), size)]
    detail, dark, brightness, contrast, movement = [], [], [], [], []
    for frame in frames:
        mean = sum(frame)/size
        brightness.append(mean)
        contrast.append(math.sqrt(sum((v-mean)**2 for v in frame)/size))
        edges, dark_edges = [], []
        for y in range(1, SCAN_HEIGHT):
            for x in range(1, SCAN_WIDTH):
                i = y*SCAN_WIDTH+x
                edge = (abs(frame[i]-frame[i-1])+abs(frame[i]-frame[i-SCAN_WIDTH]))/2
                edges.append(edge)
                # Reject almost-black pixels; score visible detail in shadows.
                dark_edges.append(edge if 8 <= frame[i] <= 80 else 0.)
        detail.append(sum(edges)/len(edges))
        dark.append(sum(dark_edges)/len(dark_edges))
    for a, b in zip(frames, frames[1:]):
        movement.append(sum(abs(x-y) for x, y in zip(a, b))/size)
    return dict(motion=median(movement), detail=median(detail), dark_detail=median(dark),
                brightness=median(brightness), contrast=median(contrast), frames=len(frames))


def choose_scenes(candidates, count, duration):
    usable = [c for c in candidates if c['features']['detail'] >= .6 and
              c['features']['contrast'] >= 4 and 2 < c['features']['brightness'] < 250]
    if not usable:
        raise ValueError('В просмотренных фрагментах нет достаточно содержательных кадров. '
                         'Выберите сцену вручную: чёрные экраны и однотонные кадры не дают надёжной рекомендации.')
    selected = []
    criteria = [('motion', 'движение'), ('detail', 'мелкие детали'), ('dark_detail', 'детали в тенях')]
    for index in range(count):
        available = [c for c in usable if all(c['start']+c['duration'] <= s['start']+.001 or
                     s['start']+s['duration'] <= c['start']+.001 for s in selected)]
        if not available:
            break
        feature, reason = criteria[index % len(criteria)]
        if max(c['features'][feature] for c in available) < .1:
            feature, reason = 'detail', 'мелкие детали'
        def rank(c):
            distance = min((abs(c['start']-s['start'])/max(1., duration) for s in selected), default=1.)
            return (c['features'][feature]*(.8+.2*distance), -c['start'])
        best = max(available, key=rank)
        selected.append(dict(best, reason=reason))
    return sorted(selected, key=lambda s: s['start'])


def select_scenes(info, plan, ffmpeg, cancel, log, progress):
    starts = scan_starts(info.duration, plan.seconds)
    length = min(plan.seconds, info.duration)
    candidates = []
    for index, start in enumerate(starts):
        if cancel.is_set():
            raise DiscForgeError('Проба отменена.')
        log(f'Поиск сложных сцен: {index+1}/{len(starts)} · {timecode(start)}')
        # Sample the centre of each candidate. Decode at most three seconds,
        # with input seeking; never decode the whole movie just to select scenes.
        sample_seconds = min(3., length)
        sample_start = start+(length-sample_seconds)/2
        frames = max(2, math.floor(sample_seconds*SCAN_FPS))
        raw = capture([ffmpeg, '-hide_banner', '-nostdin', '-v', 'error',
                       '-ss', f'{sample_start:.6f}', '-i', info.path, '-map', '0:v:0',
                       '-an', '-sn', '-dn', '-vf', f'fps={SCAN_FPS},scale={SCAN_WIDTH}:{SCAN_HEIGHT},format=gray',
                       '-frames:v', str(frames), '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'], cancel, timeout=45)
        features = complexity(raw)
        if features['frames'] < frames-1:
            raise DiscForgeError('Сканирование сцены неполное. Повторите анализ исходного файла.')
        candidates.append(dict(start=start, duration=length, features=features))
        progress((index+1)*100/len(starts))
    selected = choose_scenes(candidates, plan.scene_count, info.duration)
    log(f'Просмотрено {len(candidates)} фрагментов; выбрано {len(selected)} без пересечений. '
        'Это выборочная проверка, а не анализ каждого кадра фильма.')
    for scene in selected:
        log(f"Сцена {timecode(scene['start'])} · {scene['reason']} · {scene['duration']:g} с")
    return dict(candidates=candidates, selected=selected, requested=plan.scene_count)


def recommend_scenes(scenes):
    result = dict(status='unavailable', method=None, bitrate=None, best_bitrate=None,
                  eligible=[], per_scene=[], explanation='Не все сцены оценены полностью. Повторите проверку.')
    if not scenes:
        return result
    rates = {v['bitrate'] for v in scenes[0]['variants']}
    if not rates or any({v['bitrate'] for v in s['variants']} != rates or
                       len(s['variants']) != len(rates) for s in scenes):
        return result
    # Different encoders/scalers must never be combined into one recommendation.
    if len({(s['encoder'], s['preset'], s['cuda']) for s in scenes}) != 1:
        return result
    for s in scenes:
        for v in s['variants']:
            metric = v.get('quality', {}).get('ssim')
            if not _valid(metric) or metric['frames'] != s['frames']:
                return result
    vmaf = all(_valid(v['quality'].get('vmaf')) and v['quality']['vmaf']['frames'] == s['frames']
               for s in scenes for v in s['variants'])
    advice = [recommend(s['variants'], force_ssim=not vmaf) for s in scenes]
    result['per_scene'] = advice
    method = result['method'] = 'vmaf' if vmaf else 'ssim'
    common = rates.intersection(*(set(a['eligible']) for a in advice))
    result['eligible'] = sorted(common)
    # Maximin is for manual inspection only when no rate passes all scenes.
    result['best_bitrate'] = max(sorted(rates, reverse=True), key=lambda r: min(
        next(v for v in s['variants'] if v['bitrate'] == r)['quality'][method]['minimum'] for s in scenes))
    if common:
        rate = result['bitrate'] = min(common)
        result['status'] = 'recommended' if vmaf else 'provisional'
        result['explanation'] = (f"{'Рекомендуется' if vmaf else 'Предварительно, только SSIM'}: {rate/1e6:g} Мбит/с. "
            f'Это минимальный проверенный битрейт, прошедший пороги и сравнение с лучшими пробами '
            f'во всех {len(scenes)} сценах. Хорошие сцены не компенсируют плохие. ')
    else:
        result['status'] = 'below_target'
        result['explanation'] = ('Общего подходящего битрейта среди проб нет: ни один не проходит все сцены. '
            'Повысьте пробные битрейты или измените режим кодирования и повторите сравнение. ')
    result['explanation'] += 'Проверка выборочная; она не гарантирует отсутствие потерь во всём фильме.'
    if not vmaf:
        result['explanation'] += ' Полного VMAF нет: все сцены сравниваются по SSIM, достоверность ниже.'
    return result


def run_auto_trial(info, plan, config, directory, cancel, log, progress):
    plan.validate(info)
    stamp = source_stamp(info.path)
    if stamp[1] != info.size:
        raise ValueError('Размер исходника изменился после анализа. Добавьте фильм заново.')
    if max(plan.rates) > video_peak(info):
        raise ValueError('Выбранные битрейты превышают доступный для видео поток с учётом звука.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    selection = select_scenes(info, plan, config['ffmpeg'], cancel, log, lambda p: progress(p*.15))
    ensure_source(info.path, stamp)
    seconds = sum(s['duration'] for s in selection['selected'])
    reserve = int(seconds*FPS*WIDTH*HEIGHT*(4 if plan.enhance else 2)+seconds*sum(plan.rates)/8*1.3+128_000_000)
    if shutil.disk_usage(directory).free < reserve:
        raise ValueError(f'Для всех сцен требуется около {reserve/1e9:.2f} ГБ свободного места. '
                         'Уменьшите длину или количество сцен.')
    scenes, backend = [], None
    for index, selected in enumerate(selection['selected']):
        if cancel.is_set():
            raise DiscForgeError('Проба отменена.')
        ensure_source(info.path, stamp)
        log(f"Проверяем сцену {index+1}/{len(selection['selected'])} · {timecode(selected['start'])} · {selected['reason']}")
        scene_plan = replace(plan, start=selected['start'], automatic=False)
        scene = run_trial(info, scene_plan, config, directory/f'scene-{index+1}', cancel,
            lambda message, i=index: log(f'Сцена {i+1}: {message}'),
            lambda p, i=index: progress(15+(i+p/100)*84/len(selection['selected'])), backend=backend)
        backend = scene['backend']
        scene['selection'] = selected
        scenes.append(scene)
    ensure_source(info.path, stamp)
    if cancel.is_set():
        raise DiscForgeError('Проба отменена.')
    result = dict(source=stamp, scenes=scenes, selection=selection, recommendation=recommend_scenes(scenes))
    from settings import write_json
    write_json(directory/'scenes.json', result)
    log(result['recommendation']['explanation'])
    progress(100)
    return result
