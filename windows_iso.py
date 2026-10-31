"""Create UDF 2.50 images using Windows IMAPI2FS. Does not open a recorder."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import uuid
from contextlib import contextmanager


@contextmanager
def pending_image(destination):
    """Keep output directory ACLs so Windows can mount the published ISO."""
    path = destination.parent / ('.DiscForge_menu_' + uuid.uuid4().hex + '.partial.iso')
    try:
        yield path
    finally:
        if path.exists():
            path.unlink()


def create_iso(source: Path, destination: Path, capacity: int, cancel: threading.Event,
               log=lambda _: None, progress=None, overlay: Path | None = None) -> None:
    if sys.platform == 'darwin':
        from macos_iso import create_iso as create_macos_iso
        return create_macos_iso(source, destination, capacity, cancel, log, progress, overlay)
    if sys.platform != 'win32':
        raise RuntimeError('ISO с меню пока собирается только на Windows. Выберите папку BDMV.')
    if cancel.is_set():
        raise RuntimeError('Операция отменена пользователем.')
    if destination.exists():
        raise FileExistsError(destination)
    powershell = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = Path(__file__).parent / 'tools/windows_iso.ps1'
    command = [str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(script), '-Source', str(source.resolve()),
               '-Destination', str(destination.resolve()), '-Capacity', str(capacity)]
    if overlay is not None:
        command.extend(['-Overlay', str(overlay.resolve())])
    expected = sum(p.stat().st_size for p in source.rglob('*') if p.is_file()) + 1_048_576
    log('Создаём файловую систему UDF 2.50…')
    # A file prevents pipe deadlocks; this helper emits only bounded diagnostics.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryDirectory(prefix='DiscForge_progress_') as metrics:
        marker = Path(metrics) / 'bytes.txt'
        command.extend(['-ProgressFile', str(marker)])
        proc = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while proc.poll() is None:
                if progress and marker.exists():
                    try:
                        progress(min(0.999, int(marker.read_text())/max(1, expected)))
                    except (OSError, ValueError):
                        pass  # Writer may be replacing a progress sample.
                if cancel.wait(0.1):
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
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError('Windows не создала ISO-образ.')

    if progress:
        progress(1.0)


def iso_backend_file():
    if sys.platform == 'darwin':
        from settings import find_binary
        return Path(find_binary('discforge-udf'))
    return Path(__file__).parent / 'tools/windows_iso.ps1'
