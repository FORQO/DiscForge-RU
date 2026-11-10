"""Project-wide visual samples: persistent inheritance, geometry and export parity."""
import copy
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
import zipfile
from dataclasses import replace
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from button_template import project_button_template
from disc_menu import MenuDesign, button_style, page_elements, pages, render_page, install_menu
from collection_menu import catalog_page
from episode_buttons import TEMPLATE_STYLE_KEYS, overlaps, page_layout


class ProjectTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def sample(self):
        source = 'catalog:movie:4'
        design = MenuDesign('Коллекция', theme='amber',
            labels={'ru:play':'Начать просмотр', 'ru:chapter:1':'Встреча'},
            chapter_titles=['Пролог','Сцена'],
            custom_buttons=[{'id':'custom:111111111111','page':0,'action':'play:0'}],
            button_styles={source:{'variant':'glass','fill':'#223344','font_size':40,'bold':True,
                                   'padding_x':36,'opacity':82,'width':3,'numbered':False},
                           'play':{'disabled':True,'opacity':1,'font_size':12},
                           'chapter:1':{'text':'#ff0000','italic':True}},
            layout={source:[150,180,600,90]},
            button_editor={'pages':{'catalog:1':{'mode':'column','width':400}},
                           'order':{'catalog:0':[source,'catalog:movie:0']}})
        rects=[[150,320,500,60],[1000,320,500,60],[150,450,500,60],[1000,450,500,60],[150,180,600,90]]
        updated=project_button_template(design,source,rects[-1],rects,'catalog:0',[0,10],
                                        [f'Серия {i+1}' for i in range(12)])
        return design, updated

    def test_style_sizes_and_layout_apply_to_unseen_keys_and_other_movies(self):
        original, updated = self.sample()
        expected = button_style(original,'catalog:movie:4')
        for key in ('play','chapter:1','chapter:998','audio:31','sub:32','home:199',
                    'catalog:movie:98','catalog:next:18'):
            style=button_style(updated,key)
            for name in TEMPLATE_STYLE_KEYS & expected.keys():
                self.assertEqual(style[name],expected[name],(key,name))
        for chapters in ([0],[0,10],list(range(12))):
            design=replace(updated,audio_tracks=[{'language':'rus','title':'Русский'}],
                           subtitle_tracks=[{'language':'eng','title':'English'}])
            for page in range(len(pages(chapters,design))):
                self.check_geometry(page_elements(design,chapters,page))
        for page in range(3):
            catalog, entries=catalog_page(updated,[f'Серия {i}' for i in range(12)],page)
            self.check_geometry(page_elements(catalog,[0],0,entries))

    def check_geometry(self, elements):
        rects=[list(e['rect']) for e in elements if e['button']]
        for index, rect in enumerate(rects):
            self.assertEqual(rect[2:],[600,90])
            self.assertFalse(overlaps(rect,rects[:index]+rects[index+1:]))
            self.assertGreaterEqual(rect[0],0); self.assertGreaterEqual(rect[1],0)
            self.assertLessEqual(rect[0]+rect[2],1920); self.assertLessEqual(rect[1]+rect[3],960)

    def test_names_actions_disabled_states_and_source_data_are_preserved(self):
        original, updated=self.sample()
        self.assertEqual(original.labels,updated.labels)
        self.assertEqual(original.chapter_titles,updated.chapter_titles)
        self.assertEqual(original.custom_buttons,updated.custom_buttons)
        self.assertEqual(pages([0,10],original),pages([0,10],updated))
        self.assertTrue(button_style(updated,'play')['disabled'])
        self.assertFalse(button_style(updated,'chapter:1').get('disabled',False))
        self.assertEqual(original.layout,updated.layout)
        self.assertEqual(original.button_editor['order'],updated.button_editor['order'])
        self.assertNotIn('template',original.button_editor)
        self.assertEqual(original.button_styles['play']['opacity'],1)

    def test_project_roundtrip_and_individual_overrides(self):
        _, updated=self.sample()
        restored=MenuDesign.from_dict('Коллекция',json.loads(json.dumps(updated.to_dict())))
        self.assertEqual(updated.button_editor,restored.button_editor)
        restored.button_styles['chapter:9']={'font_size':18}
        self.assertEqual(button_style(restored,'chapter:9')['font_size'],18)
        self.assertEqual(button_style(restored,'chapter:8')['font_size'],40)
        self.assertEqual(page_layout(restored,'catalog:19')['width'],600)

    def test_invalid_template_metadata_is_rejected(self):
        _, good=self.sample()
        for bad in ({}, {'style':{},'layout':{}},
                    {'style':{'disabled':True},'layout':good.button_editor['template']['layout']},
                    {'style':{},'layout':dict(good.button_editor['template']['layout'],width=-1)}):
            with self.subTest(template=bad), self.assertRaises(ValueError):
                MenuDesign('Bad',button_editor={'template':bad})

    def test_reapply_uses_the_new_sample_and_removes_old_individual_overrides(self):
        _, design=self.sample()
        design.button_styles['catalog:movie:4']={'font_size':48}
        design.button_styles['chapter:20']={'font_size':16,'disabled':True}
        updated=project_button_template(design,'catalog:movie:4',[150,400,600,90],
                                        [[150,400,600,90]],'catalog:0',[0,10])
        self.assertEqual(button_style(updated,'chapter:20')['font_size'],48)
        self.assertTrue(button_style(updated,'chapter:20')['disabled'])

    def test_collection_export_inherits_project_template(self):
        from bdmv_fixtures import minimal_mpls
        from disc_collection import merge_menu
        _, design=self.sample()
        titles=[f'Серия {i+1}' for i in range(5)]
        catalog, entries=catalog_page(design,titles)
        expected=render_page(catalog,[0],entries_override=entries)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'source'; playlist=source/'BDMV/PLAYLIST/00000.mpls'
            playlist.parent.mkdir(parents=True); playlist.write_bytes(minimal_mpls())
            install_menu(source,MenuDesign('Фильм'),[0])
            merge_menu(root/'output',[source]*5,design,titles)
            with zipfile.ZipFile(root/'output/BDMV/JAR/00000.jar') as jar:
                actual=QImage.fromData(jar.read('page0.png')).convertToFormat(expected.format())
                self.assertEqual(actual,expected)

    def test_exported_png_and_runtime_geometry_match_the_preview(self):
        from test_chapter_timeline import playlist
        original=MenuDesign('Фильм',theme='amber',labels={'ru:chapter:1':'Встреча'})
        updated=project_button_template(original,'play',[150,400,600,90],[[150,400,600,90]],'0',[0,10])
        expected=render_page(updated,[0,10],1)
        elements=[e for e in page_elements(updated,[0,10],1) if e['button']]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); target=root/'BDMV/PLAYLIST/00000.mpls'
            target.parent.mkdir(parents=True)
            target.write_bytes(playlist([(0,20*45000)],[(0,0),(0,10*45000)]))
            install_menu(root,updated,[0,10])
            with zipfile.ZipFile(root/'BDMV/JAR/00000.jar') as jar:
                actual=QImage.fromData(jar.read('page1.png')).convertToFormat(expected.format())
                self.assertEqual(actual,expected)
                props=dict(line.split('=',1) for line in jar.read('menu.properties').decode().splitlines())
                for index, element in enumerate(elements):
                    self.assertEqual([int(props[f'page.1.{index}.{key}']) for key in ('x','y','w','h')],
                                     list(element['rect']))
                saved=json.loads(jar.read('discforge-design.json'))
                self.assertEqual(saved['design']['button_editor'],updated.button_editor)


class ApplyButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from menu_editor import MenuEditor
        self.editor=MenuEditor()
        self.editor.set_collection([(str(i),f'Серия {i+1}') for i in range(12)],True,'0')
        self.panel=self.editor.button_inspector

    def tearDown(self):
        self.editor.close(); self.editor.deleteLater(); self.app.processEvents()

    def test_selected_sample_applies_across_pages_and_undo_is_one_action(self):
        self.editor.button_styles['catalog:movie:4']={'font_size':44,'opacity':88,'width':4}
        self.editor.element_layout['catalog:movie:4']=[150,180,600,90]
        self.editor.refresh(); self.editor.select_button('catalog:movie:4')
        # A batch scope must not silently change the sample to the first button.
        self.panel.scope.setCurrentIndex(1)
        before=copy.deepcopy(self.editor.settings()); count=len(self.editor._undo)
        self.assertTrue(self.panel.apply_all_button.isEnabled())
        self.panel.apply_all_button.click()
        design=self.editor.current_design()
        self.assertEqual(button_style(design,'catalog:movie:11')['font_size'],44)
        self.assertEqual(button_style(design,'chapter:998')['opacity'],88)
        self.assertEqual(page_layout(design,'99')['width'],600)
        self.assertEqual(len(self.editor._undo),count+1)
        self.editor.undo_edit(); self.assertEqual(self.editor.settings(),before)
        self.editor.redo_edit(); self.assertEqual(button_style(self.editor.current_design(),'play')['font_size'],44)

    def test_text_or_multiple_selection_cannot_be_used_as_one_sample(self):
        self.editor.select_button('text:title')
        self.assertFalse(self.panel.apply_all_button.isEnabled())
        self.editor.select_button('catalog:movie:0')
        self.editor.preview.selected_keys={'catalog:movie:0','catalog:movie:1'}
        self.panel.sync(); self.assertFalse(self.panel.apply_all_button.isEnabled())
        before=self.editor.settings(); self.panel.apply_to_project()
        self.assertEqual(self.editor.settings(),before)

    def test_oversized_sample_rejects_without_partial_project_changes(self):
        self.editor.element_layout['catalog:movie:0']=[150,400,1500,500]
        self.editor.refresh(); self.editor.select_button('catalog:movie:0')
        before=copy.deepcopy(self.editor.settings()); count=len(self.editor._undo)
        self.panel.apply_all_button.click()
        self.assertEqual(self.editor.settings(),before)
        self.assertEqual(len(self.editor._undo),count)
        self.assertIn('не помещаются',self.editor.caption.text())
