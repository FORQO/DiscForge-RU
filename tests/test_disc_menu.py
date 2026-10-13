"""Menu graph, packaged runtime and chapter binding regressions."""
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import hashlib

from disc_menu import MenuDesign, bdjo_bytes, index_bytes, pages, install_menu
from bdmv_fixtures import minimal_mpls


class MenuGraphTests(unittest.TestCase):
    def test_every_chapter_is_reachable_and_every_page_has_home(self):
        for count in (1, 2, 6, 7, 13, 999):
            graph = pages(list(range(count)))
            reached, played, pending = set(), set(), [0]
            while pending:
                page = pending.pop()
                if page in reached:
                    continue
                reached.add(page)
                self.assertLessEqual(len(graph[page]), 8)
                for _, action in graph[page]:
                    kind, value = action.split(':')
                    if kind == 'page':
                        self.assertLess(int(value), len(graph))
                        pending.append(int(value))
                    else:
                        played.add(int(value))
                if page:
                    self.assertIn('page:0', [action for _, action in graph[page]])
            self.assertEqual(reached, set(range(len(graph))))
            self.assertEqual(played, set(range(count)))

    def test_chapter_navigation_has_previous_and_next_without_wrapping(self):
        # Middle pages need five chapter buttons + Previous + Next + Home.
        for count in (2, 5, 6, 7, 10, 11, 13, 999):
            with self.subTest(chapters=count):
                graph = pages(list(range(count)))
                scene_pages = graph[1:]
                self.assertEqual(len(scene_pages), (count + 4) // 5)
                for page_number, entries in enumerate(scene_pages, 1):
                    actions = [action for _, action in entries]
                    self.assertLessEqual(len(actions), 8)
                    self.assertEqual(actions[-1], 'page:0')
                    previous = f'page:{page_number - 1}'
                    following = f'page:{page_number + 1}'
                    self.assertEqual(previous in actions, page_number > 1)
                    self.assertEqual(following in actions, page_number < len(scene_pages))
                    chapter_actions = [a for a in actions if a.startswith('play:')]
                    start = (page_number - 1) * 5
                    self.assertEqual(chapter_actions,
                                     [f'play:{i}' for i in range(start, min(start + 5, count))])

    def test_navigation_backwards_reaches_first_scene(self):
        graph = pages(list(range(999)))
        page = len(graph) - 1
        visited = set()
        while page > 1:
            self.assertNotIn(page, visited)
            visited.add(page)
            choices = [action for _, action in graph[page]]
            page = int(next(a.split(':')[1] for a in choices
                            if a == f'page:{page - 1}'))
        self.assertEqual(page, 1)
        self.assertEqual(len(visited), len(graph) - 2)

    def test_invalid_design_is_rejected(self):
        for title, theme in [('', 'midnight'), ('x'*81, 'midnight'), ('a\nb', 'cinema'), ('ok', 'bad')]:
            with self.assertRaises(ValueError):
                MenuDesign(title, theme)

    def test_index_first_play_top_menu_and_title_reference_the_xlet(self):
        data = index_bytes()
        offset = struct.unpack_from('>I', data, 8)[0]
        self.assertEqual(data[:8], b'INDX0200')
        self.assertEqual(struct.unpack_from('>I', data, offset)[0], len(data)-offset-4)
        for start in (offset+4, offset+16, offset+30):
            self.assertEqual(data[start] >> 6, 2)
            self.assertEqual(data[start+6:start+11], b'00000')

    def test_bdjo_section_boundaries_and_runtime_reference(self):
        data = bdjo_bytes()
        offsets = struct.unpack_from('>6I', data, 8)
        self.assertEqual(offsets[0], 48)
        for start, end in zip(offsets[:4], offsets[1:5]):
            self.assertEqual(start + 4 + struct.unpack_from('>I', data, start)[0], end)
        self.assertIn(b'DiscForgeXlet', data)
        self.assertIn(b'00000', data)

    def test_packaged_runtime_targets_java_13_and_contains_no_api_stubs(self):
        with zipfile.ZipFile(Path(__file__).resolve().parents[1] / 'assets/bdj/menu.jar') as jar:
            classes = [n for n in jar.namelist() if n.endswith('.class')]
            self.assertEqual(classes, ['DiscForgeXlet.class'])
            for name in classes:
                data = jar.read(name)
                self.assertEqual(data[:4], b'\xca\xfe\xba\xbe')
                self.assertEqual(struct.unpack_from('>H', data, 6)[0], 47)
            source = (Path(__file__).resolve().parents[1] / 'bdj/DiscForgeXlet.java').read_bytes().replace(b'\r\n', b'\n')
            digest = hashlib.sha256(source).hexdigest().encode('ascii')
            self.assertIn(digest, jar.read('META-INF/MANIFEST.MF').replace(b'\r\n ', b''))


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'Qt renderer is not installed')
class MenuBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_bundle_has_images_runtime_and_matching_backups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            playlist = root / 'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True)
            playlist.write_bytes(minimal_mpls())
            install_menu(root, MenuDesign('Тестовое меню'), [0.0])
            for name in ('BDJO/00000.bdjo', 'JAR/00000.jar', 'index.bdmv'):
                self.assertEqual((root/'BDMV'/name).read_bytes(), (root/'BDMV/BACKUP'/name).read_bytes())
            with zipfile.ZipFile(root/'BDMV/JAR/00000.jar') as jar:
                self.assertTrue(jar.read('page0.png').startswith(b'\x89PNG'))
                self.assertIn(b'page.0.0=play:0', jar.read('menu.properties'))
                self.assertIn('DiscForgeXlet.class', jar.namelist())

    def test_menu_rejects_chapters_missing_from_authored_playlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            playlist = root/'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True)
            playlist.write_bytes(minimal_mpls())
            with self.assertRaisesRegex(ValueError, 'Количество глав'):
                install_menu(root, MenuDesign('Тест'), [0, 4])
            self.assertFalse((root/'BDMV/JAR').exists())

    def test_preview_remote_opens_chapters_and_returns_home(self):
        from menu_editor import MenuEditor
        editor = MenuEditor()
        editor.set_chapters([0, 60, 120])
        editor.press('down')
        editor.press('enter')
        self.assertEqual(editor.page, 1)
        editor.press('down')
        editor.press('enter')
        self.assertIn('главы 2', editor.caption.text())
        editor.press('home')
        self.assertEqual((editor.page, editor.focus), (0, 0))
        editor.close()
