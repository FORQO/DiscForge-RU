"""Conservative, versioned frame treatment and best-effort input pacing."""
VERSION = 'gentle-1'
QUIET_RATES = (0.0, 0.5, 1.0, 2.0)


def enhancement_for(config, path):
    """Explicit per-film map wins over the legacy whole-queue switch."""
    from pathlib import Path
    if 'video_enhancements' in config:
        values = config['video_enhancements']
        return isinstance(values, dict) and values.get(str(Path(path).resolve())) is True
    return config.get('enhance') is True


def project_enhancements(data, paths):
    """Validate before replacing the current project; ignore orphaned entries."""
    from pathlib import Path
    allowed = {str(Path(path).resolve()) for path in paths}
    if 'video_enhancements' not in data:
        return {path: True for path in allowed} if data.get('enhance') is True else {}
    values = data['video_enhancements']
    if (not isinstance(values, dict) or any(not isinstance(path, str) or type(flag) is not bool
                                          for path, flag in values.items())):
        raise ValueError('Неверные настройки улучшения фильмов.')
    return {str(Path(path).resolve()): True for path, flag in values.items()
            if flag and str(Path(path).resolve()) in allowed}


def validate(enhance=False, quiet_rate=0.0):
    if type(enhance) is not bool or type(quiet_rate) not in (int, float) or quiet_rate not in QUIET_RATES:
        raise ValueError('Неверные настройки улучшения или тихого режима.')


def frame_filter():
    # Mild temporal/spatial denoise before resizing; preserve grain by default
    # (the whole treatment is opt-in). Sharpen luma only, before black borders.
    return ('fps=24000/1001,hqdn3d=1.0:0.75:1.5:1.0,'
            'scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,'
            'unsharp=5:5:0.20:5:5:0.0,'
            'pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1')


def pace(command, quiet_rate=0.0):
    """Limit input media seconds per wall second, not GPU power/utilisation.

    Placed before the first input, including with seeking. No realtime burst or
    catch-up mode, no driver changes. Cancellation remains the runner's job.
    """
    validate(False, quiet_rate)
    result = list(command)
    if quiet_rate:
        at = result.index('-i')
        result[at:at] = ['-readrate', str(float(quiet_rate))]
    return result


def describe(enhance, quiet_rate, log):
    validate(enhance, quiet_rate)
    if enhance:
        log('Бережное улучшение: мягкое шумоподавление → Lanczos → лёгкая резкость. '
            'Фильтры и декодирование — CPU; выбранный видеокодер сохраняется. Это не AI.')
    if quiet_rate:
        log(f'Тихий режим: темп чтения до {quiet_rate:g}×. Сборка будет дольше; '
            'это ограничение скорости, а не гарантированный процент загрузки или температуры GPU.')
