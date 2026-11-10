"""Numeric edits retain precision, restored values, disabled states and undo."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QLocale, QSignalBlocker, QTime
from PySide6.QtWidgets import QApplication, QAbstractSpinBox
from PySide6.QtTest import QTest, QSignalSpy
from numeric_slider import IntegerEdit, DecimalEdit, TimeEdit, SliderValue, step_buttons_hidden


class NumericSliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_integer_updates_both_ways_and_restores_with_signals_blocked(self):
        edit = IntegerEdit(); edit.setRange(0, 100)
        field = SliderValue(edit, 'Заливка')
        changed = QSignalSpy(edit.valueChanged)
        try:
            with QSignalBlocker(edit):
                edit.setValue(42)
            self.assertEqual(field.slider.value(), 42)
            self.assertEqual(changed.count(), 0)
            field.slider.setValue(67)
            self.assertEqual(edit.value(), 67)
            self.assertEqual(changed.count(), 1)
            edit.setMaximum(50)
            self.assertEqual((edit.value(), field.slider.value(), field.slider.maximum()), (50, 50, 50))
            edit.setMinimum(20)
            self.assertEqual(field.slider.minimum(), 20)
        finally:
            field.close()

    def test_decimal_preserves_typed_precision_locale_suffix_and_keyboard_step(self):
        edit = DecimalEdit(); edit.setRange(5, 28); edit.setDecimals(1)
        edit.setSingleStep(.5); edit.setSuffix(' Мбит/с'); edit.setLocale(QLocale('ru_RU'))
        field = SliderValue(edit, 'Битрейт')
        try:
            field.show(); self.app.processEvents()
            edit.lineEdit().setText('17,3 Мбит/с'); edit.interpretText()
            self.assertAlmostEqual(edit.value(), 17.3)
            self.assertEqual(field.slider.value(), 173)
            QTest.keyClick(field.slider, Qt.Key_Right)
            self.assertAlmostEqual(edit.value(), 17.8)
            with QSignalBlocker(edit):
                edit.setValue(6.5)
            self.assertEqual(field.slider.value(), 65)
            self.assertEqual(edit.suffix(), ' Мбит/с')
        finally:
            field.close()

    def test_time_slider_uses_seconds_and_honors_media_duration(self):
        edit = TimeEdit(); edit.setDisplayFormat('HH:mm:ss')
        edit.setMaximumTime(QTime(2, 0))
        field = SliderValue(edit, 'Начало')
        try:
            with QSignalBlocker(edit):
                edit.setTime(QTime(1, 2, 3))
            self.assertEqual(field.slider.value(), 3723)
            field.slider.setValue(304)
            self.assertEqual(edit.time(), QTime(0, 5, 4))
            edit.setMaximumTime(QTime(0, 1))
            self.assertEqual((field.slider.maximum(), field.slider.value()), (60, 60))
        finally:
            field.close()

    def test_disabled_readonly_and_parent_states_apply_to_slider(self):
        edit = IntegerEdit()
        field = SliderValue(edit, 'Размер')
        try:
            edit.setEnabled(False); self.assertFalse(field.slider.isEnabled())
            edit.setEnabled(True); self.assertTrue(field.slider.isEnabled())
            field.setEnabled(False); self.assertFalse(field.slider.isEnabled())
            field.setEnabled(True); self.assertTrue(field.slider.isEnabled())
            edit.setReadOnly(True); self.assertFalse(field.slider.isEnabled())
            edit.setReadOnly(False); self.assertTrue(field.slider.isEnabled())
        finally:
            field.close()

    def test_menu_slider_drag_is_one_undo_and_static_text_hides_button_fields(self):
        from menu_editor import MenuEditor
        editor = MenuEditor()
        try:
            editor.select_button('play')
            before = editor.settings()
            count = len(editor._undo)
            slider = editor.font_size_control.slider
            initial = editor.font_size.value()
            slider.setSliderDown(True)
            slider.setValue(initial + 2); slider.setValue(initial + 4)
            slider.setSliderDown(False)
            self.assertEqual(editor.font_size.value(), initial + 4)
            self.assertEqual(len(editor._undo), count + 1)
            editor.undo_edit()
            self.assertEqual(editor.settings(), before)
            editor.select_button('text:title')
            self.assertTrue(editor.border_width_control.isHidden())
            self.assertTrue(editor.opacity_control.isHidden())
            editor.select_button('play')
            self.assertFalse(editor.border_width_control.isHidden())
            self.assertFalse(editor.opacity_control.isHidden())
        finally:
            editor.close()

    def test_all_application_numeric_fields_have_matching_slider_and_no_arrows(self):
        from main import Window
        from quality_dialog import QualityDialog
        from core import MediaInfo
        with patch('main.load_settings', return_value={}):
            window = Window()
        dialog = QualityDialog(MediaInfo('demo.mkv', 60, 1, 'h264', 1920, 1080,
                                         '24/1', '', '', 0, 0, 0), {}, 8_000_000, parent=window)
        try:
            numbers = window.findChildren(QAbstractSpinBox)
            self.assertEqual(len(numbers), 25)
            for edit in numbers:
                self.assertTrue(step_buttons_hidden(edit))
                self.assertIsInstance(edit.parentWidget(), SliderValue)
                self.assertIs(edit.slider_control.number, edit)
            dialog.mode.setCurrentIndex(1)
            self.assertTrue(dialog.start.slider_control.slider.isEnabled())
            dialog.mode.setCurrentIndex(0)
            self.assertFalse(dialog.start.slider_control.slider.isEnabled())
        finally:
            dialog.reject(); window.capacity_clock.stop()
            window.hide(); window.deleteLater(); self.app.processEvents()

    def test_styled_fields_keep_text_visible_without_arrow_hit_areas(self):
        from studio_ui import studio_style
        for theme in ('graphite', 'blue'):
            edit = DecimalEdit(); edit.setRange(5, 28); edit.setDecimals(1)
            edit.setSuffix(' Мбит/с'); edit.setValue(17.3)
            field = SliderValue(edit, 'Битрейт'); field.setStyleSheet(studio_style(theme))
            try:
                field.show(); self.app.processEvents()
                self.assertTrue(step_buttons_hidden(edit))
                self.assertGreater(edit.lineEdit().width(), edit.fontMetrics().horizontalAdvance(edit.text()))
            finally:
                field.close()

    def test_quality_parameters_scroll_instead_of_clipping_controls(self):
        from quality_dialog import QualityDialog
        from core import MediaInfo
        from studio_ui import studio_style
        from PySide6.QtCore import QPoint, QRect
        dialog = QualityDialog(MediaInfo('demo.mkv', 3600, 1, 'h264', 1920, 1080,
                                         '24/1', '', '', 0, 0, 0), {}, 8_000_000)
        try:
            dialog.setStyleSheet(studio_style('graphite'))
            dialog.show(); dialog.resize(900, 700); self.app.processEvents()
            self.assertEqual((dialog.width(), dialog.height()), (900, 700))
            self.assertGreater(dialog.controls_scroll.verticalScrollBar().maximum(), 0)
            for control in dialog.findChildren(SliderValue):
                for child in (control.label, control.number, control.slider):
                    rect = QRect(child.mapTo(control, QPoint()), child.size())
                    self.assertTrue(control.rect().contains(rect), control.label.text())
        finally:
            dialog.reject(); dialog.deleteLater(); self.app.processEvents()
