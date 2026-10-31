import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtWidgets import QApplication, QDialog, QListWidgetItem
from PySide6.QtTest import QTest
from main import Window
from studio_ui import ExportDialog
from test_disc_capacity import film
from dataclasses import replace


class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = patch('main.load_settings', return_value={})
        self.settings.start()
        self.w = Window()
        self.w.set_theme('graphite')
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        self.w.capacity_clock.stop()
        self.w.hide()
        self.w.deleteLater()
        self.app.processEvents()
        self.settings.stop()

    def populate(self):
        path = str(Path('studio-test.mkv').resolve())
        item = QListWidgetItem('Фильм'); item.setData(Qt.UserRole, path)
        self.w.movies.addItem(item)
        self.w._scanned(path, replace(film(), path=path), '')
        self.w.movies.setCurrentRow(0)
        self.w._refresh_buttons()
        return path

    def test_empty_project_and_iso_tools_are_reachable(self):
        self.assertEqual(self.w.studio_stack.currentIndex(), 1)
        self.assertFalse(self.w.log_panel.isVisible())
        self.assertFalse(self.w.progress.isVisible())
        self.w.tabs.setCurrentIndex(5)
        self.assertEqual(self.w.studio_stack.currentIndex(), 0)
        self.w.tabs.setCurrentIndex(0)
        self.w._open_empty_menu()
        self.assertEqual(self.w.studio_stack.currentIndex(), 0)
        self.assertEqual(self.w.tabs.currentWidget(), self.w.menu_editor)

    def test_canvas_remote_and_footer_do_not_overlap_at_supported_sizes(self):
        self.populate()
        self.w.tabs.setCurrentWidget(self.w.menu_editor)
        editor = self.w.menu_editor
        for width, height in [(1380, 850), (1040, 680)]:
            for preview in [False, True]:
                with self.subTest(width=width, preview=preview):
                    self.w.resize(width, height)
                    editor.preview_mode.setChecked(preview)
                    self.app.processEvents()
                    self.assertEqual(self.w.width(), width)
                    self.assertEqual(self.w.height(), height)
                    canvas = QRect(editor.preview.mapTo(self.w, QPoint()), editor.preview.size())
                    controls = QRect(editor.preview_mode.mapTo(self.w, QPoint()), editor.preview_mode.size())
                    self.assertFalse(canvas.intersects(controls))
                    self.assertGreaterEqual(editor.preview.target_rect().width(), 450)
                    self.assertEqual(editor.inspector.isVisible(), not preview)
                    self.assertEqual(editor.remote_panel.isVisible(), preview)
                    if preview:
                        remote = QRect(editor.remote_panel.mapTo(self.w, QPoint()), editor.remote_panel.size())
                        self.assertFalse(canvas.intersects(remote))
                    self.assertLessEqual(self.w.build_button.mapTo(self.w, self.w.build_button.rect().bottomRight()).x(), width)

    def test_selection_opens_properties_and_preview_cannot_edit_text(self):
        self.populate()
        self.w.tabs.setCurrentWidget(self.w.menu_editor)
        editor = self.w.menu_editor
        editor.preview_selection('text:title')
        self.assertEqual(editor.inspector_tabs.currentIndex(), 1)
        self.assertFalse(editor.action_selector.isVisible())
        before = editor.title.text()
        editor.preview_mode.setChecked(True)
        self.app.processEvents()
        title = next(e for e in editor.preview.elements if e['id'] == 'text:title')
        QTest.mouseDClick(editor.preview, Qt.LeftButton, pos=editor.preview.display_rect(title['rect']).center())
        self.assertIsNone(editor.preview.edit_key)
        self.assertEqual(editor.title.text(), before)
        editor.preview_mode.setChecked(False)
        self.assertFalse(editor.preview.view_only)

    def test_export_cancel_preserves_settings_and_accept_transfers_values(self):
        self.populate()
        self.w.output_folder.setText('/original')
        with patch.object(ExportDialog, 'exec', return_value=QDialog.Rejected), patch.object(self.w, 'start_build') as start:
            self.w.show_export_dialog()
            start.assert_not_called()
        self.assertEqual(self.w.output_folder.text(), '/original')
        def confirm(dialog):
            dialog.folder.setText('/new destination')
            dialog.profile.setCurrentText('BD50')
            dialog.mode.setCurrentIndex(1)
            return QDialog.Accepted
        with patch.object(ExportDialog, 'exec', new=confirm), patch.object(self.w, 'start_build') as start:
            self.w.show_export_dialog()
            start.assert_called_once_with(confirmed=True)
        self.assertEqual(self.w.output_folder.text(), '/new destination')
        self.assertEqual(self.w._config()['output'], '/new destination')
        self.assertEqual(self.w.profile.currentText(), 'BD50')
        self.assertEqual(self.w.output_mode.currentIndex(), 1)

    def test_log_opens_on_errors_and_toggle_remains_available(self):
        self.w.log_toggle.setChecked(True)
        self.assertTrue(self.w.log_panel.isVisible())
        self.w.log_toggle.setChecked(False)
        self.w._message('ОШИБКА: тест')
        self.assertTrue(self.w.log_panel.isVisible())
        self.assertIn('тест', self.w.log.toPlainText())


if __name__ == '__main__':
    unittest.main()
