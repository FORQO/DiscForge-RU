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
        platform_patch = patch.object(vlc_preview.sys, 'platform', 'win32')
        platform_patch.start()
        self.addCleanup(platform_patch.stop)
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

    def test_disc_root_with_unicode_and_spaces(self):
        disc = self.root / 'Диск Blu-ray'
        (disc / 'BDMV').mkdir(parents=True)
        (disc / 'BDMV/index.bdmv').write_bytes(b'INDX')
        with patch.object(vlc_preview.subprocess, 'Popen') as popen:
            vlc_preview.launch_bluray(disc, self.vlc, self.root)
        self.assertEqual(popen.call_args.args[0][-1], 'bluray:///' + disc.resolve().as_posix())
        self.assertIn('--bluray-menu', popen.call_args.args[0])
        self.assertEqual(popen.call_args.kwargs['env']['BDJ_JVM_PATH'],
                         str(self.root / 'runtime/java/bin/server/jvm.dll'))

    def test_non_bluray_directory_rejected(self):
        with patch.object(vlc_preview.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(ValueError, 'BDMV'):
                vlc_preview.launch_bluray(self.root, self.vlc, self.root)
            popen.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows argument parsing')
    def test_explorer_command_preserves_drive_root_and_quoted_iso(self):
        import ctypes
        from ctypes import wintypes
        from explorer_integration import commands
        parse = ctypes.windll.shell32.CommandLineToArgvW
        parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        parse.restype = ctypes.POINTER(wintypes.LPWSTR)
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        for command, target in zip(commands(self.root / 'Disk Forge.exe'), ['F:\\', str(self.iso)]):
            count = ctypes.c_int()
            pointer = parse(command.replace('%1', target), ctypes.byref(count))
            try:
                args = [pointer[i] for i in range(count.value)]
                self.assertEqual(len(args), 3)
                self.assertEqual(args[1], '--open-bluray')
                self.assertEqual(Path(args[2]).resolve(), Path(target).resolve())
            finally:
                free(pointer)

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
