"""Real H.264 samples -> CPU/NVENC trials -> synchronized Qt viewer (no ISO needed)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QFont
from core import probe_media, nvenc_available
from quality_trial import TrialPlan, run_trial, capture, comparison_command, WIDTH, HEIGHT
from quality_dialog import QualityDialog, TrialWorker
from quality_metrics import score_variant, recommend
from main import interface_style


def wait(app, predicate, seconds=60):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for Qt trial/viewer')


def digest_image(image):
    image = image.convertToFormat(QImage.Format_RGB888)
    return hashlib.sha256(image.constBits()).hexdigest()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    output = (args.output/("run-'оценка, "+str(time.time_ns()))).resolve()
    output.mkdir()
    config = {name: str((args.tools/(name+'.exe')).resolve()) for name in ('ffmpeg', 'ffprobe')}
    config['output'] = str(output)
    source = output/'движение и детали.mkv'
    subprocess.run([config['ffmpeg'], '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc2=size=640x360:rate=25:duration=6', '-c:v', 'libx264', '-threads', '4', '-preset', 'fast',
        '-crf', '18', '-pix_fmt', 'yuv420p', '-vf', 'setsar=1', str(source)], check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    info = probe_media(str(source), config['ffprobe'])
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    cancel = threading.Event()
    logs = []
    def log(message):
        logs.append(message)
        print(message, flush=True)
    plan = TrialPlan(1, 2, (5_000_000, 8_000_000, 12_000_000), 0)
    result = run_trial(info, plan, config, output/'cpu', cancel, log, lambda _: None)
    assert len(result['variants']) == 3 and result['encoder'] == 'CPU x264'
    assert result['frames'] == 47
    assert all(Path(v['path']).stat().st_size > 0 for v in result['variants'])
    assert result['recommendation']['status'] == 'recommended'
    assert result['recommendation']['bitrate'] in plan.rates
    assert all(v['quality']['vmaf']['frames'] == 47 and v['quality']['ssim']['frames'] == 47 for v in result['variants'])
    identical = score_variant(config['ffmpeg'], result['reference'], result['reference'], 47,
                              output, cancel, log, lambda _: None)
    assert identical['ssim']['mean'] == 1.
    assert identical['vmaf']['mean'] > 95
    damaged = output/'degraded.mkv'
    subprocess.run([config['ffmpeg'], '-v', 'error', '-y', '-i', result['reference'],
                    '-vf', 'negate', '-c:v', 'ffv1', str(damaged)], check=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    bad_score = score_variant(config['ffmpeg'], result['reference'], damaged, 47, output,
                              cancel, log, lambda _: None)
    assert bad_score['ssim']['mean'] < .95 and bad_score['vmaf']['mean'] < 90
    assert recommend([dict(bitrate=5_000_000, quality=bad_score)])['status'] == 'below_target'
    ssim_only = score_variant(config['ffmpeg'], result['reference'], result['variants'][-1]['path'],
                             47, output, cancel, log, lambda _: None, vmaf=False)
    assert ssim_only['vmaf'] is None
    assert recommend([dict(bitrate=12_000_000, quality=ssim_only)])['status'] == 'provisional'
    metric_cancel = threading.Event()
    timers = []
    def cancel_during_vmaf(message):
        if message.startswith('Оценка VMAF'):
            timer = threading.Timer(.25, metric_cancel.set)
            timers.append(timer)
            timer.start()
    try:
        try:
            score_variant(config['ffmpeg'], result['reference'], result['variants'][0]['path'], 47,
                          output, metric_cancel, cancel_during_vmaf, lambda _: None)
        except RuntimeError:
            assert metric_cancel.is_set()
        else:
            raise AssertionError('Metric cancellation was swallowed')
    finally:
        for timer in timers:
            timer.cancel()
    assert not list(output.glob('metrics-*'))
    # The right panel must show exactly the selected frame, including after a
    # late seek. Compare it against a separately decoded single-stream frame.
    for index in (0, 23, result['frames']-1):
        raw = capture(comparison_command(config['ffmpeg'], result['reference'], result['variants'][0]['path'], index), cancel)
        assert len(raw) == WIDTH*2*HEIGHT*3
        pair = QImage(raw, WIDTH*2, HEIGHT, WIDTH*2*3, QImage.Format_RGB888)
        for half, path in enumerate((result['reference'], result['variants'][0]['path'])):
            separate = capture([config['ffmpeg'], '-v', 'error', '-i', path, '-vf', f'select=eq(n\\,{index})',
                '-frames:v', '1', '-fps_mode', 'passthrough', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], cancel)
            expected = QImage(separate, WIDTH, HEIGHT, WIDTH*3, QImage.Format_RGB888)
            assert digest_image(pair.copy(half*WIDTH, 0, WIDTH, HEIGHT)) == digest_image(expected)
    gpu = None
    if nvenc_available(config['ffmpeg'], cancel, log):
        gpu = run_trial(info, TrialPlan(1, 1, (5_000_000, 8_000_000), 1), config, output/'gpu', cancel, log, lambda _: None)
        assert gpu['encoder'] == 'NVIDIA NVENC'
        assert all(v['quality']['vmaf'] and v['quality']['ssim'] for v in gpu['variants'])
    app = QApplication.instance() or QApplication([])
    from disc_menu import initialize_fonts
    initialize_fonts()
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(interface_style('graphite'))
    dialog = QualityDialog(info, config, 8_000_000, encoder=0)
    dialog.start.setTime(dialog.start.minimumTime().addSecs(1))
    dialog.duration.setValue(2)
    for widget, rate in zip(dialog.rates, plan.rates):
        widget.setValue(rate/1e6)
    dialog.result_plan = dialog._plan()
    dialog.worker = SimpleNamespace(cancel=threading.Event(), isRunning=lambda: False)
    dialog._completed(result)
    dialog._finished()
    dialog.show()
    try:
        wait(app, lambda: not dialog.left.item.pixmap().isNull())
        dialog.slider.setValue(result['frames']-1)
        wait(app, lambda: dialog.frame_label.text().startswith('Кадр 47/'))
        assert dialog.apply_button.isEnabled()
        assert dialog.pick_recommended.isEnabled()
        assert result['variants'][dialog.table.currentRow()]['bitrate'] == result['recommendation']['bitrate']
        assert dialog.worst.isEnabled()
        dialog.grab().save(str(output/'quality-fit.png'))
        dialog.zoom.setCurrentIndex(1)
        app.processEvents()
        dialog.grab().save(str(output/'quality-100.png'))
        dialog.table.selectRow(2)
        wait(app, lambda: dialog.frame_worker and not dialog.frame_worker.isRunning())
        dialog.play.setChecked(True)
        wait(app, lambda: dialog.slider.value() == result['frames']-1 and not dialog.play.isChecked())
        # Start a fresh run through the actual UI and cancel it. No completed
        # results may be applied and no temporary directory may survive.
        dialog._begin()
        temp = Path(dialog.temporary.name)
        dialog._cancel()
        wait(app, lambda: not dialog._busy() and dialog.temporary is None)
        assert not temp.exists() and not dialog.apply_button.isEnabled()
        assert dialog.result is None
        dialog._begin()
        temp = Path(dialog.temporary.name)
        dialog.reject()
        wait(app, lambda: not dialog._busy() and not dialog.isVisible())
        assert not temp.exists() and dialog.temporary is None
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    report = dict(passed=True, cpu=result, gpu=gpu, synchronized_frames=[0, 23, 46],
                  ui_playback=True, ui_cancel_cleanup=True, ui_close_during_encode=True, source_unchanged=True,
                  identical_quality=identical, degraded_quality=bad_score, ssim_fallback=ssim_only,
                  cancellation_during_vmaf=True)
    (output/'RESULT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: '+str(output), flush=True)


if __name__ == '__main__':
    main()
