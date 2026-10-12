"""Create UDF 2.50 images using Windows IMAPI2FS. Does not open a recorder."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


def create_iso(source: Path, destination: Path, capacity: int, cancel: threading.Event,
               log=lambda _: None) -> None:
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
    log('Создаём файловую систему UDF 2.50…')
    # A file prevents pipe deadlocks; this helper emits only bounded diagnostics.
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while proc.poll() is None:
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
