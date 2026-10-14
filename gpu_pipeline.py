"""Probe a real source through CUDA filters before selecting the hardware path."""
import threading


def cuda_command(command):
    result = list(command)
    at = result.index('-i')
    result[at:at] = ['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda']
    result[result.index('-vf')+1] = (
        'scale_cuda=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2:format=yuv420p,'
        'pad_cuda=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1')
    at = result.index('-pix_fmt')
    del result[at:at+2]
    result[result.index('-r'):result.index('-r')] = ['-fps_mode', 'cfr']
    return result


def supported(info, ffmpeg, cancel, log, runner, make_video):
    v = info.video_details
    if (info.is_hdr or v.get('pix_fmt') != 'yuv420p' or
            v.get('field_order') != 'progressive' or v.get('sample_aspect_ratio') != '1:1' or
            any(s.get('rotation', 0) for s in v.get('side_data_list', []))):
        log('NVDEC: параметры источника требуют CPU-фильтров; кодирование остаётся на NVENC.')
        return False
    probe = cuda_command(make_video(ffmpeg, info.path, '-', 8_000_000, True, True))
    probe[-3:] = ['-frames:v', '24', '-f', 'null', '-']
    timeout = threading.Event()
    timer = threading.Timer(30, timeout.set)
    class Cancellation:
        def is_set(self): return cancel.is_set() or timeout.is_set()
        def wait(self, seconds):
            cancel.wait(seconds)
            return self.is_set()
    timer.start()
    try:
        runner(probe, 'Проверка NVDEC и CUDA на исходнике', 0, 0, 1, Cancellation(), log, lambda _: None)
        log('GPU-конвейер: NVDEC → масштабирование CUDA → NVENC. Частота кадров приводится к 23,976.')
        return True
    except RuntimeError as exc:
        if cancel.is_set():
            raise
        log('CUDA-проверка не пройдена; используются CPU-фильтры + NVENC. ' + str(exc))
        return False
    finally:
        timer.cancel()
