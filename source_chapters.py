"""Normalize ffprobe chapter starts to the millisecond authoring timeline."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from fractions import Fraction


def import_chapters(raw, duration):
    """Return (times, notices). Bad metadata must not prevent opening a film."""
    if raw is None or raw == []:
        return (), ()
    if not isinstance(raw, list):
        return (), ('Не удалось прочитать главы из файла: неверный список.',)
    times = set()
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
            times.add(seconds)
        except (KeyError, ValueError, TypeError, InvalidOperation, ZeroDivisionError, OverflowError):
            skipped += 1
    notices = [f'Пропущены некорректные отметки глав: {skipped}.'] if skipped else []
    if not times:
        return (), tuple(notices)
    times.add(0.0)
    if len(times) > 999:
        return (), tuple(notices + ['В файле более 999 глав. Автоимпорт отменён; задайте главы по интервалу.'])
    return tuple(sorted(times)), tuple(notices)
