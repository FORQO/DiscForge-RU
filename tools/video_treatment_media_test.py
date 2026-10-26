"""Real gentle/NVENC trials, pacing, cancellation, reports and authored BDMV."""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from core import probe_media, create_bluray, run_command, DiscForgeError
from disc_menu import MenuDesign, initialize_fonts
from quality_trial import TrialPlan, run_trial, variant_command, capture, comparison_command, WIDTH, HEIGHT
from quality_cache import fingerprint, make_record, restore_record
from quality_dialog import QualityDialog, TrialWorker


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tools', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    out = args.output/('run-'+str(time.time_ns()))
    out.mkdir(parents=True)
    config = {key: str(args.tools/(exe+'.exe')) for key, exe in
              [('ffmpeg', 'ffmpeg'), ('ffprobe', 'ffprobe'), ('tsmuxer', 'tsMuxeR')]}
    source = out/'движение и шум.mkv'
    subprocess.run([config['ffmpeg'], '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc2=size=512x288:rate=25:duration=8', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8',
        '-vf', 'noise=alls=4:allf=t+u,setsar=1', '-c:v', 'libx264', '-threads', '4', '-crf', '23',
        '-c:a', 'aac', '-shortest', str(source)], check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    info = probe_media(str(source), config['ffprobe'])
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    cancel = threading.Event()
    logs = []
    def log(message):
        logs.append(message)
        print(message, flush=True)
    app = QApplication.instance() or QApplication([])
    initialize_fonts()
    app.setFont(QFont('Noto Sans', 9))
    from main import interface_style
    app.setStyleSheet(interface_style('graphite'))
    plan = TrialPlan(1, 2, (8_000_000,), encoder=1, enhance=True, quiet_rate=1)
    identity = fingerprint(info, plan, config, cancel, log, lambda _: None)
    result = run_trial(info, plan, config, out/'trials', cancel, log, lambda _: None)
    assert result['enhance'] and not result['cuda'] and result['backend']['nvenc']
    assert result['frames'] == 47
    pair = capture(comparison_command(config['ffmpeg'], result['before'], result['reference'], 7), cancel)
    assert len(pair) == WIDTH*2*HEIGHT*3
    # Compare corresponding pixels row by row, not two contiguous byte halves.
    before = b''.join(pair[y*WIDTH*6:y*WIDTH*6+WIDTH*3] for y in range(HEIGHT))
    after = b''.join(pair[y*WIDTH*6+WIDTH*3:(y+1)*WIDTH*6] for y in range(HEIGHT))
    assert before != after
    record = make_record(result, plan, identity)
    restored = restore_record(json.loads(json.dumps(record)), info, plan, config, cancel, log, lambda _: None)
    assert restored['archived'] and restored['enhance']
    for changed in (replace(plan, enhance=False), replace(plan, quiet_rate=0)):
        try:
            restore_record(record, info, changed, config, cancel, log, lambda _: None)
            raise AssertionError('Stale report accepted')
        except ValueError:
            pass
    timings = {}
    for rate in (0.0, 0.5):
        command = variant_command(config['ffmpeg'], result['reference'], out/f'pace-{rate}.mkv',
                                  8_000_000, True, False, 47, result['peak'], rate)
        start = time.monotonic()
        run_command(command, 'Проверка темпа', 0, 100, 2, cancel, log, lambda _: None)
        timings[str(rate)] = time.monotonic()-start
    assert timings['0.5'] > timings['0.0']+1 and timings['0.5'] > 2.5, timings
    stopped = threading.Event()
    timer = threading.Timer(.4, stopped.set)
    timer.start()
    start = time.monotonic()
    try:
        run_command(command, 'Отмена тихой пробы', 0, 100, 2, stopped, log, lambda _: None)
        raise AssertionError('Cancellation ignored')
    except DiscForgeError:
        assert stopped.is_set() and time.monotonic()-start < 5
    finally:
        timer.cancel()
    dialog = QualityDialog(info, dict(config, enhance=True, quiet_rate=1), 8_000_000, encoder=1)
    dialog.worker = TrialWorker(info, plan, config, out/'trials', dialog)
    dialog.result_plan = plan
    dialog.mode.setCurrentIndex(1)
    dialog._completed(result)
    dialog.show()
    for mode in range(3):
        dialog.comparison_mode.setCurrentIndex(mode)
        dialog._show_frame()
        end = time.monotonic()+15
        while time.monotonic() < end:
            app.processEvents()
            if dialog.frame_worker and not dialog.frame_worker.isRunning():
                break
            time.sleep(.02)
        assert not dialog.left.item.pixmap().isNull()
        assert not dialog.right.item.pixmap().isNull()
    dialog.comparison_mode.setCurrentIndex(0)
    for _ in range(30):
        app.processEvents()
        time.sleep(.02)
    dialog.grab().save(str(out/'before-after.png'))
    dialog.reject()
    # Use the production pipeline with AAC -> AC-3, BD-J and verification.
    commands = []
    def runner(cmd, *a, **kw):
        commands.append(cmd)
        return run_command(cmd, *a, **kw)
    with patch('core.run_command', side_effect=runner):
        disc = create_bluray(info, 'BD25', str(out/'build'), config['ffmpeg'], config['tsmuxer'],
            False, True, cancel, log, lambda _: None, chapters=[0.0], menu=MenuDesign('Бережная обработка', theme='graphite'),
            ffprobe=config['ffprobe'], resilient=True, video_bitrate=8_000_000, enhance=True, quiet_rate=1)
    encoded = [cmd for cmd in commands if '-c:v' in cmd and cmd[cmd.index('-c:v')+1] == 'h264_nvenc']
    assert encoded and '-readrate' in encoded[-1] and 'hqdn3d=' in encoded[-1][encoded[-1].index('-vf')+1]
    assert (disc/'BDMV/JAR/00000.jar').is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    (out/'RESULT.json').write_text(json.dumps(dict(passed=True, frames=result['frames'],
        timings=timings, backend=result['backend'], bdmv=str(disc), source_unchanged=True,
        comparison_pixels_changed=True, cache_restored=True, cancellation=True), indent=2), encoding='utf-8')
    (out/'log.txt').write_text('\n'.join(logs), encoding='utf-8')
    print('PASS:', out, flush=True)


if __name__ == '__main__':
    main()
