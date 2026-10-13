"""Menu-only update: read-only mount, overlay metadata, one ISO copy, no codecs."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from disc_menu import MenuDesign, focus_patch, install_menu, validate_menu_chapters
from tools.iso_udf_preflight import inspect, CAPACITIES
from windows_iso import create_iso, pending_image


def powershell(code, iso):
    env = dict(os.environ, DISCFORGE_ISO=str(Path(iso).resolve()))
    exe = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command',
                             "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; " + code],
                            env=env, capture_output=True, timeout=45,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        message = result.stderr.decode('utf-8', errors='replace')[-1600:]
        if 'ISO has no readable mounted volume' in message:
            raise RuntimeError('Windows не смогла прочитать подключённый ISO. Для некоторых образов ранних версий '
                               'нужно сначала скопировать файл через Проводник в обычную папку и выбрать эту копию. '
                               'Исходный файл и его права доступа не изменены.')
        raise RuntimeError(message)
    return result.stdout.decode('utf-8-sig').strip()


@contextmanager
def mounted_iso(iso):
    if sys.platform != 'win32':
        raise RuntimeError('Обновление ISO пока доступно только в Windows.')
    data = json.loads(powershell(r'''
$disk = Get-DiskImage -ImagePath $env:DISCFORGE_ISO
$owned = -not $disk.Attached
try {
    if ($owned) { $disk = Mount-DiskImage -ImagePath $env:DISCFORGE_ISO -StorageType ISO -Access ReadOnly -PassThru }
    $volume = $disk | Get-Volume
    $root = $volume.Path
    if ($volume.DriveLetter) { $root = [string]$volume.DriveLetter + ':\' }
    if (-not $root) { throw 'ISO has no readable mounted volume' }
    @{root=$root; owned=$owned} | ConvertTo-Json -Compress
} catch {
    if ($owned) { Dismount-DiskImage -ImagePath $env:DISCFORGE_ISO -ErrorAction SilentlyContinue | Out-Null }
    throw
}
''', iso))
    try:
        yield Path(data['root'])
    finally:
        if data['owned']:
            powershell('Dismount-DiskImage -ImagePath $env:DISCFORGE_ISO | Out-Null', iso)


def chapter_positions(playlist):
    with playlist.open('rb') as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if not 40 <= len(data) <= 16 * 1024 * 1024 or data[:8] not in (b'MPLS0100', b'MPLS0200'):
        raise ValueError('Некорректный плейлист.')
    def u16(at): return int.from_bytes(data[at:at+2], 'big')
    def u32(at): return int.from_bytes(data[at:at+4], 'big')
    play, marks = u32(8), u32(12)
    if play < 40 or play+10 > len(data) or marks < play+10 or marks+6 > len(data):
        raise ValueError('Некорректные разделы плейлиста.')
    intervals, elapsed, cursor = [], 0, play+10
    for _ in range(u16(play+6)):
        end = cursor+2+u16(cursor)
        if end > len(data) or end <= cursor+32:
            raise ValueError('Некорректный PlayItem.')
        start, stop = u32(cursor+14), u32(cursor+18)
        intervals.append((start, elapsed))
        elapsed += stop-start
        cursor = end
    count = u16(marks+4)
    if not 1 <= count <= 999 or marks+6+14*count > len(data):
        raise ValueError('Некорректный список глав.')
    chapters = []
    for i in range(count):
        at = marks+6+14*i
        ref = u16(at+2)
        if ref >= len(intervals):
            raise ValueError('Некорректная ссылка главы.')
        start, elapsed = intervals[ref]
        chapters.append((elapsed+u32(at+4)-start)/45000)
    if abs(chapters[0]) <= 2:
        chapters[0] = 0.0
    validate_menu_chapters(playlist, chapters)
    return chapters


def open_menu(root):
    playlists = list((root / 'BDMV/PLAYLIST').glob('*.mpls'))
    if len(playlists) != 1 or playlists[0].name != '00000.mpls':
        raise ValueError('Поддерживается ISO DiscForge с одним фильмом и плейлистом 00000.')
    chapters = chapter_positions(playlists[0])
    path = root / 'BDMV/JAR/00000.jar'
    if not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('В ISO не найдено поддерживаемое меню DiscForge.')
    with zipfile.ZipFile(path) as jar:
        names = jar.namelist()
        if (len(names) != len(set(names)) or 'DiscForgeXlet.class' not in names or 'menu.properties' not in names
                or sum(i.file_size for i in jar.infolist()) > 512 * 1024 * 1024
                or any(i.file_size > 32 * 1024 * 1024 for i in jar.infolist())):
            raise ValueError('Неподдерживаемый или слишком большой архив меню.')
        props = dict(line.split('=', 1) for line in jar.read('menu.properties').decode('ascii').splitlines() if '=' in line)
        if props.get('playlist') != '00000':
            raise ValueError('Неподдерживаемый плейлист меню.')
        count = int(props['pages'])
        if not 1 <= count <= 256:
            raise ValueError('Некорректное количество страниц.')
        from disc_audio import playlist_tracks
        tracks = playlist_tracks(playlists[0])
        audio_count = int(props.get('audio.count', '0'))
        if audio_count and (audio_count != len(tracks) or not 1 <= int(props.get('audio.page', '0')) < count):
            raise ValueError('Аудиодорожки меню не совпадают с плейлистом.')
        for page in range(count):
            n = int(props[f'page.{page}.count'])
            if not 0 <= n <= 16:
                raise ValueError('Некорректное количество кнопок.')
            for i in range(n):
                action = props[f'page.{page}.{i}']
                match = re.fullmatch(r'(page|play|audio):(\d+)', action)
                limit = count if match and match[1] == 'page' else len(chapters)
                if match and match[1] == 'audio':
                    limit = len(tracks)+1
                    if int(match[2]) == 0:
                        raise ValueError('Некорректная аудиодорожка.')
                if not match or int(match[2]) >= limit:
                    raise ValueError('Некорректное действие кнопки.')
        design = None
        if 'discforge-design.json' in names:
            document = json.loads(jar.read('discforge-design.json'))
            if document.get('version') != 1:
                raise ValueError('Неподдерживаемая версия настроек меню.')
            raw = document['design']
            design = MenuDesign.from_dict(raw['title'], raw)
            from disc_audio import bind_tracks
            design = bind_tracks(design, playlists[0])
            if design.background:
                from disc_menu import background_image
                background_image(design.background)
    return chapters, props, design


def repair_menu(root, overlay, props, cancel):
    """Preserve legacy raster pages and actions; replace Xlet and focus patches."""
    from PySide6.QtCore import QBuffer, QIODevice, QByteArray
    from PySide6.QtGui import QImageReader
    target = overlay / 'BDMV/JAR/00000.jar'
    target.parent.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).parent / 'assets/bdj/menu.jar'
    with zipfile.ZipFile(root / 'BDMV/JAR/00000.jar') as source, zipfile.ZipFile(template) as engine, \
         zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as output:
        for name in source.namelist():
            if name in engine.namelist() or name.endswith('.class') or re.fullmatch(r'focus\d+_\d+\.png', name) or name == 'menu.properties':
                continue
            output.writestr(name, source.read(name))
        for name in engine.namelist():
            output.writestr(name, engine.read(name))
        for page in range(int(props['pages'])):
            if cancel.is_set():
                raise RuntimeError('Операция отменена.')
            buffer = QBuffer()
            buffer.setData(QByteArray(source.read(f'page{page}.png')))
            buffer.open(QIODevice.ReadOnly)
            reader = QImageReader(buffer, b'PNG')
            if reader.size().width() != 1920 or reader.size().height() != 1080:
                raise ValueError('Неподдерживаемый размер страницы меню.')
            base = reader.read()
            if base.isNull():
                raise ValueError('Не удалось прочитать страницу меню.')
            for i in range(int(props[f'page.{page}.count'])):
                key = f'page.{page}.{i}'
                x,y,w,h = [int(props[key+'.'+field]) for field in ('x','y','w','h')]
                radius = int(props.get(key+'.radius', '12'))
                color = '#'+props.get(key+'.highlight', '73d8ed')
                if min(x,y) < 0 or min(w,h) < 32 or x+w > 1920 or y+h > 1080 or not 0 <= radius <= h//2 or not re.fullmatch('#[0-9a-fA-F]{6}', color):
                    raise ValueError('Некорректная геометрия или цвет кнопки.')
                element = dict(rect=[x,y,w,h], style=dict(shape='pill' if radius > 12 else 'rounded' if radius else 'rectangle', highlight=color))
                patch,x,y = focus_patch(base, element)
                buffer = QBuffer()
                buffer.open(QIODevice.WriteOnly)
                if not patch.save(buffer, 'PNG'):
                    raise ValueError('Не удалось нарисовать выделение.')
                name = f'focus{page}_{i}.png'
                output.writestr(name, bytes(buffer.data()))
                props.update({key+'.focus':name, key+'.focusX':str(x), key+'.focusY':str(y)})
        output.writestr('menu.properties', '\n'.join(k+'='+v for k,v in props.items()).encode('ascii'))
    backup = overlay / 'BDMV/BACKUP/JAR/00000.jar'
    backup.parent.mkdir(parents=True)
    shutil.copyfile(target, backup)
    identity = hashlib.sha256(target.read_bytes()).digest()[:16]
    for name in ('id.bdmv', 'BACKUP/id.bdmv'):
        path = overlay / 'CERTIFICATE' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'BDID0200'+bytes(32)+struct.pack('>I',0xffff0001)+identity)


def update_iso(source, destination, design, cancel, log=lambda _: None, progress=lambda _: None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or destination.exists():
        raise ValueError('Выберите новое имя ISO. Исходный образ не перезаписывается.')
    if not source.is_file() or source.suffix.lower() != '.iso':
        raise ValueError('Выберите существующий ISO.')
    size = source.stat().st_size
    profile = 'BD25' if size <= CAPACITIES['BD25'] else 'BD50'
    report = inspect(source, profile)
    if not report.valid:
        raise ValueError('Исходный ISO не прошёл проверку UDF 2.50: '+ '; '.join(report.errors))
    if shutil.disk_usage(destination.parent).free < size + 128 * 1024 * 1024:
        raise ValueError('Недостаточно места для обновлённого ISO. Требуется размер исходного образа плюс 128 МБ.')
    if cancel.is_set():
        raise RuntimeError('Операция отменена.')
    with tempfile.TemporaryDirectory(prefix='DiscForge_menu_', dir=destination.parent) as temp, pending_image(destination) as pending:
        overlay = Path(temp) / 'overlay'
        overlay.mkdir()
        log('Подключаем исходный ISO только для чтения…')
        with mounted_iso(source) as root:
            chapters, props, _ = open_menu(root)
            log('Обновляем только меню. Видео, звук и тайминги глав сохраняются.')
            if design is None:
                repair_menu(root, overlay, props, cancel)
            else:
                playlist = overlay / 'BDMV/PLAYLIST/00000.mpls'
                playlist.parent.mkdir(parents=True)
                shutil.copyfile(root / 'BDMV/PLAYLIST/00000.mpls', playlist)
                from disc_audio import bind_tracks
                design = bind_tracks(design, playlist)
                install_menu(overlay, design, chapters)
            log('Копируем содержимое в новый ISO без перекодирования и повторного мультиплексирования…')
            create_iso(root, pending, CAPACITIES[profile], cancel, log, progress, overlay=overlay)
            if cancel.is_set():
                raise RuntimeError('Операция отменена.')
            report = inspect(pending, profile)
            if not report.valid:
                raise ValueError('Обновлённый ISO не прошёл проверку: '+ '; '.join(report.errors))
        # Windows rename does not replace an existing destination, including a race.
        if destination.exists():
            raise FileExistsError(destination)
        pending.rename(destination)
    log('Готово: '+str(destination))
    return destination
