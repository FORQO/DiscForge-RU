import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import QScreen
from PySide6.QtTest import QTest, QSignalSpy
from menu_editor import MenuEditor
from fullscreen_preview import FullscreenPreview


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

    def test_controls_stay_inside_work_area_on_each_taskbar_edge(self):
        editor = MenuEditor()
        dialog = FullscreenPreview(editor)
        dialog.setWindowFlag(Qt.FramelessWindowHint)
        try:
            dialog.show()
            for monitor in (QRect(0, 0, 1280, 720), QRect(-1920, 120, 1920, 1080),
                            QRect(2560, 0, 1024, 768), QRect(0, 0, 400, 400),
                            QRect(0, 0, 1280, 720)):
                for insets in ((0, 0, 0, 48), (0, 48, 0, 0),
                               (64, 0, 0, 0), (0, 0, 64, 0)):
                    with self.subTest(monitor=monitor, insets=insets):
                        left, top, right, bottom = insets
                        available = monitor.adjusted(left, top, -right, -bottom)
                        with patch.object(QScreen, 'availableGeometry', return_value=available):
                            dialog.setGeometry(monitor)
                            dialog.screen().availableGeometryChanged.emit(available)
                            self.app.processEvents()
                            buttons = dialog.findChildren(QPushButton)
                            self.assertEqual(len(buttons), 8)
                            for button in buttons:
                                bounds = QRect(button.mapToGlobal(QPoint()), button.size())
                                self.assertTrue(button.isVisible())
                                self.assertTrue(available.contains(bounds),
                                                f'{button.text()}: {bounds} outside {available}')
                            image = dialog.image_rect()
                            self.assertTrue(available.contains(QRect(
                                dialog.mapToGlobal(image.topLeft()), image.size())))
                            self.assertFalse(image.intersects(dialog.bar.geometry()))
                            self.assertLessEqual(abs(image.width() * 9 - image.height() * 16), 16)
        finally:
            dialog.close()
            editor.close()

    def test_taskbar_height_change_keeps_controls_visible(self):
        editor = MenuEditor()
        dialog = FullscreenPreview(editor)
        dialog.setWindowFlag(Qt.FramelessWindowHint)
        monitor = QRect(0, 0, 1280, 720)
        try:
            with patch.object(QScreen, 'availableGeometry', return_value=monitor) as work_area:
                dialog.show()
                dialog.setGeometry(monitor)
                self.app.processEvents()
                for taskbar_height in (48, 96, 0):
                    available = monitor.adjusted(0, 0, 0, -taskbar_height)
                    work_area.return_value = available
                    dialog.screen().availableGeometryChanged.emit(available)
                    self.app.processEvents()
                    for button in dialog.findChildren(QPushButton):
                        self.assertTrue(available.contains(QRect(
                            button.mapToGlobal(QPoint()), button.size())), button.text())
        finally:
            dialog.close()
            editor.close()
