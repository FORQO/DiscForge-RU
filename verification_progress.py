"""Byte-based verification telemetry; completion requires explicit validation."""
from pathlib import Path
import time


class VerificationProgress:
    def __init__(self, total, count, callback=None, *, scope='hash', clock=time.monotonic):
        self.total, self.count = total, count
        self.callback, self.scope, self.clock = callback, scope, clock
        self.done = self.index = 0
        self.started = None
        self.last_emit = -float('inf')
        self.phase = self.filename = ''

    def emit(self, done=None, *, force=False, state='reading', complete=False):
        if self.callback is None:
            return
        now = self.clock()
        if not force and now-self.last_emit < .25:
            return
        self.last_emit = now
        done = self.done if done is None else done
        elapsed = now-self.started if self.started is not None else 0
        speed = done/elapsed if elapsed >= 1 and done else 0
        remaining = max(0, self.total-done)/speed if speed else -1
        self.callback(dict(scope=self.scope, phase=self.phase, file=self.filename,
                           index=self.index, count=self.count, done=done, total=self.total,
                           speed=speed, remaining=remaining, state=state, complete=complete))

    def status(self, phase):
        self.phase, self.filename = phase, ''
        self.emit(force=True, state='waiting')

    def digest(self, path, cancel, phase, name=None):
        from build_cache import digest
        self.phase, self.filename = phase, name or Path(path).name
        self.index += 1
        if self.started is None:
            self.started = self.clock()
        offset = self.done
        self.emit(force=True)
        result = digest(path, cancel, progress=lambda done, total: self.emit(offset+done))
        self.done += result['size']
        self.emit(force=True)
        return result

    def finish(self):
        self.emit(force=True, complete=True)
