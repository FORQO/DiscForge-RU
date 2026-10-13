"""Static Full HD BD-J menu assets. The disc and preview share one renderer.

Binary field layout is derived from Sun's BSD-licensed HD Cookbook tools;
see THIRD_PARTY_NOTICES.md. Hardware interoperability is experimental.
"""
from __future__ import annotations

import io
import base64
import re
import math
import shutil
import struct
import zipfile
from dataclasses import dataclass, field, asdict
from functools import lru_cache
from pathlib import Path
import copy
from menu_layout import TEXT_RECTS, element_rect, text_style, default_button_rect

THEMES = {'midnight': ('#101721', '#202d3c', '#bacde1'),
          'graphite': ('#222427', '#3b3e43', '#d6d8dd'),
          'cinema': ('#181315', '#38272b', '#edbc83')}
BUTTONS = ((150, 400, 760, 82), (150, 506, 760, 82),
           (150, 612, 760, 82), (150, 718, 760, 82),
           (150, 824, 760, 82), (1010, 400, 760, 82),
           (1010, 506, 760, 82), (1010, 612, 760, 82))
_font_loaded = False

TEXT = {
    'ru': {'play': 'Смотреть фильм', 'chapters': 'Выбор главы',
           'previous': '←  Предыдущие главы', 'next': 'Следующие главы  →',
           'home': '←  Главное меню', 'subtitle': 'Избранное в вашей коллекции',
           'chapter': 'Глава', 'of': 'из', 'brand': 'B L U - R A Y',
           'hint': 'Вверх / Вниз — выбор     OK — открыть     Back — главное меню'},
    'en': {'play': 'Play movie', 'chapters': 'Scene selection',
           'previous': '←  Previous scenes', 'next': 'Next scenes  →',
           'home': '←  Main menu', 'subtitle': 'A film from your collection',
           'chapter': 'Chapter', 'of': 'of', 'brand': 'B L U - R A Y',
           'hint': 'Up / Down  Select     OK  Open     Pop-up Menu / Back  Main menu'},
}


@lru_cache(maxsize=2)
def background_image(data: str):
    from PySide6.QtGui import QImage
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise ValueError('Повреждено изображение фона.') from exc
    image = QImage.fromData(raw, 'PNG')
    if image.isNull() or image.width() > 1920 or image.height() > 1080:
        raise ValueError('Фон должен быть PNG размером не более 1920×1080.')
    return image


