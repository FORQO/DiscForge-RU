import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
import zipfile
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPainter, QImage
from disc_menu import MenuDesign, render_page, page_elements, focus_patch, install_menu
from bdmv_fixtures import minimal_mpls


class FocusPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_export_patch_matches_preview_at_edges_and_all_shapes(self):
        for shape in ('rectangle', 'rounded', 'pill'):
            # The serialized names are validated by MenuDesign.
            for rect in ([150, 400, 760, 100], [0, 0, 760, 100], [1160, 980, 760, 100]):
                design = MenuDesign('Movie', layout={'play': rect},
                                    button_styles={'play': {'shape': shape, 'highlight': '#ff00dd'}})
                base = render_page(design, [0])
                element = next(e for e in page_elements(design, [0], 0) if e['button'])
                patch, x, y = focus_patch(base, element)
                composite = base.copy()
                painter = QPainter(composite)
                painter.drawImage(x, y, patch)
                painter.end()
                self.assertEqual(composite, render_page(design, [0], focus=0), (shape, rect))
                self.assertLess(patch.width(), 800)

    def test_jar_contains_focus_resource_and_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            playlist = root / 'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True)
            playlist.write_bytes(minimal_mpls())
            design = MenuDesign('Movie', button_styles={'play': {'shape': 'pill', 'highlight': '#ff00dd'}})
            install_menu(root, design, [0])
            with zipfile.ZipFile(root / 'BDMV/JAR/00000.jar') as jar:
                self.assertIn(b'page.0.0.focus=focus0_0.png', jar.read('menu.properties'))
                image = QImage.fromData(jar.read('focus0_0.png'))
                self.assertFalse(image.isNull())
                self.assertFalse(image.hasAlphaChannel())
                expected, _, _ = focus_patch(render_page(design, [0]),
                                            next(e for e in page_elements(design, [0], 0) if e['button']))
                self.assertEqual(image.convertToFormat(expected.format()), expected)
