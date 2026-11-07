"""Small, disposable library previews. No source files or projects are rewritten."""
from pathlib import Path
import subprocess
import threading
import time
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool


class ThumbnailSignals(QObject):
    ready = Signal(str, bytes)


class ThumbnailTask(QRunnable):
    def __init__(self, path, duration, ffmpeg, signals, cancel):
        super().__init__()
        self.path, self.duration, self.ffmpeg = path, duration, ffmpeg
        self.signals, self.cancel = signals, cancel

    def run(self):
        if self.cancel.is_set():
            return
        command = [self.ffmpeg, '-v', 'error', '-ss', str(min(30, max(0, self.duration*.1))),
                   '-threads', '1', '-i', self.path, '-an', '-sn', '-frames:v', '1', '-vf',
                   'scale=160:90:force_original_aspect_ratio=increase,crop=160:90',
                   '-f', 'image2pipe', '-vcodec', 'mjpeg', 'pipe:1']
        process = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic()+8
            while not self.cancel.is_set() and time.monotonic() < deadline:
                try:
                    data, _ = process.communicate(timeout=.2)
                    if process.returncode == 0 and 0 < len(data) <= 500_000 and not self.cancel.is_set():
                        self.signals.ready.emit(self.path, data)
                    return
                except subprocess.TimeoutExpired:
                    pass
        except (OSError, RuntimeError):
            pass
        finally:
            if process is not None and process.poll() is None:
                process.kill(); process.communicate()


class LibraryThumbnails(QObject):
    ready = Signal(str, bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(2)
        self.cancel = threading.Event()
        self.requested = set()
        self.signals = ThumbnailSignals(self)
        self.signals.ready.connect(self.ready)

    def request(self, path, duration, ffmpeg):
        if self.cancel.is_set() or path in self.requested or not Path(path).is_file() or not ffmpeg:
            return
        self.requested.add(path)
        self.pool.start(ThumbnailTask(path, duration, ffmpeg, self.signals, self.cancel))

    def shutdown(self):
        self.cancel.set(); self.pool.clear(); self.pool.waitForDone(1000)
