"""Conservative AVC/AC-3 remux eligibility; not Blu-ray certification.

No decoding: scan packet timing/size and every SPS/PPS in the file. Unknown
or unsupported properties fail closed. Reports live in memory, never in projects.
"""
from dataclasses import dataclass, field, replace
from audio_selection import available_tracks, selected_tracks
from collections import deque
from fractions import Fraction
from pathlib import Path
import hashlib
import heapq
import math
import queue
import re
import subprocess
import threading
import time

MODES = ('auto', 'copy', 'video_copy', 'encode')
RATES = {Fraction(24000,1001): '23.976', Fraction(24): '24',
         Fraction(50): '50', Fraction(60000,1001): '59.94'}


def signature(path):
    p = Path(path)
    st = p.stat()
    with p.open('rb') as f:
        head = f.read(65536)
        f.seek(max(0, st.st_size-65536))
        tail = f.read(65536)
    return (str(p.resolve()), st.st_size, st.st_mtime_ns, hashlib.sha256(head+tail).hexdigest())


@dataclass
class CopyReport:
    signature: tuple = ()
    video_ok: bool = False
    audio_ok: bool = False
    reasons: list = field(default_factory=list)
    audio_reason: str = ''
    video_bytes: int = 0
    audio_bytes: int = 0
    fps: str = ''
    audio_results: dict = field(default_factory=dict)
    video_max_bps: int = 40_000_000

    def for_selection(self, info):
        tracks = selected_tracks(info)
        if not tracks:
            return replace(self, audio_ok=True, audio_bytes=0, audio_reason='Аудиодорожки не выбраны.')
        if not self.audio_results:
            return self
        results = [self.audio_results.get(t.get('index'), {'ok':False, 'bytes':0, 'reason':'дорожка не проверена'}) for t in tracks]
        return replace(self, audio_ok=all(r['ok'] for r in results),
                       audio_bytes=sum(r['bytes'] if r['ok'] else math.ceil(info.duration*640000/8) for r in results),
                       audio_reason='; '.join(dict.fromkeys(r['reason'] for r in results if not r['ok'])))

    def recommendation(self, info, capacity):
        self = self.for_selection(info)
        if not self.video_ok:
            return 'encode', 'Нужно перекодирование: ' + '; '.join(self.reasons)
        audio_bps = sum(int(t.get('bit_rate') or 640000) if self.audio_results.get(t.get('index'), {}).get('ok') else 640000 for t in selected_tracks(info))
        if self.video_max_bps + audio_bps > 44_000_000:
            return 'encode', 'Суммарный битрейт выбранных потоков слишком высок. Выберите меньше дорожек или перекодирование.'
        audio_size = self.audio_bytes if self.audio_ok or self.audio_results else math.ceil(info.duration*640000/8)*len(selected_tracks(info))
        estimated = int((self.video_bytes+audio_size)*1.07)+256_000_000
        if estimated > capacity:
            return 'encode', 'Исходные потоки с запасом на меню не помещаются на выбранный диск. Выберите BD50 или перекодирование.'
        if self.audio_ok:
            return 'copy', 'Рекомендуется без перекодирования: видео и выбранные аудиодорожки прошли проверки, размер подходит. Качество исходных потоков сохраняется.'
        return 'video_copy', 'Рекомендуется копировать видео, преобразовать только звук в AC-3: ' + self.audio_reason


def stream_lines(command, cancel, consume):
    """Bounded pipe, responsive cancellation and a watchdog for stalled tools."""
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = queue.Queue(maxsize=1024)
    stop = threading.Event()
    def reader():
        try:
            for line in proc.stdout:
                while not stop.is_set():
                    try:
                        output.put(line, timeout=.1)
                        break
                    except queue.Full:
                        pass
                if stop.is_set():
                    break
        finally:
            while not stop.is_set():
                try:
                    output.put(None, timeout=.1)
                    break
                except queue.Full:
                    pass
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    recent = deque(maxlen=3)
    last = time.monotonic()
    try:
        while True:
            if cancel.is_set():
                raise RuntimeError('Анализ отменён.')
            if time.monotonic()-last > 60:
                raise RuntimeError('Инструмент анализа не отвечает.')
            try:
                line = output.get(timeout=.1)
            except queue.Empty:
                continue
            if line is None:
                break
            last = time.monotonic()
            recent.append(line.strip())
            consume(line.strip())
        proc.wait(timeout=5)
        if proc.returncode:
            raise ValueError('Поток не удалось проверить: ' + ' '.join(recent)[-400:])
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        thread.join(timeout=2)
        proc.stdout.close()


