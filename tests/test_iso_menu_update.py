import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import contextlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
from types import SimpleNamespace
from PySide6.QtWidgets import QApplication
from disc_menu import MenuDesign, install_menu
from bdmv_fixtures import minimal_mpls
import iso_menu_update as updater


class IsoMenuUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.disc = self.root / 'disc'
        playlist = self.disc / 'BDMV/PLAYLIST/00000.mpls'
        playlist.parent.mkdir(parents=True)
        playlist.write_bytes(minimal_mpls())
        install_menu(self.disc, MenuDesign('Фильм', button_styles={'play': {'shape':'pill'}}), [0])

    def test_editable_design_roundtrip(self):
        chapters, props, design = updater.open_menu(self.disc)
        self.assertEqual(chapters, [0])
        self.assertEqual(design.title, 'Фильм')
        self.assertEqual(design.button_styles['play']['shape'], 'pill')

    def test_legacy_repair_keeps_images_and_actions(self):
        original = self.disc / 'BDMV/JAR/00000.jar'
        with zipfile.ZipFile(original) as archive:
            entries = {n:archive.read(n) for n in archive.namelist() if n != 'discforge-design.json'}
        with zipfile.ZipFile(original, 'w') as archive:
            for n,data in entries.items(): archive.writestr(n,data)
        _, props, design = updater.open_menu(self.disc)
        self.assertIsNone(design)
        overlay = self.root/'overlay'
        updater.repair_menu(self.disc, overlay, props, threading.Event())
        with zipfile.ZipFile(overlay/'BDMV/JAR/00000.jar') as result:
            self.assertEqual(result.read('page0.png'), entries['page0.png'])
            self.assertIn(b'page.0.0=play:0', result.read('menu.properties'))
            self.assertIn('focus0_0.png', result.namelist())
        self.assertEqual((overlay/'BDMV/JAR/00000.jar').read_bytes(), (overlay/'BDMV/BACKUP/JAR/00000.jar').read_bytes())

    def test_invalid_action_blocks_before_writing(self):
        original = self.disc/'BDMV/JAR/00000.jar'
        with zipfile.ZipFile(original) as jar:
            entries={n:jar.read(n) for n in jar.namelist()}
        entries['menu.properties']=entries['menu.properties'].replace(b'=play:0', b'=play:999')
        with zipfile.ZipFile(original,'w') as jar:
            for n,data in entries.items():jar.writestr(n,data)
        with self.assertRaisesRegex(ValueError,'действие'):
            updater.open_menu(self.disc)

    def run_update(self, create, design=None):
        source=self.root/'source.iso'
        source.write_bytes(b'original')
        target=self.root/'result.iso'
        cancel=threading.Event()
        with patch.object(updater,'mounted_iso',return_value=contextlib.nullcontext(self.disc)), \
             patch.object(updater,'inspect',return_value=SimpleNamespace(valid=True)), \
             patch.object(updater,'create_iso',side_effect=create):
            updater.update_iso(source,target,design,cancel)
        return source,target

    def test_replacement_uses_existing_media_tree_and_chapters(self):
        def create(root,pending,capacity,cancel,log,progress,overlay):
            self.assertEqual(root,self.disc)
            self.assertFalse((overlay/'BDMV/STREAM').exists())
            self.assertEqual((overlay/'BDMV/PLAYLIST/00000.mpls').read_bytes(),minimal_mpls())
            _,_,design=updater.open_menu(overlay)
            self.assertEqual(design.title,'Новое меню')
            pending.write_bytes(b'updated')
        source,target=self.run_update(create,MenuDesign('Новое меню'))
        self.assertEqual(source.read_bytes(),b'original')
        self.assertEqual(target.read_bytes(),b'updated')

    def test_cancel_removes_partial_and_preserves_source(self):
        def create(root,pending,capacity,cancel,log,progress,overlay):
            pending.write_bytes(b'partial')
            cancel.set()
        with self.assertRaisesRegex(RuntimeError,'отменена'):
            self.run_update(create)
        self.assertFalse((self.root/'result.iso').exists())
        self.assertEqual(list(self.root.glob('DiscForge_menu_*')),[])
        self.assertEqual((self.root/'source.iso').read_bytes(),b'original')

    def test_no_overwrite(self):
        source=self.root/'source.iso'
        source.write_bytes(b'original')
        with self.assertRaisesRegex(ValueError,'не перезаписывается'):
            updater.update_iso(source,source,None,threading.Event())
        self.assertEqual(source.read_bytes(),b'original')

    def test_existing_mount_not_ejected(self):
        with patch.object(updater.sys,'platform','win32'),patch.object(updater,'powershell',return_value=json.dumps({'root':str(self.disc),'owned':False})) as shell:
            with updater.mounted_iso(self.root/'source.iso') as root:
                self.assertEqual(root,self.disc)
            self.assertEqual(shell.call_count,1)

    def test_owned_mount_ejected_after_error(self):
        with patch.object(updater.sys,'platform','win32'),patch.object(updater,'powershell',side_effect=[json.dumps({'root':str(self.disc),'owned':True}),'']) as shell:
            with self.assertRaises(RuntimeError):
                with updater.mounted_iso(self.root/'source.iso'):
                    raise RuntimeError('test')
            self.assertIn('Dismount-DiskImage',shell.call_args.args[0])
