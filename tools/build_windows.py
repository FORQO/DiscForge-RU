"""Build Windows Studio with Python and system DLLs on a clean search path."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--distpath', type=Path, default=ROOT/'dist')
    parser.add_argument('--workpath', type=Path, default=ROOT/'build/pyinstaller-windows')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Run this builder on Windows.')
    system = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    environment = os.environ.copy()
    environment['PATH'] = os.pathsep.join(map(str, (
        Path(sys.executable).parent, Path(sys.base_prefix), system/'System32', system, system/'System32/Wbem')))
    environment['PYTHONUTF8'] = '1'
    for name in ('PYTHONPATH', 'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
        environment.pop(name, None)
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
        '--icon', str(ROOT/'assets/brand/diskforge.ico'), '--onedir',
        '--add-data', str(ROOT/'assets')+';assets',
        '--add-data', str(ROOT/'tools/windows_iso.ps1')+';tools',
        '--add-data', str(ROOT/'LICENSE')+';.',
        '--add-data', str(ROOT/'THIRD_PARTY_NOTICES.md')+';.',
        '--name', 'DiscForge_RU', '--distpath', str(args.distpath.resolve()),
        '--workpath', str(args.workpath.resolve()), '--specpath', str(args.workpath.resolve()),
        str(ROOT/'main.py')], cwd=ROOT, env=environment, check=True)


if __name__ == '__main__':
    main()
