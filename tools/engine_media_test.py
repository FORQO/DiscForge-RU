"""Opt-in real FFmpeg → tsMuxer → IMAPI regression and GPU benchmark.

Creates only short synthetic media in the supplied workspace. Never burns discs.
Run on Windows with --ffmpeg, --ffprobe, --tsmuxer and --work ABSOLUTE_PATH.
"""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication
import core
from audio_selection import select_tracks
from disc_menu import MenuDesign
from smart_copy import analyze_copy
from build_cache import digest
from settings import write_json


def execute(command):
    p = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=180,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if p.returncode:
        raise RuntimeError(p.stderr[-5000:])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('ffmpeg','ffprobe','tsmuxer','work'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--benchmark',action='store_true')
    args=parser.parse_args()
    work=Path(args.work).resolve();work.mkdir(parents=True,exist_ok=True)
    runroot=work/('run-'+str(time.time_ns()))
    app=QGuiApplication.instance() or QGuiApplication([])
    cancel=threading.Event()
    result={'hardware_player_tested':False,'tests':{},'benchmark':{}}
    logs=[]
    def log(message):
        print(message,flush=True);logs.append(message)
    def fixture(name,size,fps,audio=True):
        target=work/(name+'.mkv')
        if target.exists():return target
        meta=work/'chapters.ffmeta'
        meta.write_text(';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=Начало\n'
                        '[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=4000\ntitle=Сцена с длинным названием — русский и English 0123456789\n',encoding='utf-8')
        sub=work/'captions.srt'
        sub.write_text('1\n00:00:00,500 --> 00:00:01,800\nПроверка / Test\n\n2\n00:00:02,000 --> 00:00:03,500\nВторая сцена\n\n',encoding='utf-8')
        command=[args.ffmpeg,'-v','error','-y','-f','lavfi','-i',f'testsrc2=size={size}:rate={fps}:duration=4']
        if audio:
            command+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4','-i',str(sub),'-f','ffmetadata','-i',str(meta),
                      '-map','0:v','-map','1:a','-map','1:a','-map','1:a','-map','1:a','-map','2:s','-map_metadata','3','-map_chapters','3',
                      '-c:a:0','ac3','-b:a:0','384k','-c:a:1','dca','-strict','-2','-b:a:1','768k',
                      '-c:a:2','pcm_s24le','-c:a:3','aac','-b:a:3','192k','-ac','2','-c:s','srt',
                      '-metadata:s:a:0','language=rus','-metadata:s:a:1','language=eng',
                      '-metadata:s:a:2','language=rus','-metadata:s:a:3','language=eng','-metadata:s:s:0','language=rus']
        command+=['-c:v','libx264','-threads','4','-preset','fast','-profile:v','high','-level:v','4.1','-pix_fmt','yuv420p',
                  '-b:v','6M','-maxrate','8M','-bufsize','8M','-g','50' if fps=='50' else '24','-bf','3',
                  '-x264-params','bluray-compat=1:nal-hrd=vbr:open-gop=0:ref=3','-t','4',str(target)]
        execute(command);return target
    def probe(path):return core.probe_media(str(path),args.ffprobe)
    try:
        source=fixture('mixed-720p50','1280x720','50')
        info=select_tracks(probe(source),[1,2,3,4])
        from subtitles import select_subtitles, embedded_tracks
        info=select_subtitles(info,embedded_tracks(info))
        report=analyze_copy(info,args.ffprobe,args.ffmpeg,cancel)
        result['analysis']=report.__dict__
        assert report.video_ok,report.reasons
        info=replace(info,copy_report=report)
        from PySide6.QtGui import QImage, QColor
        from disc_menu import import_background
        background=work/'menu-background.png'
        picture=QImage(320,180,QImage.Format_RGB32)
        picture.fill(QColor('#405060'))
        assert picture.save(str(background))
        embedded=import_background(str(background))
        assert len(embedded)>260
        menu=MenuDesign('Проверка движка',chapter_titles=list(info.source_chapter_titles),background=embedded)
        calls=[]
        real_run=core.run_command
        def observe(command,*a,**kw):
            calls.append(command)
            return real_run(command,*a,**kw)
        def build(output, media=info, design=menu, iso=True, mode='auto', nvenc=False):
            events=[]
            image=core.create_bluray(media,'BD25',str(runroot/output),args.ffmpeg,args.tsmuxer,iso,nvenc,cancel,log,lambda _:None,
                                     menu=design,fast=True,processing_mode=mode,ffprobe=args.ffprobe,resilient=True,verification=events.append)
            final=[e for e in events if e and e['scope']=='iso-final']
            if iso:
                assert final and final[-1]['complete'] and final[-1]['done']==final[-1]['total']
                assert [e['done'] for e in final]==sorted(e['done'] for e in final)
                assert any(e['phase'].startswith('1/2') for e in final)
                assert any(e['phase'].startswith('2/2') for e in final)
                assert any(e['state']=='waiting' for e in final)
                result['tests']['iso_verification_byte_progress']=True
            return image
        # Fault injection only at ISO creation; encode and authoring are real.
        with patch('core.run_command',side_effect=observe),patch('core.create_iso',side_effect=RuntimeError('INJECTED ISO FAILURE')):
            try:build('resume')
            except RuntimeError as exc:
                assert str(exc)=='INJECTED ISO FAILURE',str(exc)
            else:raise AssertionError('Failure was not injected')
        encoded_calls=len([c for c in calls if '-c:v' in c and any(str(x).endswith('video.h264') for x in c)])
        assert encoded_calls==1,encoded_calls
        calls.clear()
        with patch('core.run_command',side_effect=observe):image=build('resume')
        assert not any('-c:v' in c and any(str(x).endswith('video.h264') for x in c) for c in calls),'encoded twice'
        validation=json.loads(image.with_name(image.name+'.verification.json').read_text(encoding='utf-8'))
        assert validation['reused_encode'] and validation['reused_disc'] and validation['iso_payload_verified']
        assert [a['copy'] for a in validation['audio']]==[True,True,True,False],validation['audio']
        result['tests']['mixed_audio_subtitles_chapters_iso_resume']=True
        with patch('disc_verify.verify_image_payload',side_effect=RuntimeError('INJECTED VERIFICATION FAILURE')):
            try:build('resume-verification')
            except RuntimeError as exc:assert str(exc)=='INJECTED VERIFICATION FAILURE',str(exc)
            else:raise AssertionError('Verification failure was not injected')
        with patch('core.create_iso',side_effect=AssertionError('ISO recreated after completed stage')):
            build('resume-verification')
        result['tests']['completed_iso_reused_after_verification_error']=True
        # Menu edits reuse elementary streams but must rebuild authored metadata.
        calls.clear()
        with patch('core.run_command',side_effect=observe):
            picture.fill(QColor('#605040'))
            assert picture.save(str(background))
            image2=build('resume',design=replace(menu,background=import_background(str(background))))
        assert not any('-c:v' in c and any(str(x).endswith('video.h264') for x in c) for c in calls)
        v2=json.loads(image2.with_name(image2.name+'.verification.json').read_text(encoding='utf-8'))
        assert v2['reused_encode'] and not v2['reused_disc']
        result['tests']['embedded_background_change_reuses_encode']=True
        # Changing settings invalidates only affected checkpoints; audio policies also apply to full video encoding.
        encoded_info=replace(info,audio_modes={'2':'ac3'})
        encoded_image=build('gpu-audio-policy',media=encoded_info,design=None,mode='encode',nvenc=True)
        encoded_report=json.loads(encoded_image.with_name(encoded_image.name+'.verification.json').read_text(encoding='utf-8'))
        assert [a['copy'] for a in encoded_report['audio']]==[True,False,True,False]
        result['tests']['gpu_encode_preserves_selected_audio_and_no_menu_iso']=True
        # Verify the larger LPCM budget and authoring path with a real 96 kHz / 24-bit / 5.1 track.
        surround=work/'lpcm-96k-51.mkv'
        execute([args.ffmpeg,'-v','error','-y','-i',str(source),'-f','lavfi','-i',
                 'anullsrc=sample_rate=96000:channel_layout=5.1','-map','0:v','-map','1:a',
                 '-c:v','copy','-c:a','pcm_s24le','-t','4',str(surround)])
        surround_image=build('lpcm-96k-51',media=probe(surround),design=MenuDesign('LPCM 96 kHz / 5.1'))
        surround_report=json.loads(surround_image.with_name(surround_image.name+'.verification.json').read_text(encoding='utf-8'))
        assert surround_report['audio'][0]['copy']
        result['tests']['lpcm_96k_24bit_51']=True
        for name,size,fps,expected in [('compatible-1080p24','1920x1080','24',True),
                                       ('convert-1080p25','1920x1080','25',False)]:
            sample=probe(fixture(name,size,fps,audio=False))
            checked=analyze_copy(sample,args.ffprobe,args.ffmpeg,cancel)
            assert checked.video_ok==expected,(name,checked.reasons)
            build(name,media=replace(sample,copy_report=checked),design=MenuDesign(name),nvenc=not expected)
            result['tests'][name]=True
        # A genuine timestamp gap in a compatible-size/rate AVC container must fail closed.
        broken=work/'broken-timestamps.mkv'
        execute([args.ffmpeg,'-v','error','-y','-i',str(source),'-map','0:v','-an','-c:v','copy',
                 '-bsf:v',r'setts=pts=PTS+gte(N\,100)*1/TB:dts=DTS+gte(N\,100)*1/TB',str(broken)])
        bad=analyze_copy(probe(broken),args.ffprobe,args.ffmpeg,cancel)
        assert not bad.video_ok
        result['tests']['broken_timestamps_rejected']=True
        if args.benchmark:
            from gpu_pipeline import supported,cuda_command
            sample=probe(fixture('gpu-resize','1920x800','24',audio=False))
            assert supported(sample,args.ffmpeg,cancel,log,core.run_command,core.make_video_command),'CUDA unavailable'
            timings={}
            for gpu in (False,True):
                runs=[]
                for n in range(3):
                    output=work/f'benchmark-{gpu}-{n}.h264'
                    command=core.make_video_command(args.ffmpeg,sample.path,str(output),8000000,True,True)
                    if gpu:command=cuda_command(command)
                    start=time.perf_counter();execute(command);runs.append(time.perf_counter()-start)
                    output.unlink()
                timings['cuda_frames' if gpu else 'cpu_frames']=dict(seconds=runs,median=statistics.median(runs))
            result['benchmark']={'source':{'size':'1920x800','fps':24,'duration':4},'encoder':'NVENC p3','runs':timings,
                                 'speed_ratio':timings['cpu_frames']['median']/timings['cuda_frames']['median']}
        result['passed']=True
    except Exception as exc:
        result['passed']=False;result['error']=repr(exc)
        raise
    finally:
        write_json(work/'results.json',result)
        (work/'build.log').write_text('\n'.join(logs),encoding='utf-8')
    return 0


if __name__=='__main__':raise SystemExit(main())
