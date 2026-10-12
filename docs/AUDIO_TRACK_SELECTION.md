# Исходные аудиодорожки: подготовка CLI

Экспериментальный модуль `tools/audio_tracks.py` анализирует результат ffprobe
и формирует нумерацию дорожек **0:a:N** независимо от их абсолютных
номеров в контейнере. Он сохраняет число каналов и язык дорожки, отклоняет
отрицательные/дробные индексы и некорректные каналы. Модуль не вызывает
FFmpeg и не изменяет выходные файлы.

Пример использования из Python:

```python
from tools.audio_tracks import parse_audio_tracks, select_audio_track

tracks = parse_audio_tracks(ffprobe_json)
selected = select_audio_track(tracks, 1)  # вторая дорожка
assert selected.ffmpeg_map == "0:a:1"
```

**Статус:** это изолированная основа для последующего подключения к
`core.py`, CLI и GUI. На данном этапе приложение **по-прежнему берёт
первую аудиодорожку**; несколько дорожек в BDMV, выбор субтитров и
проверка на физическом Blu-ray-плеере не реализованы.

Проверка: `python -m unittest tests.test_audio_tracks -v`.