def import_background(path: str) -> str:
    """Normalize once and embed the image so saved projects are portable."""
    from PySide6.QtCore import Qt, QBuffer, QIODevice, QSize
    from PySide6.QtGui import QImageReader
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 100_000_000:
        raise ValueError('Не удалось прочитать изображение или оно слишком большое.')
    reader.setScaledSize(size.scaled(QSize(1920, 1080), Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise ValueError('Не удалось открыть фон: ' + reader.errorString())
    image = image.scaled(1920, 1080, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, 'PNG'):
        raise ValueError('Не удалось сохранить фон.')
    return base64.b64encode(bytes(buffer.data())).decode('ascii')


def initialize_fonts():
    global _font_loaded
    if not _font_loaded:
        from PySide6.QtGui import QFontDatabase
        if QFontDatabase.addApplicationFont(str(Path(__file__).parent / 'assets/fonts/NotoSans-Regular.ttf')) < 0:
            raise RuntimeError('Не удалось загрузить шрифт меню Noto Sans.')
        QFontDatabase.addApplicationFont(str(Path(__file__).parent / 'assets/fonts/NotoSansSymbols2-Regular.ttf'))
        _font_loaded = True


@dataclass(frozen=True)
class MenuDesign:
    title: str
    theme: str = 'midnight'
    language: str = 'ru'
    background: str = ''
    background_fit: str = 'cover'
    dim: int = 40
    labels: dict[str, str] = field(default_factory=dict)
    hidden_buttons: list[str] = field(default_factory=list)
    button_styles: dict[str, dict] = field(default_factory=dict)
    layout: dict[str, list[int]] = field(default_factory=dict)
    text_styles: dict[str, dict] = field(default_factory=dict)
    custom_buttons: list[dict] = field(default_factory=list)
    hidden_text: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not isinstance(self.hidden_text, list) or any(key not in TEXT_RECTS for key in self.hidden_text):
            raise ValueError('Некорректный список удалённых текстов.')
        object.__setattr__(self, 'hidden_text', list(dict.fromkeys(self.hidden_text)))
        if not isinstance(self.title, str) or not 1 <= len(self.title.strip()) <= 80:
            raise ValueError('Название меню должно содержать от 1 до 80 символов.')
        if any(ord(c) < 32 for c in self.title) or not isinstance(self.theme, str) or self.theme not in THEMES:
            raise ValueError('Некорректное оформление меню.')
        if not isinstance(self.language, str) or self.language not in TEXT:
            raise ValueError('Неизвестный язык меню.')
        if self.background_fit not in ('cover', 'contain') or type(self.dim) is not int or not 0 <= self.dim <= 80:
            raise ValueError('Некорректные настройки фона.')
        if not isinstance(self.background, str) or len(self.background) > 16_000_000:
            raise ValueError('Некорректное изображение фона.')
        if not isinstance(self.labels, dict) or len(self.labels) > 2100:
            raise ValueError('Некорректные подписи кнопок.')
        for key, value in self.labels.items():
            if (not isinstance(key, str) or not re.fullmatch(r'(ru|en):(play|chapters|previous|next|home|subtitle|brand|hint|chapter:\d{1,3}|custom:[a-f0-9]{12})', key)
                    or not isinstance(value, str) or not 1 <= len(value.strip()) <= 120
                    or any(ord(c) < 32 for c in value)):
                raise ValueError('Подпись должна содержать от 1 до 80 символов без переносов строк.')
        object.__setattr__(self, 'labels', dict(self.labels))
        def valid_id(key):
            return isinstance(key, str) and re.fullmatch(r'(play|chapters|chapter:\d{1,3}|(previous|next|home):\d{1,3}|custom:[a-f0-9]{12})', key)
        if (not isinstance(self.hidden_buttons, list) or len(self.hidden_buttons) > 2000
                or any(not valid_id(key) for key in self.hidden_buttons)):
            raise ValueError('Некорректный список удалённых кнопок.')
        if not isinstance(self.button_styles, dict) or len(self.button_styles) > 2000:
            raise ValueError('Некорректное оформление кнопок.')
        for key, style in self.button_styles.items():
            if not valid_id(key) or not isinstance(style, dict):
                raise ValueError('Некорректное оформление кнопки.')
            for name, value in style.items():
                if name in ('fill', 'border', 'text', 'highlight'):
                    valid = isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value)
                elif name == 'shape':
                    valid = value in ('rounded', 'rectangle', 'pill')
                elif name in ('width', 'font_size', 'opacity'):
                    low, high = {'width': (0, 10), 'font_size': (12, 160), 'opacity': (0, 100)}[name]
                    valid = type(value) is int and low <= value <= high
                elif name == 'font_family':
                    valid = isinstance(value, str) and 1 <= len(value) <= 100 and not any(ord(c) < 32 for c in value)
                elif name in ('bold', 'italic'):
                    valid = type(value) is bool
                else:
                    valid = False
                if not valid:
                    raise ValueError('Некорректный параметр оформления кнопки: ' + name)
        object.__setattr__(self, 'hidden_buttons', list(dict.fromkeys(self.hidden_buttons)))
        object.__setattr__(self, 'button_styles', {k: dict(v) for k, v in self.button_styles.items()})
        if not isinstance(self.layout, dict) or len(self.layout) > 2500:
            raise ValueError('Некорректная компоновка меню.')
        for key, rect in self.layout.items():
            if (not (valid_id(key) or key in ['text:'+k for k in TEXT_RECTS])
                    or not isinstance(rect, (tuple, list)) or len(rect) != 4
                    or any(type(n) is not int for n in rect)):
                raise ValueError('Некорректная область элемента.')
            x, y, w, h = rect
            if min(x, y) < 0 or w < 32 or h < 32 or x+w > 1920 or y+h > 1080:
                raise ValueError('Элемент должен находиться внутри меню 1920×1080.')
        if not isinstance(self.text_styles, dict) or len(self.text_styles) > len(TEXT_RECTS):
            raise ValueError('Некорректные стили текста.')
        for key, style in self.text_styles.items():
            if key not in TEXT_RECTS or not isinstance(style, dict):
                raise ValueError('Неизвестный текстовый элемент.')
            for name, value in style.items():
                valid = (name == 'text' and isinstance(value, str) and re.fullmatch(r'#[a-fA-F0-9]{6}', value)
                         or name == 'font_size' and type(value) is int and 12 <= value <= 160
                         or name == 'font_family' and isinstance(value, str) and 1 <= len(value) <= 100 and not any(ord(c) < 32 for c in value)
                         or name in ('bold', 'italic') and type(value) is bool)
                if not valid:
                    raise ValueError('Некорректный стиль текста.')
        if not isinstance(self.custom_buttons, list) or len(self.custom_buttons) > 500:
            raise ValueError('Слишком много дополнительных кнопок.')
        ids = set()
        for button in self.custom_buttons:
            if (not isinstance(button, dict) or set(button) != {'id', 'page', 'action'}
                    or not isinstance(button['id'], str) or not re.fullmatch(r'custom:[a-f0-9]{12}', button['id'])
                    or button['id'] in ids or type(button['page']) is not int or not 0 <= button['page'] <= 200
                    or not isinstance(button['action'], str) or not re.fullmatch(r'(play|page):\d{1,3}', button['action'])):
                raise ValueError('Некорректная дополнительная кнопка.')
            ids.add(button['id'])
        for key in ('layout', 'text_styles', 'custom_buttons'):
            object.__setattr__(self, key, copy.deepcopy(getattr(self, key)))

    def text(self, key):
        return self.labels.get(self.language + ':' + key, TEXT[self.language].get(key, ''))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, title, data):
        return cls(title, **{key: data[key] for key in
                   ('theme', 'language', 'background', 'background_fit', 'dim', 'labels',
                    'hidden_buttons', 'button_styles', 'layout', 'text_styles', 'custom_buttons', 'hidden_text') if key in data})


