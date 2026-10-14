"""Opt-in real TrueHD/Atmos authoring tests. Samples are never redistributed.

Provide --atmos pointing to https://fate-suite.ffmpeg.org/truehd/atmos.thd
and --plain pointing to https://samples.ffmpeg.org/A-codecs/TrueHD/TrueHD.raw.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication
from audio_selection import select_tracks
from build_cache import digest
import core
from disc_menu import MenuDesign
from settings import write_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('ffmpeg', 'ffprobe', 'tsmuxer', 'work', 'atmos', 'plain'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--only', choices=('atmos','plain','96k'))
    args = p.parse_args()
    work = Path(args.work).resolve() / ('run-'+str(time.time_ns()))
    work.mkdir(parents=True)
    app = QGuiApplication([])
    cancel = threading.Event()
    results = {'hardware_player_tested': False, 'tests': {}, 'samples': {}}
    def execute(command):
        result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=120,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(result.stderr[-6000:])
    def log(s): print(s, flush=True)
    try:
        for kind in ('atmos', 'plain', '96k'):
            if args.only and args.only != kind: continue
            sample = Path(getattr(args, kind)) if kind != '96k' else None
            if sample:
                results['samples'][kind] = {'sha256': digest(sample, cancel)}
            # Repeat the complete FATE access units to obtain a 2-second test.
            # No encoder and no truehd_core filter touch these bytes.
            raw = work / (kind+'.thd')
            if kind == 'atmos':
                raw.write_bytes(sample.read_bytes()*19)
                duration = '2.026667'
            elif kind == 'plain':
                execute([args.ffmpeg, '-v','error','-y','-i',str(sample),'-t','2','-c:a','copy','-f','truehd',str(raw)])
                duration = '2'
            else:
                duration = '2'
                execute([args.ffmpeg,'-v','error','-y','-f','lavfi','-i','sine=frequency=600:sample_rate=96000:duration=2',
                         '-ac','2','-c:a','truehd','-strict','-2','-f','truehd',str(raw)])
            source = work / (kind+'.mkv')
            execute([args.ffmpeg,'-v','error','-y','-f','lavfi','-i',f'testsrc2=size=1280x720:rate=50:duration={duration}',
                     '-i',str(raw),'-f','lavfi','-i',f'sine=frequency=440:sample_rate=48000:duration={duration}',
                     '-map','0:v','-map','2:a','-map','1:a','-c:a:0','ac3','-b:a:0','384k','-c:a:1','copy',
                     '-metadata:s:a:0','language=rus','-metadata:s:a:1','language=eng',
                     '-c:v','libx264','-threads','4','-preset','fast','-profile:v','high','-level:v','4.1',
                     '-b:v','6M','-maxrate','8M','-bufsize','8M','-g','50','-bf','3',
                     '-x264-params','bluray-compat=1:nal-hrd=vbr:open-gop=0:ref=3',str(source)])
            info = select_tracks(core.probe_media(str(source),args.ffprobe), [2] if kind == '96k' else [1,2])
            log(f'{kind}: '+str([(s['codec_name'], s.get('profile'), s['sample_rate']) for s in info.audio_streams]))
            image = core.create_bluray(info,'BD25',str(work/kind),args.ffmpeg,args.tsmuxer,True,False,cancel,log,lambda _:None,
                         chapters=[0],menu=MenuDesign('TrueHD / Atmos · проверка'),processing_mode='encode' if kind == '96k' else 'auto',ffprobe=args.ffprobe,resilient=True,fast=True)
            report = json.loads(image.with_name(image.name+'.verification.json').read_text(encoding='utf-8'))
            assert report['iso_payload_verified'] and report['audio_tracks'] == (1 if kind == '96k' else 2)
            assert len(report['lossless_truehd']) == 1 and report['lossless_truehd'][0]['bit_exact']
            if kind == 'atmos':
                assert 'Atmos' in report['lossless_truehd'][0]['profile']
            results['tests'][kind] = report
            if kind == 'atmos':
                # Identical rebuild reuses the prepared TrueHD/AC-3 streams.
                with patch('audio_pipeline.append_audio',side_effect=AssertionError('audio processed twice')):
                    again=core.create_bluray(info,'BD25',str(work/kind),args.ffmpeg,args.tsmuxer,True,False,cancel,log,lambda _:None,
                         chapters=[0],menu=MenuDesign('TrueHD / Atmos · проверка'),processing_mode='auto',ffprobe=args.ffprobe,resilient=True,fast=True)
                resumed=json.loads(again.with_name(again.name+'.verification.json').read_text(encoding='utf-8'))
                assert resumed['reused_encode'] and resumed['reused_disc']
                results['tests']['resume_without_audio_processing']=True
                with patch('truehd_audio.payload_hash',side_effect=['a'*64,'b'*64]):
                    try:
                        core.create_bluray(info,'BD25',str(work/kind),args.ffmpeg,args.tsmuxer,True,False,cancel,log,lambda _:None,
                            chapters=[0],menu=MenuDesign('TrueHD / Atmos · проверка'),processing_mode='auto',ffprobe=args.ffprobe,resilient=True,fast=True)
                    except ValueError as exc:
                        assert 'TrueHD/Atmos изменился' in str(exc)
                    else:raise AssertionError('Damaged TrueHD accepted')
                results['tests']['changed_truehd_rejected']=True
    finally:
        write_json(work/'truehd-verification.json', results)
        print('REPORT '+str(work/'truehd-verification.json'),flush=True)


if __name__ == '__main__': main()