def metadata_reasons(info):
    v = info.video_details
    reasons = []
    if info.video_codec != 'h264':
        reasons.append('копирование сейчас поддерживает AVC/H.264; этот файл — '+info.video_codec)
    if info.is_hdr:
        reasons.append('HDR требует отдельного UHD-конвейера')
    try:
        fps = Fraction(info.video_fps)
    except (ValueError, ZeroDivisionError):
        fps = Fraction(0)
    allowed = (Fraction(24000,1001), Fraction(24)) if (info.width, info.height)==(1920,1080) else tuple(RATES) if (info.width,info.height)==(1280,720) else ()
    if fps not in allowed:
        reasons.append('для копирования поддерживаются 1080p 23,976/24 и 720p 23,976/24/50/59,94; разрешение и частота этого файла не подходят')
    if v.get('pix_fmt') != 'yuv420p':
        reasons.append('требуется 8-битное видео 4:2:0')
    levels = (40,41) if info.height==1080 else (32,40,41) if fps>30 else (31,32,40,41)
    if v.get('profile') not in ('High', 'Main') or int(v.get('level') or 0) not in levels:
        reasons.append('требуется профиль Main/High и уровень, подходящий разрешению и частоте (не выше 4.1)')
    if v.get('field_order') != 'progressive':
        reasons.append('прогрессивная развёртка не подтверждена')
    if v.get('sample_aspect_ratio') != '1:1':
        reasons.append('квадратные пиксели не подтверждены')
    return reasons


class HeaderCheck:
    def __init__(self, fps=None):
        self.current = None
        self.fields = {}
        self.seen = {}
        self.fps = fps

    def finish(self):
        if self.current is None:
            return
        f = self.fields
        kind = self.current
        # Different IDs/configurations can be valid H.264, but this remux subset
        # requires stable sequence and picture parameters throughout the title.
        old = self.seen.get(kind)
        if old is not None and old != f:
            raise ValueError('SPS/PPS меняются внутри фильма; безопасное копирование не подтверждено')
        if kind == 'sps':
            required = ('profile_idc','level_idc','frame_mbs_only_flag','max_num_ref_frames',
                        'max_dec_frame_buffering','max_num_reorder_frames','nal_hrd_parameters_present_flag',
                        'bit_rate_scale','cpb_size_scale','bit_rate_value_minus1[0]','cpb_size_value_minus1[0]')
            if any(k not in f for k in required):
                raise ValueError('не подтверждены SPS/HRD и ограничения буфера Blu-ray')
            if (f['profile_idc'] not in (77,100) or f['level_idc']>41 or f['frame_mbs_only_flag'] != 1
                or f.get('bit_depth_luma_minus8',0) != 0 or f.get('bit_depth_chroma_minus8',0) != 0
                or f.get('chroma_format_idc',1) != 1 or f['max_num_ref_frames']>4
                or f['max_dec_frame_buffering']>4 or f['max_num_reorder_frames']>3):
                raise ValueError('параметры SPS не входят в поддерживаемый профиль копирования Blu-ray')
            if f['nal_hrd_parameters_present_flag'] != 1 or f.get('cpb_cnt_minus1') != 0:
                raise ValueError('HRD потока не соответствует проверяемому режиму')
            rate = (f['bit_rate_value_minus1[0]']+1)*2**(6+f['bit_rate_scale'])
            buffer = (f['cpb_size_value_minus1[0]']+1)*2**(4+f['cpb_size_scale'])
            if rate>40_000_000 or buffer>30_000_000:
                raise ValueError('HRD превышает 40 Мбит/с или буфер 30 Мбит')
            if self.fps is not None:
                if f.get('timing_info_present_flag') != 1 or f.get('num_units_in_tick',0)<=0:
                    raise ValueError('не подтверждена частота кадров в SPS')
                rate = f.get('time_scale',0)/(2*f['num_units_in_tick'])
                if abs(rate-self.fps)>.001:
                    raise ValueError('частота кадров SPS не совпадает с временными метками контейнера')
        else:
            if f.get('num_slice_groups_minus1') != 0 or f.get('weighted_bipred_idc',0) not in (0,1,2):
                raise ValueError('PPS не входит в поддерживаемый профиль копирования')
        self.seen[kind] = dict(f)
        self.current = None

    def feed(self, line):
        if 'Sequence Parameter Set' in line or 'Picture Parameter Set' in line:
            self.finish()
            self.current = 'sps' if 'Sequence Parameter Set' in line else 'pps'
            self.fields = {}
        elif 'Packet:' in line:
            self.finish()
        elif self.current:
            match = re.search(r'\]\s+\d+\s+(\w+(?:\[\d+\])?)\s+[01]+\s+=\s+(-?\d+)\s*$', line)
            if match:
                self.fields[match[1]] = int(match[2])

    def complete(self):
        self.finish()
        if set(self.seen) != {'sps','pps'}:
            raise ValueError('SPS/PPS не найдены')


