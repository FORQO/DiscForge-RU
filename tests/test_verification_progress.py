import contextlib
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from build_cache import digest
from verification_progress import VerificationProgress
from disc_verify import verify_image_payload


class ByteProgressTests(unittest.TestCase):
    def test_digest_reports_chunks_and_remains_cancellable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'file'
            data = b'a'*(9*1024*1024)
            p.write_bytes(data)
            events=[]
            result=digest(p,threading.Event(),lambda done,total:events.append((done,total)))
            self.assertEqual(result['sha256'],hashlib.sha256(data).hexdigest())
            self.assertGreater(len(events),1)
            self.assertEqual(events[-1],(len(data),len(data)))
            cancel=threading.Event()
            with self.assertRaisesRegex(RuntimeError,'отменена'):
                digest(p,cancel,lambda *_:cancel.set())

    def test_file_change_during_hash_never_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'file';p.write_bytes(b'x')
            events=[]
            tracker=VerificationProgress(1,1,events.append)
            def mutate(event):
                events.append(event)
                if event['done']:
                    p.write_bytes(b'changed')
            tracker.callback=mutate
            # Force telemetry each chunk to mutate while digest still checks stat.
            tracker.clock=lambda: 1000000*len(events)
            with self.assertRaises(ValueError):tracker.digest(p,threading.Event(),'hash')
            self.assertFalse(any(e['complete'] for e in events))

    def test_eta_covers_both_passes(self):
        now=[0];events=[]
        p=VerificationProgress(1000,2,events.append,scope='iso-final',clock=lambda:now[0])
        p.started=0;p.phase='1/2'
        now[0]=2;p.emit(200,force=True)
        self.assertEqual(events[-1]['speed'],100)
        self.assertEqual(events[-1]['remaining'],8)
        self.assertFalse(events[-1]['complete'])
        p.done=1000;p.finish()
        self.assertTrue(events[-1]['complete'])

    def test_corrupt_payload_never_signals_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'data').write_bytes(b'bad')
            expected={'data':{'size':3,'sha256':hashlib.sha256(b'yes').hexdigest()}}
            events=[];tracker=VerificationProgress(6,2,events.append,scope='iso-final')
            with patch('iso_menu_update.mounted_iso',return_value=contextlib.nullcontext(root)):
                with self.assertRaisesRegex(ValueError,'повреждены'):
                    verify_image_payload('test.iso',expected,threading.Event(),tracker)
            self.assertFalse(any(e['complete'] for e in events))


from PySide6.QtWidgets import QApplication
from verification_panel import VerificationPanel


class VerificationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_waiting_progress_stale_estimate_and_cancel(self):
        panel=VerificationPanel();panel.resize(900,110)
        event=dict(scope='iso-final',phase='1/2 · Файлы внутри ISO',file='BDMV/STREAM/00000.m2ts',
                   index=3,count=16,done=12_000_000_000,total=48_000_000_000,
                   speed=100_000_000,remaining=360,state='waiting',complete=False)
        panel.update_progress(event)
        self.assertEqual(panel.bar.maximum(),0)
        event['state']='reading';panel.update_progress(event)
        self.assertEqual(panel.bar.value(),250)
        self.assertIn('00:06:00',panel.stats.text())
        self.assertIn('BDMV/STREAM',panel.file.text())
        panel.updated-=5;panel.refresh()
        self.assertNotIn('00:06:00',panel.stats.text())
        panel.stop(False)
        self.assertFalse(panel.timer.isActive())
        self.assertIn('Осталось: —',panel.stats.text())
        panel.update_progress(None)
        self.assertTrue(panel.isHidden())
        panel.close()
