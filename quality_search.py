"""Bounded adaptive bitrate search using the exact same scenes and reference frames."""
import math
from pathlib import Path
import shutil

from bitrate_advisor import MAX_RATE
from core import DiscForgeError
from quality_trial import run_trial, extend_trial, video_peak, ensure_source
from quality_scenes import run_auto_trial, recommend_scenes

MAX_EXTRA_ROUNDS = 8
RATE_STEP = 500_000


def next_rate(tested, advice, ceiling):
    """Raise until a pass, then refine the nearest bracket; never assume monotonic quality."""
    tested = sorted(set(tested))
    if advice['status'] == 'unavailable' or not tested:
        return None
    passing = advice.get('bitrate')
    if passing:
        lower = max((r for r in tested if r < passing), default=passing)
        if passing-lower <= RATE_STEP:
            return None
        # Initial controls allow 0.1 Mbps values. A 0.5-only grid can miss
        # the entire bracket (for example 5.1..5.7) and claim false precision.
        candidate = math.floor((lower+passing)/2/100_000)*100_000
        return candidate if lower < candidate < passing and candidate not in tested else None
    high = tested[-1]
    if high >= ceiling:
        return None
    candidate = min(ceiling, math.ceil(max(high+2_000_000, high*1.4)/RATE_STEP)*RATE_STEP)
    return candidate if candidate > high else None


def run_quality_search(info, plan, config, directory, cancel, log, progress):
    runner = run_auto_trial if plan.automatic else run_trial
    if not plan.retry_rates:
        return runner(info, plan, config, directory, cancel, log, progress)
    log('Первичные пробы. Если общего подходящего битрейта нет, начнётся автоматический подбор.')
    result = runner(info, plan, config, directory, cancel, log, lambda p: progress(p*.6))
    ceiling = min(MAX_RATE, video_peak(info))//100_000*100_000
    history = [dict(rates=list(plan.rates), status=result['recommendation']['status'],
                    bitrate=result['recommendation'].get('bitrate'))]
    trigger = result['recommendation']['status'] == 'below_target'
    stop = 'Первичные пробы уже дали рекомендацию; дополнительные варианты не требуются.'
    if result['recommendation']['status'] == 'unavailable':
        stop = 'Автоподбор остановлен: неполные метрики. Увеличение битрейта не исправит ошибку оценки.'
    if trigger:
        for attempt in range(MAX_EXTRA_ROUNDS):
            if cancel.is_set():
                raise DiscForgeError('Проба отменена.')
            ensure_source(info.path, result['source'])
            scenes = result.get('scenes', [result])
            tested = [v['bitrate'] for v in scenes[0]['variants']]
            advice = result['recommendation']
            candidate = next_rate(tested, advice, ceiling)
            if candidate is None:
                if advice['status'] == 'unavailable':
                    stop = 'Автоподбор остановлен: неполные метрики, автоматический совет недоступен.'
                elif advice.get('bitrate'):
                    stop = ('Подходит наименьший проверенный битрейт; более низкие значения не проверялись.'
                            if advice['bitrate'] == min(tested) else
                            'Уточнение завершено: интервал до ближайшей меньшей пробы не более 0,5 Мбит/с.')
                else:
                    stop = (f'Достигнут предел {ceiling/1e6:g} Мбит/с с учётом выбранного звука. '
                            'Качество не достигнуто; измените кодер или пресет и повторите проверку.')
                break
            reserve = math.ceil(sum(s['duration'] for s in scenes)*candidate/8*1.3)+128_000_000
            if shutil.disk_usage(directory).free < reserve:
                stop = (f'Автоподбор остановлен: для следующего раунда нужно около {reserve/1e9:.2f} ГБ '
                        'свободного места. Завершённые пробы доступны для просмотра.')
                break
            stage = 'уточняем границу' if advice.get('bitrate') else 'повышаем битрейт'
            log(f'Автоподбор {attempt+1}/{MAX_EXTRA_ROUNDS}: {stage}, {candidate/1e6:g} Мбит/с · {len(scenes)} сцен.')
            updated = []
            for index, scene in enumerate(scenes):
                if cancel.is_set():
                    raise DiscForgeError('Проба отменена.')
                log(f'Раунд {attempt+1} · сцена {index+1}/{len(scenes)} · {candidate/1e6:g} Мбит/с')
                updated.append(extend_trial(info, plan, config, scene, candidate, cancel, log,
                    lambda p, i=index, a=attempt: progress(60+(a+(i+p/100)/len(scenes))*39/MAX_EXTRA_ROUNDS)))
            # Commit only a complete round. Every scene must have the same rates.
            if result.get('scenes'):
                result = dict(result, scenes=updated, recommendation=recommend_scenes(updated))
            else:
                result = updated[0]
            history.append(dict(rates=[candidate], status=result['recommendation']['status'],
                                bitrate=result['recommendation'].get('bitrate')))
            log(result['recommendation']['explanation'])
        else:
            stop = (f'Выполнено {MAX_EXTRA_ROUNDS} дополнительных раундов — предел автоматического поиска. '
                    'Итог относится только к проверенным значениям.')
    ensure_source(info.path, result['source'])
    if cancel.is_set():
        raise DiscForgeError('Проба отменена.')
    result['search'] = dict(rounds=history, ceiling=ceiling, triggered=trigger, stop=stop,
                            extra_limit=MAX_EXTRA_ROUNDS, resolution=RATE_STEP)
    result['recommendation'] = dict(result['recommendation'],
        explanation=result['recommendation']['explanation']+' '+stop)
    from settings import write_json
    write_json(Path(directory)/'search.json', result)
    log(stop)
    progress(100)
    return result
