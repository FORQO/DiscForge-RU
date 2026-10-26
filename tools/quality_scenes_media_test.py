"""Real multi-scene FFmpeg CPU/NVENC integration and Qt scene navigation."""
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
from core import probe_media, nvenc_available
from quality_scenes import run_auto_trial
from quality_trial import TrialPlan
from quality_dialog import QualityDialog
from quality_trial_media_test import wait
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = (args.output/("run-'сцены, "+str(time.time_ns()))).resolve()
    output.mkdir(parents=True)
    config = {name: str((args.tools/(name+'.exe')).resolve()) for name in ('ffmpeg', 'ffprobe')}
    config['output'] = str(output)
    source = output/'чёрный экран, движение, текстура и тени.mkv'
    command = [config['ffmpeg'], '-v', 'error', '-y']
    for input_filter in ('color=c=black:s=640x360:r=25:d=3',
                         'testsrc2=s=640x360:r=25:d=5',
                         'color=c=gray:s=640x360:r=25:d=5,noise=alls=30:allf=u',
                         'testsrc2=s=640x360:r=25:d=5,eq=brightness=-0.2'):
        command += ['-f', 'lavfi', '-i', input_filter]
    command += ['-filter_complex', '[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0,setsar=1[v]',
                '-map', '[v]', '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-crf', '16',
                '-pix_fmt', 'yuv420p', str(source)]
    subprocess.run(command, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    info = probe_media(str(source), config['ffprobe'])
    def log(message):
        print(message, flush=True)
    progress = []
    plan = TrialPlan(0, 1, (5_000_000, 8_000_000, 12_000_000), 0, automatic=True)
    result = run_auto_trial(info, plan, config, output/'cpu', threading.Event(), log, progress.append)
    assert len(result['scenes']) == 3
    assert len(result['selection']['candidates']) == 24
    assert all(s['start'] >= 2.5 for s in result['scenes']), 'Black segment selected'
    assert all(a['start']+a['duration'] <= b['start']+.01 for a, b in zip(result['scenes'], result['scenes'][1:]))
    assert all(v['quality']['ssim']['frames'] == s['frames'] and v['quality']['vmaf']['frames'] == s['frames']
               for s in result['scenes'] for v in s['variants'])
    common = set(plan.rates).intersection(*(set(a['eligible']) for a in result['recommendation']['per_scene']))
    assert result['recommendation']['bitrate'] == (min(common) if common else None)
    assert progress[-1] == 100 and all(a <= b+.001 for a, b in zip(progress, progress[1:])), progress
    gpu = None
    if nvenc_available(config['ffmpeg'], threading.Event(), log):
        gpu = run_auto_trial(info, TrialPlan(0, 1, (8_000_000,), 1, automatic=True, scene_count=2),
                             config, output/'gpu', threading.Event(), log, lambda _: None)
        assert len(gpu['scenes']) == 2
        assert all(s['encoder'] == 'NVIDIA NVENC' and s['cuda'] for s in gpu['scenes'])
        assert all(v['quality']['ssim'] and v['quality']['vmaf'] for s in gpu['scenes'] for v in s['variants'])
    app = QApplication.instance() or QApplication([])
    from disc_menu import initialize_fonts
    from main import interface_style
    initialize_fonts()
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(interface_style('graphite'))
    dialog = QualityDialog(info, config, 8_000_000, encoder=0)
    dialog.duration.setValue(1)
    for widget, rate in zip(dialog.rates, plan.rates):
        widget.setValue(rate/1e6)
    dialog.result_plan = dialog._plan()
    dialog.worker = SimpleNamespace(cancel=threading.Event(), isRunning=lambda: False)
    dialog._completed(result)
    dialog._finished()
    dialog.show()
    try:
        wait(app, lambda: not dialog.left.item.pixmap().isNull())
        for index in range(3):
            dialog.scene_results.setCurrentIndex(index)
            wait(app, lambda: dialog.frame_worker and not dialog.frame_worker.isRunning())
            assert dialog.result['reference'] == result['scenes'][index]['reference']
            assert dialog.result['recommendation'] == result['recommendation']
            dialog.slider.setValue(dialog.slider.maximum())
            wait(app, lambda: dialog.frame_label.text().startswith(f"Кадр {dialog.result['frames']}/"))
        dialog.table.selectRow(0)
        dialog._weak_scene()
        wait(app, lambda: dialog.frame_worker and not dialog.frame_worker.isRunning())
        assert dialog.apply_button.isEnabled()
        dialog.grab().save(str(output/'automatic-scenes.png'))
        # UI cancellation while the automatic scanner is running must remove
        # the whole batch and must never expose a partially completed recommendation.
        dialog._begin()
        temp = Path(dialog.temporary.name)
        wait(app, lambda: 'Поиск сложных сцен' in dialog.log.toPlainText())
        dialog._cancel()
        wait(app, lambda: not dialog._busy() and dialog.temporary is None)
        assert not temp.exists() and dialog.result is None and dialog.batch_result is None
        assert not dialog.apply_button.isEnabled() and not dialog.pick_recommended.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    report = dict(passed=True, cpu=result, gpu=gpu, scene_navigation=True, weakest_scene=True,
                  scan_cancel_cleanup=True, source_unchanged=True, progress_monotonic=True)
    (output/'RESULT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: '+str(output), flush=True)


if __name__ == '__main__':
    main()
