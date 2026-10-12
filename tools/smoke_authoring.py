"""Opt-in REAL encoder/muxer/menu/ISO integration test; never writes a disc."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QGuiApplication
from core import create_bluray, probe_media
from disc_menu import MenuDesign


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('ffmpeg', 'ffprobe', 'tsmuxer', 'output'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--folder', action='store_true')
    args = p.parse_args()
    app = QGuiApplication([])
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = out / 'discforge-smoke.mp4'
    subprocess.run([args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-n',
                    '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24000/1001',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                    '-t', '12', '-c:v', 'libx264', '-c:a', 'aac', str(source)], check=True)
    result = create_bluray(probe_media(str(source), args.ffprobe), 'BD25', str(out),
                           args.ffmpeg, args.tsmuxer, not args.folder, False,
                           threading.Event(), print, lambda n: print(f'{n}%'),
                           [0, 4, 8], MenuDesign('DiscForge · проверка меню'))
    print('RESULT:', result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
