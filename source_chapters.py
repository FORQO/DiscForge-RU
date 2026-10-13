"""Normalize ffprobe chapter starts to the millisecond authoring timeline."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction


def import_chapters(raw, duration):
    times, titles, notices = import_chapter_metadata(raw, duration)
    return times, notices


def clean_title(value):
    import unicodedata
    if not isinstance(value, str):
        return ''
    value = ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in value)
    return ' '.join(value.split())[:120].strip()


def import_chapter_metadata(raw, duration):
    """Return aligned (times, titles, notices); tolerate malformed metadata."""
    if raw is None or raw == []:
        return (), (), ()
    if not isinstance(raw, list):
        return (), (), ('Не удалось прочитать главы из файла: неверный список.',)
    times = {}
    skipped = 0
    for chapter in raw:
        try:
            if not isinstance(chapter, dict):
                raise ValueError()
            value = chapter.get('start_time')
            if value is None:
                base = Fraction(str(chapter['time_base']))
                value = Decimal(str(chapter['start'])) * Decimal(base.numerator) / Decimal(base.denominator)
            if isinstance(value, bool):
                raise ValueError()
            value = Decimal(str(value))
            if not value.is_finite() or value < 0 or value >= Decimal(str(duration)):
                raise ValueError()
            seconds = float(value.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP))
            if seconds >= duration:
                raise ValueError()
            tags = chapter.get('tags')
            title = ''
            if isinstance(tags, dict):
                title = next((clean_title(v) for k, v in tags.items() if isinstance(k, str) and k.lower() == 'title'), '')
            # Duplicate starts share one scene; keep the first non-empty title.
            if not times.get(seconds):
                times[seconds] = title
        except (KeyError, ValueError, TypeError, InvalidOperation, ZeroDivisionError, OverflowError):
            skipped += 1
    notices = [f'Пропущены некорректные отметки глав: {skipped}.'] if skipped else []
    if not times:
        return (), (), tuple(notices)
    times.setdefault(0.0, '')
    if len(times) > 999:
        return (), (), tuple(notices + ['В файле более 999 глав. Автоимпорт отменён; задайте главы по интервалу.'])
    ordered = tuple(sorted(times))
    return ordered, tuple(times[t] for t in ordered), tuple(notices)


def titles_for_chapters(info, chapters):
    titles = dict(zip(info.source_chapters, info.source_chapter_titles))
    return [titles.get(t, '') for t in chapters]


def is_chapter_label(key):
    import re
    return isinstance(key, str) and re.fullmatch(r'(ru|en):chapter:\d{1,3}', key) is not None
