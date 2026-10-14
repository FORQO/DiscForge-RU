import hashlib
import io
import math
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core import MediaInfo, write_meta
from audio_pipeline import copy_format, plan_audio, append_audio, scan_audio
from disc_audio import menu_tracks
from disc_menu import MenuDesign, pages
from truehd_audio import frames, interleave, paths, payload_hash


def media(**changes):
    track = dict(index=7, codec_name='truehd', profile='Dolby TrueHD + Dolby Atmos', sample_rate='48000', channels=8,
                 tags={'language': 'eng', 'title': 'Original'})
    track.update(changes)
    return MediaInfo('movie.mkv', 1, 1000, 'h264', 1920, 1080, '24/1', '', 'truehd', 8, 1, 0,
                     video_details={'index': 0}, audio_streams=(track,))


def access_unit(rate=0):
    # Opaque synthetic payload, testing framing only (not a decodable sample).
    data = bytearray(64)
    data[:2] = (len(data)//2).to_bytes(2, 'big')
    data[4:8] = b'\xf8\x72\x6f\xba'
    data[8] = rate << 4
    data[12:14] = b'\xb7\x52'
    return bytes(data)


class TrueHDTests(unittest.TestCase):
    def test_auto_and_copy_preserve_atmos_and_create_independent_companion(self):
        info = media()
        for mode in ('auto', 'copy'):
            info = replace(info, audio_modes={'7': mode})
            audio = plan_audio(info, {7: {'ok': True, 'bytes': 1000}}, Path('work'), lambda _:None)
            self.assertTrue(audio[0][1]['output_copy'])
            command = append_audio(['ffmpeg', '-i', info.path], audio, info)
            self.assertEqual(command.count('-map'), 2)
            self.assertIn('truehd', command)
            self.assertIn('copy', command)
            self.assertIn('ac3', command)
            self.assertIn('-xerror', command)
            self.assertNotIn('truehd_core', command)
            self.assertNotIn('-af', command)

    def test_incompatible_truehd_never_silently_loses_atmos(self):
        for mode in ('auto', 'copy'):
            with self.assertRaises(ValueError):
                plan_audio(replace(media(), audio_modes={'7':mode}), {7:{'ok':False,'reason':'timestamp gap'}}, Path('.'),print)
        audio = plan_audio(replace(media(),audio_modes={'7':'ac3'}), {}, Path('.'),lambda _:None)
        self.assertFalse(audio[0][1]['output_copy'])
        self.assertNotIn('Atmos', menu_tracks(replace(media(),audio_modes={'7':'ac3'}))[0]['title'])
        self.assertIsNone(copy_format(media(sample_rate='44100').audio_streams[0]))

    def test_one_remote_action_per_track_and_pes_substream_identifiers(self):
        info = media()
        design = MenuDesign('Test', audio_tracks=menu_tracks(info))
        actions = [action for page in pages([0],design) for _,action in page]
        self.assertEqual([a for a in actions if a.startswith('audio:')], ['audio:1'])
        self.assertIn('Atmos',str(pages([0],design)))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            audio=plan_audio(info,{7:{'ok':True,'bytes':1000}},root,lambda _:None)
            write_meta(root/'x.meta',root/'video.h264',audio,[0])
            self.assertIn('--new-audio-pes',(root/'x.meta').read_text())
            self.assertNotIn('down-to-ac3',(root/'x.meta').read_text())

    def test_interleave_keeps_every_byte_and_integer_timing_without_drift(self):
        for rate in (0,1):
            with tempfile.TemporaryDirectory() as temp:
                target=Path(temp)/'a.thd+ac3'; raw, core=paths(target)
                unit=access_unit(rate); count=12001
                raw.write_bytes(unit*count)
                ac3=b'\x0b\x77\0\0\x24\x40'+bytes(2554)
                core.write_bytes(ac3*math.ceil(count*5/192))
                self.assertEqual(interleave(target,threading.Event()),hashlib.sha256(unit*count).hexdigest())
                result=target.read_bytes(); cursor=0; units=cores=0
                while cursor<len(result):
                    if result[cursor:cursor+2]==b'\x0b\x77':
                        self.assertLess(units*5-cores*192,5)
                        cursor+=2560;cores+=1
                    else:
                        self.assertEqual(result[cursor:cursor+64],unit)
                        cursor+=64;units+=1
                self.assertEqual(units,count)
                self.assertEqual(cores,math.ceil(count*5/192))

    def test_invalid_frames_rates_and_cancellation_fail_closed(self):
        for data in (b'\0',access_unit()[:-1],access_unit(8),bytes(64)):
            with self.assertRaises(ValueError):list(frames(io.BytesIO(data),threading.Event()))
        cancel=threading.Event();cancel.set()
        with self.assertRaises(RuntimeError):list(frames(io.BytesIO(access_unit()),cancel))

    def test_submillisecond_mkv_pts_and_cumulative_drift(self):
        lines=['stream_index=0|pts_time=0']+[
            f'stream_index=7|pts_time={i/1200:.3f}|duration_time=N/A|size=64|flags=K__' for i in range(1200)]
        def feed(_command,_cancel,consume):
            for line in lines:consume(line)
        with patch('smart_copy.stream_lines',side_effect=feed):
            self.assertTrue(scan_audio(media(),'ffprobe',threading.Event())[7]['ok'])
            # A small error per frame must not accumulate into an undetected drift.
            lines[600]='stream_index=7|pts_time=0.510|duration_time=N/A|size=64|flags=K__'
            self.assertFalse(scan_audio(media(),'ffprobe',threading.Event())[7]['ok'])

    def test_hash_uses_encoded_stream_and_retains_all_extensions(self):
        def feed(command,cancel,consume):
            self.assertIn('copy',command)
            self.assertEqual(command[command.index('-map')+1],'0:7')
            self.assertNotIn('truehd_core',command)
            consume('SHA256='+'a'*64)
        with patch('smart_copy.stream_lines',side_effect=feed):
            self.assertEqual(payload_hash('x.mkv',7,'ffmpeg',threading.Event()),'a'*64)


if __name__ == '__main__':unittest.main()
