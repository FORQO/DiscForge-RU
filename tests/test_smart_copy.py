import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from core import MediaInfo, make_copy_command, prepare_processing, DiscForgeError, write_meta
from smart_copy import CopyReport, HeaderCheck, PacketCheck, metadata_reasons, choose_mode, signature, stream_lines


def media():
    return MediaInfo('film.mkv',2,1000,'h264',1920,1080,'24/1','','ac3',2,1,0,
        dict(index=0,pix_fmt='yuv420p',profile='High',level=41,field_order='progressive',sample_aspect_ratio='1:1'),
        dict(index=1,sample_rate='48000',bit_rate='640000'))


class SmartCopyTests(unittest.TestCase):
    def test_supported_and_unsupported_metadata(self):
        self.assertEqual(metadata_reasons(media()),[])
        for changes in (dict(width=1920,height=800),dict(video_codec='hevc'),dict(video_fps='25/1'),
                        dict(video_color_transfer='smpte2084'),dict(video_details={}),
                        dict(video_details=dict(media().video_details,pix_fmt='yuv420p10le'))):
            with self.subTest(changes=changes):
                self.assertTrue(metadata_reasons(replace(media(),**changes)))
        self.assertEqual(metadata_reasons(replace(media(),width=1280,height=720,video_fps='50/1')),[])

    def test_strict_modes_never_silently_transcode(self):
        r=CopyReport(video_ok=True,audio_ok=True,video_bytes=100,audio_bytes=10)
        self.assertEqual(choose_mode('auto',r,media(),25_000_000_000)[0],'copy')
        self.assertEqual(choose_mode('video_copy',r,media(),25_000_000_000)[0],'copy')
        r.audio_ok=False
        self.assertEqual(choose_mode('auto',r,media(),25_000_000_000)[0],'video_copy')
        with self.assertRaises(ValueError): choose_mode('copy',r,media(),25_000_000_000)
        r.video_ok=False
        for mode in ('copy','video_copy'):
            with self.assertRaises(ValueError): choose_mode(mode,r,media(),25_000_000_000)
        self.assertEqual(choose_mode('auto',r,media(),25_000_000_000)[0],'encode')

    def test_disc_capacity_changes_recommendation(self):
        r=CopyReport(video_ok=True,audio_ok=True,video_bytes=27_000_000_000)
        self.assertEqual(r.recommendation(media(),25_000_000_000)[0],'encode')
        self.assertEqual(r.recommendation(media(),50_000_000_000)[0],'copy')

    def test_remux_command_has_no_video_filters_or_encoder(self):
        for hybrid in (False,True):
            cmd=make_copy_command('ffmpeg',media(),'video.h264','audio.ac3',hybrid)
            self.assertEqual(cmd[cmd.index('-c:v')+1],'copy')
            self.assertEqual(cmd[cmd.index('-c:a')+1],'ac3' if hybrid else 'copy')
            self.assertNotIn('-vf',cmd)
            self.assertNotIn('-r',cmd)
            self.assertNotIn('-b:v',cmd)
            self.assertIn('h264_mp4toannexb',cmd)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'project.meta'
            write_meta(path,Path('v.h264'),None,[0],fps='24')
            self.assertIn('fps=24\n',path.read_text())

    def packet(self,index,pts=None,flags=None,dts=None,size=100):
        t=index/24
        return f'stream_index=0|pts_time={t if pts is None else pts:.6f}|dts_time={t if dts is None else dts}|size={size}|flags={flags if flags is not None else "K_" if index%24==0 else "__"}'

    def test_packet_scan_accepts_cfr_and_initial_missing_dts(self):
        info=replace(media(),audio_tracks=0,audio_details={})
        p=PacketCheck(info)
        for n in range(48): p.feed(self.packet(n,dts='N/A' if n<2 else None))
        p.complete()
        self.assertEqual(p.video_bytes,4800)

    def test_packet_scan_rejects_vfr_long_gop_corruption_bitrate(self):
        info=replace(media(),audio_tracks=0,audio_details={})
        cases=[lambda n:self.packet(n,pts=n/24+(0.02 if n==30 else 0)),
               lambda n:self.packet(n,flags='K_' if n==0 else '__'),
               lambda n:self.packet(n,flags='K_C'),lambda n:self.packet(n,size=2_000_000)]
        for generate in cases:
            with self.subTest(case=generate),self.assertRaises(ValueError):
                p=PacketCheck(info)
                for n in range(48): p.feed(generate(n))
                p.complete()

    def test_header_gate_requires_hrd_and_stable_parameters(self):
        fields=dict(profile_idc=100,level_idc=41,frame_mbs_only_flag=1,max_num_ref_frames=3,
                    max_dec_frame_buffering=4,max_num_reorder_frames=2,nal_hrd_parameters_present_flag=1,
                    bit_rate_scale=1,cpb_size_scale=3,cpb_cnt_minus1=0)
        fields['bit_rate_value_minus1[0]']=234374
        fields['cpb_size_value_minus1[0]']=234374
        h=HeaderCheck();h.current='sps';h.fields=fields.copy();h.finish()
        h.current='pps';h.fields=dict(num_slice_groups_minus1=0,weighted_bipred_idc=2);h.finish();h.complete()
        h.current='sps';h.fields=dict(fields,level_idc=42)
        with self.assertRaises(ValueError):h.finish()
        for change in (dict(nal_hrd_parameters_present_flag=0),dict(max_dec_frame_buffering=6),dict(bit_rate_scale=4)):
            h=HeaderCheck();h.current='sps';h.fields=dict(fields,**change)
            with self.assertRaises(ValueError):h.finish()
        for rate in (24,23.976):
            h=HeaderCheck(rate);h.current='sps'
            h.fields=dict(fields,timing_info_present_flag=1,time_scale=48000,num_units_in_tick=1000)
            if rate==24: h.finish()
            else:
                with self.assertRaises(ValueError):h.finish()

    def test_audio_gaps_and_corruption_are_rejected(self):
        for second in ('stream_index=1|pts_time=1|duration_time=.032|size=2560|flags=K_',
                       'stream_index=1|pts_time=.032|duration_time=.032|size=2560|flags=K_C'):
            p=PacketCheck(media())
            p.feed('stream_index=1|pts_time=0|duration_time=.032|size=2560|flags=K_')
            with self.assertRaises(ValueError):p.feed(second)

    def test_changed_source_invalidates_cached_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)/'film.mkv';p.write_bytes(b'old')
            r=CopyReport(signature=signature(p),video_ok=True,audio_ok=True)
            info=replace(media(),path=str(p),copy_report=r)
            p.write_bytes(b'changed')
            with self.assertRaisesRegex(DiscForgeError,'изменился'):
                prepare_processing(info,'auto','BD25','ffprobe','ffmpeg',threading.Event(),lambda _:None)

    def test_cancel_silent_scanner(self):
        cancel=threading.Event()
        timer=threading.Timer(.15,cancel.set);timer.start()
        try:
            with self.assertRaisesRegex(RuntimeError,'отменён'):
                stream_lines([sys.executable,'-c','import time; time.sleep(10)'],cancel,lambda _:None)
        finally:timer.cancel()


