"""Bounded, disposable video trials using the production Blu-ray encoder settings."""
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import shutil
import subprocess
import time

from core import DiscForgeError, make_video_command, nvenc_available, run_command
from bitrate_advisor import MIN_RATE, MAX_RATE

FPS = 24000 / 1001
WIDTH, HEIGHT = 1920, 1080


@dataclass(frozen=True)
class TrialPlan:
    start: float
    seconds: float
    rates: tuple
    encoder: int = 2  # Same indices as the preparation panel.
    fast: bool = False
    gpu_frames: bool = True
    automatic: bool = False
    scene_count: int = 3
    retry_rates: bool = False
    enhance: bool = False
    quiet_rate: float = 0.0

    def validate(self, info):
        from video_treatment import validate
        validate(self.enhance, self.quiet_rate)
        if type(self.scene_count) is not int or not 2 <= self.scene_count <= 5:
            raise ValueError('Выберите от двух до пяти сцен.')
        if info.is_hdr:
            raise ValueError('Проба HDR пока не поддерживается: сначала нужен согласованный SDR-конвейер.')
        if not all(math.isfinite(x) for x in (info.duration, self.start, self.seconds)):
            raise ValueError('Некорректная длительность фрагмента.')
        if self.start < 0 or not 1 <= self.seconds <= 30 or self.start >= info.duration:
            raise ValueError('Выберите фрагмент от 1 до 30 секунд в пределах фильма.')
        if not 1 <= len(self.rates) <= 3 or len(set(self.rates)) != len(self.rates):
            raise ValueError('Выберите от одного до трёх разных битрейтов.')
        if any(type(r) is not int or not MIN_RATE <= r <= MAX_RATE for r in self.rates):
            raise ValueError('Битрейт должен быть от 5 до 28 Мбит/с.')
        if self.encoder not in (0, 1, 2):
            raise ValueError('Неизвестный видеокодер.')
        frames = math.floor(min(self.seconds, info.duration-self.start) * FPS)
        if frames < 1:
            raise ValueError('В конце фильма недостаточно кадров; выберите более раннее начало.')
        return frames


def suggested_rates(rate):
    center = min(MAX_RATE, max(MIN_RATE, round(rate / 500_000)*500_000))
    values = sorted({max(MIN_RATE, center-2_000_000), center, min(MAX_RATE, center+2_000_000)})
    for delta in (4_000_000, -4_000_000, 500_000, -500_000):
        if len(values) == 3:
            break
        value = min(MAX_RATE, max(MIN_RATE, center+delta))
        values = sorted(set(values + [value]))
    return tuple(values)


def source_stamp(path):
    stat = Path(path).stat()
    return (str(Path(path).resolve()), stat.st_size, stat.st_mtime_ns)


def ensure_source(path, stamp):
    if source_stamp(path) != tuple(stamp):
        raise ValueError('Исходный файл изменился. Повторите анализ и пробное кодирование.')


