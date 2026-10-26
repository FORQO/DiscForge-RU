import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from menu_navigation import nearest_button


class SpatialTests(unittest.TestCase):
    def test_grid_ignores_list_order_and_does_not_wrap(self):
        # Bottom right, top left, bottom left, top right.
        rects = [(400, 400, 100, 100), (100, 100, 100, 100),
                 (100, 400, 100, 100), (400, 100, 100, 100)]
        for current, key, target in ((1, 'right', 3), (1, 'down', 2), (0, 'left', 2),
                                     (0, 'up', 3), (3, 'right', 3), (2, 'down', 2)):
            self.assertEqual(nearest_button(rects, current, key), target)

    def test_diagonals_distance_sizes_and_ties(self):
        self.assertEqual(nearest_button([(0, 0, 100, 100), (110, 80, 100, 100),
                                         (300, 0, 100, 100)], 0, 'right'), 1)
        # Compare centres, not top-left coordinates (the wide button extends left).
        self.assertEqual(nearest_button([(100, 0, 100, 100), (0, 0, 600, 100)], 0, 'right'), 1)
        # At equal distance prefer the candidate directly on the requested axis.
        self.assertEqual(nearest_button([(0, 0, 20, 20), (30, 40, 20, 20),
                                         (50, 0, 20, 20)], 0, 'right'), 2)
        self.assertEqual(nearest_button([(0, 50, 20, 20), (50, 0, 20, 20),
                                         (50, 100, 20, 20)], 0, 'right'), 1)

    def test_empty_single_identical_centres_and_invalid_direction(self):
        for rects in ([], [(10, 10, 50, 50)], [(0, 0, 100, 100), (10, 10, 80, 80)]):
            for key in ('up', 'down', 'left', 'right', 'unknown'):
                self.assertEqual(nearest_button(rects, 0, key), 0)


class PreviewSpatialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_moved_hidden_buttons_and_fullscreen_use_live_geometry(self):
        from PySide6.QtGui import QImage, QKeyEvent
        from PySide6.QtCore import Qt, QEvent
        from menu_editor import MenuEditor
        from fullscreen_preview import FullscreenPreview
        from disc_menu import button_id, pages
        with patch('menu_editor.render_page', return_value=QImage(1920, 1080, QImage.Format_RGB32)):
            editor = MenuEditor()
            fullscreen = None
            try:
                editor.set_chapters([0, 60, 120])
                entries = pages(editor.chapters, editor.current_design(preview=True))[0]
                keys = [button_id(0, action) for _, action in entries]
                editor.element_layout[keys[0]] = [100, 400, 200, 80]
                editor.element_layout[keys[1]] = [600, 400, 200, 80]
                editor.refresh()
                editor.press('down')
                self.assertEqual(editor.focus, 0)
                editor.press('right')
                self.assertEqual(editor.focus, 1)
                fullscreen = FullscreenPreview(editor)
                fullscreen.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Left, Qt.NoModifier))
                self.assertEqual(editor.focus, 0)
                editor.element_layout[keys[1]] = [100, 100, 200, 80]
                editor.refresh()
                editor.press('up')
                self.assertEqual(editor.focus, 1)
                editor.hidden_buttons.append(keys[1])
                editor.refresh()
                editor.press('down')
                self.assertEqual(editor.focus, 0)
            finally:
                if fullscreen:
                    fullscreen.close()
                editor.deleteLater()

    def test_collection_exports_geometry_used_by_preview(self):
        from disc_menu import MenuDesign, page_elements
        from collection_menu import catalog_page
        from disc_collection import merge_menu
        design = MenuDesign('Collection')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'source'
            jarpath = source/'BDMV/JAR/00000.jar'
            jarpath.parent.mkdir(parents=True)
            with zipfile.ZipFile(jarpath, 'w') as jar:
                jar.writestr('menu.properties', 'pages=1\naudio.count=0\nsubtitle.count=0\naudio.page=0\nsubtitle.page=0\npage.0.count=0')
                jar.writestr('page0.png', b'fixture')
            # Pixel rendering is already covered separately; test authoring metadata.
            with patch('disc_menu.render_page'), patch('disc_collection._png', return_value=b'fixture'), \
                 patch('disc_menu.focus_patch', return_value=(None, 0, 0)):
                merge_menu(root/'out', [source, source], design, ['First', 'Second'])
            with zipfile.ZipFile(root/'out/BDMV/JAR/00000.jar') as jar:
                props = dict(line.split('=', 1) for line in jar.read('menu.properties').decode().splitlines())
            catalog, entries = catalog_page(design, ['First', 'Second'])
            rects = [e['rect'] for e in page_elements(catalog, [0.], 0, entries) if e['button']]
            exported = [tuple(int(props[f'page.0.{i}.{field}']) for field in ('x', 'y', 'w', 'h')) for i in range(2)]
            self.assertEqual(exported, rects)
            self.assertEqual(nearest_button(exported, 0, 'down'), 1)
            self.assertEqual(nearest_button(exported, 0, 'right'), 0)


if __name__ == '__main__':
    unittest.main()
