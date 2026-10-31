"""Build the native UDF writer and fetch the pinned Apple Silicon tsMuxer CLI."""
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REV = 'c6b1186209e42c877052e762c9185f3226ef8ea2'


def download(url, target, sha256):
    if not target.exists():
        with urllib.request.urlopen(url, timeout=60) as source, target.open('wb') as out:
            shutil.copyfileobj(source, out)
    if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
        target.unlink()
        raise ValueError('Checksum mismatch: ' + url)
    return target


def main():
    if platform.system() != 'Darwin':
        raise RuntimeError('Run this script on macOS with Xcode Command Line Tools installed.')
    cache = ROOT / 'build/macos-tools'
    cache.mkdir(parents=True, exist_ok=True)
    binary = ROOT / 'bin'
    binary.mkdir(exist_ok=True)
    archive = download('https://codeload.github.com/justdan96/tsMuxer/tar.gz/' + REV,
                       cache / 'source.tar.gz', '09e028daee4ec96f3299cb3218151e334ba5bf2eeb444dd1ae7a01ed3de95bb4')
    source = cache / ('tsMuxer-' + REV)
    with tarfile.open(archive) as tar:
        tar.extractall(cache, filter='data')
    from patch_macos_udf import patch_source
    patch_source(source)
    files = ['tsMuxer/iso_writer.cpp', 'tsMuxer/convertUTF.cpp', 'tsMuxer/utf8Converter.cpp',
             'tsMuxer/vod_common.cpp', 'libmediation/types/types.cpp',
             'libmediation/fs/osdep/file_unix.cpp', 'libmediation/fs/osdep/directory_unix.cpp']
    subprocess.run(['clang++', '-std=c++17', '-O2', '-DTSMUXER_VERSION="DiskForge"',
                    '-I', str(source/'libmediation'), '-I', str(source/'tsMuxer'),
                    str(ROOT/'tools/macos_udf.cpp'), *[str(source/f) for f in files],
                    '-o', str(binary/'discforge-udf')], check=True)
    if platform.machine() == 'arm64':
        archive = download('https://github.com/justdan96/tsMuxer/releases/download/2.7.0/tsMuxer-2.7.0-mac.zip',
                           cache/'tsmuxer.zip', 'bf90b608321f491b955fd9432000a8370c048e9c306e3a5833dfd75e5fa530e1')
        with zipfile.ZipFile(archive) as zip:
            (binary/'tsMuxeR').write_bytes(zip.read('tsMuxeR'))
        (binary/'tsMuxeR').chmod(0o755)
    elif not (binary/'tsMuxeR').is_file():
        raise RuntimeError('Place a native Intel macOS tsMuxeR in bin/tsMuxeR before packaging.')
    if platform.machine() == 'arm64':
        java = download('https://cdn.azul.com/zulu/bin/zulu8.98.0.107-ca-jre8.0.512-macosx_aarch64.tar.gz',
                        cache/'java.tar.gz', 'e363187e4c49650906b17466d5f158a2a488a0ed8dde296a54399fa45d19e895')
        extracted_java = cache/'zulu8.98.0.107-ca-jre8.0.512-macosx_aarch64'
        if extracted_java.exists():
            shutil.rmtree(extracted_java)
        with tarfile.open(java) as tar:
            tar.extractall(cache, filter='data')
        java_home = cache/'zulu8.98.0.107-ca-jre8.0.512-macosx_aarch64/Contents/Home'
        runtime = ROOT/'runtime/java'
        if runtime.exists():
            shutil.rmtree(runtime)
        shutil.copytree(java_home, runtime, symlinks=True)
    else:
        if not (ROOT/'runtime/java/lib/server/libjvm.dylib').is_file():
            raise RuntimeError('Place a native Intel macOS Java 8 JRE in runtime/java.')
    shutil.copyfile(source/'LICENSE', ROOT/'licenses/tsMuxer-Apache-2.0.txt')
    print('Native macOS tools ready:', binary)


if __name__ == '__main__':
    main()
