"""Explicit byte budgets. GB means 1,000,000,000 bytes, as on blank discs.

This is a planning estimate, not a promise about VBR encoders or PGS rendering.
The authored folder and final ISO must also pass a strict byte-size gate.
"""
from dataclasses import dataclass
import math
from pathlib import Path
from audio_selection import selected_tracks
from audio_pipeline import copy_format


@dataclass(frozen=True)
class Budget:
    capacity: int
    video: int = 0
    audio: int = 0
    subtitles: int = 0
    overhead: int = 0
    titles: tuple = ()

    @property
    def total(self):
        return self.video + self.audio + self.subtitles + self.overhead

    @property
    def remaining(self):
        return self.capacity - self.total

    @property
    def warning(self):
        return self.total >= self.capacity * .9

    def explanation(self):
        parts = (f'Оценка: {self.total/1e9:.2f} / {self.capacity/1e9:g} ГБ. '
                 + (f'Не хватает {-self.remaining/1e9:.2f} ГБ.' if self.remaining < 0 else f'Свободно ≈ {self.remaining/1e9:.2f} ГБ.')
                 + f'\nВидео {self.video/1e9:.2f} · звук {self.audio/1e9:.2f} · субтитры {self.subtitles/1e9:.2f} · меню и запас {self.overhead/1e9:.2f} ГБ.')
        if self.remaining < 0:
            parts += '\nСборка запрещена: уберите фильм/дорожки, выберите BD50 или явно уменьшите битрейт перекодирования.'
        elif self.warning:
            parts += '\nМеста осталось мало. Фактический размер будет проверен перед сохранением.'
        return parts


def video_mode(info, requested, capacity):
    if requested != 'auto':
        return requested
    if not info.copy_report:
        return 'encode'
    # Do not silently transcode compatible video to squeeze a collection onto a disc.
    return info.copy_report.recommendation(info, capacity)[0]


def estimate(items, profile, mode='auto', *, collection=False, bitrate=12_000_000, menu=True, rates=None,
             enhancements=None):
    from core import CAPACITIES, bitrate_plan
    cap = CAPACITIES[profile]
    video = audio = subs = 0
    rows = []
    pages = 1
    for info, chapters in items:
        from video_treatment import enhancement_for
        requested = 'encode' if enhancement_for({'video_enhancements': enhancements or {}}, info.path) else mode
        selected = video_mode(info, requested, 10**18 if collection else cap)
        if selected == 'encode':
            rate = (rates or {}).get(str(Path(info.path).resolve()))
            if rate is None:
                rate = bitrate if collection else bitrate_plan(info.duration, profile, len(selected_tracks(info)))
            if type(rate) is not int or not 5_000_000 <= rate <= 28_000_000:
                raise ValueError('Битрейт видео должен быть от 5 до 28 Мбит/с.')
            v = math.ceil(info.duration * rate / 8)
        else:
            v = info.copy_report.video_bytes if info.copy_report and info.copy_report.video_bytes else info.size
        a = 0
        for track in selected_tracks(info):
            fmt = copy_format(track)
            policy = info.audio_modes.get(str(track.get('index')), 'auto')
            checked = info.copy_report.audio_results.get(track.get('index'), {}) if info.copy_report else {}
            if fmt and policy != 'ac3' and checked.get('ok'):
                size = checked.get('bytes') or math.ceil(info.duration * fmt[2] / 8)
                if track.get('codec_name') == 'truehd':
                    size += math.ceil(info.duration * 640000 / 8)
            else:
                size = math.ceil(info.duration * 640000 / 8)
                if fmt and policy != 'ac3' and not checked:
                    size = max(size, math.ceil(info.duration * fmt[2] / 8))
            a += size
        s = 0
        for track in info.selected_subtitles:
            # Text becomes bitmap PGS in tsMuxer. Its compressed source size is not its disc size.
            source_size = Path(track['path']).stat().st_size if track.get('source') == 'external' else 0
            s += max(source_size, math.ceil(info.duration * 300000 / 8))
        pages += 1 + (len(chapters)+4)//5 + (len(selected_tracks(info))+4)//5 + (len(info.selected_subtitles)+5)//5
        video += v
        audio += a
        subs += s
        rows.append((info.name, v+a+s, selected))
    overhead = math.ceil((video+audio+subs)*.05) + (pages*8_000_000 if menu else 0) + (32_000_000 if items else 0)
    return Budget(cap, video, audio, subs, overhead, tuple(rows))


def require_fit(budget):
    if budget.remaining < 0:
        raise ValueError(budget.explanation() + '\n\n' + '\n'.join(f'{name}: ≈ {size/1e9:.2f} ГБ потоков' for name, size, _ in budget.titles))
