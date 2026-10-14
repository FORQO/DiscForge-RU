"""Run real BD-J navigation through libVLC; virtual A/V outputs, no physical player."""
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', required=True)
    parser.add_argument('--java', required=True)
    parser.add_argument('--log', required=True)
    parser.add_argument('--child', action='store_true')
    args = parser.parse_args()
    if not args.child:
        java = Path(args.java).resolve()
        env = dict(os.environ, JAVA_HOME=str(java), JRE_HOME=str(java), BDJ_JVM_PATH=str(java/'bin/server/jvm.dll'), BD_DEBUG_MASK='0xffff')
        env['PATH'] = str(java/'bin')+';'+str(java/'bin/server')+';'+env['PATH']
        with open(args.log, 'wb') as output:
            result = subprocess.run([sys.executable, __file__, *sys.argv[1:], '--child'], stdout=output, stderr=subprocess.STDOUT,
                                    env=env, timeout=100, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(Path(args.log).read_text(encoding='utf-8', errors='replace')[-6000:])
        print('PASS: actual VLC BD-J collection navigation')
        return
    java = Path(args.java).resolve()
    os.environ.update(JAVA_HOME=str(java), JRE_HOME=str(java), BDJ_JVM_PATH=str(java/'bin/server/jvm.dll'), BD_DEBUG_MASK='0xffff')
    os.environ['PATH'] = str(java/'bin')+';'+str(java/'bin/server')+';'+os.environ['PATH']
    for key in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS'):
        os.environ.pop(key, None)
    dlls = [os.add_dll_directory(str(java/'bin')), os.add_dll_directory(str(java/'bin/server')),
            os.add_dll_directory(r'C:\Program Files\VideoLAN\VLC')]
    jvm = c.CDLL(str(java/'bin/server/jvm.dll'))
    vlc = c.CDLL(r'C:\Program Files\VideoLAN\VLC\libvlc.dll')
    ptr, integer, string = c.c_void_p, c.c_int, c.c_char_p
    def api(name, result, params):
        fn = getattr(vlc, name)
        fn.restype, fn.argtypes = result, params
        return fn
    new = api('libvlc_new', ptr, [integer, c.POINTER(string)])
    media_new = api('libvlc_media_new_location', ptr, [ptr, string])
    player_new = api('libvlc_media_player_new_from_media', ptr, [ptr])
    play = api('libvlc_media_player_play', integer, [ptr])
    nav = api('libvlc_media_player_navigate', None, [ptr, integer])
    opts = [b'--ignore-config', b'--vout=dummy', b'--aout=dummy', b'--verbose=2', b'--avcodec-hw=none', b'--bluray-menu']
    instance = new(len(opts), (string*len(opts))(*opts))
    media = media_new(instance, ('bluray:///'+Path(args.iso).resolve().as_posix()).encode())
    player = player_new(media)
    assert player and play(player) == 0
    crts = [c.CDLL('msvcrt.dll'), c.CDLL('ucrtbase.dll')]
    def output():
        for crt in crts:
            crt.fflush(None)
        return Path(args.log).read_text(encoding='utf-8', errors='replace')
    def waitfor(text, start=0, timeout=30):
        until = time.monotonic()+timeout
        while time.monotonic() < until:
            if text in output()[start:]:
                return
            time.sleep(.1)
        raise AssertionError('Missing '+text)
    def press(key):
        time.sleep(.2)
        nav(player, key)
        time.sleep(.25)
    try:
        waitfor('DiscForge: menu page 0')
        mark = len(output()); press(5)
        waitfor('DiscForge: menu page 0', mark)  # Back on the catalog stays on the catalog
        mark = len(output())
        press(2); press(0)  # second movie, then play
        waitfor('DiscForge: menu page 5', mark)
        press(0)
        waitfor('DiscForge: playlist 00001', mark)
        waitfor('DiscForge: play chapter 0', mark)
        waitfor('DiscForge: menu page 5', len(output()), timeout=15)  # end of short film
        mark = len(output()); press(5)
        waitfor('DiscForge: menu page 0', mark)
        mark = len(output()); press(0)
        waitfor('DiscForge: menu page 1', mark)
        press(2); press(2); press(0)  # first movie audio page
        waitfor('DiscForge: menu page 3', mark)
        press(2); press(2); press(2); press(0)
        waitfor('DiscForge: selected primary audio 4', mark)
        press(2); press(2); press(2); press(0)  # subtitles
        waitfor('DiscForge: menu page 4', mark)
        press(2); press(0)
        waitfor('DiskForge: selected subtitle 1', mark)
        press(0)
        waitfor('DiscForge: playlist 00000', mark)
        waitfor('DiscForge: play chapter 0', mark)
        waitfor('DiscForge: menu page 1', len(output()), timeout=15)
        assert 'DiscForge: java.' not in output()
        report = dict(passed=True, playlists=['00001', '00000'], first_movie_audio=4, first_movie_subtitle=1,
                      returned_to_catalog=True, virtual_outputs=True, hardware_player_tested=False)
        Path(args.log).with_suffix('.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('PASS', flush=True)
        os._exit(0)
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stderr.flush()
        os._exit(1)


if __name__ == '__main__':
    main()
