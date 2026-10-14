"""Display names are independent of source paths and playlist positions."""
from pathlib import Path
import unicodedata


def validate_title(value):
    if not isinstance(value, str) or len(value) > 80 or any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('Название фильма: не более 80 символов, без переносов строк и управляющих символов.')
    return value.strip()


def normalize_names(raw, paths):
    if not isinstance(raw, dict):
        raise ValueError('Неверный раздел названий фильмов.')
    known = {str(Path(p).resolve()) for p in paths}
    result = {}
    for path, value in raw.items():
        if not isinstance(path, str):
            raise ValueError('Неверный путь в названиях фильмов.')
        title = validate_title(value)
        path = str(Path(path).resolve())
        if path in known and title:
            result[path] = title
    return result


def movie_title(path, names):
    return names.get(str(Path(path).resolve())) or Path(path).stem[:80]
