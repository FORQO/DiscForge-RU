"""Selectable soft subtitles: text becomes Blu-ray PGS, never burned into video."""
from dataclasses import replace
from pathlib import Path
import re
import sys
from audio_selection import language

TEXT_CODECS = {'subrip', 'srt', 'ass', 'ssa', 'webvtt', 'mov_text', 'text'}
SUPPORTED_CODECS = TEXT_CODECS | {'hdmv_pgs_subtitle', 'pgs'}


def embedded_tracks(info):
    return [dict(source='embedded', index=t['index'], path='', codec=t.get('codec_name', ''),
                 language=language(t), title=' '.join(''.join(c if ord(c) >= 32 else ' ' for c in str((t.get('tags') or {}).get('title', ''))).split())[:80])
            for t in info.subtitle_streams if type(t.get('index')) is int] if info else []


def validate_selection(items):
    if not isinstance(items, (list, tuple)) or len(items) > 32:
        raise ValueError('Можно включить до 32 дорожек субтитров.')
    result, seen = [], set()
    for t in items:
        if (not isinstance(t, dict) or set(t) != {'source', 'index', 'path', 'codec', 'language', 'title'} or
                t['source'] not in ('embedded', 'external') or type(t['index']) is not int or
                not isinstance(t['path'], str) or any(c in t['path'] for c in '\r\n\x00') or
                not isinstance(t['codec'], str) or t['codec'] not in SUPPORTED_CODECS or not isinstance(t['language'], str) or
                not re.fullmatch('[a-z]{3}', t['language']) or not isinstance(t['title'], str) or
                len(t['title']) > 80 or any(ord(c) < 32 for c in t['title'])):
            raise ValueError('Некорректные настройки субтитров.')
        if t['source'] == 'embedded':
            if t['index'] < 0 or t['path']:
                raise ValueError('Некорректный поток субтитров.')
            key = ('embedded', t['index'])
        else:
            if t['index'] != -1 or Path(t['path']).suffix.lower() not in ('.srt', '.sup'):
                raise ValueError('Добавьте файл SRT или PGS/SUP.')
            if t['codec'] != ('subrip' if Path(t['path']).suffix.lower() == '.srt' else 'hdmv_pgs_subtitle'):
                raise ValueError('Формат субтитров не совпадает с расширением файла.')
            key = ('external', str(Path(t['path']).resolve()).casefold())
        if key in seen:
            raise ValueError('Одна дорожка субтитров добавлена дважды.')
        seen.add(key)
        result.append(dict(t))
    return result


def select_subtitles(info, items):
    items = validate_selection(items)
    available = {t['index']: t for t in embedded_tracks(info)}
    for t in items:
        if t['source'] == 'embedded' and (t['index'] not in available or t['codec'] != available[t['index']]['codec']):
            raise ValueError('Выбранный поток субтитров отсутствует в фильме. Выберите дорожку заново.')
    return replace(info, selected_subtitles=tuple(items))


def menu_subtitles(info):
    return [dict(language=t['language'], title=t['title']) for t in info.selected_subtitles] if info else []


def external_track(filename, lang='rus', title=None):
    p = Path(filename).resolve()
    t = dict(source='external', index=-1, path=str(p),
             codec='subrip' if p.suffix.lower() == '.srt' else 'hdmv_pgs_subtitle',
             language=lang, title=p.stem[:80] if title is None else title)
    return validate_selection([t])[0]


