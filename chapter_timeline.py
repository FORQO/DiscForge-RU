"""Check that authored MPLS EntryMarks match BD-J chapter buttons.

An EntryMark timestamp uses the referenced PlayItem's 45-kHz clock.
An expected chapter time uses the concatenated playlist timeline.
This structural gate does not guarantee frame-accurate hardware playback.
"""
from __future__ import annotations
from studio_locale import tr

import math
from collections.abc import Sequence

CLOCK = 45_000
TOLERANCE = 2 * CLOCK  # allow GOP/keyframe snapping by tsMuxer
MAX_PLAYLIST_BYTES = 16 * 1024 * 1024


def validate_chapter_timeline(data: bytes, chapters: Sequence[float]) -> None:
    if not 40 <= len(data) <= MAX_PLAYLIST_BYTES or data[:8] not in (b'MPLS0100', b'MPLS0200'):
        raise ValueError(tr('Некорректный MPLS для проверки времени глав.'))
    if not 1 <= len(chapters) <= 999:
        raise ValueError(tr('Некорректное число глав меню.'))
    previous = -1.0
    for number in chapters:
        try:
            finite = math.isfinite(number)
        except (OverflowError, TypeError, ValueError):
            finite = False
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not finite or number < 0 or number <= previous):
            raise ValueError(tr('Отметки глав должны строго возрастать.'))
        previous = number
    if chapters[0] != 0:
        raise ValueError(tr('Первая глава должна начинаться с нуля.'))

    def u16(at: int) -> int:
        return int.from_bytes(data[at:at + 2], 'big')

    def u32(at: int) -> int:
        return int.from_bytes(data[at:at + 4], 'big')

    play, marks = u32(8), u32(12)
    if play < 40 or play % 2 or play + 10 > len(data):
        raise ValueError(tr('Некорректный PlayList в меню.'))
    play_end = play + 4 + u32(play)
    if play_end > len(data) or play_end > marks or marks % 2 or marks + 6 > len(data):
        raise ValueError(tr('Некорректные границы MPLS для времени глав.'))
    count = u16(play + 6)
    if not count:
        raise ValueError(tr('В MPLS отсутствуют PlayItem.'))
    intervals = []
    cursor = play + 10
    for _ in range(count):
        if cursor + 2 > play_end:
            raise ValueError(tr('Обрезанный PlayItem в меню.'))
        size = u16(cursor)
        end = cursor + 2 + size
        if size < 32 or end > play_end:
            raise ValueError(tr('Некорректные границы PlayItem меню.'))
        start_ticks, end_ticks = u32(cursor + 14), u32(cursor + 18)
        if start_ticks >= end_ticks:
            raise ValueError(tr('Некорректный интервал PlayItem меню.'))
        intervals.append((start_ticks, end_ticks))
        cursor = end
    elapsed = [0]
    for start, end in intervals:
        elapsed.append(elapsed[-1] + end - start)

    size = u32(marks)
    n = u16(marks + 4)
    if n != len(chapters) or size < 2 + n * 14 or marks + 4 + size > len(data):
        raise ValueError(tr('Количество или границы EntryMark не совпадают с меню.'))
    previous_position = -1
    for i, expected in enumerate(chapters):
        record = marks + 6 + 14 * i
        if data[record + 1] != 1:
            raise ValueError(tr('Меню поддерживает только EntryMark.'))
        ref = u16(record + 2)
        if ref >= len(intervals):
            raise ValueError(tr('EntryMark ссылается на отсутствующий PlayItem.'))
        tick = u32(record + 4)
        start, end = intervals[ref]
        if tick < start or tick >= end:
            raise ValueError(tr('EntryMark вне интервала PlayItem.'))
        absolute = elapsed[ref] + tick - start
        if absolute <= previous_position:
            raise ValueError(tr('EntryMark расположены не по возрастанию.'))
        if abs(absolute - round(expected * CLOCK)) > TOLERANCE:
            raise ValueError(tr('Время EntryMark не соответствует главе меню.'))
        previous_position = absolute
