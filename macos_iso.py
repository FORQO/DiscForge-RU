"""Package an authored Blu-ray folder using the native tsMuxer UDF 2.50 writer."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from settings import find_binary


def create_iso(source, destination, capacity, cancel, log=lambda _: None, progress=None, overlay=None):
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if cancel.is_set():
        raise RuntimeError('Операция отменена пользователем.')
    if destination.exists():
        raise FileExistsError(destination)
    if not (source / 'BDMV').is_dir():
        raise ValueError('Не найдена папка BDMV.')
    if destination.resolve().is_relative_to(source):
        raise ValueError('Сохраняйте ISO за пределами исходной папки Blu-ray.')
    binary = find_binary('discforge-udf')
    if not Path(binary).is_file() and not shutil.which(binary):
        raise FileNotFoundError('Не найден discforge-udf. Выполните сборку инструментов macOS.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Overlay files replace authored menu files without altering the source disc.
    with tempfile.TemporaryDirectory(prefix='.diskforge-iso-', dir=destination.parent) as temporary:
        temporary = Path(temporary)
        tree = source
        if overlay is not None:
            tree = temporary / 'tree'
            tree.mkdir()
            for base in (source, Path(overlay).resolve()):
                for path in sorted(base.rglob('*')):
                    if cancel.is_set():
                        raise RuntimeError('Операция отменена пользователем.')
                    if path.is_symlink():
                        raise ValueError('Символические ссылки в структуре Blu-ray не поддерживаются.')
                    target = tree / path.relative_to(base)
                    if path.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif path.is_file():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.unlink(missing_ok=True)
                        try:
                            os.link(path, target)
                        except OSError:
                            shutil.copyfile(path, target)
                    else:
                        raise ValueError('Неподдерживаемый файл: ' + str(path))
        expected = sum(p.stat().st_size for p in tree.rglob('*') if p.is_file())
        if expected > capacity:
            raise ValueError('Размер файлов превышает ёмкость диска.')
        pending = temporary / 'image.iso'
        log('Создаём ISO UDF 2.50 средствами macOS…')
        with tempfile.TemporaryFile() as output:
            proc = subprocess.Popen([binary, str(tree), str(pending), str(capacity)],
                                    stdout=output, stderr=subprocess.STDOUT)
            try:
                while proc.poll() is None:
                    if progress and pending.exists():
                        progress(min(.99, pending.stat().st_size / max(expected, 1)))
                    if cancel.wait(.1):
                        raise RuntimeError('Операция отменена пользователем.')
                output.seek(0)
                message = output.read(16000).decode('utf-8', errors='replace')
                if proc.returncode:
                    raise RuntimeError('Не удалось создать ISO: ' + message[-2000:])
                log(message.strip())
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
        if cancel.is_set():
            raise RuntimeError('Операция отменена пользователем.')
        if not pending.is_file() or not 0 < pending.stat().st_size <= capacity:
            raise ValueError('Пустой ISO или превышена ёмкость диска.')
        # Exclusive, atomic publication: never overwrite an existing result.
        os.link(pending, destination)
    if progress:
        progress(1.)


from contextlib import contextmanager
import plistlib


@contextmanager
def mounted_iso(iso):
    # A unique mountpoint and -nobrowse keep verification out of Finder.
    with tempfile.TemporaryDirectory(prefix='DiskForge-mount-') as temporary:
        mount = Path(temporary).resolve() / 'disc'
        mount.mkdir()
        result = subprocess.run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-plist',
                                 '-mountpoint', str(mount), str(Path(iso).resolve())],
                                capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError('Не удалось подключить ISO: ' + result.stderr.decode('utf-8', errors='replace')[-1600:])
        try:
            data = plistlib.loads(result.stdout)
            if not any(item.get('mount-point') and Path(item['mount-point']).resolve() == mount for item in data.get('system-entities', [])):
                raise RuntimeError('macOS не предоставила точку подключения ISO.')
            yield mount
        finally:
            detached = subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)], capture_output=True, timeout=60)
            if detached.returncode:
                raise RuntimeError('Не удалось отключить проверяемый ISO: ' +
                                   detached.stderr.decode('utf-8', errors='replace')[-1000:])
