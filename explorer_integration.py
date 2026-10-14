"""Optional per-user Explorer verbs; never replace VLC/default associations."""
from pathlib import Path
import sys

LABEL = 'Открыть Blu-ray в VLC'
VERB = 'DiskForge.OpenBluRay'
KEYS = (rf'Software\Classes\Drive\shell\{VERB}',
        rf'Software\Classes\SystemFileAssociations\.iso\shell\{VERB}')


def commands(executable):
    executable = str(Path(executable).resolve())
    if '"' in executable:
        raise ValueError('Недопустимый путь программы.')
    # A drive root ends in a backslash, which would escape a closing quote in
    # Windows argv parsing. The harmless dot makes "F:\\." safe to pass.
    return (f'"{executable}" --open-bluray "%1."',
            f'"{executable}" --open-bluray "%1"')


def notify_shell():
    import ctypes
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def install():
    import winreg
    if not getattr(sys, 'frozen', False):
        raise ValueError('Добавление в Проводник доступно в собранной portable-версии DiskForge.')
    executable = Path(sys.executable).resolve()
    from vlc_preview import app_root
    if not (app_root() / 'runtime/java/bin/server/jvm.dll').is_file():
        raise ValueError('Не найдена Java из комплекта. Распакуйте всю папку DiskForge.')
    for path, command in zip(KEYS, commands(executable)):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            for name, value in [('', LABEL), ('Icon', f'"{executable}",0'),
                                ('MultiSelectModel', 'Single')]:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path + r'\command') as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, command)
    notify_shell()


def uninstall():
    import winreg
    # Only our two known verbs; never remove parent classes or other software.
    for path in KEYS:
        for target in (path + r'\command', path):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, target)
            except FileNotFoundError:
                pass
    notify_shell()
