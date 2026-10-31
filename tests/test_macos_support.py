import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import macos_iso
import settings
import vlc_preview


class MacSupportTests(unittest.TestCase):
    def test_bundle_paths_and_tsmuxer_case(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'bin').mkdir()
            binary = root/'bin/tsMuxeR'
            binary.touch()
            with patch.object(sys, 'platform', 'darwin'), patch.object(sys, '_MEIPASS', root, create=True), \
                 patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(root/'MacOS/DiskForge')):
                self.assertEqual(settings.find_binary('tsmuxer'), str(binary))

    def test_vlc_app_and_java_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            iso = root/'Фильм с меню.iso'
            iso.write_bytes(b'iso')
            app = root/'VLC.app'
            exe = app/'Contents/MacOS/VLC'
            exe.parent.mkdir(parents=True)
            exe.touch()
            java, jvm = root/'jdk', root/'jdk/lib/server/libjvm.dylib'
            with patch.object(sys, 'platform', 'darwin'), patch.object(vlc_preview, 'macos_java', return_value=(java, jvm)), \
                 patch.object(vlc_preview.subprocess, 'Popen') as popen, \
                 patch.dict(os.environ, {'JAVA_TOOL_OPTIONS': '-bad', 'DYLD_LIBRARY_PATH': '/bad'}):
                vlc_preview.launch_iso(iso, app)
            self.assertEqual(popen.call_args.args[0][0], str(exe))
            self.assertIn('--bluray-menu', popen.call_args.args[0])
            self.assertNotIn('--no-one-instance', popen.call_args.args[0])
            self.assertEqual(popen.call_args.kwargs['env']['BDJ_JVM_PATH'], str(jvm))
            self.assertNotIn('JAVA_TOOL_OPTIONS', popen.call_args.kwargs['env'])
            self.assertNotIn('DYLD_LIBRARY_PATH', popen.call_args.kwargs['env'])

    def test_mount_detaches_on_reader_error(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            if 'attach' in command:
                mount = str(Path(command[command.index('-mountpoint')+1]).resolve())
                return subprocess.CompletedProcess(command, 0, plistlib.dumps({'system-entities': [{'mount-point': mount}]}), b'')
            return subprocess.CompletedProcess(command, 0, b'', b'')
        with patch.object(macos_iso.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'reader'):
                with macos_iso.mounted_iso('test.iso'):
                    raise ValueError('reader failed')
        self.assertEqual(commands[-1][1], 'detach')
        self.assertIn('-readonly', commands[0])

    def test_pre_cancel_and_existing_destination_never_spawn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'BDMV').mkdir()
            target = root/'out.iso'
            target.write_bytes(b'keep')
            with patch.object(macos_iso.subprocess, 'Popen') as popen:
                with self.assertRaises(FileExistsError):
                    macos_iso.create_iso(root, target, 1000, threading.Event())
                cancel = threading.Event(); cancel.set()
                with self.assertRaisesRegex(RuntimeError, 'отменена'):
                    macos_iso.create_iso(root, root/'new.iso', 1000, cancel)
                popen.assert_not_called()
            self.assertEqual(target.read_bytes(), b'keep')

    def test_tool_failure_never_publishes_partial_iso(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source'; (source/'BDMV').mkdir(parents=True)
            target = root/'out.iso'
            def fail_writer(command, **kwargs):
                Path(command[2]).write_bytes(b'partial ISO')
                return Mock(returncode=1, poll=Mock(return_value=1), wait=Mock(return_value=1))
            with patch.object(macos_iso, 'find_binary', return_value=sys.executable), \
                    patch.object(macos_iso.subprocess, 'Popen', side_effect=fail_writer):
                with self.assertRaisesRegex(RuntimeError, 'создать ISO'):
                    macos_iso.create_iso(source, target, 25000000000, threading.Event())
            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob('.diskforge-iso-*')))


@unittest.skipUnless(sys.platform == 'darwin' and (Path(__file__).resolve().parents[1]/'bin/discforge-udf').is_file(),
                     'Build native macOS tools first')
class NativeMacISOTests(unittest.TestCase):
    def test_large_directory_overlay_and_payload_readback(self):
        from tools.iso_udf_preflight import inspect
        with tempfile.TemporaryDirectory(prefix='DiskForge — тест ') as temp:
            root = Path(temp)
            source, overlay = root/'Исходник', root/'Правка'
            for folder in ('BDMV/STREAM', 'BDMV/PLAYLIST', 'BDMV/CLIPINF', 'BDMV/BACKUP/PLAYLIST'):
                (source/folder).mkdir(parents=True)
                for i in range(99):
                    (source/folder/f'{i:05d}.bin').write_bytes(f'{folder}:{i}'.encode()*10)
            (overlay/'BDMV/PLAYLIST').mkdir(parents=True)
            (overlay/'BDMV/PLAYLIST/00000.bin').write_bytes(b'updated menu')
            target = root/'Коллекция 99.iso'
            macos_iso.create_iso(source, target, 25000000000, threading.Event(), overlay=overlay)
            self.assertTrue(inspect(target, 'BD25').valid)
            with macos_iso.mounted_iso(target) as mounted:
                files = list(mounted.rglob('*.bin'))
                self.assertEqual(len(files), 396)
                for path in files:
                    relative = path.relative_to(mounted)
                    expected = overlay/relative if (overlay/relative).is_file() else source/relative
                    self.assertEqual(path.read_bytes(), expected.read_bytes(), str(relative))
            self.assertNotEqual((source/'BDMV/PLAYLIST/00000.bin').read_bytes(), b'updated menu')


if __name__ == '__main__':
    unittest.main()