def normalize_srt(source, destination, duration):
    """Normalize Unicode / legacy Russian SRT and clip cues to movie duration."""
    with Path(source).open('rb') as stream:
        raw = stream.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('SRT больше 32 МБ.')
    if raw.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
        text = raw.decode('utf-32')
    elif raw.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = raw.decode('utf-16')
    else:
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: text = raw.decode('cp1251')
    pattern = re.compile(r'(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})(?:[^\n]*)\n(.+)', re.S)
    cues = []
    def stamp(ms):
        h, rem = divmod(ms, 3600000); m, rem = divmod(rem, 60000); s, ms = divmod(rem, 1000)
        return f'{h:02}:{m:02}:{s:02},{ms:03}'
    for block in re.split(r'\n\s*\n', text.replace('\r\n', '\n').replace('\r', '\n').strip()):
        block = re.sub(r'^\d+\s*\n', '', block.strip())
        match = pattern.fullmatch(block)
        if not match:
            raise ValueError('Некорректный SRT: нужны тайминги ЧЧ:ММ:СС,ммм --> ЧЧ:ММ:СС,ммм и текст.')
        a = [int(x) for x in match.groups()[:8]]
        if max(a[1], a[2], a[5], a[6]) > 59:
            raise ValueError('Некорректное время в SRT.')
        begin = ((a[0]*60+a[1])*60+a[2])*1000+a[3]
        end = ((a[4]*60+a[5])*60+a[6])*1000+a[7]
        if end <= begin:
            raise ValueError('Конец субтитра должен быть позже начала.')
        if begin >= round(duration*1000): continue
        end = min(end, round(duration*1000))
        content = match[9].strip()
        if not content or '\x00' in content: raise ValueError('Некорректный текст SRT.')
        cues.append((begin, end, content))
    if not cues:
        raise ValueError('В субтитрах нет реплик в пределах фильма.')
    cues.sort(key=lambda c: c[0])
    # tsMuxer 2.7 requires a final empty separator to render the last cue.
    # Explicit BOM avoids its UTF-8 detector skipping the first three data bytes.
    Path(destination).write_text('\n\n'.join(f'{i}\n{stamp(a)} --> {stamp(b)}\n{t}' for i, (a,b,t) in enumerate(cues, 1))+'\n\n', encoding='utf-8-sig')


def prepare_subtitles(info, work, ffmpeg, cancel, log, runner):
    items = select_subtitles(info, info.selected_subtitles).selected_subtitles
    outputs = []
    command = [ffmpeg, '-hide_banner', '-nostdin', '-y', '-loglevel', 'error', '-progress', 'pipe:1', '-i', info.path]
    extracted = []
    for i, t in enumerate(items):
        text = t['codec'] in TEXT_CODECS
        target = work / f'subtitle_{i}{".srt" if text else ".sup"}'
        if t['source'] == 'embedded':
            raw = work / f'subtitle_raw_{i}{".srt" if text else ".sup"}'
            command += ['-map', f'0:{t["index"]}', '-c:s', 'srt' if text else 'copy', '-f', 'srt' if text else 'sup', str(raw)]
            extracted.append((raw, target, text))
        else:
            raw = Path(t['path'])
            if not raw.is_file(): raise ValueError('Не найден файл субтитров: '+str(raw))
            if text: normalize_srt(raw, target, info.duration)
            else:
                import shutil
                shutil.copyfile(raw, target)
        outputs.append((target, t))
    if extracted:
        runner(command, 'Извлечение субтитров', 0, 0, info.duration, cancel, log, lambda _: None)
        for raw, target, text in extracted:
            if text: normalize_srt(raw, target, info.duration)
            else: raw.replace(target)
    for target, t in outputs:
        if cancel.is_set(): raise RuntimeError('Операция отменена.')
        if target.suffix == '.sup':
            with target.open('rb') as stream:
                if stream.read(2) != b'PG': raise ValueError('Ожидались субтитры Blu-ray PGS/SUP.')
    return outputs


def meta_lines(outputs, fps, width, height):
    result = ''
    font = 'Arial' if sys.platform == 'win32' else 'OpenSans' if sys.platform == 'darwin' else 'FreeSans'
    for target, t in outputs:
        path = target.resolve().as_posix()
        if any(c in path for c in '\r\n"'): raise ValueError('Недопустимый путь субтитров.')
        result += f'{"S_TEXT/UTF8" if target.suffix == ".srt" else "S_HDMV/PGS"}, "{path}", lang={t["language"]}, fps={fps}, video-width={width}, video-height={height}'
        if target.suffix == '.srt':
            result += f', font-name="{font}", font-size=48, font-color=0xffffffff, font-border=2, bottom-offset=60'
        result += '\n'
    return result
