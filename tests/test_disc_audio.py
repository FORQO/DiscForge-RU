import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
import zipfile
from PySide6.QtWidgets import QApplication
from disc_audio import menu_tracks, playlist_tracks, bind_tracks
from disc_menu import MenuDesign, pages, resolve_action, page_elements, install_menu, validate_menu_navigation
from bdmv_fixtures import audio_mpls
from iso_menu_update import open_menu


class DiscAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.playlist = self.root/'BDMV/PLAYLIST/00000.mpls'
        self.playlist.parent.mkdir(parents=True)
        self.playlist.write_bytes(audio_mpls(['rus', 'eng']))

    def test_excluded_and_nonsequential_source_indices_become_disc_order(self):
        info = SimpleNamespace(audio_streams=tuple(dict(index=i, tags=dict(language=lang, title=title))
                               for i, lang, title in [(1, 'rus', 'Дубляж'), (3, 'fra', 'Excluded'), (7, 'eng', 'Original')]),
                               selected_audio=(7, 1))
        tracks = menu_tracks(info)
        design = bind_tracks(MenuDesign('Фильм'), self.playlist, tracks)
        graph = pages([0], design)
        self.assertEqual([a for _, a in graph[1]], ['audio:1', 'audio:2', 'page:0'])
        self.assertIn('Дубляж', graph[1][0][0])
        self.assertIn('Original', graph[1][1][0])
        self.assertNotIn('Excluded', str(graph))
        self.assertEqual(resolve_action(design, graph[0][-1][1]), 'page:1')

    def test_mismatch_blocks_authoring(self):
        with self.assertRaisesRegex(ValueError, 'не совпадают'):
            bind_tracks(MenuDesign('Фильм'), self.playlist, [dict(language='eng', title='Wrong')])

    def test_all_32_tracks_and_999_chapters_reachable(self):
        design = MenuDesign('Film', audio_tracks=[dict(language='eng', title=f'Voice {i}') for i in range(32)])
        chapters = list(range(999))
        graph = pages(chapters, design)
        pending, seen, audio, marks = [0], set(), set(), set()
        while pending:
            p = pending.pop()
            if p in seen: continue
            seen.add(p)
            self.assertLessEqual(len(graph[p]), 8)
            for _, action in graph[p]:
                kind, value = resolve_action(design, action).split(':')
                if kind == 'page': pending.append(int(value))
                elif kind == 'audio': audio.add(int(value))
                else: marks.add(int(value))
        self.assertEqual(audio, set(range(1, 33)))
        self.assertEqual(marks, set(range(999)))
        self.assertEqual(len(seen), len(graph))
        validate_menu_navigation(chapters, design)

    def test_no_audio_no_audio_menu_and_legacy_graph_unchanged(self):
        self.playlist.write_bytes(audio_mpls())
        design = bind_tracks(MenuDesign('Silent'), self.playlist, [])
        self.assertEqual(pages([0], design), [[('Смотреть фильм', 'play:0')]])

    def test_corrupt_stream_envelopes_fail_closed(self):
        original = self.playlist.read_bytes()
        for data in [original[:92], original[:85]+b'\xff\xff'+original[87:], original[:89]+b'\xff'+original[90:]]:
            self.playlist.write_bytes(data)
            with self.assertRaises(ValueError): playlist_tracks(self.playlist)

    def test_audio_labels_styles_and_export_roundtrip(self):
        design = bind_tracks(MenuDesign('Film', labels={'ru:audio:2': 'Авторская озвучка'},
                            button_styles={'audio:2': {'text': '#abcdef'}}, layout={'audio': [150, 612, 760, 82]}), self.playlist)
        elements = page_elements(design, [0], 1)
        self.assertEqual(next(e for e in elements if e['id']=='audio:2')['shown'], 'Авторская озвучка')
        self.assertEqual(next(e for e in elements if e['id']=='text:chapter_heading')['key'], 'audio')
        install_menu(self.root, design, [0])
        _, props, restored = open_menu(self.root)
        self.assertEqual(restored, design)
        self.assertEqual(props['audio.count'], '2')
        self.assertEqual(props['audio.page'], '1')
        self.assertEqual(props['page.1.1'], 'audio:2')
        with zipfile.ZipFile(self.root/'BDMV/JAR/00000.jar') as jar:
            self.assertIn('focus1_1.png', jar.namelist())

    def test_legacy_iso_infers_audio_from_playlist(self):
        install_menu(self.root, MenuDesign('Legacy'), [0])
        _, _, design = open_menu(self.root)
        self.assertEqual([t['language'] for t in design.audio_tracks], ['rus', 'eng'])

    def test_preview_changes_with_selected_source_tracks(self):
        from menu_editor import MenuEditor
        editor = MenuEditor()
        self.addCleanup(editor.close)
        info = SimpleNamespace(audio_streams=(dict(index=5, tags=dict(language='eng', title='Original')),), selected_audio=(5,))
        editor.set_audio(info)
        editor.press('down')
        editor.press('ok')
        self.assertEqual(editor.page, 1)
        editor.press('ok')
        self.assertEqual(editor.page, 0)
        self.assertIn('Original', editor.caption.text())
        info.selected_audio = ()
        editor.set_audio(info)
        self.assertEqual(editor.audio_tracks, [])
