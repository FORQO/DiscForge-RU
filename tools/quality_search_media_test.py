"""Real adaptive CPU/NVENC probes, retained-reference checks and Qt results."""
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
from quality_trial import TrialPlan
from quality_search import run_quality_search
from quality_dialog import QualityDialog
from quality_trial_media_test import wait
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--noise', type=int, default=5)
    parser.add_argument('--cpu-only', action='store_true')
    parser.add_argument('--expect-pass', action='store_true')
    args = parser.parse_args()
    output = (args.output/("run-'поиск, "+str(time.time_ns()))).resolve()
    output.mkdir(parents=True)
    config = {name: str((args.tools/(name+'.exe')).resolve()) for name in ('ffmpeg', 'ffprobe')}
    config['output'] = str(output)
    source = output/'детали и шум.mkv'
    subprocess.run([config['ffmpeg'], '-v', 'error', '-y', '-f', 'lavfi', '-i',
        f'testsrc2=s=1920x1080:r=25:d=6,noise=alls={args.noise}:allf=t+u', '-c:v', 'libx264',
        '-threads', '4', '-preset', 'fast', '-crf', '10', '-pix_fmt', 'yuv420p', str(source)], check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    info = probe_media(str(source), config['ffprobe'])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    records = []
    def log(message):
        print(message, flush=True)
    modes = [False]
    if not args.cpu_only and nvenc_available(config['ffmpeg'], threading.Event(), log):
        modes.append(True)
    for gpu in modes:
        plan = TrialPlan(0, 1, (5_000_000,), 1 if gpu else 0, automatic=gpu, scene_count=2, retry_rates=True)
        directory = output/('gpu' if gpu else 'cpu')
        progress = []
        originals = {}
        def remember(value):
            progress.append(value)
            # First-pass terminal progress is mapped to 60, before any retries.
            if value == 60 and not originals:
                for path in directory.rglob('*.mkv'):
                    originals[str(path)] = (path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
        result = run_quality_search(info, plan, config, directory, threading.Event(), log, remember)
        assert result['search']['triggered'], 'Fixture no longer exercises adaptive search'
        assert len(result['search']['rounds']) > 1
        assert len(result['search']['rounds']) <= 9
        if args.expect_pass:
            assert result['recommendation']['bitrate'], 'Expected a successful adaptive recommendation'
        assert progress == sorted(progress) and progress[-1] == 100
        scenes = result.get('scenes', [result])
        assert len(scenes) == (2 if gpu else 1)
        assert len({tuple(v['bitrate'] for v in s['variants']) for s in scenes}) == 1
        for scene in scenes:
            assert all(v['quality']['vmaf'] and v['quality']['ssim'] for v in scene['variants'])
            assert all(v['quality']['ssim']['frames'] == scene['frames'] for v in scene['variants'])
        for path, (mtime, digest) in originals.items():
            assert Path(path).stat().st_mtime_ns == mtime, 'Original trial was rewritten'
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        records.append(result)
    app = QApplication.instance() or QApplication([])
    from disc_menu import initialize_fonts
    from main import interface_style
    initialize_fonts()
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(interface_style('graphite'))
    dialog = QualityDialog(info, config, 5_000_000)
    dialog.worker = SimpleNamespace(cancel=threading.Event(), isRunning=lambda: False)
    dialog.result_plan = dialog._plan()
    dialog._completed(records[-1])
    dialog._finished()
    dialog.progress.setValue(100)
    dialog.show()
    try:
        wait(app, lambda: dialog.frame_worker and not dialog.frame_worker.isRunning())
        assert dialog.table.rowCount() > 3
        assert dialog.apply_button.isEnabled()
        assert dialog.pick_recommended.isEnabled() == bool(records[-1]['recommendation']['bitrate'])
        dialog.grab().save(str(output/'adaptive-search.png'))
        dialog.retry_rates.setChecked(False)
        assert not dialog.apply_button.isEnabled()
    finally:
        dialog.reject()
        dialog.deleteLater()
        app.processEvents()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    (output/'RESULT.json').write_text(json.dumps(dict(passed=True, results=records,
        retained_references=True, old_trials_unchanged=True, source_unchanged=True, ui=True),
        ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: '+str(output), flush=True)


if __name__ == '__main__':
    main()
