import json
import os
import tempfile
import threading
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch

from build_cache import BuildCache, digest
from avc_audit import IDRCheck
from audio_pipeline import copy_format, plan_audio
from core import MediaInfo, make_video_command
from gpu_pipeline import cuda_command, supported


def media():
    return MediaInfo('movie.mkv', 4, 1000, 'h264', 1920, 1080, '24/1', '', 'ac3', 2, 1, 0,
        video_details={'index': 0, 'pix_fmt': 'yuv420p', 'sample_aspect_ratio': '1:1', 'field_order': 'progressive'},
        audio_streams=({'index':1,'codec_name':'ac3','sample_rate':'48000','channels':2,'bit_rate':'384000'},))


class CheckpointTests(unittest.TestCase):
    def test_explicit_encoding_still_checks_changed_source_and_rotation(self):
        from core import prepare_processing, DiscForgeError
        from smart_copy import signature, CopyReport, metadata_reasons
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source';source.write_bytes(b'original')
            info=replace(media(),path=str(source),copy_report=CopyReport(signature=signature(source)))
            source.write_bytes(b'changed!')
            with self.assertRaises(DiscForgeError):
                prepare_processing(info,'encode','BD25','ffprobe','ffmpeg',threading.Event(),lambda _:None)
        self.assertTrue(any('поворот' in reason for reason in metadata_reasons(replace(media(),
            video_details=dict(media().video_details,side_data_list=[{'rotation':90}])))))

    def test_source_change_in_middle_even_with_preserved_size_and_time(self):
        from smart_copy import signature
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'source';source.write_bytes(b'a'*300000)
            before=signature(source);stat=source.stat()
            with source.open('r+b') as f:
                f.seek(150000);f.write(b'b')
            os.utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns))
            self.assertNotEqual(before,signature(source))

    def test_resume_requires_complete_artifacts_settings_and_full_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            cancel = threading.Event()
            source = Path(temp)/'source'
            source.write_bytes(b'a'*200000)
            signature = digest(source, cancel)
            with BuildCache(temp, source, cancel, lambda _: None) as cache:
                artifact = cache.root/'video'
                artifact.write_bytes(b'a'*200000)
                cache.commit('encode', [signature, 1], [artifact])
                root = cache.root
            with BuildCache(temp, source, cancel, lambda _: None) as cache:
                self.assertEqual(cache.root, root)
                self.assertTrue(cache.reusable('encode', [signature, 1], [artifact]))
                self.assertFalse(cache.reusable('encode', [signature, 2], [artifact]))
                stat = artifact.stat()
                with artifact.open('r+b') as f:
                    f.seek(100000); f.write(b'b')
                os.utime(artifact, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                self.assertFalse(cache.reusable('encode', [signature, 1], [artifact]))
                cache.begin('encode')
            self.assertNotIn('encode', json.loads((root/'checkpoint.json').read_text())['stages'])

    def test_concurrent_build_lock_and_untrusted_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            with BuildCache(temp, 'source', threading.Event(), lambda _:None) as cache:
                with self.assertRaises(RuntimeError):
                    with BuildCache(temp, 'source', threading.Event(), lambda _:None): pass
                external = Path(temp)/'outside'; external.write_bytes(b'data')
                with self.assertRaises(ValueError): cache.commit('x', 1, [external])

    def test_cancellation_does_not_mark_stage_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            cancel=threading.Event()
            with BuildCache(temp, 'source', cancel, lambda _:None) as cache:
                artifact=cache.root/'video';artifact.write_bytes(b'video')
                cancel.set()
                with self.assertRaises(RuntimeError):cache.commit('encode', 1, [artifact])
                self.assertNotIn('encode', cache.data['stages'])


class IDRTests(unittest.TestCase):
    def stream(self, count, idrs):
        return b''.join(b'\x00\x00\x01\x09\xf0\x00\x00\x00\x01'+bytes([0x65 if n in idrs else 0x41])+b'\x80' for n in range(count))

    def test_chunk_boundaries_and_closed_gops(self):
        check=IDRCheck(24)
        data=self.stream(48,{0,24})
        for start in range(0,len(data),7):check.feed(data[start:start+7])
        check.feed(b'',final=True)
        self.assertEqual(check.frames,48)
        duplicate=IDRCheck(24)
        duplicate.feed(b'\x00\x00\x01\x09\xf0'+data,final=True)
        self.assertEqual(duplicate.frames,48)

    def test_key_frames_without_idr_and_long_gop_rejected(self):
        for idrs in ({24},{0}):
            with self.assertRaises(ValueError): IDRCheck(24).feed(self.stream(48,idrs),final=True)


class AudioPlanTests(unittest.TestCase):
    def test_mixed_formats_and_strict_copy(self):
        ac3=media().audio_streams[0]
        dts=dict(ac3,index=2,codec_name='dts',profile='DTS',bit_rate='1536000')
        pcm=dict(ac3,index=3,codec_name='pcm_s24le')
        atmos=dict(ac3,index=4,codec_name='eac3')
        info=replace(media(),audio_streams=(ac3,dts,pcm,atmos),selected_audio=(1,2,3,4))
        plans=plan_audio(info,{i:{'ok':True} for i in range(1,5)},Path('work'),lambda _:None)
        self.assertEqual([t['output_mux'] for _,t in plans],['A_AC3','A_DTS','A_LPCM','A_AC3'])
        self.assertEqual([t['output_copy'] for _,t in plans],[True,True,True,False])
        self.assertIsNone(copy_format(dict(dts,profile='DTS-HD MA')))
        with self.assertRaises(ValueError):plan_audio(replace(info,audio_modes={'4':'copy'}),{},Path('work'),lambda _:None)
        plans=plan_audio(replace(info,audio_modes={'1':'ac3'}),{1:{'ok':True}},Path('work'),lambda _:None)
        self.assertFalse(plans[0][1]['output_copy'])


class GPUPathTests(unittest.TestCase):
    def test_cuda_stays_on_device_and_uses_cfr(self):
        command=cuda_command(make_video_command('ffmpeg','in','out',8000000,True))
        self.assertLess(command.index('-hwaccel'), command.index('-i'))
        self.assertIn('pad_cuda', command[command.index('-vf')+1])
        self.assertNotIn('-pix_fmt',command)
        self.assertEqual(command[command.index('-fps_mode')+1],'cfr')

    def test_failure_falls_back_cancel_propagates(self):
        def fail(*args):raise RuntimeError('GPU failed')
        cancel=threading.Event()
        self.assertFalse(supported(media(),'ffmpeg',cancel,lambda _:None,fail,make_video_command))
        cancel.set()
        with self.assertRaises(RuntimeError):supported(media(),'ffmpeg',cancel,lambda _:None,fail,make_video_command)


class PayloadTests(unittest.TestCase):
    def test_iso_payload_mismatch_fails_even_when_file_size_is_same(self):
        from contextlib import contextmanager
        from disc_verify import verify_image_payload
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);file=root/'payload';file.write_bytes(b'abcd')
            expected={'payload':digest(file,threading.Event())}
            @contextmanager
            def mounted(_):yield root
            with patch('iso_menu_update.mounted_iso',mounted):
                verify_image_payload('test.iso',expected,threading.Event())
                file.write_bytes(b'abce')
                with self.assertRaisesRegex(ValueError,'повреждены'):
                    verify_image_payload('test.iso',expected,threading.Event())
                file.write_bytes(b'abcd');(root/'unexpected').write_bytes(b'x')
                with self.assertRaisesRegex(ValueError,'Состав'):
                    verify_image_payload('test.iso',expected,threading.Event())


class EngineUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_audio_policy_persists_and_last_project_is_openable(self):
        from main import Window
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import Qt
        from settings import write_json
        with patch('main.load_settings',return_value={}),patch('main.save_settings'),tempfile.TemporaryDirectory() as temp:
            w=Window()
            try:
                path=str((Path(temp)/'film.mkv').resolve())
                item=QListWidgetItem('Film');item.setData(Qt.UserRole,path)
                w.movies.addItem(item);w.movies.setCurrentRow(0)
                w._scanned(path,replace(media(),path=path),'')
                w.audio_panel.tracks.setCurrentRow(0)
                w.audio_panel.policy.setCurrentIndex(2)
                self.assertEqual(w.media[path].audio_modes,{'1':'ac3'})
                w.output_folder.setText(temp)
                project=Path(temp)/'.diskforge-cache/last-project.dfr.json'
                write_json(project,w._project_data())
                with patch.object(w,'_queue_scan') as scan,patch('main.QMessageBox.warning') as warning:
                    w.open_last_build()
                    warning.assert_not_called();scan.assert_called_once_with([path])
                self.assertEqual(w.audio_modes[path],{'1':'ac3'})
                self.assertTrue(w.gpu_frames.isChecked())
            finally:w.close()

    def test_close_requests_cancel_and_waits_for_worker(self):
        from main import Window,QMessageBox
        from PySide6.QtGui import QCloseEvent
        from unittest.mock import Mock
        with patch('main.load_settings',return_value={}),patch('main.save_settings'):
            w=Window();worker=Mock();worker.isRunning.return_value=True;w.build_thread=worker
            try:
                event=QCloseEvent()
                with patch('main.QMessageBox.question',return_value=QMessageBox.Yes):w.closeEvent(event)
                self.assertFalse(event.isAccepted());self.assertTrue(w._close_after_build)
                worker.cancel.assert_called_once()
                worker.isRunning.return_value=False
                with patch.object(w,'close') as close:w._finish_deferred_close();close.assert_called_once()
            finally:w.build_thread=None;w.close()


if __name__ == '__main__':unittest.main()