def button_id(page, action):
    if action.startswith('custom:'):
        return action
    kind, value = action.split(':')
    if kind == 'play':
        return 'play' if page == 0 else 'chapter:' + value
    if page == 0:
        return 'chapters'
    return ('home' if value == '0' else 'previous' if int(value) < page else 'next') + ':' + str(page)


def button_label_key(key):
    return key if key.startswith(('chapter:', 'custom:')) else key.split(':')[0]


def resolve_action(design, action):
    if action.startswith('custom:'):
        return next(button['action'] for button in design.custom_buttons if button['id'] == action)
    return action


def button_style(design, key):
    bg, _, accent = THEMES[design.theme]
    style = dict(shape='rounded', fill=bg, border='#45494f' if design.theme == 'graphite' else '#354252', text='#f5f6f8',
                 highlight=accent, width=1, font_size=28, opacity=75, font_family='Noto Sans', bold=False, italic=False)
    style.update(design.button_styles.get(key, {}))
    return style


def button_radius(style, height=82):
    return {'rectangle': 0, 'rounded': min(12, height//2), 'pill': height//2}[style['shape']]


def validate_menu_navigation(chapters, design):
    graph = pages(chapters, design)
    for button in design.custom_buttons:
        if button['page'] >= len(graph) and button['id'] not in design.hidden_buttons:
            raise ValueError('Страница дополнительной кнопки больше не существует.')
    for entries in graph:
        for _, action in entries:
            kind, value = resolve_action(design, action).split(':')
            if int(value) >= (len(chapters) if kind == 'play' else len(graph)):
                raise ValueError('Кнопка ссылается на отсутствующую главу или страницу. Исправьте её действие.')
    pending, seen = [0], set()
    while pending:
        page = pending.pop()
        if page in seen:
            continue
        seen.add(page)
        for _, action in graph[page]:
            kind, value = resolve_action(design, action).split(':')
            if kind == 'play':
                return
            pending.append(int(value))
    raise ValueError('В меню нет доступной кнопки запуска фильма. Восстановите кнопки перед созданием ISO.')


def pages(chapters: list[float], design: MenuDesign | None = None) -> list[list[tuple[str, str]]]:
    """Make every scene reachable; allow up to sixteen visible buttons per page.

    Five chapter buttons leave room for Previous, Next and Home on middle
    pages. Custom buttons use the same exported geometry as the renderer.
    """
    if not chapters or len(chapters) > 999:
        raise ValueError('Для меню требуется от 1 до 999 глав.')
    previous = None
    for timestamp in chapters:
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError('Отметки глав должны быть конечными числами.')
        try:
            valid = math.isfinite(timestamp) and timestamp >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError('Отметки глав должны быть конечными неотрицательными числами.')
        if previous is None and timestamp != 0:
            raise ValueError('Первая глава должна начинаться с 00:00:00.')
        if previous is not None and timestamp <= previous:
            raise ValueError('Отметки глав должны строго возрастать.')
        previous = timestamp
    design = design or MenuDesign('Blu-ray')
    result = [[(design.text('play'), 'play:0')]]
    if len(chapters) > 1:
        result[0].append((design.text('chapters'), 'page:1'))
        per_page = 5
        count = (len(chapters) + per_page - 1) // per_page
        for p in range(count):
            entries = []
            for i in range(p * per_page, min((p + 1) * per_page, len(chapters))):
                sec = int(chapters[i])
                h, rem = divmod(sec, 3600)
                m, s = divmod(rem, 60)
                name = design.text(f'chapter:{i}') or f'{TEXT[design.language]["chapter"]} {i + 1}'
                entries.append((f'{i + 1:02}   {name}   ·   {h:02}:{m:02}:{s:02}', f'play:{i}'))
            if p:
                entries.append((design.text('previous'), f'page:{p}'))
            if p + 1 < count:
                entries.append((design.text('next'), f'page:{p + 2}'))
            entries.append((design.text('home'), 'page:0'))
            result.append(entries)
    for p, entries in enumerate(result):
        entries.extend((design.text(button['id']) or ('Новая кнопка' if design.language == 'ru' else 'New button'), button['id'])
                       for button in design.custom_buttons if button['page'] == p)
        result[p] = [entry for entry in entries if button_id(p, entry[1]) not in design.hidden_buttons]
        if len(result[p]) > 16:
            raise ValueError('На странице поддерживается до 16 кнопок. Удалите лишние кнопки.')
    return result

def validate_menu_chapters(playlist: Path, chapters: list[float]) -> None:
    """Bounded MPLS EntryMark binding check before writing the menu JAR.

    The general BDMV preflight still validates full PlayItem timing and clip
    references; this specific gate prevents invalid chapter buttons even when
    install_menu() is called on its own.
    """
    from mpls_preflight import MAX_PLAYLIST_BYTES
    try:
        with playlist.open('rb') as handle:
            data = handle.read(MAX_PLAYLIST_BYTES + 1)
    except OSError as exc:
        raise ValueError('Не удалось прочитать плейлист для меню.') from exc
    if not 40 <= len(data) <= MAX_PLAYLIST_BYTES or data[:8] not in (b'MPLS0100', b'MPLS0200'):
        raise ValueError('Некорректный плейлист для меню.')
    play_start = int.from_bytes(data[8:12], 'big')
    marks_start = int.from_bytes(data[12:16], 'big')
    if play_start < 40 or play_start % 2 or play_start + 10 > len(data):
        raise ValueError('Некорректный раздел PlayList в меню.')
    play_size = int.from_bytes(data[play_start:play_start + 4], 'big')
    play_end = play_start + 4 + play_size
    if play_size < 6 or play_end > len(data):
        raise ValueError('Некорректные границы PlayList в меню.')
    play_items = int.from_bytes(data[play_start + 6:play_start + 8], 'big')
    if play_items == 0:
        raise ValueError('В плейлисте нет PlayItem для меню.')
    if marks_start < play_end or marks_start % 2 or marks_start + 6 > len(data):
        raise ValueError('В плейлисте отсутствует корректный раздел глав.')
    marks_size = int.from_bytes(data[marks_start:marks_start + 4], 'big')
    marks_end = marks_start + 4 + marks_size
    if marks_size < 2 or marks_end > len(data):
        raise ValueError('Некорректные границы раздела глав.')
    count = int.from_bytes(data[marks_start + 4:marks_start + 6], 'big')
    if count != len(chapters) or 2 + 14 * count > marks_size:
        raise ValueError('Количество глав меню не совпадает с плейлистом.')
    for index in range(count):
        record = marks_start + 6 + 14 * index
        if data[record + 1] != 1:
            raise ValueError('Меню поддерживает только EntryMark главы.')
        ref_item = int.from_bytes(data[record + 2:record + 4], 'big')
        if ref_item >= play_items:
            raise ValueError('Глава меню ссылается на отсутствующий PlayItem.')
    # Verify the actual 45-kHz chapter positions, not only EntryMark counts.
    from chapter_timeline import validate_chapter_timeline
    validate_chapter_timeline(data, chapters)


def page_elements(design, chapters, page):
    graph = pages(chapters, design)
    result = []
    for key in ('brand', 'title', 'subtitle' if page == 0 else 'chapter_heading', 'hint'):
        if key in design.hidden_text:
            continue
        edit_key = 'chapters' if key == 'chapter_heading' else key
        text = design.title if key == 'title' else design.text(edit_key)
        shown = f'{text}  /  {page} {TEXT[design.language]["of"]} {len(graph)-1}' if key == 'chapter_heading' else text
        result.append(dict(id='text:'+key, key=edit_key, rect=element_rect(design, 'text:'+key, TEXT_RECTS[key]),
                           text=text, shown=shown, style=text_style(design, key), button='', index=-1))
    for i, (shown, action) in enumerate(graph[page]):
        bid = button_id(page, action)
        key = button_label_key(bid)
        text = design.text(key)
        if not text:
            text = f'{TEXT[design.language]["chapter"]} {int(action.split(":")[1])+1}' if key.startswith('chapter:') else shown
        result.append(dict(id=bid, key=key, rect=element_rect(design, bid, default_button_rect(i)),
                           text=text, shown=shown, style=button_style(design, bid), button=bid, index=i))
    return result


def render_page(design: MenuDesign, chapters: list[float], page: int = 0, focus: int = -1):
    """Return a QImage; caller owns a QGuiApplication (also for CLI authoring)."""
    from PySide6.QtCore import Qt, QRect
    from PySide6.QtGui import QImage, QPainter, QColor, QFont, QLinearGradient, QPen
    initialize_fonts()
    graph = pages(chapters, design)
    entries = graph[page]
    background = background_image(design.background) if design.background else None
    bg, secondary, accent = THEMES[design.theme]
    image = QImage(1920, 1080, QImage.Format_RGB32)
    image.fill(QColor(bg))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 1080, 1920, 0)
    gradient.setColorAt(0, QColor(bg))
    gradient.setColorAt(1, QColor(secondary))
    painter.fillRect(image.rect(), gradient)
    if background is not None:
        mode = Qt.KeepAspectRatioByExpanding if design.background_fit == 'cover' else Qt.KeepAspectRatio
        scaled = background.scaled(1920, 1080, mode, Qt.SmoothTransformation)
        painter.drawImage((1920-scaled.width())//2, (1080-scaled.height())//2, scaled)
        painter.fillRect(image.rect(), QColor(0, 0, 0, round(design.dim * 255 / 100)))
    def label(rect, text, style):
        font = QFont(style['font_family'])
        font.setPixelSize(style['font_size'])
        font.setBold(style['bold'])
        font.setItalic(style['italic'])
        painter.save()
        painter.setClipRect(rect)
        painter.setFont(font)
        painter.setPen(QColor(style['text']))
        shown = painter.fontMetrics().elidedText(text, Qt.ElideRight, rect.width())
        painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, shown)
        painter.restore()
    for element in page_elements(design, chapters, page):
        rect = QRect(*element['rect'])
        style = element['style']
        if not element['button']:
            label(rect, element['shown'], style)
            continue
        painter.setPen(QPen(QColor(style['border']), style['width']) if style['width'] else Qt.NoPen)
        fill = QColor(style['fill'])
        fill.setAlpha(round(style['opacity'] * 255 / 100))
        painter.setBrush(fill)
        radius = button_radius(style, rect.height())
        painter.drawRoundedRect(rect, radius, radius)
        inset = min(28, rect.width()//10)
        label(rect.adjusted(inset, 0, -inset, 0), element['shown'], style)
    # Draw focus last, just as the disc runtime does, including overlapping items.
    for element in page_elements(design, chapters, page):
        if element['button'] and element['index'] == focus:
            paint_focus(painter, element)
    painter.end()
    return image


def paint_focus(painter, element):
    """One antialiased stroke; concentric AWT arcs leave gaps at the corners."""
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QColor, QPen, QPainter
    rect = QRectF(*element['rect']).adjusted(-1, -1, 1, 1)
    radius = button_radius(element['style'], element['rect'][3])
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(element['style']['highlight']), 2))
    painter.drawRoundedRect(rect, radius + 1 if radius else 0, radius + 1 if radius else 0)
    painter.restore()