try:
    from PySide6.QtWidgets import QApplication,QListWidgetItem
    from PySide6.QtCore import Qt
    from main import Window,BuildThread
except ImportError:
    Window=None


@unittest.skipIf(Window is None,'Qt unavailable')
class SmartCopyGUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_recommendation_updates_with_capacity_and_project_mode(self):
        with patch('main.save_settings'):
            w=Window()
            w.disc_mode.setCurrentIndex(1)  # this test covers separate, individually budgeted discs
            r=CopyReport(video_ok=True,audio_ok=True,video_bytes=27_000_000_000)
            info=replace(media(),copy_report=r)
            w.media[info.path]=info;w.chapters[info.path]=[0]
            item=QListWidgetItem('Film');item.setData(Qt.UserRole,info.path);w.movies.addItem(item);w.movies.setCurrentRow(0)
            w._select_media()
            self.assertIn('не помещаются',w.recommendation.text())
            w.profile.setCurrentText('BD50')
            self.assertIn('без перекодирования',w.recommendation.text())
            w.processing_mode.setCurrentIndex(1)
            self.assertEqual(w._project_data()['processing_mode'],'copy')
            w._use_recommendation()
            self.assertEqual(w.processing_mode.currentIndex(),0)
            w.close()

    def test_copy_queue_does_not_probe_gpu(self):
        info=replace(media(),copy_report=CopyReport(video_ok=True,audio_ok=True,video_bytes=100))
        worker=BuildThread([(info,[0])],dict(ffmpeg='ffmpeg',tsmuxer='tsmuxer',output='out'),'BD25',True,False,auto=True,processing_mode='auto')
        with patch('main.prepare_processing',return_value=(info,'copy','copy')),patch('main.nvenc_available') as gpu,patch('main.create_bluray',return_value=Path('out.iso')) as build:
            worker.run()
            gpu.assert_not_called()
            self.assertEqual(build.call_args.kwargs['processing_mode'],'copy')
