import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import threading
import unittest
from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from core import parse_probe, write_meta
from subtitles import (embedded_tracks, select_subtitles, external_track, validate_selection,
                       normalize_srt, prepare_subtitles, menu_subtitles, meta_lines)
from disc_audio import playlist_tracks, playlist_subtitles, bind_subtitles
from disc_menu import (MenuDesign, pages, resolve_action, install_menu, page_elements,
                       validate_menu_navigation, subtitle_page_start)
from bdmv_fixtures import audio_mpls
from iso_menu_update import open_menu


def media():
    return parse_probe('movie.mkv', dict(format=dict(duration='60', size='100'), streams=[
        dict(index=0, codec_type='video', codec_name='h264'),
        dict(index=4, codec_type='subtitle', codec_name='subrip', tags=dict(language='rus', title='Русский')),
        dict(index=7, codec_type='subtitle', codec_name='hdmv_pgs_subtitle', tags=dict(language='eng')),
        dict(index=9, codec_type='subtitle', codec_name='dvd_subtitle', tags=dict(language='fra'))]))


class SubtitleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_probe_keeps_global_indices_and_no_implicit_selection(self):
        info = media()
        self.assertEqual([t['index'] for t in embedded_tracks(info)], [4, 7, 9])
        self.assertEqual(info.selected_subtitles, ())
        info = select_subtitles(info, [embedded_tracks(info)[1]])
        self.assertEqual(menu_subtitles(info), [dict(language='eng', title='')])
        with self.assertRaises(ValueError):
            select_subtitles(info, [dict(embedded_tracks(info)[0], index=5)])
        with self.assertRaises(ValueError):
            select_subtitles(info, [embedded_tracks(info)[2]])

    def test_selection_bounds_duplicates_and_corrupt_project(self):
        t = embedded_tracks(media())[0]
        for items in [[t, t], [dict(t, language='ru')], [dict(t, title='bad\nline')],
                      [dict(t, codec=[])], [dict(t, index=True)], [dict(t, path='x')], [t]*33]:
            with self.subTest(items=items), self.assertRaises(ValueError):
                validate_selection(items)
        srt = external_track(self.root/'русский.srt')
        self.assertEqual(srt['language'], 'rus')
        with self.assertRaises(ValueError):
            validate_selection([srt, srt])

    def test_srt_unicode_legacy_encoding_clips_and_sorts(self):
        srt = self.root/'source.srt'
        out = self.root/'normalized.srt'
        text = '2\r\n00:00:50,000 --> 00:01:10,000\r\nФинал\r\n\r\n1\r\n00:00:01,200 --> 00:00:03,400\r\nПривет\r\n'
        for encoding in ['utf-8-sig', 'utf-16', 'utf-32', 'cp1251']:
            srt.write_bytes(text.encode(encoding))
            normalize_srt(srt, out, 60)
            actual = out.read_text('utf-8-sig')
            self.assertTrue(out.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertTrue(actual.endswith('\n\n'))
            self.assertTrue(actual.startswith('1\n00:00:01,200'))
            self.assertIn('00:01:00,000\nФинал', actual)
            self.assertIn('Привет', actual)

    def test_invalid_srt_fails_before_video_work(self):
        srt = self.root/'bad.srt'
        for text in ['', '1\nnot a timing\nText', '1\n00:61:00,000 --> 00:62:00,000\nText',
                     '1\n00:00:03,000 --> 00:00:02,000\nText']:
            srt.write_text(text, encoding='utf-8')
            info = select_subtitles(media(), [external_track(srt)])
            runner = Mock()
            with self.assertRaises(ValueError):
                prepare_subtitles(info, self.root, 'ffmpeg', threading.Event(), lambda _: None, runner)
            runner.assert_not_called()

    def test_extracts_selected_global_indices_in_one_command(self):
        info = select_subtitles(media(), embedded_tracks(media())[:2])
        calls = []
        def runner(command, *args):
            calls.append(command)
            (self.root/'subtitle_raw_0.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nTest', encoding='utf-8')
            (self.root/'subtitle_raw_1.sup').write_bytes(b'PGfixture')
        outputs = prepare_subtitles(info, self.root, 'ffmpeg', threading.Event(), lambda _: None, runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual([calls[0][i+1] for i,x in enumerate(calls[0]) if x=='-map'], ['0:4', '0:7'])
        self.assertEqual(len(outputs), 2)
        meta = self.root/'disc.meta'
        write_meta(meta, self.root/'video.h264', None, [0], subtitles=outputs, width=1280, height=720, fps='59.94')
        text = meta.read_text('utf-8')
        self.assertIn('S_TEXT/UTF8', text)
        self.assertIn('S_HDMV/PGS', text)
        self.assertIn('lang=rus, fps=59.94, video-width=1280, video-height=720', text)
        self.assertNotIn('default=', text)

    def test_macos_text_subtitles_use_existing_bundled_font(self):
        with patch('subtitles.sys.platform', 'darwin'):
            text = meta_lines([(self.root/'captions.srt', {'language': 'rus'})], '50', 1280, 720)
        font = text.split('font-name="', 1)[1].split('"', 1)[0]
        self.assertTrue(Path(font).is_file())
        self.assertTrue(font.endswith('/assets/fonts/NotoSans-Regular.ttf'))

    def test_frozen_macos_font_path_and_missing_resource(self):
        bundle = self.root / 'Studio пакет'
        font = bundle / 'assets/fonts/NotoSans-Regular.ttf'
        font.parent.mkdir(parents=True)
        font.write_bytes(b'font-fixture')
        with patch('subtitles.sys.platform', 'darwin'), patch('subtitles.sys._MEIPASS', str(bundle), create=True):
            text = meta_lines([(self.root/'captions.srt', {'language': 'rus'})], '50', 1280, 720)
            self.assertIn('font-name="' + font.resolve().as_posix() + '"', text)
            font.unlink()
            with self.assertRaisesRegex(ValueError, 'шрифт'):
                meta_lines([(self.root/'captions.srt', {'language': 'rus'})], '50', 1280, 720)
            # PGS already contains pixels and must not require a font.
            self.assertNotIn('font-name', meta_lines([(self.root/'captions.sup', {'language': 'rus'})], '50', 1280, 720))

    def test_pgs_header_checked(self):
        path = self.root/'fake.sup'
        path.write_bytes(b'notPGS')
        with self.assertRaisesRegex(ValueError, 'PGS'):
            prepare_subtitles(select_subtitles(media(), [external_track(path)]), self.root, 'ffmpeg',
                              threading.Event(), lambda _: None, Mock())

    def test_playlist_inventory_and_menu_roundtrip(self):
        playlist = self.root/'BDMV/PLAYLIST/00000.mpls'
        playlist.parent.mkdir(parents=True)
        playlist.write_bytes(audio_mpls(['rus'], ['rus','eng']))
        self.assertEqual(len(playlist_tracks(playlist)), 1)
        self.assertEqual([t['pid'] for t in playlist_subtitles(playlist)], [0x1200, 0x1201])
        design = bind_subtitles(MenuDesign('Film'), playlist)
        install_menu(self.root, design, [0])
        _, props, restored = open_menu(self.root)
        self.assertEqual(restored.subtitle_tracks, design.subtitle_tracks)
        self.assertEqual(props['subtitle.count'], '2')
        self.assertEqual(props['page.1.0'], 'sub:0')
        self.assertEqual(props['page.1.2'], 'sub:2')
        with self.assertRaises(ValueError):
            bind_subtitles(design, playlist, [dict(language='fra',title='')])

    def test_32_subtitles_with_audio_and_999_chapters_reachable(self):
        tracks = [dict(language='eng', title=str(i)) for i in range(32)]
        design = MenuDesign('Film', audio_tracks=tracks, subtitle_tracks=tracks)
        chapters = list(range(999))
        graph = pages(chapters, design)
        pending, seen, selected = [0], set(), set()
        while pending:
            p = pending.pop()
            if p in seen: continue
            seen.add(p)
            for _, action in graph[p]:
                kind, value = resolve_action(design, action).split(':')
                if kind=='page': pending.append(int(value))
                if kind=='sub': selected.add(int(value))
        self.assertEqual(selected, set(range(33)))
        self.assertEqual(len(seen), len(graph))
        validate_menu_navigation(chapters, design)
        elements = page_elements(design, chapters, subtitle_page_start(chapters, design))
        self.assertEqual(next(e for e in elements if e['id']=='text:chapter_heading')['key'], 'subtitles')
        self.assertEqual(next(e for e in elements if e['id']=='sub:0')['shown'], 'Выключить')

    def test_preview_subtitles_and_labels_editable(self):
        from menu_editor import MenuEditor
        editor = MenuEditor()
        self.addCleanup(editor.close)
        editor.set_subtitles(select_subtitles(media(), embedded_tracks(media())[:2]))
        editor.focus = 1
        editor.press('ok')
        self.assertEqual(editor.page, 1)
        editor.inline_text('sub:1', 'Перевод')
        self.assertEqual(pages([0], editor.current_design(preview=True))[1][1][0], 'Перевод')
        editor.focus = 0
        editor.press('ok')
        self.assertIn('выключены', editor.caption.text())

    def test_panel_disables_unsupported_and_restores_selection(self):
        from subtitle_panel import SubtitlePanel
        panel = SubtitlePanel()
        self.addCleanup(panel.close)
        info = select_subtitles(media(), [embedded_tracks(media())[1]])
        panel.set_media(info)
        self.assertEqual(panel.tracks.item(1).checkState(), Qt.Checked)
        self.assertFalse(panel.tracks.item(2).flags() & Qt.ItemIsEnabled)
        result = []
        panel.selection_changed.connect(result.append)
        panel.tracks.item(1).setCheckState(Qt.Unchecked)
        self.assertEqual(result[-1], [])

    def test_brand_theme_and_project_subtitles(self):
        from main import Window, interface_style, STYLE
        with patch('main.load_settings', return_value={'ui_theme':'unknown'}), patch('main.save_settings'):
            window = Window()
            try:
                self.assertEqual(window.ui_theme, 'graphite')
                self.assertFalse(window.windowIcon().isNull())
                self.assertEqual(interface_style('blue'), STYLE)
                window.set_theme('blue', save=True)
                self.assertEqual(window._config()['ui_theme'], 'blue')
                self.assertEqual(window.tabs.count(), 6)
                selection = [external_track(self.root/'file.srt')]
                window.subtitle_selections['movie.mkv'] = selection
                self.assertEqual(window._project_data()['subtitles']['movie.mkv'], selection)
            finally:
                window.close()
