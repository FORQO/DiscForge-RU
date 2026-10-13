import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import vlc_preview


def fake_pe(path, machine=0x8664):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'MZ' + bytes(58) + struct.pack('<I', 64) + b'PE\0\0' + struct.pack('<H', machine))


class VlcPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.iso = self.root / 'Фильм с меню.iso'
        self.iso.write_bytes(b'test')
        self.vlc = self.root / 'VLC/vlc.exe'
        fake_pe(self.vlc)
        fake_pe(self.root / 'runtime/java/bin/server/jvm.dll')
        jar = self.root / 'runtime/java/lib/rt.jar'
        jar.parent.mkdir(parents=True)
        jar.touch()

    def test_unicode_iso_private_java_and_separate_instance(self):
        with patch.dict(os.environ, {'JAVA_HOME': 'other-java', 'JAVA_TOOL_OPTIONS': '-bad'}):
            before = dict(os.environ)
            with patch.object(vlc_preview.subprocess, 'Popen') as popen:
                vlc_preview.launch_iso(self.iso, self.vlc, self.root)
            args, kwargs = popen.call_args
            self.assertEqual(args[0][-1], 'bluray:///' + self.iso.resolve().as_posix())
            self.assertIn('--bluray-menu', args[0])
            self.assertIn('--no-one-instance', args[0])
            self.assertIn('--no-one-instance-when-started-from-file', args[0])
            self.assertEqual(kwargs['env']['JAVA_HOME'], str(self.root / 'runtime/java'))
            self.assertNotIn('JAVA_TOOL_OPTIONS', kwargs['env'])
            self.assertFalse(kwargs.get('shell', False))
            self.assertEqual(dict(os.environ), before)

    def test_missing_java_blocks_launch(self):
        (self.root / 'runtime/java/bin/server/jvm.dll').unlink()
        with patch.object(vlc_preview.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(FileNotFoundError, 'Java'):
                vlc_preview.launch_iso(self.iso, self.vlc, self.root)
            popen.assert_not_called()

    def test_32bit_vlc_blocks_launch(self):
        fake_pe(self.vlc, 0x14c)
        with self.assertRaisesRegex(ValueError, '64-bit'):
            vlc_preview.launch_iso(self.iso, self.vlc, self.root)

    def test_invalid_iso(self):
        self.iso.write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'ISO'):
            vlc_preview.launch_iso(self.iso, self.vlc, self.root)

    def test_frozen_dll_directory_restored_on_spawn_failure(self):
        if os.name != 'nt':
            self.skipTest('Windows DLL search path')
        with patch.object(vlc_preview.sys, 'frozen', True, create=True), \
             patch.object(vlc_preview.sys, '_MEIPASS', 'original', create=True), \
             patch('ctypes.windll.kernel32.SetDllDirectoryW') as dll, \
             patch.object(vlc_preview.subprocess, 'Popen', side_effect=OSError('spawn')):
            with self.assertRaises(OSError):
                vlc_preview.launch_iso(self.iso, self.vlc, self.root)
            self.assertEqual([call.args for call in dll.call_args_list], [(None,), ('original',)])
