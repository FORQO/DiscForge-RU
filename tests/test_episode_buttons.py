"""Episode-list editing gestures and the actual on-disc geometry/resources."""
import copy
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from dataclasses import replace
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from disc_menu import MenuDesign, page_elements, pages, render_page, install_menu, button_style
from episode_buttons import STYLE_PRESETS
from bdmv_fixtures import minimal_mpls
from menu_editor import MenuEditor
from unittest.mock import patch


class EpisodeButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = MenuEditor()
        self.editor.resize(1280, 900)
        self.editor.show(); self.app.processEvents()
        self.editor.set_chapters([0, 10, 20])
        self.panel = self.editor.button_inspector

    def tearDown(self):
        self.editor.close(); self.app.processEvents()

    def grid(self):
        self.editor.add_button(); self.editor.add_button()
        self.panel.scope.setCurrentIndex(1)
        self.panel.numbers['button_width'][1].setValue(500)
        self.panel.numbers['button_height'][1].setValue(80)
        self.panel.mode.setCurrentIndex(self.panel.mode.findData('grid'))
        self.app.processEvents()

    def buttons(self):
        return [e for e in self.editor.preview.elements if e['button']]

    def point(self, rect):
        return self.editor.preview.display_rect(rect).center()

    def test_drag_reorders_across_columns_and_undo_is_one_action(self):
        self.grid()
        buttons = self.buttons(); first, last = buttons[0], buttons[-1]
        before = self.editor.settings()
        undo_count = len(self.editor._undo)
        preview = self.editor.preview
        start, end = self.point(first['rect']), self.point(last['rect'])
        QTest.mousePress(preview, Qt.LeftButton, pos=start)
        QTest.mouseMove(preview, end, 20)
        self.assertTrue(preview.drag['moved'])
        self.assertIsNotNone(preview.drag['ghost'])
        QTest.mouseRelease(preview, Qt.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual(self.buttons()[-1]['id'], first['id'])
        self.assertEqual(len(self.editor._undo), undo_count+1)
        after = self.editor.settings()
        self.editor.undo_edit(); self.assertEqual(self.editor.settings(), before)
        self.editor.redo_edit(); self.assertEqual(self.editor.settings(), after)
        restored = MenuDesign.from_dict('Film', json.loads(json.dumps(after)))
        self.assertEqual(restored.button_editor['order']['0'][-1], first['id'])

    def test_short_click_does_not_move_or_add_history(self):
        self.grid(); old = self.editor.settings(); count = len(self.editor._undo)
        preview = self.editor.preview; pos = self.point(self.buttons()[0]['rect'])
        QTest.mousePress(preview, Qt.LeftButton, pos=pos)
        QTest.mouseMove(preview, pos+QPoint(1, 1))
        QTest.mouseRelease(preview, Qt.LeftButton, pos=pos+QPoint(1, 1))
        self.assertEqual(old, self.editor.settings())
        self.assertEqual(count, len(self.editor._undo))

    def test_multiselect_style_and_independent_size(self):
        self.grid(); self.panel.scope.setCurrentIndex(0)
        preview = self.editor.preview
        for i, element in enumerate(self.buttons()[:2]):
            QTest.mouseClick(preview, Qt.LeftButton, Qt.ControlModifier if i else Qt.NoModifier, self.point(element['rect']))
        keys = set(preview.selected_keys); self.assertEqual(len(keys), 2)
        geometry = copy.deepcopy(self.editor.element_layout)
        self.panel.apply_preset(self.panel.preset.findData('glass'))
        for key in keys:
            self.assertEqual(self.editor.button_styles[key]['variant'], 'glass')
        self.assertEqual(geometry, self.editor.element_layout)
        self.panel.numbers['font_size'][1].setValue(42)
        for key in keys:
            self.assertEqual(self.editor.button_styles[key]['font_size'], 42)

    def test_individual_size_keeps_grid_and_other_resolved_sizes(self):
        self.grid()
        self.panel.scope.setCurrentIndex(0)
        self.editor.preview_selection('play')
        self.panel.numbers['button_width'][1].setValue(400)
        buttons = self.buttons()
        self.assertEqual(buttons[0]['rect'].width(), 400)
        self.assertTrue(all(e['rect'].width() == 500 for e in buttons[1:]))
        self.assertEqual(self.editor.button_editor['pages']['0']['mode'], 'grid')

    def test_oversized_grid_edit_is_atomic(self):
        self.grid(); before = self.editor.settings()
        self.panel.numbers['button_width'][1].setValue(1500)
        self.assertEqual(before, self.editor.settings())
        self.assertTrue('помещаются' in self.editor.caption.text() or 'границы' in self.editor.caption.text())

    def test_free_layout_prevents_collision_and_preview_never_drags(self):
        self.grid()
        self.panel.mode.setCurrentIndex(self.panel.mode.findData('free'))
        first, second = self.buttons()[:2]
        before = copy.deepcopy(self.editor.element_layout)
        r = second['rect']
        self.editor.change_geometry(first['id'], [r.x(), r.y(), r.width(), r.height()])
        self.assertEqual(before, self.editor.element_layout)
        self.editor.set_preview_mode(True)
        QTest.mousePress(self.editor.preview, Qt.LeftButton, pos=self.point(first['rect']))
        QTest.mouseMove(self.editor.preview, self.point(second['rect']), 20)
        QTest.mouseRelease(self.editor.preview, Qt.LeftButton, pos=self.point(second['rect']))
        self.assertIsNone(self.editor.preview.drag)
        self.assertEqual(before, self.editor.element_layout)

    def test_remote_nearest_button_skips_disabled_cell(self):
        self.grid(); buttons = self.buttons()
        self.editor.button_styles.setdefault(buttons[1]['id'], {})['disabled'] = True
        self.editor.refresh(); self.editor.focus = 0
        self.editor.press('right')
        self.assertNotEqual(self.editor.focus, 1)
        self.assertEqual(self.editor.focus, 3)

    def test_native_export_keeps_dense_enabled_actions_and_static_focus(self):
        self.grid(); buttons = self.buttons()
        self.editor.button_styles.setdefault(buttons[2]['id'], {})['disabled'] = True
        design = self.editor.current_design(preview=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); playlist = root/'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True)
            from test_chapter_timeline import playlist as chapter_playlist
            playlist.write_bytes(chapter_playlist([(0, 30*45000)], [(0, 0), (0, 10*45000), (0, 20*45000)]))
            install_menu(root, design, [0, 10, 20])
            with zipfile.ZipFile(root/'BDMV/JAR/00000.jar') as jar:
                props = dict(line.split('=', 1) for line in jar.read('menu.properties').decode().splitlines())
                self.assertEqual(props['page.0.count'], '3')
                self.assertEqual(props['page.0.1'], 'page:1')
                self.assertEqual(props['animation.mode'], 'static')
                self.assertIn(props['page.0.2.focus'], jar.namelist())
                self.assertIn(props['page.0.2.pressed'], jar.namelist())
                self.assertNotEqual(jar.read(props['page.0.2.focus']), jar.read(props['page.0.2.pressed']))
                saved = json.loads(jar.read('discforge-design.json'))['design']
                self.assertEqual(saved['button_editor'], design.button_editor)
                self.assertEqual([int(props['page.0.2.'+k]) for k in ('x', 'y', 'w', 'h')],
                                 list(buttons[3]['rect'].getRect()))

    def test_all_variants_and_long_titles_are_clipped(self):
        for preset in STYLE_PRESETS.values():
            style = dict(preset, font_size=160, padding_x=100, padding_y=100)
            design = MenuDesign('Film', layout={'play': [150, 400, 180, 40]}, button_styles={'play': style})
            normal = render_page(design, [0])
            long = render_page(replace(design, labels={'ru:play': 'Очень длинное название эпизода '*3}), [0])
            for rect in ((0, 0, 1920, 400), (0, 440, 1920, 640), (0, 400, 150, 40), (330, 400, 1590, 40)):
                self.assertEqual(normal.copy(*rect), long.copy(*rect))

    def test_catalog_geometry_uses_stable_ids_with_export_page_actions(self):
        from collection_menu import catalog_page
        self.editor.set_collection([('a', 'Первый'), ('b', 'Второй')], True, 'a')
        self.panel.scope.setCurrentIndex(1)
        self.panel.numbers['button_width'][1].setValue(500)
        self.panel.mode.setCurrentIndex(self.panel.mode.findData('grid'))
        self.panel.reorder('catalog:movie:0', 1)
        design = self.editor.current_design(preview=True)
        root, entries = catalog_page(design, ['Первый', 'Второй'], 0, [12, 30])
        self.assertEqual(entries[0][1], 'page:30')
        elements = [e for e in page_elements(root, [0], 0, entries) if e['button']]
        self.assertEqual(elements[0]['id'], 'catalog:movie:1')
        self.assertEqual(elements[1]['rect'][0], 690)

    def test_invalid_new_settings_rejected_and_old_projects_unchanged(self):
        for editor in ({'animation': 'flash'}, {'pages': {'0': {'columns': 0}}},
                       {'order': {'0': ['play', 'play']}}, {'presets': {'bad': {'font_size': 44}}}):
            with self.assertRaises(ValueError):
                MenuDesign('Film', button_editor=editor)
        self.assertEqual(MenuDesign.from_dict('Old', {}).button_editor, {})
        self.assertEqual(tuple(page_elements(MenuDesign('Old'), [0], 0)[4]['rect']), (150, 400, 760, 82))

    def test_custom_preset_roundtrip_excludes_geometry_and_typography(self):
        self.grid()
        self.panel.change_style({'font_size': 43, 'radius': 19, 'padding_x': 36})
        with patch('episode_button_editor.QInputDialog.getText', return_value=('Мой янтарь', True)):
            self.panel.save_preset()
        design = MenuDesign.from_dict('Saved', json.loads(json.dumps(self.editor.settings())))
        preset = design.button_editor['presets']['Мой янтарь']
        self.assertNotIn('font_size', preset); self.assertNotIn('radius', preset); self.assertNotIn('padding_x', preset)
        self.editor.apply_design(design)
        self.assertGreaterEqual(self.panel.preset.findData('custom:Мой янтарь'), 0)

    def test_no_animation_settles_immediately_and_finite_motion_stops(self):
        self.grid(); self.editor.set_preview_mode(True)
        self.editor.focus = 0; self.editor.refresh()
        self.panel.animation.setCurrentIndex(self.panel.animation.findData('none'))
        self.editor.press('right')
        self.assertEqual(self.editor.preview._focus_levels[self.buttons()[1]['id']], 1)
        self.panel.animation.setCurrentIndex(self.panel.animation.findData('restrained'))
        self.editor.press('left')
        QTest.qWait(400)
        self.assertFalse(self.editor.preview.timer.isActive())

    def test_size_reset_keeps_free_coordinates_and_appearance(self):
        self.editor.set_chapters([0])
        self.editor.preview_selection('play')
        self.editor.change_geometry('play', [400, 550, 500, 60])
        self.panel.change_style({'variant': 'glass', 'opacity': 58, 'font_size': 40})
        self.panel.reset_sizes()
        self.assertEqual(self.editor.element_layout['play'], [400, 550, 760, 82])
        self.assertEqual(self.editor.button_styles['play']['variant'], 'glass')
        self.assertNotIn('font_size', self.editor.button_styles['play'])


if __name__ == '__main__':
    unittest.main()
