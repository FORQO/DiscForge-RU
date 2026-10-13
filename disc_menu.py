"""Static Full HD BD-J menu assets. The disc and preview share one renderer.

Binary field layout is derived from Sun's BSD-licensed HD Cookbook tools;
see THIRD_PARTY_NOTICES.md. Hardware interoperability is experimental.
"""
from __future__ import annotations

import io
import math
import shutil
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

THEMES = {'midnight': ('#0c1426', '#1c3550', '#73d8ed'),
          'cinema': ('#181315', '#38272b', '#edbc83')}
BUTTONS = ((150, 400, 760, 82), (150, 506, 760, 82),
           (150, 612, 760, 82), (150, 718, 760, 82),
           (150, 824, 760, 82), (1010, 400, 760, 82),
           (1010, 506, 760, 82), (1010, 612, 760, 82))
_font_loaded = False


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

    def __post_init__(self):
        if not isinstance(self.title, str) or not 1 <= len(self.title.strip()) <= 80:
            raise ValueError('Название меню должно содержать от 1 до 80 символов.')
        if any(ord(c) < 32 for c in self.title) or self.theme not in THEMES:
            raise ValueError('Некорректное оформление меню.')


def pages(chapters: list[float]) -> list[list[tuple[str, str]]]:
    """Make every scene reachable in both directions with at most eight buttons.

    Five chapter buttons leave room for Previous, Next and Home on middle
    pages, matching the eight focus rectangles in the packaged BD-J Xlet.
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
    result = [[('Смотреть фильм', 'play:0')]]
    if len(chapters) > 1:
        result[0].append(('Выбор главы', 'page:1'))
        per_page = 5
        count = (len(chapters) + per_page - 1) // per_page
        for p in range(count):
            entries = []
            for i in range(p * per_page, min((p + 1) * per_page, len(chapters))):
                sec = int(chapters[i])
                h, rem = divmod(sec, 3600)
                m, s = divmod(rem, 60)
                entries.append((f'{i + 1:02}   Глава {i + 1}   ·   {h:02}:{m:02}:{s:02}', f'play:{i}'))
            if p:
                entries.append(('←  Предыдущие главы', f'page:{p}'))
            if p + 1 < count:
                entries.append(('Следующие главы  →', f'page:{p + 2}'))
            entries.append(('←  Главное меню', 'page:0'))
            result.append(entries)
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


def render_page(design: MenuDesign, chapters: list[float], page: int = 0, focus: int = -1):
    """Return a QImage; caller owns a QGuiApplication (also for CLI authoring)."""
    from PySide6.QtCore import Qt, QRect
    from PySide6.QtGui import QImage, QPainter, QColor, QFont, QLinearGradient, QPen
    initialize_fonts()
    entries = pages(chapters)[page]
    bg, secondary, accent = THEMES[design.theme]
    image = QImage(1920, 1080, QImage.Format_RGB32)
    image.fill(QColor(bg))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    gradient = QLinearGradient(0, 1080, 1920, 0)
    gradient.setColorAt(0, QColor(bg))
    gradient.setColorAt(1, QColor(secondary))
    painter.fillRect(image.rect(), gradient)
    painter.setPen(QPen(QColor(accent), 2))
    for radius in (230, 320, 410):
        painter.drawEllipse(1420-radius, 170-radius, radius*2, radius*2)
    def label(rect, text, size, color, bold=False):
        font = QFont('Noto Sans', size)
        font.setPixelSize(size)
        font.setBold(bold)
        painter.setFont(font)
        painter.setPen(QColor(color))
        shown = painter.fontMetrics().elidedText(text, Qt.ElideRight, rect.width())
        painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, shown)
    label(QRect(150, 120, 1000, 50), 'D I S C F O R G E   /   B L U - R A Y', 24, accent)
    label(QRect(150, 200, 1590, 115), design.title, 66, '#ffffff', True)
    subtitle = 'Фильм начинается здесь' if page == 0 else f'Выбор главы  /  {page} из {len(pages(chapters))-1}'
    label(QRect(150, 310, 1500, 55), subtitle, 28, '#b9c8d9')
    for i, (text, _) in enumerate(entries):
        rect = QRect(*BUTTONS[i])
        painter.setPen(QPen(QColor(accent if i == focus else '#54667b'), 4 if i == focus else 1))
        painter.setBrush(QColor('#233b51' if i == focus else bg))
        painter.drawRoundedRect(rect, 12, 12)
        label(rect.adjusted(28, 0, -20, 0), text, 30, '#ffffff', i == focus)
    label(QRect(150, 966, 1620, 45), 'Вверх / Вниз  Выбор     OK  Открыть     Pop-up Menu / Back  Главное меню', 24, '#b9c8d9')
    painter.end()
    return image


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
    all_pages = pages(chapters)
    validate_menu_chapters(playlists[0], chapters)
    for path in ('JAR', 'BDJO', 'BACKUP/BDJO', 'BACKUP/JAR'):
        (bdmv / path).mkdir(parents=True, exist_ok=True)
    props = ['pages=' + str(len(all_pages)), 'playlist=00000']
    for p, entries in enumerate(all_pages):
        props.append(f'page.{p}.count={len(entries)}')
        for i, (_, action) in enumerate(entries):
            props.append(f'page.{p}.{i}={action}')
    with zipfile.ZipFile(template) as source, zipfile.ZipFile(bdmv / 'JAR/00000.jar', 'w', zipfile.ZIP_DEFLATED) as jar:
        for name in source.namelist():
            jar.writestr(name, source.read(name))
        jar.writestr('menu.properties', '\n'.join(props).encode('ascii'))
        for p in range(len(all_pages)):
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if not render_page(design, chapters, p).save(buffer, 'PNG'):
                raise RuntimeError('Не удалось отрисовать меню.')
            jar.writestr(f'page{p}.png', bytes(buffer.data()))
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
