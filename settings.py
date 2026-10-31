"""Persist user tool paths and output directory (never private credentials)."""
from pathlib import Path
import json
import os
import sys
import shutil
import tempfile

def config_dir(platform: str, home: Path, appdata: str | None) -> Path:
    if platform == 'darwin':
        return home / 'Library' / 'Application Support' / 'DiscForgeRU'
    if platform == 'win32':
        return Path(appdata) / 'DiscForgeRU' if appdata else home / 'AppData' / 'Roaming' / 'DiscForgeRU'
    return Path(os.getenv('XDG_CONFIG_HOME') or home / '.config') / 'DiscForgeRU'


CFG = config_dir(sys.platform, Path.home(), os.getenv('APPDATA')) / 'settings.json'


def find_binary(name: str) -> str:
    app_root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
    resources = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    names = ('tsMuxeR', 'tsmuxer') if name.lower() == 'tsmuxer' else (name,)
    folders = [app_root / 'tools', app_root / 'bin', resources / 'bin', resources / 'tools']
    if sys.platform == 'darwin':
        folders += [Path('/opt/homebrew/bin'), Path('/usr/local/bin'),
                    Path('/Applications/tsMuxerGUI.app/Contents/MacOS')]
    for folder in folders:
        for alias in names:
            candidate = folder / (alias + '.exe' if sys.platform == 'win32' else alias)
            if candidate.is_file():
                return str(candidate)
    return next((found for alias in names if (found := shutil.which(alias))), name)


def defaults():
    return {
        'ui_theme': 'graphite',
        'ffmpeg': find_binary('ffmpeg'),
        'ffprobe': find_binary('ffprobe'),
        'tsmuxer': find_binary('tsmuxer') if sys.platform == 'darwin' else find_binary('tsMuxeR'),
        'output': str(Path.home() / 'DiscForge_Output'),
    }


def load():
    current = defaults()
    try:
        content = json.loads(CFG.read_text(encoding='utf-8'))
        if isinstance(content, dict):
            current.update({k: str(v) for k, v in content.items() if k in current})
    except (OSError, ValueError):
        pass
    return current


def write_json(path: Path, value) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save(value):
    write_json(CFG, value)
