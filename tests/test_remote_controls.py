import unittest
from pathlib import Path
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QPushButton
from menu_editor import MenuEditor
from fullscreen_preview import FullscreenPreview
from disc_menu import pages


class RemoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_arrows_move_focus_without_opening_button(self):
        editor = MenuEditor()
        try:
            editor.chapters = [0, 60, 120]
            editor.refresh()
            count = len(pages(editor.chapters, editor.current_design(preview=True))[0])
            self.assertGreater(count, 1)
            editor.remote_buttons['right'].click()
            self.assertEqual((editor.page, editor.focus), (0, 0))
            editor.remote_buttons['down'].click()
            self.assertEqual(editor.focus, 1)
            editor.remote_buttons['left'].click()
            self.assertEqual(editor.focus, 1)
            editor.preview.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
            self.assertEqual(editor.focus, 0)
            editor.press('unexpected')
            self.assertEqual(editor.page, 0)
            editor.resize(1300, 800)
            editor.show()
            self.app.processEvents()
            home = editor.remote_buttons['home']
            self.assertLess(editor.fullscreen_button.x()-(home.x()+home.width()), 20)
        finally:
            editor.close()
            editor.deleteLater()

    def test_fullscreen_has_all_four_controls_and_same_keyboard_mapping(self):
        editor = MenuEditor()
        editor.chapters = [0, 60]
        editor.refresh()
        fullscreen = FullscreenPreview(editor)
        try:
            labels = {b.text() for b in fullscreen.findChildren(QPushButton)}
            self.assertTrue({'Влево', 'Вправо', 'Вверх', 'Вниз'} <= labels)
            fullscreen.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))
            self.assertEqual(editor.focus, 1)
            fullscreen.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))
            self.assertEqual(editor.focus, 0)
            source = (Path(__file__).resolve().parents[1]/'bdj/DiscForgeXlet.java').read_text(encoding='utf-8')
            self.assertIn('keys.addAllArrowKeys()', source)
            for direction in ('up', 'down', 'left', 'right'):
                self.assertIn('queue("focus:'+direction+'")', source)
            self.assertIn('moveFocus(action.substring(6))', source)
        finally:
            fullscreen.close()
            editor.deleteLater()


if __name__ == '__main__':
    unittest.main()
