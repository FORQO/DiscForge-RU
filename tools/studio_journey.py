"""Real Studio journey: Qt controls, project round-trip, FFmpeg, BDMV and ISO.

Synthetic media only. Settings/file pickers are isolated; scanners and builders are real.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt, QTimer, QPoint, QRect
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtTest import QTest
from main import Window
from studio_ui import ExportDialog
from settings import write_json
from iso_menu_update import mounted_iso


def run(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    fixtures = output / 'Исходники с пробелами'
    fixtures.mkdir(exist_ok=True)
    meta = fixtures / 'chapters.txt'
    meta.write_text(';FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=4000\ntitle=Начало\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=4000\nEND=8000\ntitle=Вторая сцена\n', encoding='utf-8')
    sub = fixtures / 'титры.srt'
    sub.write_text('1\n00:00:00,500 --> 00:00:03,000\nПроверка Studio / Test\n\n2\n00:00:04,100 --> 00:00:07,000\nВторая сцена\n', encoding='utf-8')
    first, second = (fixtures / n for n in ('Первый фильм.mkv', 'Второй фильм.mkv'))
    if not first.exists():
        subprocess.run([args.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=50:duration=8',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=8', '-i', str(sub), '-f', 'ffmetadata', '-i', str(meta),
            '-map', '0:v', '-map', '1:a', '-map', '1:a', '-map', '2:s', '-map_metadata', '3', '-map_chapters', '3',
            '-c:v', 'libx264', '-threads', '4', '-preset', 'fast', '-profile:v', 'high', '-level:v', '4.1', '-pix_fmt', 'yuv420p',
            '-b:v', '6M', '-maxrate', '8M', '-bufsize', '8M', '-g', '50', '-bf', '3', '-x264-params', 'bluray-compat=1:nal-hrd=vbr:open-gop=0:ref=3',
            '-c:a:0', 'ac3', '-b:a:0', '384k', '-c:a:1', 'aac', '-b:a:1', '192k', '-ac', '2', '-c:s', 'srt',
            '-metadata:s:a:0', 'language=rus', '-metadata:s:a:1', 'language=eng', '-metadata:s:s:0', 'language=rus', '-t', '8', str(first)], check=True)
        subprocess.run([args.ffmpeg, '-v', 'error', '-y', '-i', str(first), '-map', '0:v', '-map', '0:a:0', '-c', 'copy', str(second)], check=True)
    app = QApplication.instance() or QApplication([])
    errors, checks, results = [], {}, {}
    def warning(_parent, title, message, *unused):
        errors.append(f'{title}: {message}')
        return QMessageBox.Ok
    def wait(predicate, label, timeout=300):
        until = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if errors:
                raise AssertionError('\n'.join(errors))
            if time.monotonic() > until:
                raise TimeoutError(label)
            QTest.qWait(20)
        app.processEvents()
        if errors:
            raise AssertionError('\n'.join(errors))
    config = dict(ffmpeg=args.ffmpeg, ffprobe=args.ffprobe, tsmuxer=args.tsmuxer, output=str(output / 'Готовые диски'), ui_theme='graphite')
    with patch('main.load_settings', return_value=config), patch('main.save_settings'), patch.object(QMessageBox, 'warning', side_effect=warning):
        w = Window(); w.resize(1380, 850); w.show(); app.processEvents()
        try:
            def scan(paths):
                with patch.object(QFileDialog, 'getOpenFileNames', return_value=([str(p) for p in paths], '')):
                    w.add_media()
                wait(lambda: w.scan_thread is not None and not w.scan_thread.isRunning(), 'scan')
                assert all(str(p) in w.media for p in paths), w.log.toPlainText()
            scan([first])
            w.movies.setCurrentRow(0)
            w.disc_mode.setCurrentIndex(1)
            w.tabs.setCurrentIndex(1)
            w.audio_panel._check_all(True)
            assert len(w.media[str(first)].selected_audio) == 2
            w.tabs.setCurrentIndex(2)
            w.subtitle_panel.tracks.item(0).setCheckState(Qt.Checked)
            assert len(w.media[str(first)].selected_subtitles) == 1
            w.tabs.setCurrentIndex(3)
            assert len(w.chapters[str(first)]) == 2
            w.chapter_list.setCurrentRow(1)
            w.chapter_time.setText('00:00:03.000')
            w.edit_chapter_button.click()
            assert w.chapters[str(first)] == [0., 3.]
            w.tabs.setCurrentWidget(w.menu_editor)
            w.menu_title.setText('Вечер кино · Studio')
            w.menu_editor.inline_text('chapter:1', 'Поворот сюжета')
            w.menu_editor.preview_mode.setChecked(True)
            assert w.menu_editor.preview.view_only
            w.menu_editor.preview_mode.setChecked(False)
            checks['scan_audio_subtitles_chapters_menu'] = True
            for theme in ('graphite', 'blue'):
                w.set_theme(theme)
                for width, height in ((1380, 850), (1040, 680)):
                    w.resize(width, height)
                    for tab in range(w.tabs.count()):
                        w.tabs.setCurrentIndex(tab); app.processEvents()
                        assert w.width() == width and w.height() == height, (theme, tab, w.size())
                        assert w.build_button.mapTo(w, w.build_button.rect().bottomRight()).x() < width
                        assert w.grab().save(str(output / f'{theme}-{width}-tab{tab}.png'))
            w.set_theme('graphite'); w.resize(1380, 850)
            checks['six_tabs_two_themes_two_sizes'] = True
            def roundtrip(name):
                path = output / (name + '.dfr.json')
                before = w._project_data()
                with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(path), '')):
                    w.save_project()
                assert json.loads(path.read_text(encoding='utf-8')) == before
                assert w.new_project()
                with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(path), '')):
                    w.open_project()
                wait(lambda: w.scan_thread and not w.scan_thread.isRunning(), 'reopen')
                after = w._project_data()
                assert after == before, {k: (before[k],after[k]) for k in before if before[k] != after[k]}
            roundtrip('single-project')
            checks['single_project_roundtrip'] = True
            def build(name, iso=True):
                destination = output / name
                captured = []
                def confirm():
                    d = app.activeModalWidget()
                    assert isinstance(d, ExportDialog)
                    d.folder.setText(str(destination)); d.mode.setCurrentIndex(0 if iso else 1)
                    d.create.click()
                QTimer.singleShot(0, confirm)
                w.show_export_dialog()
                assert w.build_thread is not None, w.log.toPlainText()
                w.build_thread.item_done.connect(lambda _p, target: captured.append(target))
                wait(lambda: not w.build_thread.isRunning(), 'build ' + name, 600)
                assert w._studio_result == 'Готово', w.log.toPlainText()
                assert captured, w.log.toPlainText()
                target = Path(captured[-1])
                results[name] = str(target)
                (output / (name + '.log')).write_text(w.log.toPlainText(), encoding='utf-8')
                if iso:
                    report = json.loads(target.with_name(target.name + '.verification.json').read_text(encoding='utf-8'))
                    assert report['iso_payload_verified'], report
                    w.tabs.setCurrentIndex(5)
                    w.image_tools.check.click()
                    wait(lambda: w.image_tools.worker and not w.image_tools.worker.isRunning(), 'ISO metadata')
                    assert w.image_tools.verified, w.image_tools.report.toPlainText()
                    with mounted_iso(target) as folder:
                        inspect(folder, name)
                else:
                    inspect(target, name)
                checks[name] = True
                print('PASS', name, flush=True)
                return target
            def inspect(folder, name):
                streams = sorted((folder / 'BDMV/STREAM').glob('*.m2ts'))
                assert len(streams) == (2 if name.startswith('collection') else 1)
                for stream in streams:
                    subprocess.run([args.ffmpeg, '-v', 'error', '-xerror', '-i', str(stream), '-map', '0:v:0', '-map', '0:a', '-f', 'null', '-'], check=True, capture_output=True)
                with zipfile.ZipFile(folder / 'BDMV/JAR/00000.jar') as jar:
                    props = jar.read('menu.properties').decode('ascii')
                    assert 'play:1' in props and 'audio' in props
                    if name.startswith('collection'):
                        data = json.loads(jar.read('discforge-collection.json'))
                        assert data['titles'] == ['Второй — переименован', 'Первый фильм'], data['titles']
                        assert 'movie.0.audio.count=1' in props and 'movie.1.audio.count=2' in props
                        assert 'movie.1.subtitle.count=1' in props
            build('single-iso')
            scan([second]); w.disc_mode.setCurrentIndex(0)
            w.movies.setCurrentRow(1); w.movie_title_edit.setText('Второй — переименован'); w.movie_title_edit.textEdited.emit(w.movie_title_edit.text())
            w.movie_up.click()
            assert w.movies.item(0).data(Qt.UserRole) == str(second)
            roundtrip('collection-project')
            checks['collection_project_roundtrip_order_titles'] = True
            build('collection-iso')
            build('collection-bdmv', False)
            (output / 'journey.json').write_text(json.dumps(dict(passed=True, platform=sys.platform, checks=checks, results=results, physical_disc_tested=False), ensure_ascii=False, indent=2), encoding='utf-8')
            print('PASS', json.dumps(checks), flush=True)
        finally:
            for thread in (w.scan_thread, w.build_thread):
                if thread and thread.isRunning():
                    thread.cancel_event.set(); thread.wait(30000)
            w.capacity_clock.stop(); w.build_clock.stop(); w.gpu_monitor.stop(); w.hide()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('ffmpeg', 'ffprobe', 'tsmuxer', 'output'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--java', help='Also exercise both ISOs through actual libVLC')
    args = parser.parse_args()
    run(args)
    if args.java:
        report = json.loads((Path(args.output)/'journey.json').read_text(encoding='utf-8'))
        for name, iso in report['results'].items():
            if iso.endswith('.iso'):
                subprocess.run([sys.executable, str(ROOT/'tools/studio_vlc_journey.py'), '--iso', iso, '--java', args.java, '--log', str(Path(args.output)/('vlc-'+name+'.log'))], check=True)
