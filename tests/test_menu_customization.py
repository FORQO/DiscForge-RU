"""Customization must survive save/load and be baked into the on-disc JAR."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage, QColor
except ImportError:
    QApplication = None
from disc_menu import MenuDesign, import_background, pages, render_page, install_menu
from bdmv_fixtures import minimal_mpls


@unittest.skipIf(QApplication is None, 'Qt renderer is not installed')
class CustomMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_languages_and_edits_preserve_all_navigation_actions(self):
        chapters = list(range(18))
        original = [[action for _, action in page] for page in pages(chapters)]
        for lang in ('ru', 'en'):
            design = MenuDesign('Film / Фильм', language=lang,
                                labels={lang+':play': 'Начать / Start', lang+':chapter:6': 'The chase / Погоня'})
            graph = pages(chapters, design)
            self.assertEqual([[action for _, action in page] for page in graph], original)
            self.assertEqual(graph[0][0][0], 'Начать / Start')
            self.assertIn('The chase / Погоня', graph[2][1][0])
        self.assertEqual(pages([0, 1], MenuDesign('Film', language='en'))[0][1][0], 'Scene selection')

    def test_embedded_background_survives_source_removal_and_reaches_jar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'фон.png'
            image = QImage(300, 600, QImage.Format_RGB32)
            image.fill(QColor('#c02040'))
            image.save(str(source))
            design = MenuDesign('Тест / Test', language='en', background=import_background(str(source)),
                                dim=0, labels={'en:play': 'Запустить / Play'})
            source.unlink()
            data = json.loads(json.dumps(design.to_dict()))
            restored = MenuDesign.from_dict(data['title'], data)
            rendered = render_page(restored, [0])
            self.assertEqual(rendered.pixelColor(1900, 900).name(), '#c02040')
            playlist = root/'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True)
            playlist.write_bytes(minimal_mpls())
            install_menu(root, restored, [0])
            with zipfile.ZipFile(root/'BDMV/JAR/00000.jar') as jar:
                on_disc = QImage.fromData(jar.read('page0.png'))
                # PNG readers may select a different internal QImage format.
                # Compare all displayed pixels, independently of that storage.
                self.assertEqual(on_disc.size(), rendered.size())
                actual = on_disc.convertToFormat(QImage.Format_RGBA8888)
                expected = rendered.convertToFormat(QImage.Format_RGBA8888)
                actual_bytes, expected_bytes = bytes(actual.constBits()), bytes(expected.constBits())
                if actual_bytes != expected_bytes:
                    output = Path('build/test-artifacts/menu-pixels')
                    output.mkdir(parents=True, exist_ok=True)
                    actual.save(str(output/'on-disc.png'))
                    expected.save(str(output/'preview.png'))
                    again = render_page(restored, [0]).convertToFormat(QImage.Format_RGBA8888)
                    again.save(str(output/'repeat.png'))
                    roundtrip = QImage(str(output/'preview.png')).convertToFormat(QImage.Format_RGBA8888)
                    changed = [i for i in range(0, len(actual_bytes), 4)
                               if actual_bytes[i:i+4] != expected_bytes[i:i+4]]
                    points = [(i//4 % actual.width(), i//4 // actual.width()) for i in changed]
                    bounds = (min(x for x, y in points), min(y for x, y in points),
                              max(x for x, y in points), max(y for x, y in points))
                    self.fail(f'On-disc menu differs: {len(changed)} pixels, bounds={bounds}, '
                              f'repeat_matches_disc={bytes(again.constBits()) == actual_bytes}, '
                              f'PNG_roundtrip_matches_preview={bytes(roundtrip.constBits()) == expected_bytes}, '
                              f'samples={[(points[n], list(expected_bytes[i:i+4]), list(actual_bytes[i:i+4])) for n, i in enumerate(changed[:8])]}')
                self.assertIn(b'page.0.0=play:0', jar.read('menu.properties'))

    def test_editor_language_switch_retains_independent_labels_and_reset(self):
        from menu_editor import MenuEditor
        editor = MenuEditor()
        try:
            editor.label_value.setText('Запуск')
            editor.edit_label()
            editor.language.setCurrentIndex(1)
            self.assertEqual(editor.label_value.text(), '')
            editor.label_value.setText('Start')
            editor.edit_label()
            editor.language.setCurrentIndex(0)
            self.assertEqual(editor.label_value.text(), 'Запуск')
            editor.label_value.clear()
            editor.edit_label()
            self.assertEqual(editor.design().text('play'), 'Смотреть фильм')
            self.assertEqual(editor.labels['en:play'], 'Start')
        finally:
            editor.close()

    def test_project_restores_settings_and_new_project_resets_them(self):
        from main import Window
        window = Window()
        try:
            design = MenuDesign('Мой фильм', 'cinema', 'en', dim=65,
                                labels={'en:play': 'Start', 'ru:play': 'Начать'})
            window.menu_editor.apply_design(design)
            with tempfile.TemporaryDirectory() as tmp:
                file = Path(tmp)/'test.dfr.json'
                file.write_text(json.dumps(window._project_data()), encoding='utf-8')
                window.new_project()
                self.assertEqual(window.menu_editor.design().labels, {})
                with patch('main.QFileDialog.getOpenFileName', return_value=(str(file), '')), \
                     patch.object(window, '_queue_scan'), patch('main.QMessageBox.warning') as warning:
                    window.open_project()
                    warning.assert_not_called()
                self.assertEqual(window.menu_editor.design(), design)
                # An invalid background must not discard the current work.
                data = window._project_data()
                data['menu']['background'] = 'broken'
                file.write_text(json.dumps(data), encoding='utf-8')
                with patch('main.QFileDialog.getOpenFileName', return_value=(str(file), '')), \
                     patch('main.QMessageBox.warning') as warning:
                    window.open_project()
                    warning.assert_called_once()
                self.assertEqual(window.menu_editor.design(), design)
        finally:
            with patch('main.save_settings'):
                window.close()

    def test_invalid_customization_rejected(self):
        for kwargs in ({'language': 'de'}, {'dim': 90}, {'labels': {'ru:play': ''}},
                       {'labels': {'ru:play': 'a\nb'}}, {'background': 3}):
            with self.assertRaises(ValueError):
                MenuDesign('Film', **kwargs)

    def test_contain_and_dimming(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'portrait.png'
            image = QImage(100, 200, QImage.Format_RGB32)
            image.fill(QColor('white'))
            image.save(str(path))
            data = import_background(str(path))
            cover = render_page(MenuDesign('Film', background=data, dim=0), [0])
            contain = render_page(MenuDesign('Film', background=data, background_fit='contain', dim=50), [0])
            self.assertEqual(cover.pixelColor(1900, 900).red(), 255)
            self.assertLess(contain.pixelColor(1900, 900).red(), 100)
            self.assertAlmostEqual(contain.pixelColor(960, 900).red(), 127, delta=1)
