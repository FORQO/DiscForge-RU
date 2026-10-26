"""Portable, bounded quality reports embedded in projects; never cached video paths."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil

from core import DiscForgeError, nvenc_available, run_command, make_video_command
from quality_trial import TrialPlan, source_stamp, ensure_source, video_peak, capture
from quality_metrics import POLICY, MODEL, LIMITS, recommend
from quality_scenes import recommend_scenes

SCHEMA = 1
ENGINE = 'quality-treatment-1'  # Bump when preparation, encoding or assessment semantics change.
MAX_RECORD = 256_000


def digest_file(path, cancel, progress=lambda _: None):
    stamp = source_stamp(path)
    digest = hashlib.sha256()
    done = 0
    with open(path, 'rb') as stream:
        while True:
            if cancel.is_set():
                raise DiscForgeError('Проверка сохранённых проб отменена.')
            block = stream.read(4*1024*1024)
            if not block:
                break
            digest.update(block)
            done += len(block)
            progress(done*100/max(1, stamp[1]))
    ensure_source(path, stamp)
    return dict(stamp=list(stamp), sha256=digest.hexdigest())


def context(info, plan):
    return dict(engine=ENGINE, policy=POLICY, model=MODEL, limits=LIMITS,
        plan=asdict(plan),
        encoder=plan.encoder, fast=plan.fast, gpu_frames=plan.gpu_frames,
        video=dict(duration=info.duration, width=info.width, height=info.height,
                   codec=info.video_codec, fps=info.video_fps, transfer=info.video_color_transfer,
                   details=info.video_details), peak=video_peak(info),
        audio=dict(selected=info.selected_audio, streams=info.audio_streams, modes=info.audio_modes),
        platform=platform.system(), architecture=platform.machine())


def fingerprint(info, plan, config, cancel, log, progress):
    if source_stamp(info.path)[1] != info.size:
        raise ValueError('Размер исходника изменился после анализа. Добавьте фильм заново.')
    log('Проверка фильма для сохранённых проб: SHA-256, чтение без перекодирования…')
    source = digest_file(info.path, cancel, lambda p: progress(p*.9))
    tools = {}
    for index, name in enumerate(('ffmpeg', 'ffprobe')):
        path = shutil.which(config[name]) or config[name]
        tools[name] = digest_file(path, cancel)
        progress(90+(index+1)*4)
    gpu = None
    if plan.encoder != 0:
        from gpu_monitor import find_nvidia_smi
        smi = find_nvidia_smi()
        if smi:
            try:
                gpu = capture([smi, '--query-gpu=name,uuid,driver_version', '--format=csv,noheader'],
                              cancel, timeout=10).decode('utf-8', 'replace').strip()
            except (OSError, RuntimeError):
                if cancel.is_set():
                    raise
    progress(100)
    # JSON normalization makes tuple/list representation identical after reload.
    return json.loads(json.dumps(dict(source=source, tools=tools, context=context(info, plan), gpu=gpu),
                                 ensure_ascii=False, allow_nan=False))


def compact_scene(scene):
    result = {k: scene[k] for k in ('frames', 'start', 'duration', 'encoder', 'preset', 'cuda', 'peak', 'backend')}
    result['variants'] = [{k: v[k] for k in ('bitrate', 'bytes', 'seconds', 'film_video_bytes', 'quality')}
                          for v in scene['variants']]
    if 'selection' in scene:
        result['selection'] = dict(reason=scene['selection']['reason'])
    return result


def make_record(result, plan, identity):
    scenes = result.get('scenes', [result])
    record = dict(schema=SCHEMA, created=datetime.now(timezone.utc).isoformat(), identity=identity,
        plan=asdict(plan), scenes=[compact_scene(s) for s in scenes],
        selected_rate=result['recommendation'].get('bitrate'))
    if result.get('search'):
        record['search'] = result['search']
    return validate_record(record)


def validate_record(raw):
    """Validate optional project data before any worker or file operation uses it."""
    if not isinstance(raw, dict) or raw.get('schema') != SCHEMA:
        raise ValueError('Неизвестный формат сохранённых проб.')
    if len(json.dumps(raw, ensure_ascii=False, allow_nan=False)) > MAX_RECORD:
        raise ValueError('Сохранённый отчёт проб слишком велик.')
    data = json.loads(json.dumps(raw, ensure_ascii=False, allow_nan=False))
    plan_data = data['plan']
    if not isinstance(plan_data, dict):
        raise ValueError('Неверные параметры сохранённых проб.')
    # Old reports stay readable, but the changed engine/context requires new trials.
    plan_data.setdefault('enhance', False)
    plan_data.setdefault('quiet_rate', 0.0)
    if set(plan_data) != set(TrialPlan.__dataclass_fields__):
        raise ValueError('Неверные параметры сохранённых проб.')
    plan = TrialPlan(**dict(plan_data, rates=tuple(plan_data['rates'])))
    from types import SimpleNamespace
    plan.validate(SimpleNamespace(is_hdr=False, duration=1e9))
    if any(type(plan_data[k]) is not bool for k in ('fast', 'gpu_frames', 'automatic', 'retry_rates')):
        raise ValueError('Неверные параметры сохранённых проб.')
    identity = data['identity']
    for item in [identity['source'], identity['tools']['ffmpeg'], identity['tools']['ffprobe']]:
        stamp, digest = item['stamp'], item['sha256']
        if (not isinstance(stamp, list) or len(stamp) != 3 or not isinstance(stamp[0], str)
            or any(type(x) is not int or x < 0 for x in stamp[1:]) or not isinstance(digest, str)
            or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
            raise ValueError('Неверный отпечаток сохранённых проб.')
    if not isinstance(identity['context'], dict) or not isinstance(data['created'], str):
        raise ValueError('Неверный контекст сохранённых проб.')
    scenes = data['scenes']
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 5:
        raise ValueError('Неверное число сохранённых сцен.')
    cleaned = []
    for scene in scenes:
        if (type(scene['frames']) is not int or not 1 <= scene['frames'] <= 721
            or not 0 < scene['duration'] <= 31 or not math.isfinite(scene['start']) or scene['start'] < 0
            or scene['encoder'] not in ('CPU x264', 'NVIDIA NVENC')
            or scene['preset'] not in ('medium', 'fast', 'p3', 'p5') or type(scene['cuda']) is not bool
            or not 5_000_000 <= scene['peak'] <= 30_000_000):
            raise ValueError('Неверные параметры сохранённой сцены.')
        if set(scene['backend']) != {'nvenc', 'cuda', 'vmaf'} or any(type(x) is not bool for x in scene['backend'].values()):
            raise ValueError('Неверный кодер сохранённой сцены.')
        if not isinstance(scene['variants'], list) or not 1 <= len(scene['variants']) <= 11:
            raise ValueError('Неверное число сохранённых вариантов.')
        rates = []
        for v in scene['variants']:
            rate = v['bitrate']
            if type(rate) is not int or not 5_000_000 <= rate <= min(28_000_000, scene['peak']):
                raise ValueError('Неверный сохранённый битрейт.')
            rates.append(rate)
            if (not isinstance(v['quality'], dict) or not isinstance(v['quality'].get('warnings', []), list)
                or any(not isinstance(w, str) for w in v['quality'].get('warnings', []))):
                raise ValueError('Неверные пояснения сохранённых метрик.')
            if any(not isinstance(v[k], (int, float)) or not math.isfinite(v[k]) or v[k] < 0
                   for k in ('bytes', 'seconds', 'film_video_bytes')):
                raise ValueError('Неверные размеры сохранённых проб.')
            for method, low, high in (('ssim', -1, 1), ('vmaf', 0, 100)):
                metric = v['quality'].get(method)
                if metric is None:
                    continue
                if (metric['frames'] != scene['frames'] or type(metric['worst_frame']) is not int
                    or not 0 <= metric['worst_frame'] < scene['frames']
                    or any(not isinstance(metric[k], (int, float)) or not math.isfinite(metric[k])
                           or not low <= metric[k] <= high for k in ('mean', 'p5', 'minimum'))):
                    raise ValueError('Неверные сохранённые метрики.')
        if len(set(rates)) != len(rates):
            raise ValueError('Повторяющиеся сохранённые битрейты.')
        # Whitelist fields; cached paths/reference filenames are never trusted.
        cleaned.append(compact_scene(scene))
    if any({v['bitrate'] for v in s['variants']} != set(rates) for s in cleaned):
        raise ValueError('Наборы битрейтов сохранённых сцен различаются.')
    selected = data.get('selected_rate')
    if selected is not None and (type(selected) is not int or selected not in rates):
        raise ValueError('Выбранный сохранённый битрейт отсутствует в пробах.')
    result = dict(schema=SCHEMA, created=data['created'], plan=plan_data, identity=identity,
                  scenes=cleaned, selected_rate=selected)
    if isinstance(data.get('search'), dict):
        result['search'] = data['search']
    return result


def load_records(raw, paths):
    records, skipped = {}, 0
    if raw is None:
        return records, skipped
    if not isinstance(raw, dict):
        return records, 1
    allowed = set(paths)
    for path, record in raw.items():
        try:
            normalized = str(Path(path).resolve())
            if normalized in allowed:
                records[normalized] = validate_record(record)
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            skipped += 1
    return records, skipped


def restore_record(record, info, plan, config, cancel, log, progress):
    record = validate_record(record)
    previous = record['identity']
    current_context = json.loads(json.dumps(context(info, plan), allow_nan=False))
    if previous['context'] != current_context:
        raise ValueError('Фильм, звук, параметры кодирования или правила оценки изменились. Создайте новые пробы.')
    ensure_source(info.path, previous['source']['stamp'])
    current = fingerprint(info, plan, config, cancel, log, lambda p: progress(p*.9))
    same_tools = all(current['tools'][key]['sha256'] == previous['tools'][key]['sha256'] and
                     current['tools'][key]['stamp'][1] == previous['tools'][key]['stamp'][1]
                     for key in ('ffmpeg', 'ffprobe'))
    if (current['source'] != previous['source'] or current['context'] != previous['context']
        or current['gpu'] != previous.get('gpu') or not same_tools):
        raise ValueError('Содержимое фильма, инструменты или видеокарта/драйвер изменились. Создайте новые пробы.')
    backend = record['scenes'][0]['backend']
    nvenc = plan.encoder != 0 and nvenc_available(config['ffmpeg'], cancel, log)
    cuda = False
    if nvenc and plan.gpu_frames and not plan.enhance:
        from gpu_pipeline import supported
        cuda = supported(info, config['ffmpeg'], cancel, log, run_command, make_video_command)
    if (nvenc, cuda) != (backend['nvenc'], backend['cuda']):
        raise ValueError('Доступность кодера или GPU-фильтров изменилась. Создайте новые пробы.')
    ensure_source(info.path, current['source']['stamp'])
    if cancel.is_set():
        raise DiscForgeError('Проверка сохранённых проб отменена.')
    scenes = []
    for scene in record['scenes']:
        scenes.append(dict(scene, source=current['source']['stamp'], archived=True, enhance=plan.enhance,
                           recommendation=recommend(scene['variants'])))
    if plan.automatic:
        result = dict(source=current['source']['stamp'], scenes=scenes, archived=True,
                      recommendation=recommend_scenes(scenes))
    else:
        result = scenes[0]
    result['_project_record'] = record
    progress(100)
    log('Сохранённые оценки проверены. Повторный подбор не требуется.')
    return result
