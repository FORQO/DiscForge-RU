"""Launch installed VLC with our private Java runtime and Blu-ray menus."""
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys


def app_root():
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent


def pe_machine(path):
    with Path(path).open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Ожидался исполняемый файл Windows: ' + str(path))
        stream.seek(60)
        header = stream.read(4)
        if len(header) != 4:
            raise ValueError('Повреждён файл Windows: ' + str(path))
        offset = struct.unpack('<I', header)[0]
        stream.seek(offset)
        if stream.read(4) != b'PE\0\0':
            raise ValueError('Некорректный файл Windows: ' + str(path))
        machine = stream.read(2)
        if len(machine) != 2:
            raise ValueError('Повреждён файл Windows: ' + str(path))
        return struct.unpack('<H', machine)[0]


def find_vlc():
    if sys.platform == 'darwin':
        for candidate in (Path('/Applications/VLC.app/Contents/MacOS/VLC'),
                          Path.home() / 'Applications/VLC.app/Contents/MacOS/VLC'):
            if candidate.is_file():
                return candidate
        found = shutil.which('vlc')
        return Path(found) if found else None
    candidates = []
    if sys.platform == 'win32':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r'Software\VideoLAN\VLC', 0, winreg.KEY_READ | view) as key:
                        candidates.append(Path(winreg.QueryValueEx(key, 'InstallDir')[0]) / 'vlc.exe')
                except OSError:
                    pass
    for name in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)'):
        if os.environ.get(name):
            candidates.append(Path(os.environ[name]) / 'VideoLAN/VLC/vlc.exe')
    found = shutil.which('vlc.exe')
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        try:
            if candidate.is_file() and pe_machine(candidate) == 0x8664:
                return candidate
        except (OSError, ValueError, struct.error):
            pass
    return None


def launch_iso(filename, vlc=None, root=None):
    iso = Path(filename).resolve()
    if not iso.is_file() or iso.suffix.lower() != '.iso' or iso.stat().st_size == 0:
        raise ValueError('Выберите существующий непустой ISO-образ.')
    return launch_bluray(iso, vlc, root)


def launch_bluray(filename, vlc=None, root=None):
    """Accept an ISO or the root of a mounted/physical Blu-ray disc."""
    source = Path(filename).resolve()
    if source.is_dir():
        if not (source / 'BDMV/index.bdmv').is_file():
            raise ValueError('На выбранном диске не найдена структура Blu-ray (BDMV/index.bdmv).')
    elif not source.is_file() or source.suffix.lower() != '.iso' or source.stat().st_size == 0:
        raise ValueError('Выберите непустой ISO-образ или диск Blu-ray.')
    if sys.platform == 'darwin':
        return launch_macos(source, vlc, root)
    executable = Path(vlc) if vlc else find_vlc()
    if executable is None:
        raise FileNotFoundError('Не найден VLC 64-bit. Выберите установленный vlc.exe.')
    java = (Path(root) if root else app_root()) / 'runtime/java'
    jvm = java / 'bin/server/jvm.dll'
    if not jvm.is_file() or not (java / 'lib/rt.jar').is_file():
        raise FileNotFoundError('Не найдена Java из комплекта DiscForge: ' + str(java) +
                                '\nРаспакуйте всю папку программы вместе с runtime.')
    if pe_machine(executable) != 0x8664 or pe_machine(jvm) != 0x8664:
        raise ValueError('Для Java из комплекта требуется VLC 64-bit. Выберите 64-разрядный vlc.exe.')
    env = os.environ.copy()
    env.update(JAVA_HOME=str(java), JRE_HOME=str(java), BDJ_JVM_PATH=str(jvm))
    env['PATH'] = str(java / 'bin') + os.pathsep + str(jvm.parent) + os.pathsep + env.get('PATH', '')
    # Private JVM must not inherit options injected for another Java application.
    for name in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS'):
        env.pop(name, None)
    mrl = 'bluray:///' + source.as_posix()
    command = [str(executable), '--no-one-instance', '--no-one-instance-when-started-from-file',
               '--bluray-menu', mrl]
    # PyInstaller's DLL directory otherwise leaks into the external process.
    frozen = sys.platform == 'win32' and getattr(sys, 'frozen', False)
    if frozen:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(command, env=env, cwd=str(executable.parent))
    finally:
        if frozen:
            ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)


def macos_java(root=None):
    """Find a JVM for BD-J without changing the user's global Java configuration."""
    resources = Path(root) if root else Path(getattr(sys, '_MEIPASS', app_root()))
    candidates = [resources / 'runtime/java', resources / 'runtime/java/Contents/Home']
    if os.environ.get('JAVA_HOME'):
        candidates.append(Path(os.environ['JAVA_HOME']))
    try:
        found = subprocess.run(['/usr/libexec/java_home'], capture_output=True, text=True, timeout=5)
        if found.returncode == 0 and found.stdout.strip():
            candidates.append(Path(found.stdout.strip()))
    except (OSError, subprocess.TimeoutExpired):
        pass
    for base in ('/Library/Java/JavaVirtualMachines', str(Path.home() / 'Library/Java/JavaVirtualMachines')):
        candidates.extend(sorted(Path(base).glob('*/Contents/Home'), reverse=True))
    for java in candidates:
        for name in ('lib/server/libjvm.dylib', 'jre/lib/server/libjvm.dylib'):
            jvm = java / name
            if jvm.is_file():
                return java, jvm
    raise FileNotFoundError('Для меню Blu-ray в VLC установите Java JDK той же архитектуры, что и VLC, '
                            'либо задайте JAVA_HOME. Само создание ISO не требует Java.')


def launch_macos(source, vlc=None, root=None):
    executable = Path(vlc) if vlc else find_vlc()
    if executable and executable.suffix.lower() == '.app':
        executable = executable / 'Contents/MacOS/VLC'
    if executable is None or not executable.is_file():
        raise FileNotFoundError('Не найден VLC. Установите VLC.app в папку «Программы».')
    java, jvm = macos_java(root)
    env = os.environ.copy()
    env.update(JAVA_HOME=str(java), JRE_HOME=str(java), BDJ_JVM_PATH=str(jvm))
    for name in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS', 'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH'):
        env.pop(name, None)
    return subprocess.Popen([str(executable), '--bluray-menu',
                             'bluray:///' + source.as_posix()], env=env, cwd=str(executable.parent))