def focus_patch(base, element):
    """Opaque, bounded patch preserves identical Qt rendering on BD-J players."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter
    bounds = QRect(*element['rect']).adjusted(-6, -6, 6, 6).intersected(base.rect())
    patch = base.copy(bounds)
    painter = QPainter(patch)
    painter.translate(-bounds.x(), -bounds.y())
    paint_focus(painter, element)
    painter.end()
    return patch, bounds.x(), bounds.y()


def _section(data: bytes) -> bytes:
    return struct.pack('>I', len(data)) + data


def _text(value: str) -> bytes:
    data = value.encode('ascii')
    return bytes([len(data)]) + data + (b'\0' if len(data) % 2 == 0 else b'')


def bdjo_bytes() -> bytes:
    terminal = _section(b'*****\x10' + bytes(4))  # 1920x1080, no menu masks
    cache = _section(b'\x01\0\x01' + b'00000*.*' + bytes(3))
    playlists = _section(b'\0\x10\0\0')  # access all; no automatic playback
    # Profile 1 v1.0, disc/title-bound application; visible, no icon.
    descriptor = (bytes(4) + b'\x10\0\0\x01\x01\0\0\0\x03\xf0\0\0'
                  + _text('') + bytes(2) + _text('00000') + _text('')
                  + _text('DiscForgeXlet') + _text(''))
    app = _section(b'\x01\0\x01\x10' + struct.pack('>IH', 0xffff0001, 0x0001)
                   + b'\0\0' + _section(descriptor))
    sections = [terminal, cache, playlists, app, struct.pack('>I', 0x7c00), b'\0\x01.\0']
    offsets, pos = [], 48
    for section in sections:
        offsets.append(pos)
        pos += len(section)
    return b'BDJO0200' + struct.pack('>6I', *offsets) + bytes(16) + b''.join(sections)


def index_bytes() -> bytes:
    # One interactive BD-J title, also used for first playback and Top Menu.
    title = b'\x80\0\0\0\xc0\0' + b'00000\0'
    app = _section(b'\0\x61' + bytes(32))  # 1080p / 23.976
    indexes = _section(title + title + b'\0\x01' + title)
    return b'INDX0200' + struct.pack('>II', 40 + len(app), 0) + bytes(24) + app + indexes


def install_menu(root: Path, design: MenuDesign, chapters: list[float]) -> None:
    """Install a real Xlet JAR + BDJO in a staging BDMV; never touches source media."""
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:
        raise RuntimeError('Для создания меню требуется QGuiApplication.')
    template = Path(__file__).parent / 'assets' / 'bdj' / 'menu.jar'
    if not template.is_file():
        raise RuntimeError('Не найден движок меню assets/bdj/menu.jar. Пересоберите приложение.')
    bdmv = root / 'BDMV'
    playlists = sorted((bdmv / 'PLAYLIST').glob('*.mpls'))
    if len(playlists) != 1 or playlists[0].stem != '00000':
        raise ValueError('Меню v1 поддерживает один фильм с плейлистом 00000.')
    # Validate chapter labels and MPLS section bounds before writing files.
    all_pages = pages(chapters, design)
    validate_menu_navigation(chapters, design)
    validate_menu_chapters(playlists[0], chapters)
    for path in ('JAR', 'BDJO', 'BACKUP/BDJO', 'BACKUP/JAR'):
        (bdmv / path).mkdir(parents=True, exist_ok=True)
    props = ['pages=' + str(len(all_pages)), 'playlist=00000']
    for p, entries in enumerate(all_pages):
        props.append(f'page.{p}.count={len(entries)}')
        for i, (_, action) in enumerate(entries):
            props.append(f'page.{p}.{i}={resolve_action(design, action)}')
            bid = button_id(p, action)
            style = button_style(design, bid)
            rect = element_rect(design, bid, default_button_rect(i))
            for name, value in zip(('x', 'y', 'w', 'h'), rect):
                props.append(f'page.{p}.{i}.{name}={value}')
            props.append(f'page.{p}.{i}.radius={button_radius(style, rect[3])}')
            props.append(f'page.{p}.{i}.highlight={style["highlight"][1:]}')
    with zipfile.ZipFile(template) as source, zipfile.ZipFile(bdmv / 'JAR/00000.jar', 'w', zipfile.ZIP_DEFLATED) as jar:
        for name in source.namelist():
            jar.writestr(name, source.read(name))
        for p in range(len(all_pages)):
            base = render_page(design, chapters, p)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if not base.save(buffer, 'PNG'):
                raise RuntimeError('Не удалось отрисовать меню.')
            jar.writestr(f'page{p}.png', bytes(buffer.data()))
            for element in page_elements(design, chapters, p):
                if not element['button']:
                    continue
                patch, x, y = focus_patch(base, element)
                name = f'focus{p}_{element["index"]}.png'
                buffer = QBuffer()
                buffer.open(QIODevice.WriteOnly)
                if not patch.save(buffer, 'PNG'):
                    raise RuntimeError('Не удалось отрисовать выделение кнопки.')
                jar.writestr(name, bytes(buffer.data()))
                key = f'page.{p}.{element["index"]}'
                props.extend((f'{key}.focus={name}', f'{key}.focusX={x}', f'{key}.focusY={y}'))
        jar.writestr('menu.properties', '\n'.join(props).encode('ascii'))
        import json
        jar.writestr('discforge-design.json', json.dumps(
            {'version': 1, 'design': design.to_dict(), 'chapters': chapters}, ensure_ascii=False).encode('utf-8'))
    (bdmv / 'BDJO/00000.bdjo').write_bytes(bdjo_bytes())
    (bdmv / 'index.bdmv').write_bytes(index_bytes())
    for name in ('BDJO/00000.bdjo', 'JAR/00000.jar', 'index.bdmv'):
        shutil.copyfile(bdmv / name, bdmv / 'BACKUP' / name)
    (root / 'CERTIFICATE/BACKUP').mkdir(parents=True, exist_ok=True)
    # Disc ID is content-derived; no network/signature/permission request files.
    import hashlib
    identity = hashlib.sha256((bdmv / 'JAR/00000.jar').read_bytes()).digest()[:16]
    disc_id = b'BDID0200' + bytes(32) + struct.pack('>I', 0xffff0001) + identity
    for suffix in ('id.bdmv', 'BACKUP/id.bdmv'):
        (root / 'CERTIFICATE' / suffix).write_bytes(disc_id)
