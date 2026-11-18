import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from studio_locale import configure_language, resolve_language, tr
from studio_strings_en import MESSAGES


class StudioLanguageTests(unittest.TestCase):
    def test_system_language_and_explicit_preference(self):
        self.assertEqual(resolve_language('auto', 'ru-RU'), 'ru')
        self.assertEqual(resolve_language('auto', 'en_GB'), 'en')
        self.assertEqual(resolve_language('unknown', 'de-DE'), 'en')
        self.assertEqual(resolve_language('en', 'ru-RU'), 'en')
        self.assertEqual(resolve_language('ru', 'en-US'), 'ru')

    def test_catalog_preserves_format_arguments_and_file_filters(self):
        import re
        from collections import Counter
        for source, translation in MESSAGES.items():
            with self.subTest(source=source):
                self.assertEqual(Counter(re.findall(r'\{\d+\}', source)),
                                 Counter(re.findall(r'\{\d+\}', translation)))
                self.assertEqual(source.count(';;'), translation.count(';;'))
                self.assertTrue(translation.strip())

    def test_language_selection_preserves_authored_titles_and_disc_language(self):
        from PySide6.QtWidgets import QApplication
        from disc_audio import track_label
        from disc_menu import MenuDesign
        from main import Window
        import settings
        app = QApplication.instance() or QApplication([])
        configure_language(app, 'en')
        self.addCleanup(configure_language, app, 'ru')
        with TemporaryDirectory() as folder, patch.object(settings, 'CFG', Path(folder) / 'settings.json'):
            window = Window()
            self.addCleanup(window.capacity_clock.stop)
            self.assertEqual([window.tabs.tabText(i) for i in range(6)],
                             ['Prepare', 'Audio', 'Subtitles', 'Chapters', 'Menu', 'Verify ISO'])
            self.assertEqual(window.menu_editor.current_design().language, 'en')
            title = 'Встреча {0} / Night'
            window.menu_editor.apply_design(MenuDesign(title, language='ru', labels={'ru:play': 'Моя кнопка'}))
            self.assertEqual(tr('Фильм: {0}\n{1}\nНачало сцены: {2}', title, 'X', '0'),
                             MESSAGES['Фильм: {0}\n{1}\nНачало сцены: {2}'].format(title, 'X', '0'))
            with patch('main.QMessageBox.information'):
                window.language_actions['ru'].trigger()
            self.assertEqual(settings.load()['ui_language'], 'ru')
            self.assertEqual(window.menu_editor.current_design().title, title)
            self.assertEqual(window.menu_editor.current_design().text('play'), 'Моя кнопка')
            self.assertIn('Русский', track_label({'language':'rus', 'title':title}, 1, 'ru'))
            configure_language(app, settings.load()['ui_language'])
            self.assertEqual(tr('Подготовка'), 'Подготовка')
            window.hide()
