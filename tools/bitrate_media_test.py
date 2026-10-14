"""Real per-title encode rates -> common ISO; only short synthetic movies."""
import argparse
from dataclasses import replace
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
from PySide6.QtGui import QGuiApplication
import core
from bitrate_advisor import analyze_bitrate, recommend
from disc_collection import create_collection
from disc_menu import MenuDesign
from smart_copy import analyze_copy


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tools', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    app = QGuiApplication.instance() or QGuiApplication([])
    cancel = threading.Event()
    output = (args.output/('run-'+str(time.time_ns()))).resolve()
    output.mkdir(parents=True)
    config = dict(ffmpeg=str((args.tools/'ffmpeg.exe').resolve()), ffprobe=str((args.tools/'ffprobe.exe').resolve()),
                  tsmuxer=str((args.tools/'tsMuxeR.exe').resolve()), output=str(output))
    jobs = []
    for index, size in enumerate(('640x360', '1024x426')):
        target = output/f'episode-{index+1}.mkv'
        subprocess.run([config['ffmpeg'], '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=24000/1001:duration=4',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4', '-map', '0:v:0', '-map', '1:a:0',
                        '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-b:v', '1500k', '-pix_fmt', 'yuv420p',
                        '-c:a', 'ac3', '-b:a', '384k', '-ac', '2', '-shortest', str(target)], check=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        info = core.probe_media(str(target), config['ffprobe'])
        info = replace(info, copy_report=analyze_copy(info, config['ffprobe'], config['ffmpeg'], cancel))
        info = replace(info, video_analysis=analyze_bitrate(info, config['ffprobe'], cancel))
        assert recommend(info).mode == 'encode'
        assert recommend(info).bitrate == 5000000
        jobs.append((info, [0., 2.]))
    config['video_rates'] = {jobs[0][0].path: 5000000, jobs[1][0].path: 6500000}
    config['movie_names'] = {jobs[0][0].path: 'Серия 1 — Автобитрейт', jobs[1][0].path: 'Серия 2 — Свой битрейт'}
    captured = []
    original = core.make_encode_command
    def command(ffmpeg, info, video, audio, bitrate, nvenc, fast=False):
        cmd = original(ffmpeg, info, video, audio, bitrate, nvenc, fast)
        captured.append({'source': info.path, 'bitrate': bitrate, 'command_rate': int(cmd[cmd.index('-b:v')+1])})
        return cmd
    with patch('core.make_encode_command', side_effect=command):
        iso = create_collection(jobs, config, 'BD25', True, False, cancel, lambda m: print(m, flush=True), lambda p: None,
                                lambda m, c: MenuDesign('Проверка автобитрейта'), modes=['encode', 'encode'], fast=False)
    assert [r['bitrate'] for r in captured] == [5000000, 6500000]
    assert all(r['bitrate'] == r['command_rate'] for r in captured)
    report = json.loads(iso.with_name(iso.name+'.verification.json').read_text('utf-8'))
    assert report['iso_payload_verified'] and all(r['passed'] for r in report['title_reports'])
    result = dict(passed=True, iso=str(iso), commands=captured,
                  recommendations=[recommend(m).__dict__ for m, _ in jobs],
                  iso_payload_verified=True, hardware_player_tested=False, perceptual_quality_measured=False)
    (output/'RESULT.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS', output/'RESULT.json', flush=True)


if __name__ == '__main__':
    main()
