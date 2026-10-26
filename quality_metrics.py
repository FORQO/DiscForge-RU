"""Frame-aligned VMAF/SSIM scoring and an explicit, conservative trial policy.

Thresholds are DiskForge heuristics, not a promise of perceptual transparency.
Recommendations concern only the tested fragment, encoder, and candidate rates.
"""
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

from core import DiscForgeError, run_command

MODEL = 'vmaf_v0.6.1'
POLICY = 'close-to-best-v1'
LIMITS = {
    'vmaf': {'mean': 95., 'p5': 90., 'minimum': 85., 'mean_gap': 1., 'p5_gap': 2.},
    'ssim': {'mean': .990, 'p5': .970, 'minimum': .950, 'mean_gap': .001, 'p5_gap': .003},
}


def summarize(values, low, high):
    if not values or any(not math.isfinite(v) or not low <= v <= high for v in values):
        raise ValueError('Метрика содержит пустые или некорректные значения.')
    ordered = sorted(values)
    return dict(frames=len(values), mean=math.fsum(values)/len(values),
                p5=ordered[math.floor(.05*(len(values)-1))], minimum=ordered[0],
                worst_frame=values.index(ordered[0]))


def parse_vmaf(path, frames):
    rows = json.loads(Path(path).read_text('utf-8'))['frames']
    if len(rows) != frames or [r['frameNum'] for r in rows] != list(range(frames)):
        raise ValueError('VMAF: отчёт не охватывает все кадры по порядку.')
    return summarize([float(r['metrics']['vmaf']) for r in rows], 0., 100.)


def parse_ssim(path, frames):
    rows = Path(path).read_text('utf-8').splitlines()
    values = []
    for index, row in enumerate(rows, 1):
        match = re.search(r'\bn:(\d+)\s.*?\bAll:([^\s]+)', row)
        if not match or int(match[1]) != index:
            raise ValueError('SSIM: пропущен или повторён кадр.')
        values.append(float(match[2]))
    if len(values) != frames:
        raise ValueError('SSIM: отчёт не охватывает все кадры.')
    return summarize(values, -1., 1.)


def metric_command(ffmpeg, reference, variant, frames, method):
    if method not in ('ssim', 'vmaf'):
        raise ValueError('Неизвестная метрика.')
    # A dedicated cwd and fixed report names keep Windows drive letters, quotes,
    # commas and Cyrillic paths out of FFmpeg's filter-expression grammar.
    binary = shutil.which(ffmpeg) or str(Path(ffmpeg).resolve())
    graph = ('[0:v]settb=1/24000,setpts=N*1001[d];'
             '[1:v]settb=1/24000,setpts=N*1001[r];[d][r]')
    if method == 'vmaf':
        graph += f'libvmaf=model=version={MODEL}:log_fmt=json:log_path=vmaf.json:n_threads=4:n_subsample=1'
    else:
        graph += 'ssim=stats_file=ssim.log'
    graph += ':shortest=1:repeatlast=0[score]'
    return [binary, '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-progress', 'pipe:1',
            '-i', str(Path(variant).resolve()), '-i', str(Path(reference).resolve()),
            '-filter_complex', graph, '-map', '[score]', '-an', '-sn', '-dn',
            '-frames:v', str(frames), '-fps_mode', 'passthrough', '-f', 'null', '-']


def has_vmaf(ffmpeg, cancel):
    from quality_trial import capture
    text = capture([ffmpeg, '-hide_banner', '-filters'], cancel, timeout=20).decode('utf-8', 'replace')
    return bool(re.search(r'^\s*\S+\s+libvmaf\s', text, re.MULTILINE))


