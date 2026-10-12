"""Non-hardware ISO publication tests; PowerShell and IMAPI are mocked."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from windows_iso import create_iso


class WindowsISOAtomicTests(unittest.TestCase):
    def make_process(self, *, content=b'img', exit_code=0, cancel=None):
        def launch(command, **_kwargs):
            pending = Path(command[command.index('-Destination') + 1])
            pending.write_bytes(content)
            proc = MagicMock()
            proc.returncode = exit_code
            if cancel:
                def poll():
                    cancel.set()
                    return None
                proc.poll.side_effect = poll
            else:
                proc.poll.return_value = exit_code
            return proc
        return launch

    def test_success_is_published_after_subprocess_exits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root/'result.iso'
            with patch('windows_iso.sys.platform', 'win32'), \
                 patch('windows_iso.subprocess.Popen', side_effect=self.make_process(content=b'good ISO')):
                create_iso(root, final, 25_000_000_000, threading.Event())
            self.assertEqual(final.read_bytes(), b'good ISO')
            self.assertFalse(list(root.glob('DiscForge_iso_*')))

    def test_failure_does_not_publish_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root/'result.iso'
            with patch('windows_iso.sys.platform', 'win32'), \
                 patch('windows_iso.subprocess.Popen', side_effect=self.make_process(exit_code=1)):
                with self.assertRaisesRegex(RuntimeError, 'Не удалось создать ISO'):
                    create_iso(root, final, 25_000_000_000, threading.Event())
            self.assertFalse(final.exists())
            self.assertFalse(list(root.glob('DiscForge_iso_*')))

    def test_cancel_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cancel = threading.Event()
            final = root/'result.iso'
            with patch('windows_iso.sys.platform', 'win32'), \
                 patch('windows_iso.subprocess.Popen', side_effect=self.make_process(cancel=cancel)):
                with self.assertRaisesRegex(RuntimeError, 'отменена'):
                    create_iso(root, final, 25_000_000_000, cancel)
            self.assertFalse(final.exists())
            self.assertFalse(list(root.glob('DiscForge_iso_*')))

    def test_existing_destination_is_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root/'result.iso'
            final.write_bytes(b'original')
            with patch('windows_iso.sys.platform', 'win32'), \
                 patch('windows_iso.subprocess.Popen') as process:
                with self.assertRaises(FileExistsError):
                    create_iso(root, final, 25_000_000_000, threading.Event())
                process.assert_not_called()
            self.assertEqual(final.read_bytes(), b'original')

    def test_zero_byte_result_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            final = root/'result.iso'
            with patch('windows_iso.sys.platform', 'win32'), \
                 patch('windows_iso.subprocess.Popen', side_effect=self.make_process(content=b'')):
                with self.assertRaisesRegex(RuntimeError, 'не создала'):
                    create_iso(root, final, 25_000_000_000, threading.Event())
            self.assertFalse(final.exists())
            self.assertFalse(list(root.glob('DiscForge_iso_*')))


if __name__ == '__main__':
    unittest.main()
