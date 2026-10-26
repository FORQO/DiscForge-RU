"""Exercise two real movies -> two playlists -> common BD-J menu -> verified ISO."""
import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
import time
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QGuiApplication
from core import probe_media
from smart_copy import analyze_copy
from subtitles import select_subtitles, embedded_tracks
from audio_selection import select_tracks
from disc_menu import MenuDesign
from disc_collection import create_collection


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('source_dir', 'tools', 'output'):
        p.add_argument('--'+key, required=True)
    p.add_argument('--reuse', help='Reuse a prior test run to prove the movie checkpoints are reusable')
    p.add_argument('--named-reversed', action='store_true', help='Rename and reorder movies, verify playlist and track bindings')
    p.add_argument('--film-enhancement', action='store_true', help='Improve only the first movie; preserve the second video')
    args = p.parse_args()
    app = QGuiApplication.instance() or QGuiApplication([])
    cancel = threading.Event()
    tools = Path(args.tools)
    output = Path(args.reuse) if args.reuse else Path(args.output)/('run-'+str(time.time_ns()))
    output.mkdir(parents=True, exist_ok=True)
    config = dict(ffmpeg=str(tools/'ffmpeg.exe'), ffprobe=str(tools/'ffprobe.exe'), tsmuxer=str(tools/'tsMuxeR.exe'), output=str(output))
    second = output/'second-movie.mkv'
    if not second.exists():
        subprocess.run([config['ffmpeg'], '-v', 'error', '-y', '-i', str(Path(args.source_dir)/'compatible-1080p24.mkv'),
                        '-i', str(Path(args.source_dir)/'mixed-720p50.mkv'), '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', str(second)],
                       check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    jobs = []
    for name, audio, subtitles in [('mixed-720p50.mkv', [1, 2, 3, 4], True), ('compatible-1080p24.mkv', [1], False)]:
        media = select_tracks(probe_media(str(second if name.startswith('compatible') else Path(args.source_dir)/name), config['ffprobe']), audio)
        if subtitles:
            media = select_subtitles(media, embedded_tracks(media))
        report = analyze_copy(media, config['ffprobe'], config['ffmpeg'], cancel)
        assert report.video_ok, report.reasons
        jobs.append((replace(media, copy_report=report), [0.0, 2.0]))
    events = []
    modes = ['video_copy', 'copy']
    if args.film_enhancement:
        config['video_enhancements'] = {str(Path(jobs[0][0].path).resolve()): True}
    expected_titles = [m.name for m, _ in jobs]
    if args.named_reversed:
        config['movie_names'] = {jobs[0][0].path: 'Серия 1 — Встреча', jobs[1][0].path: 'Серия 2 — Возвращение'}
        jobs.reverse()
        modes.reverse()
        expected_titles = ['Серия 2 — Возвращение', 'Серия 1 — Встреча']
    target = create_collection(jobs, config, 'BD25', True, False, cancel, lambda m: print(m, flush=True),
                              lambda p: None, lambda m, c: MenuDesign('Тест коллекции', logo_visible=False),
                              modes=modes, verification=events.append)
    report = json.loads(target.with_name(target.name+'.verification.json').read_text(encoding='utf-8'))
    assert report['iso_payload_verified']
    assert len(report['title_reports']) == 2
    assert all(r['passed'] for r in report['title_reports'])
    assert report['titles'] == expected_titles
    if args.reuse:
        assert all(r['reused_encode'] for r in report['title_reports'])
        if not args.named_reversed:
            assert all(r['reused_disc'] for r in report['title_reports'])
    from iso_menu_update import mounted_iso
    with mounted_iso(target) as mounted:
        assert len(list((mounted/'BDMV/PLAYLIST').glob('*.mpls'))) == 2
        if args.film_enhancement:
            # Decoded frame checksums prove the unselected movie kept its picture,
            # even when transport headers/timestamps change during Blu-ray muxing.
            def picture_hashes(path):
                result = subprocess.run([config['ffmpeg'], '-v', 'error', '-i', str(path),
                    '-map', '0:v:0', '-an', '-f', 'framemd5', '-'], check=True, capture_output=True,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                return [line.rsplit(',', 1)[-1].strip() for line in result.stdout.decode().splitlines()
                        if line and not line.startswith('#')]
            for i, (media, _) in enumerate(jobs):
                before = picture_hashes(media.path)
                after = picture_hashes(mounted/f'BDMV/STREAM/{i:05}.m2ts')
                enhanced = config['video_enhancements'].get(str(Path(media.path).resolve()), False)
                assert (before != after) if enhanced else (before == after), (media.name, enhanced)
        with zipfile.ZipFile(mounted/'BDMV/JAR/00000.jar') as jar:
            props = dict(line.split('=', 1) for line in jar.read('menu.properties').decode('ascii').splitlines() if '=' in line)
            assert props['movie.0.playlist'] == '00000'
            assert props['movie.1.playlist'] == '00001'
            assert props['movie.0.audio.count'] == ('1' if args.named_reversed else '4')
            assert props['movie.1.audio.count'] == ('4' if args.named_reversed else '1')
            assert props['movie.0.subtitle.count'] == ('0' if args.named_reversed else '1')
            assert props['movie.1.subtitle.count'] == ('1' if args.named_reversed else '0')
            assert json.loads(jar.read('discforge-collection.json'))['titles'] == expected_titles
            assert props['page.0.0'] != props['page.0.1']
            (output/('titles-preview.png' if args.named_reversed else 'collection-preview.png')).write_bytes(jar.read('page0.png'))
    assert any(e and e['scope'] == 'iso-final' for e in events)
    (output/('TITLES-RESULT.txt' if args.named_reversed else 'RESULT.txt')).write_text('PASS: two movies, names/order, independent audio/subtitles/chapters, linked playlists, UDF and ISO payload SHA-256.\n'+str(target), encoding='utf-8')
    print('PASS', target, flush=True)


if __name__ == '__main__':
    main()