def score_variant(ffmpeg, reference, variant, frames, directory, cancel, log, progress, *, vmaf=True):
    started = time.monotonic()
    result = {'ssim': None, 'vmaf': None, 'warnings': [], 'model': MODEL if vmaf else None}
    methods = ['ssim', 'vmaf'] if vmaf else ['ssim']
    if not vmaf:
        result['warnings'].append('VMAF недоступен в выбранном FFmpeg; оценка только по SSIM.')
    for index, method in enumerate(methods):
        if cancel.is_set():
            raise DiscForgeError('Проба отменена.')
        timeout = threading.Event()
        timer = threading.Timer(max(60, frames/24*12), timeout.set)
        class Cancellation:
            def is_set(self):
                return cancel.is_set() or timeout.is_set()
            def wait(self, seconds):
                cancel.wait(seconds)
                return self.is_set()
        try:
            with tempfile.TemporaryDirectory(prefix='metrics-', dir=directory) as work:
                timer.start()
                run_command(metric_command(ffmpeg, reference, variant, frames, method),
                            'Оценка '+method.upper(), index*100/len(methods), 100/len(methods),
                            frames*1001/24000, Cancellation(), log, progress, cwd=work)
                if timeout.is_set():
                    raise TimeoutError('истекло время расчёта')
                result[method] = (parse_vmaf(Path(work)/'vmaf.json', frames) if method == 'vmaf'
                                  else parse_ssim(Path(work)/'ssim.log', frames))
        except (RuntimeError, ValueError, OSError, KeyError, TypeError) as exc:
            if cancel.is_set():
                raise DiscForgeError('Проба отменена.') from exc
            reason = 'истекло время расчёта' if timeout.is_set() else str(exc)
            message = f'{method.upper()} не рассчитан: {reason}'
            result['warnings'].append(message)
            log(message)
        finally:
            timer.cancel()
        progress((index+1)*100/len(methods))
    if cancel.is_set():
        raise DiscForgeError('Проба отменена.')
    result['seconds'] = time.monotonic()-started
    return result


def _valid(summary):
    return isinstance(summary, dict) and summary.get('frames', 0) > 0 and all(
        isinstance(summary.get(k), (int, float)) and math.isfinite(summary[k])
        for k in ('mean', 'p5', 'minimum'))


def recommend(variants, *, force_ssim=False):
    """Smallest tested rate meeting absolute floors and close to the best trial.

    Missing VMAF downgrades the entire comparison to SSIM, never mixes scales.
    An incomplete SSIM set blocks automatic advice; manual viewing stays usable.
    """
    result = dict(policy=POLICY, limits=LIMITS, status='unavailable', method=None,
                  bitrate=None, best_bitrate=None, eligible=[], explanation='')
    if not variants or not all(_valid(v.get('quality', {}).get('ssim')) for v in variants):
        result['explanation'] = ('Автоматическая рекомендация недоступна: не все пробы получили полную оценку. '
                                 'Повторите пробу или сравните варианты вручную.')
        return result
    counts = {v['quality']['ssim']['frames'] for v in variants}
    if len(counts) != 1:
        result['explanation'] = 'Автоматическая рекомендация недоступна: разное количество оценённых кадров.'
        return result
    method = 'vmaf' if not force_ssim and all(_valid(v['quality'].get('vmaf')) and
        v['quality']['vmaf']['frames'] == v['quality']['ssim']['frames'] for v in variants) else 'ssim'
    result['method'] = method
    best = max(variants, key=lambda v: (v['quality'][method]['mean'], v['quality'][method]['p5'], -v['bitrate']))
    result['best_bitrate'] = best['bitrate']
    high_mean = max(v['quality'][method]['mean'] for v in variants)
    high_p5 = max(v['quality'][method]['p5'] for v in variants)
    eligible = []
    for v in variants:
        q = v['quality'][method]
        absolute = all(v['quality'][m][k] >= LIMITS[m][k]
                       for m in (('vmaf', 'ssim') if method == 'vmaf' else ('ssim',))
                       for k in ('mean', 'p5', 'minimum'))
        near = (high_mean-q['mean'] <= LIMITS[method]['mean_gap']+1e-9 and
                high_p5-q['p5'] <= LIMITS[method]['p5_gap']+1e-9)
        if absolute and near:
            eligible.append(v['bitrate'])
    result['eligible'] = sorted(eligible)
    if eligible:
        rate = result['bitrate'] = min(eligible)
        result['status'] = 'recommended' if method == 'vmaf' else 'provisional'
        prefix = 'Рекомендуется' if method == 'vmaf' else 'Предварительно, только SSIM'
        savings = 100*(1-rate/max(v['bitrate'] for v in variants))
        result['explanation'] = (f'{prefix}: {rate/1e6:g} Мбит/с — минимальный из проверенных битрейтов, '
            f'прошедших пороги качества и близких к лучшему результату. '
            f'Экономия видео по целевому битрейту до {savings:.0f}% относительно самой высокой пробы. '
            'Вывод относится к этому фрагменту; проверьте ещё несколько сложных сцен.')
    else:
        result['status'] = 'below_target'
        result['explanation'] = (f'Цель качества не достигнута. Лучшая проба по {method.upper()}: '
            f'{best["bitrate"]/1e6:g} Мбит/с, но рекомендовать её как оптимальную пока нельзя. '
            'Повысьте пробные битрейты или измените режим кодирования и сравните снова.')
    if method == 'ssim':
        result['explanation'] += ' Полного сравнения VMAF нет; достоверность рекомендации ниже.'
    return result
