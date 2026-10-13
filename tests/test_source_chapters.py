import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from source_chapters import import_chapters
from core import parse_probe, probe_media

DATA = {'format': {'duration': '120', 'size': '123'},
        'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1920, 'height': 1080}],
        'chapters': [{'start_time': '0'}, {'start_time': '10.250'}, {'start_time': '60.125'}]}


class ImportTests(unittest.TestCase):
    def test_probe_requests_chapters_and_keeps_fractional_timestamps(self):
        from types import SimpleNamespace
        with patch('core.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(DATA))) as run:
            info = probe_media('film.mkv')
        self.assertIn('-show_chapters', run.call_args.args[0])
        self.assertEqual(info.source_chapters, (0, 10.25, 60.125))
        self.assertFalse(info.chapter_notices)

    def test_missing_chapters_keep_manual_workflow(self):
        self.assertEqual(import_chapters(None, 120), ((), ()))
        self.assertEqual(import_chapters([], 120), ((), ()))

    def test_sort_deduplicate_and_insert_zero(self):
        times, notices = import_chapters([{'start_time': v} for v in ('60.5','10.25','10.2501')], 120)
        self.assertEqual(times, (0, 10.25, 60.5))
        self.assertFalse(notices)

    def test_time_base_fallback(self):
        self.assertEqual(import_chapters([{'start': 900900, 'time_base':'1/90000'}],120)[0], (0,10.01))

    def test_invalid_metadata_is_reported_without_losing_valid_marks(self):
        bad = [None, {}, {'start_time':True}] + [{'start_time':v} for v in ('nan','inf','-1','120','broken')]
        times, notices = import_chapters(bad + [{'start_time':'2.5'}],120)
        self.assertEqual(times,(0,2.5))
        self.assertIn(str(len(bad)), notices[0])
        self.assertTrue(import_chapters('broken',120)[1])

    def test_limit_never_silently_truncates_source(self):
        times, notices = import_chapters([{'start_time':str(t)} for t in range(1000)], 2000)
        self.assertFalse(times)
        self.assertIn('999', notices[0])


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication, QListWidgetItem
    from PySide6.QtCore import Qt
    from main import Window, chapter_timecode, parse_timecode
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'Qt is not installed')
class ImportedChapterProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('main.load_settings', return_value={}):
            self.w = Window()
        self.path = str(Path('imported.mkv').resolve())
        item = QListWidgetItem('Film')
        item.setData(Qt.UserRole, self.path)
        self.w.movies.addItem(item)
        self.w.movies.setCurrentRow(0)
        self.info = parse_probe(self.path, DATA)

    def tearDown(self):
        with patch('main.save_settings'):
            self.w.close()

    def test_import_menu_edit_and_restore_source(self):
        self.w._scanned(self.path, self.info, '')
        self.assertEqual(self.w.chapters[self.path], [0,10.25,60.125])
        self.assertEqual(self.w.menu_editor.chapters, [0,10.25,60.125])
        self.w.chapter_list.setCurrentRow(1)
        self.assertEqual(self.w.chapter_time.text(), '00:00:10.250')
        self.w.chapter_time.setText('00:00:12.345')
        self.w.edit_chapter_button.click()
        self.assertEqual(self.w.chapters[self.path], [0,12.345,60.125])
        self.w._scanned(self.path, self.info, '')
        self.assertEqual(self.w.chapters[self.path], [0,12.345,60.125])
        self.w.import_chapters_button.click()
        self.assertEqual(self.w.chapters[self.path], [0,10.25,60.125])

    def test_project_roundtrip_preserves_manual_chapters_after_scan(self):
        self.w._scanned(self.path, self.info, '')
        self.w.apply_chapters(self.path, [0,5.375,80])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'film.dfr.json'
            path.write_text(json.dumps(self.w._project_data()),encoding='utf-8')
            with patch('main.QFileDialog.getOpenFileName', return_value=(str(path),'')), patch.object(self.w,'_queue_scan'):
                self.w.open_project()
            self.w._scanned(self.path, self.info, '')
        self.assertEqual(self.w.chapters[self.path], [0,5.375,80])

    def test_two_films_keep_separate_source_timings(self):
        self.w._scanned(self.path,self.info,'')
        other = 'another.mkv'
        data = dict(DATA, chapters=[{'start_time':'25.75'}])
        self.w._scanned(other,parse_probe(other,data),'')
        self.assertEqual(self.w.chapters[self.path],[0,10.25,60.125])
        self.assertEqual(self.w.chapters[other],[0,25.75])

    def test_millisecond_editor_roundtrip(self):
        for value in (0,10.25,60.125,3599.999,7200.001):
            self.assertAlmostEqual(parse_timecode(chapter_timecode(value)),value,places=3)
        for value in ('00:00:nan','00:00:inf','00:00:60.001'):
            with self.assertRaises(ValueError):
                parse_timecode(value)

    def test_chapter_restore_keeps_custom_audio_and_subtitle_links(self):
        from disc_menu import MenuDesign
        editor = self.w.menu_editor
        editor.set_chapters([0,10])
        design = MenuDesign('Film', audio_tracks=[{'language':'rus','title':'Русский'}],
                            subtitle_tracks=[{'language':'eng','title':'English'}],
                            custom_buttons=[{'id':'custom:111111111111','page':0,'action':'page:2'},
                                            {'id':'custom:222222222222','page':3,'action':'page:2'}])
        editor.apply_design(design)
        editor.retime_chapters([0,10], [0,10,20,30,40,50])
        buttons = editor.design().custom_buttons
        self.assertEqual(buttons[0]['action'], 'page:3')
        self.assertEqual(buttons[1]['page'], 4)
        self.assertEqual(buttons[1]['action'], 'page:3')
