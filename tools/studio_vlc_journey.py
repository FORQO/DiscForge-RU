"""Exercise Windows libVLC BD-J navigation and playback with virtual A/V outputs."""
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(args):
    java = Path(args.java).resolve()
    if sys.platform != 'win32':
        raise RuntimeError('This harness requires Windows; test macOS with the VLC application.')
    jvm = java / 'bin/server/jvm.dll'
    vlcroot = Path(r'C:\Program Files\VideoLAN\VLC')
    log = Path(args.log).resolve(); log.parent.mkdir(parents=True, exist_ok=True)
    propsfile = log.with_suffix('.properties.json')
    if not args.child:
        from iso_menu_update import mounted_iso
        with mounted_iso(Path(args.iso)) as disc, zipfile.ZipFile(disc/'BDMV/JAR/00000.jar') as jar:
            props = dict(line.split('=', 1) for line in jar.read('menu.properties').decode('ascii').splitlines() if '=' in line)
        propsfile.write_text(json.dumps(props))
        env = dict(os.environ, JAVA_HOME=str(java), JRE_HOME=str(java), BDJ_JVM_PATH=str(jvm), BD_DEBUG_MASK='0xffff')
        for key in ('JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'JDK_JAVA_OPTIONS'):
            env.pop(key, None)
        env['PATH'] = str(java/'bin')+os.pathsep+str(jvm.parent)+os.pathsep+env.get('PATH', '')
        with log.open('wb') as stream:
            p = subprocess.run([sys.executable, __file__, *sys.argv[1:], '--child'], env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
        if p.returncode:
            raise RuntimeError(log.read_text(errors='replace')[-7000:])
        print('PASS: actual VLC BD-J navigation', args.iso, flush=True)
        return
    pause = time.sleep
    handles = [os.add_dll_directory(str(p)) for p in (java/'bin', jvm.parent, vlcroot)]
    handles.append(c.CDLL(str(jvm)))
    vlc = c.CDLL(str(vlcroot/'libvlc.dll'))
    def api(name, result, params):
        fn=getattr(vlc,name);fn.restype=result;fn.argtypes=params;return fn
    ptr,integer,string=c.c_void_p,c.c_int,c.c_char_p
    new=api('libvlc_new',ptr,[integer,c.POINTER(string)])
    media_new=api('libvlc_media_new_location',ptr,[ptr,string])
    player_new=api('libvlc_media_player_new_from_media',ptr,[ptr])
    play=api('libvlc_media_player_play',integer,[ptr])
    nav=api('libvlc_media_player_navigate',None,[ptr,integer])
    opts=[b'--ignore-config',b'--vout=dummy',b'--aout=dummy',b'--verbose=2',b'--avcodec-hw=none',b'--bluray-menu']
    instance=new(len(opts),(string*len(opts))(*opts));assert instance
    media=media_new(instance,('bluray:///'+Path(args.iso).resolve().as_posix()).encode())
    player=player_new(media);assert player and play(player)==0
    props=json.loads(propsfile.read_text())
    crts=[c.CDLL('msvcrt.dll'),c.CDLL('ucrtbase.dll')]
    def output():
        for crt in crts:crt.fflush(None)
        return log.read_text(errors='replace')
    def waitfor(text,start=0,timeout=30):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            if text in output()[start:]:return
            pause(.1)
        raise AssertionError('Missing '+text)
    def press(key):
        nav(player,key);pause(.35)
    def choose(page,action):
        actions=[props[f'page.{page}.{i}'] for i in range(int(props[f'page.{page}.count']))]
        index=actions.index(action)
        # All fixtures use the default vertical layout. Start from top even after restoring a selection.
        for _ in actions:press(1)
        for _ in range(index):press(2)
        mark=len(output());press(0);return mark
    checks=[]
    try:
        waitfor('DiscForge: menu page 0')
        for movie in range(int(props.get('movies','1'))):
            prefix=f'movie.{movie}.' if 'movies' in props else ''
            home=int(props.get(prefix+'home','0'))
            if 'movies' in props:
                mark=choose(0,f'page:{home}');waitfor(f'DiscForge: menu page {home}',mark)
            if int(props[prefix+'audio.count'])>1:
                page=int(props[prefix+'audio.page'])
                mark=choose(home,f'page:{page}');waitfor(f'DiscForge: menu page {page}',mark)
                mark=choose(page,'audio:2');waitfor('DiscForge: selected primary audio 2',mark);waitfor(f'DiscForge: menu page {home}',mark)
                checks.append(f'movie{movie}:audio2')
            if int(props[prefix+'subtitle.count'])>0:
                page=int(props[prefix+'subtitle.page'])
                mark=choose(home,f'page:{page}');waitfor(f'DiscForge: menu page {page}',mark)
                mark=choose(page,'sub:1');waitfor('DiskForge: selected subtitle 1',mark);waitfor(f'DiscForge: menu page {home}',mark)
                checks.append(f'movie{movie}:subtitle1')
            # Enter chapter page, start chapter 2, wait for actual playback and natural return.
            page=next(int(props[f'page.{home}.{i}'].split(':')[1]) for i in range(int(props[f'page.{home}.count']))
                      if props[f'page.{home}.{i}'].startswith('page:') and props.get('page.'+props[f'page.{home}.{i}'].split(':')[1]+'.1')=='play:1')
            mark=choose(home,f'page:{page}');waitfor(f'DiscForge: menu page {page}',mark)
            mark=choose(page,'play:1');waitfor('DiscForge: playlist '+props.get(prefix+'playlist','00000'),mark);waitfor('DiscForge: play chapter 1',mark)
            pause(.6)
            waitfor(f'DiscForge: menu page {home}',mark,timeout=20)
            checks.append(f'movie{movie}:chapter2_playback_return')
            if 'movies' in props:
                mark=len(output());press(5);waitfor('DiscForge: menu page 0',mark)
        assert 'DiscForge: java.' not in output()
        log.with_suffix('.json').write_text(json.dumps(dict(passed=True,platform=sys.platform,checks=checks,virtual_outputs=True,hardware_player_tested=False),indent=2))
        print('PASS',flush=True);os._exit(0)
    except BaseException:
        import traceback
        traceback.print_exc();sys.stderr.flush();os._exit(1)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('iso','java','log'):p.add_argument('--'+key,required=True)
    p.add_argument('--child',action='store_true');run(p.parse_args())
