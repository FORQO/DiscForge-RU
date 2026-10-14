"""Inspect real NAL types: container key-frame flags alone do not prove IDR GOPs."""
import queue
import re
import subprocess
import threading
import time


class IDRCheck:
    def __init__(self, fps):
        self.fps = fps
        self.buffer = b''
        self.frames = 0
        self.last_idr = None
        self.in_picture = False
        self.has_slice = False
        self.idr = False

    def finish_picture(self):
        if not self.in_picture or not self.has_slice:
            return
        if self.idr:
            self.last_idr = self.frames
        if self.last_idr is None or self.frames - self.last_idr >= self.fps + .01:
            raise ValueError('не подтверждены закрытые GOP с IDR не реже одной секунды')
        self.frames += 1

    def nal(self, data):
        if not data:
            return
        kind = data[0] & 31
        if data[0] & 0x80 or kind in (2,3,4,14,15,19,20,21):
            raise ValueError('повреждённый или неподдерживаемый расширенный NAL AVC')
        if kind == 9:
            self.finish_picture()
            self.in_picture, self.idr = True, False
            self.has_slice = False
        elif kind in (1, 5):
            if not self.in_picture:
                raise ValueError('не удалось разделить AVC на кадры')
            self.idr |= kind == 5
            self.has_slice = True

    def feed(self, data, final=False):
        self.buffer += data
        markers = list(re.finditer(b'\x00\x00(?:\x00)?\x01', self.buffer))
        for a, b in zip(markers, markers[1:]):
            self.nal(self.buffer[a.end():b.start()])
        if markers:
            last = markers[-1]
            if final:
                self.nal(self.buffer[last.end():])
                self.buffer = b''
            else:
                self.buffer = self.buffer[last.start():]
        if len(self.buffer) > 20_000_000:
            raise ValueError('слишком большой или повреждённый NAL')
        if final:
            self.finish_picture()
            if self.frames < 2:
                raise ValueError('AVC не содержит проверяемой последовательности кадров')


def inspect_idr(info, ffmpeg, cancel):
    from fractions import Fraction
    check = IDRCheck(float(Fraction(info.video_fps)))
    command = [ffmpeg, '-hide_banner', '-nostdin', '-v', 'error', '-i', info.path,
               '-map', f'0:{info.video_details["index"]}', '-an', '-c:v', 'copy', '-bsf:v',
               'h264_mp4toannexb,h264_metadata=aud=insert', '-f', 'h264', 'pipe:1']
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    chunks = queue.Queue(maxsize=8)
    stop = threading.Event()
    def reader():
        try:
            while not stop.is_set():
                data = proc.stdout.read(256 * 1024)
                while not stop.is_set():
                    try:
                        chunks.put(data, timeout=.1)
                        break
                    except queue.Full:
                        pass
                if not data:
                    break
        finally:
            pass
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    last = time.monotonic()
    try:
        while True:
            if cancel.is_set():
                raise RuntimeError('Анализ отменён.')
            if time.monotonic() - last > 60:
                raise ValueError('Проверка IDR не отвечает.')
            try:
                data = chunks.get(timeout=.1)
            except queue.Empty:
                continue
            last = time.monotonic()
            check.feed(data, final=not data)
            if not data:
                break
        if proc.wait(timeout=5):
            raise ValueError('Не удалось проверить IDR потока.')
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        thread.join(timeout=2)
        proc.stdout.close()
    return check.frames