class PacketCheck:
    def __init__(self, info):
        self.info = info
        self.video_index = int(info.video_details['index'])
        self.audio_index = int(info.audio_details.get('index',-1))
        self.step = 1/float(Fraction(info.video_fps))
        self.last_dts = self.last_pts = self.last_key = None
        self.pts = []
        self.window = deque()
        self.window_bytes = self.video_bytes = self.audio_bytes = self.count = 0
        self.first_pts = self.audio_start = None
        self.audio_end = None

    def pts_check(self, pts):
        if self.last_pts is not None and abs(pts-self.last_pts-self.step)>.002:
            raise ValueError('обнаружена переменная частота кадров или разрыв временных меток')
        if self.first_pts is None:
            self.first_pts = pts
        self.last_pts = pts

    def feed(self, line):
        fields = dict(p.split('=',1) for p in line.split('|') if '=' in p)
        if 'stream_index' not in fields:
            return
        index = int(fields['stream_index'])
        if index not in (self.video_index,self.audio_index):
            return
        size = int(fields.get('size',0))
        if size <= 0 or size>20_000_000:
            raise ValueError('некорректный размер пакета')
        if 'C' in fields.get('flags',''):
            raise ValueError('обнаружен повреждённый пакет')
        if index == self.audio_index:
            pts = float(fields['pts_time'])
            duration = float(fields['duration_time'])
            if not math.isfinite(pts) or not math.isfinite(duration) or duration<=0:
                raise ValueError('неизвестные временные метки звука')
            if self.audio_end is not None and abs(pts-self.audio_end)>.002:
                raise ValueError('разрыв временных меток звука требует обработки синхронизации')
            if self.info.audio_codec == 'ac3' and (size>2560 or abs(duration-.032)>.002):
                raise ValueError('пакеты AC-3 выходят за проверяемые ограничения Blu-ray')
            self.audio_end = pts+duration
            self.audio_bytes += size
            if self.audio_start is None:
                self.audio_start = pts
            return
        pts = float(fields['pts_time'])
        # Matroska commonly omits DTS for the first reordered frames. PTS for
        # every frame is still checked; missing DTS later is not accepted.
        raw_dts = fields.get('dts_time','N/A')
        dts = None if raw_dts == 'N/A' and self.count < 4 else float(raw_dts)
        if (dts is not None and not math.isfinite(dts)) or not math.isfinite(pts):
            raise ValueError('неизвестные временные метки видео')
        if self.last_dts is not None and dts is not None and abs(dts-self.last_dts-self.step)>.002:
            raise ValueError('непостоянная частота кадров или разрыв DTS')
        position = self.count*self.step
        if 'K' in fields.get('flags',''):
            self.last_key = position
        if self.last_key is None or position-self.last_key>1.01:
            raise ValueError('ключевые кадры реже одного раза в секунду; точные переходы Blu-ray не подтверждены')
        heapq.heappush(self.pts, pts)
        if len(self.pts)>16:
            self.pts_check(heapq.heappop(self.pts))
        self.window.append((position,size))
        self.window_bytes += size
        while self.window and self.window[0][0]<=position-1:
            self.window_bytes -= self.window.popleft()[1]
        if self.window_bytes*8>40_000_000:
            raise ValueError('пиковый видеобитрейт превышает 40 Мбит/с')
        self.video_bytes += size
        self.count += 1
        self.last_dts = dts

    def complete(self):
        while self.pts:
            self.pts_check(heapq.heappop(self.pts))
        if self.count<2 or abs(self.count*self.step-self.info.duration)>max(.25,self.step*3):
            raise ValueError('длительность потока не совпадает с фильмом или поток неполон')
        if self.info.audio_tracks and (self.audio_start is None or not math.isfinite(self.audio_start)
                or abs(self.audio_start-self.first_pts)>.05):
            raise ValueError('смещение звука требует отдельной обработки синхронизации')


