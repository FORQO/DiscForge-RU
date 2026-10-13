import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest, QSignalSpy
from menu_editor import MenuEditor


class FullscreenPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_fullscreen_navigation_and_escape_preserve_editor(self):
        editor=MenuEditor()
        editor.set_chapters([0,4,8])
        editor.show()
        before=editor.design().to_dict()
        preview=editor.preview
        QTest.mouseClick(editor.fullscreen_button,Qt.LeftButton)
        self.app.processEvents()
        dialog=editor.fullscreen
        self.assertTrue(dialog.isFullScreen())
        QTest.keyClick(dialog,Qt.Key_Down)
        QTest.keyClick(dialog,Qt.Key_Return)
        self.assertEqual(editor.page,1)
        done=QSignalSpy(dialog.finished)
        QTest.keyClick(dialog,Qt.Key_Escape)
        self.app.processEvents()
        self.assertEqual(done.count(),1)
        self.assertIs(editor.preview,preview)
        self.assertEqual(editor.design().to_dict(),before)
        editor.close()
