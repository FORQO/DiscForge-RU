"""Build a complete Windows portable archive from an existing PyInstaller folder."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

VERSION = '1.1.0'
DOWNLOADS = {
    'ffmpeg.zip': ('https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip',
                   '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'),
    'tsmuxer.zip': ('https://github.com/justdan96/tsMuxer/releases/download/2.7.0/tsMuxer-2.7.0-win64.zip',
                    'e3cf117d2c6f01332188123641c63fe38ab9731ae7aa23645f6f9a261a7a301c'),
    'java.zip': ('https://github.com/adoptium/temurin8-binaries/releases/download/jdk8u504-b01/OpenJDK8U-jre_x64_windows_hotspot_8u504b01.zip',
                 '82e2cdc6693737c5998445b31f69668fa0da77c7705121053f6508ac84961123'),
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(cache, name):
    url, expected = DOWNLOADS[name]
    path = cache/name
    if not path.exists():
        print('Downloading', name, flush=True)
        request = urllib.request.Request(url, headers={'User-Agent': 'DiscForge-release'})
        with urllib.request.urlopen(request, timeout=120) as response, path.open('wb') as stream:
            shutil.copyfileobj(response, stream)
    if digest(path) != expected:
        raise ValueError('Archive checksum mismatch: '+name)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=Path('build/dependencies'))
    parser.add_argument('--tools-from', type=Path, help='Reuse an already verified local portable distribution')
    args = parser.parse_args()
    app, output = args.app.resolve(), args.output.resolve()
    if not (app/'DiscForge_RU.exe').is_file():
        raise ValueError('Build the application first')
    output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[1]
    if args.tools_from:
        for name in ('tools', 'runtime'):
            shutil.copytree(args.tools_from/name, app/name, dirs_exist_ok=True)
    else:
        tools = app/'tools'; tools.mkdir(exist_ok=True)
        with zipfile.ZipFile(download(args.cache,'ffmpeg.zip')) as archive:
            for source, target in [('bin/ffmpeg.exe','ffmpeg.exe'),('bin/ffprobe.exe','ffprobe.exe'),('LICENSE','FFmpeg-LICENSE.txt'),('README.txt','FFmpeg-README.txt')]:
                member = next(n for n in archive.namelist() if n.endswith('/'+source))
                (tools/target).write_bytes(archive.read(member))
        with zipfile.ZipFile(download(args.cache,'tsmuxer.zip')) as archive:
            member = next(n for n in archive.namelist() if n.lower().endswith('tsmuxer.exe'))
            (tools/'tsMuxeR.exe').write_bytes(archive.read(member))
        with zipfile.ZipFile(download(args.cache,'java.zip')) as archive:
            for member in archive.namelist():
                parts = Path(member).parts[1:]
                if not parts or member.endswith('/'):
                    continue
                target = (app/'runtime/java').joinpath(*parts).resolve()
                if not target.is_relative_to(app/'runtime/java'):
                    raise ValueError('Invalid runtime archive path')
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(member))
    shutil.copytree(repo/'licenses', app/'licenses', dirs_exist_ok=True)
    python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if python_license.exists():
        shutil.copy2(python_license, app/'licenses/Python-LICENSE.txt')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(repo/name, app/name)
    shutil.copy2(repo/'docs/USER_GUIDE.md', app/'USER_GUIDE.md')
    report = output/'verification.json'
    subprocess.run([str(app/'DiscForge_RU.exe'), '--self-test', str(report)], timeout=60, check=True,
                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if not json.loads(report.read_text(encoding='utf-8'))['passed']:
        raise RuntimeError('Packaged application self-test failed')
    filename = f'DiscForge-RU-{VERSION}-Windows-x64.zip'
    with zipfile.ZipFile(output/filename, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app.rglob('*')):
            if path.is_file():
                archive.write(path, 'DiscForge_RU/'+path.relative_to(app).as_posix())
    (output/'SHA256SUMS.txt').write_text(digest(output/filename)+'  '+filename+'\n',encoding='ascii')
    print('Packaged', output/filename, flush=True)


if __name__ == '__main__':
    main()