def capture(command, cancel, timeout=60):
    """Bounded ffprobe operation; cancellation also terminates the child process."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    end = time.monotonic() + timeout
    try:
        while True:
            if cancel.is_set():
                raise DiscForgeError('Проба отменена.')
            if time.monotonic() > end:
                raise DiscForgeError('Превышено время проверки пробного видео.')
            try:
                output, error = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise DiscForgeError(error.decode('utf-8', 'replace')[-2000:])
        return output
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def inspect_clip(ffprobe, path, cancel):
    data = json.loads(capture([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-count_frames',
        '-show_entries', 'stream=width,height,nb_read_frames,r_frame_rate,pix_fmt', '-of', 'json', str(path)], cancel))
    stream = data['streams'][0]
    if (stream['width'], stream['height'], stream['pix_fmt']) != (WIDTH, HEIGHT, 'yuv420p'):
        raise DiscForgeError('Проба не соответствует формату 1080p yuv420p.')
    if stream['r_frame_rate'] != '24000/1001':
        raise DiscForgeError('Неверная частота кадров пробы.')
    return int(stream['nb_read_frames'])


def video_peak(info):
    # Match the build's aggregate peak constraint; reserve the passthrough maximum
    # until the full audio scan proves whether a track can actually be copied.
    from audio_selection import selected_tracks
    from audio_pipeline import copy_format
    sound = 0
    for track in selected_tracks(info):
        fmt = copy_format(track)
        checked = info.copy_report.audio_results.get(track['index'], {}) if info.copy_report else {}
        copy = fmt and info.audio_modes.get(str(track['index'])) != 'ac3' and checked.get('ok', True)
        sound += fmt[2] if copy else 640_000
    return min(30_000_000, 44_000_000-sound)


def reference_command(ffmpeg, info, destination, plan, frames, cuda=False):
    command = make_video_command(ffmpeg, info.path, str(destination), plan.rates[0], False, plan.fast,
                                 enhance=plan.enhance, quiet_rate=plan.quiet_rate)
    if cuda and plan.enhance:
        raise ValueError('Бережное улучшение использует CPU-фильтры.')
    if cuda:
        from gpu_pipeline import cuda_command
        command = cuda_command(command)
        # pad_cuda can forward a frame from a different pool when padding is a
        # no-op. Allocate an identity-copy frame before hwdownload, otherwise
        # FFmpeg rejects that frame's hardware context (not an encoder error).
        command[command.index('-vf')+1] += ',scale_cuda=passthrough=0,hwdownload,format=yuv420p'
    # Retain the exact production frame preparation, but store it losslessly.
    command = command[:command.index('-c:v')]
    at = command.index('-i')
    command[at:at] = ['-ss', f'{plan.start:.6f}']
    return command + ['-frames:v', str(frames), '-c:v', 'ffv1', '-level', '3', '-pix_fmt', 'yuv420p',
                      '-f', 'matroska', str(destination)]


def variant_command(ffmpeg, reference, destination, rate, nvenc, fast, frames, peak, quiet_rate=0.0):
    command = make_video_command(ffmpeg, str(reference), str(destination), rate, nvenc, fast, quiet_rate=quiet_rate)
    # Frames already passed the production scaler/fps conversion. Do not scale twice.
    at = command.index('-vf')
    del command[at:at+2]
    command[command.index('-maxrate')+1] = str(peak)
    command[-3:] = ['-frames:v', str(frames), '-f', 'matroska', str(destination)]
    return command


def run_trial(info, plan, config, directory, cancel, log, progress, *, backend=None):
    frames = plan.validate(info)
    stamp = source_stamp(info.path)
    if stamp[1] != info.size:
        raise ValueError('Размер исходника изменился после анализа. Добавьте фильм заново.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    peak = video_peak(info)
    if max(plan.rates) > peak:
        raise ValueError(f'Выбранный звук оставляет для видео не более {peak/1e6:.2f} Мбит/с. Измените битрейты или выбор аудио.')
    reserve = int(frames*WIDTH*HEIGHT*(4 if plan.enhance else 2) + frames/FPS*sum(plan.rates)/8*1.3 + 128_000_000)
    if shutil.disk_usage(directory).free < reserve:
        raise ValueError(f'Для временных проб требуется около {reserve/1e9:.2f} ГБ свободного места. Уменьшите длительность.')
    ffmpeg, ffprobe = config['ffmpeg'], config['ffprobe']
    nvenc = backend['nvenc'] if backend else plan.encoder != 0 and nvenc_available(ffmpeg, cancel, log)
    if plan.encoder == 1 and not nvenc:
        raise DiscForgeError('Выбран NVIDIA NVENC, но кодер недоступен. Выберите CPU или «Авто» и повторите пробу.')
    cuda = backend['cuda'] if backend else False
    if backend is None and nvenc and plan.gpu_frames and not plan.enhance:
        from gpu_pipeline import supported
        cuda = supported(info, ffmpeg, cancel, log, run_command, make_video_command)
    name = 'NVIDIA NVENC' if nvenc else 'CPU x264'
    if plan.enhance and cuda:
        raise ValueError('Параметры подготовки кадров изменились; повторите пробы.')
    from video_treatment import describe
    describe(plan.enhance, plan.quiet_rate, log)
    preset = ('p3' if plan.fast else 'p5') if nvenc else ('fast' if plan.fast else 'medium')
    log(f'{name} · {preset} · подготовка кадров: {"CUDA" if cuda else "CPU"}')
    log('Подготавливаем одинаковые кадры исходника: 1080p, 23,976 кадр/с, без потерь (FFV1).')
    reference = directory/'reference.mkv'
    before = directory/'before.mkv' if plan.enhance else reference
    if plan.enhance:
        log('Готовим «до»: обычное масштабирование без улучшения, без потерь.')
        run_command(reference_command(ffmpeg, info, before, replace(plan, enhance=False), frames),
                    'До улучшения', 0, 10, frames/FPS, cancel, log, progress)
    run_command(reference_command(ffmpeg, info, reference, plan, frames, cuda), 'Подготовка исходника',
                10 if plan.enhance else 0, 10 if plan.enhance else 20, frames/FPS, cancel, log, progress)
    actual = inspect_clip(ffprobe, reference, cancel)
    if actual < max(1, frames-1):
        raise DiscForgeError('В исходнике оказалось меньше кадров, чем ожидалось. Выберите другой фрагмент или повторите анализ.')
    frames = actual
    if plan.enhance and inspect_clip(ffprobe, before, cancel) != frames:
        raise DiscForgeError('Кадры до и после улучшения не совпали по количеству; сравнение остановлено.')
    variants = []
    for i, rate in enumerate(plan.rates):
        output = directory/f'video-{rate}.mkv'
        log(f'Кодируем вариант {i+1}/{len(plan.rates)}: {rate/1e6:g} Мбит/с…')
        started = time.monotonic()
        run_command(variant_command(ffmpeg, reference, output, rate, nvenc, plan.fast, frames, peak, plan.quiet_rate),
                    'Пробное кодирование', 20+i*50/len(plan.rates), 50/len(plan.rates), frames/FPS,
                    cancel, log, progress)
        elapsed = time.monotonic()-started
        if inspect_clip(ffprobe, output, cancel) != frames:
            raise DiscForgeError('Количество кадров пробы отличается от исходника; сравнение остановлено.')
        variants.append(dict(path=str(output), bitrate=rate, bytes=output.stat().st_size, seconds=elapsed,
                             film_video_bytes=math.ceil(info.duration*rate/8)))
    from quality_metrics import has_vmaf, score_variant, recommend
    try:
        vmaf = backend['vmaf'] if backend else has_vmaf(ffmpeg, cancel)
    except (RuntimeError, OSError) as exc:
        if cancel.is_set():
            raise
        log('Не удалось проверить VMAF; используем SSIM. '+str(exc))
        vmaf = False
    for i, variant in enumerate(variants):
        log(f'Автоматическое сравнение {i+1}/{len(variants)}: {variant["bitrate"]/1e6:g} Мбит/с…')
        variant['quality'] = score_variant(ffmpeg, reference, variant['path'], frames, directory,
            cancel, log, lambda value, i=i: progress(70+(i+value/100)*29/len(variants)), vmaf=vmaf)
    advice = recommend(variants)
    log(advice['explanation'])
    ensure_source(info.path, stamp)
    if cancel.is_set():
        raise DiscForgeError('Проба отменена.')
    result = dict(source=stamp, reference=str(reference), before=str(before), enhance=plan.enhance,
                  frames=frames, start=plan.start,
                  duration=frames/FPS, encoder=name, preset=preset, cuda=cuda, peak=peak,
                  variants=variants, recommendation=advice,
                  backend=dict(nvenc=nvenc, cuda=cuda, vmaf=vmaf))
    from settings import write_json
    write_json(directory/'trial.json', result)
    progress(100)
    return result


def extend_trial(info, plan, config, scene, rate, cancel, log, progress):
    """Score one new rate against the retained reference, without recoding old trials."""
    from dataclasses import replace
    from quality_metrics import score_variant, recommend
    replace(plan, start=scene['start'], rates=(rate,)).validate(info)
    ensure_source(info.path, scene['source'])
    if rate > scene['peak'] or rate > video_peak(info):
        raise ValueError('Битрейт превышает доступный поток с учётом звука.')
    if any(v['bitrate'] == rate for v in scene['variants']):
        raise ValueError('Этот битрейт уже проверен.')
    if cancel.is_set():
        raise DiscForgeError('Проба отменена.')
    reference = Path(scene['reference'])
    output = reference.parent/f'video-{rate}.mkv'
    frames = scene['frames']
    backend = scene['backend']
    started = time.monotonic()
    run_command(variant_command(config['ffmpeg'], reference, output, rate, backend['nvenc'],
                plan.fast, frames, scene['peak'], plan.quiet_rate), 'Дополнительная проба', 0, 65, frames/FPS,
                cancel, log, progress)
    elapsed = time.monotonic()-started
    if inspect_clip(config['ffprobe'], output, cancel) != frames:
        raise DiscForgeError('Количество кадров дополнительной пробы отличается от эталона.')
    variant = dict(path=str(output), bitrate=rate, bytes=output.stat().st_size, seconds=elapsed,
                   film_video_bytes=math.ceil(info.duration*rate/8))
    variant['quality'] = score_variant(config['ffmpeg'], reference, output, frames, reference.parent,
        cancel, log, lambda p: progress(65+p*.35), vmaf=backend['vmaf'])
    ensure_source(info.path, scene['source'])
    if cancel.is_set():
        raise DiscForgeError('Проба отменена.')
    variants = sorted(scene['variants']+[variant], key=lambda v: v['bitrate'])
    return dict(scene, variants=variants, recommendation=recommend(variants))


def comparison_command(ffmpeg, reference, variant, frame, play=False):
    # Select by decoded frame number, not rounded MKV millisecond timestamps.
    # Both streams are assigned the same clock; a seek can never drift between sides.
    graph = ('[0:v]setpts=N*1001/(24000*TB),format=rgb24[a];'
             '[1:v]setpts=N*1001/(24000*TB),format=rgb24[b];'
             f'[a][b]hstack=shortest=1,select=gte(n\\,{int(frame)})[pair]')
    command = [ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error', '-i', str(reference),
               '-i', str(variant), '-filter_complex', graph, '-map', '[pair]', '-an', '-sn',
               '-fps_mode', 'passthrough']
    if not play:
        command += ['-frames:v', '1']
    return command + ['-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
