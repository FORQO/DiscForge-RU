"""Crash-safe, content-addressed stage checkpoints; never trust file timestamps alone."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from settings import write_json

REVISION = 1


def digest(path, cancel, progress=None):
    path = Path(path)
    before = path.stat()
    h = hashlib.sha256()
    read = 0
    with path.open('rb') as stream:
        while True:
            if cancel.is_set():
                raise RuntimeError('Операция отменена; завершённые этапы сохранены.')
            block = stream.read(4 * 1024 * 1024)
            if not block:
                break
            h.update(block)
            read += len(block)
            if progress:
                progress(read, before.st_size)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Файл изменился во время проверки: ' + str(path))
    return {'size': after.st_size, 'sha256': h.hexdigest()}


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str, allow_nan=False).encode('utf-8')).hexdigest()


class BuildCache:
    def __init__(self, root, source, cancel, log, enabled=True, verification=None):
        self.enabled, self.cancel, self.log = enabled, cancel, log
        self.verification = verification
        self.root = Path(root) / '.diskforge-cache' / key(str(Path(source).resolve()).casefold())[:24]
        self.data = {'revision': REVISION, 'stages': {}}
        self.lock = self.temporary = None

    def __enter__(self):
        if not self.enabled:
            self.temporary = tempfile.TemporaryDirectory(prefix='DiscForge_work_', dir=self.root.parent.parent)
            self.root = Path(self.temporary.name)
            return self
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root / 'build.lock').open('a+b')
        try:
            self.lock.seek(0)
            if not self.lock.read(1):
                self.lock.write(b'0')
                self.lock.flush()
            self.lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            self.lock = None
            raise RuntimeError('Этот фильм уже собирается в другом окне DiskForge.')
        try:
            data = json.loads((self.root / 'checkpoint.json').read_text(encoding='utf-8'))
            if data.get('revision') == REVISION and isinstance(data.get('stages'), dict):
                self.data = data
        except (OSError, ValueError, AttributeError):
            pass
        self.log('Завершённые этапы сохраняются: ' + str(self.root))
        return self

    def __exit__(self, kind, value, traceback):
        if self.lock:
            self.lock.close()  # OS releases the lock even after a process crash.
        if self.temporary:
            self.temporary.cleanup()

    def _save(self):
        write_json(self.root / 'checkpoint.json', self.data)

    def _record(self, paths, label='Проверка файлов'):
        result, entries = {}, []
        for path in paths:
            path = Path(path).resolve()
            if not path.is_relative_to(self.root.resolve()):
                raise ValueError('Этап находится за пределами рабочего каталога.')
            files = sorted(path.rglob('*')) if path.is_dir() else [path]
            for file in files:
                if file.is_file():
                    if file.is_symlink() or not file.resolve().is_relative_to(self.root.resolve()):
                        raise ValueError('Ссылки в рабочем каталоге не поддерживаются.')
                    entries.append(file)
        from verification_progress import VerificationProgress
        tracker = VerificationProgress(sum(p.stat().st_size for p in entries), len(entries), self.verification)
        for file in entries:
            name = file.relative_to(self.root.resolve()).as_posix()
            result[name] = tracker.digest(file, self.cancel, label, name)
        tracker.finish()
        return result

    def reusable(self, stage, signature, paths):
        if not self.enabled:
            return False
        old = self.data['stages'].get(stage)
        if not isinstance(old, dict) or old.get('key') != key(signature):
            return False
        self.log('Проверяем сохранённый этап: ' + stage + '…')
        try:
            current = self._record(paths, 'Проверка сохранённого этапа: ' + stage)
            valid = bool(current) and current == old.get('files')
        except (OSError, ValueError):
            valid = False
        if valid:
            self.log('Возобновление: этап «' + stage + '» уже готов; повторная обработка не нужна.')
        return valid

    def begin(self, stage):
        if self.enabled:
            # Invalidate BEFORE modifying files, including before a process crash.
            self.data['stages'].pop(stage, None)
            self._save()

    def commit(self, stage, signature, paths, elapsed=None):
        if self.enabled:
            self.data['stages'][stage] = {'key': key(signature), 'files': self._record(paths, 'Сохранение контрольных сумм: ' + stage),
                                           'seconds': elapsed, 'finished': time.time()}
            self._save()