def analyze_copy(info, ffprobe, ffmpeg, cancel=None):
    cancel = cancel or threading.Event()
    result = CopyReport()
    try:
        result.signature = signature(info.path)
        result.reasons = metadata_reasons(info)
        if result.reasons:
            return result
        packets = PacketCheck(replace(info, audio_details={}, audio_tracks=0))
        audio_checks, audio_errors = {}, {}
        for stream in available_tracks(info):
            index = stream.get('index')
            if type(index) is not int:
                continue
            audio_info = replace(info, audio_details=stream, audio_tracks=1, audio_codec=stream.get('codec_name','?'),
                                 audio_channels=int(stream.get('channels') or 0))
            audio_checks[index] = PacketCheck(audio_info)
        def feed(line):
            packets.feed(line)
            fields = dict(p.split('=',1) for p in line.split('|') if '=' in p)
            index = int(fields.get('stream_index', -1))
            if index in audio_checks and index not in audio_errors:
                try:
                    audio_checks[index].feed(line)
                except (ValueError, KeyError, TypeError, OverflowError) as exc:
                    audio_errors[index] = str(exc)
        stream_lines([ffprobe,'-v','error','-show_packets','-show_entries',
                      'packet=stream_index,pts_time,dts_time,duration_time,size,flags','-of','compact=p=0',info.path], cancel, feed)
        packets.complete()
        headers = HeaderCheck(float(Fraction(info.video_fps)))
        stream_lines([ffmpeg,'-hide_banner','-nostdin','-loglevel','info','-i',info.path,
                      '-map',f'0:{packets.video_index}','-an','-c:v','copy','-bsf:v',
                      'h264_mp4toannexb,filter_units=pass_types=7|8,trace_headers','-f','null','-'],cancel,headers.feed)
        headers.complete()
        if signature(info.path) != result.signature:
            raise ValueError('файл изменился во время анализа')
        result.video_ok = True
        result.video_bytes = packets.video_bytes
        sps = headers.seen['sps']
        result.video_max_bps = (sps['bit_rate_value_minus1[0]']+1)*2**(6+sps['bit_rate_scale'])
        for index, checked in audio_checks.items():
            a = checked.info.audio_details
            ok = (index not in audio_errors and checked.info.audio_codec=='ac3' and int(a.get('sample_rate') or 0)==48000
                  and 1<=checked.info.audio_channels<=6 and 0<int(a.get('bit_rate') or 0)<=640000
                  and checked.audio_start is not None and abs(checked.audio_start-packets.first_pts)<=.05
                  and checked.audio_end is not None and abs(checked.audio_end-packets.first_pts-info.duration)<=.1)
            result.audio_results[index] = dict(ok=ok, bytes=checked.audio_bytes,
                reason=audio_errors.get(index, f'дорожка #{index}: {checked.info.audio_codec}; требуется AC-3 48 кГц с совместимыми временными метками'))
        selected = result.for_selection(info)
        result.audio_ok, result.audio_bytes, result.audio_reason = selected.audio_ok, selected.audio_bytes, selected.audio_reason
        result.fps = RATES[Fraction(info.video_fps)]
    except (ValueError, KeyError, TypeError, OSError, OverflowError, subprocess.SubprocessError) as exc:
        result.reasons = ['совместимость не подтверждена: '+str(exc)]
    return result


def choose_mode(mode, report, info, capacity):
    report = report.for_selection(info)
    if mode not in MODES:
        raise ValueError('Неизвестный режим обработки')
    if mode == 'encode':
        return 'encode', 'Выбрано полное перекодирование.'
    recommended, reason = report.recommendation(info,capacity)
    if mode == 'auto':
        return recommended, reason
    if recommended == 'encode' or (mode == 'copy' and not report.audio_ok):
        raise ValueError('Режим без перекодирования недоступен. '+reason)
    if mode == 'video_copy' and report.audio_ok:
        return 'copy', reason
    return mode, reason
