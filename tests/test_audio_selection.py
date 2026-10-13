import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QListWidgetItem
from PySide6.QtCore import Qt
from audio_selection import selected_tracks, select_tracks
from core import make_copy_command, make_encode_command, write_meta, bitrate_plan
from smart_copy import CopyReport, choose_mode
from test_smart_copy import media


def multi():
    tracks = tuple(dict(index=i, codec_name=codec, channels=2, sample_rate='48000', bit_rate='640000', tags={'language': lang, 'title': lang})
                   for i, codec, lang in [(1, 'ac3', 'rus'), (3, 'ac3', 'eng'), (4, 'aac', 'fra')])
    report = CopyReport(video_ok=True, video_bytes=100000, audio_results={i: dict(ok=i != 4, bytes=1000, reason='AAC') for i in (1,3,4)})
    return replace(media(), audio_tracks=3, audio_streams=tracks, copy_report=report)


class AudioTests(unittest.TestCase):
    def test_selection_default_silence_invalid_indices(self):
        self.assertEqual([t['index'] for t in selected_tracks(multi())], [1])
        self.assertEqual(selected_tracks(select_tracks(multi(), [])), [])
        for indices in ([0], [1,1], [True], ['3'], list(range(33))):
            with self.assertRaises(ValueError): select_tracks(multi(), indices)

    def test_recommendation_uses_only_chosen_audio(self):
        for indices, expected in [([3], 'copy'), ([1,3], 'copy'), ([1,4], 'video_copy'), ([], 'copy')]:
            info = select_tracks(multi(), indices)
            self.assertEqual(choose_mode('auto', info.copy_report, info, 25_000_000_000)[0], expected)
        info = select_tracks(multi(), [4])
        with self.assertRaises(ValueError): choose_mode('copy', info.copy_report, info, 25_000_000_000)

    def test_mixed_audio_copies_ac3_and_encodes_only_aac(self):
        info = select_tracks(multi(), [3,4])
        outputs = [(Path(f'a{i}.ac3'), t) for i,t in enumerate(selected_tracks(info))]
        cmd = make_copy_command('ffmpeg', info, 'v.h264', outputs, True)
        self.assertEqual([cmd[i+1] for i,v in enumerate(cmd) if v == '-map'], ['0:0','0:3','0:4'])
        self.assertEqual([cmd[i+1] for i,v in enumerate(cmd) if v == '-c:a'], ['copy','ac3'])
        cmd = make_encode_command('ffmpeg', info, 'v.h264', outputs, 10000000, False)
        self.assertEqual([cmd[i+1] for i,v in enumerate(cmd) if v == '-map'], ['0:v:0','0:3','0:4'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'mux.meta'
            write_meta(path, Path('v.h264'), outputs, [0])
            text = path.read_text()
            self.assertEqual(text.count('A_AC3'), 2)
            self.assertIn('lang=eng', text)
            self.assertIn('lang=fra', text)
            self.assertNotIn('lang=rus', text)

    def test_size_and_peak_account_for_audio(self):
        self.assertLess(bitrate_plan(7200, 'BD25', 4), bitrate_plan(7200, 'BD25', 1))
        info = multi()
        report = replace(info.copy_report, video_max_bps=43_000_000)
        self.assertEqual(report.recommendation(select_tracks(info,[1]), 25_000_000_000)[0], 'copy')
        self.assertEqual(report.recommendation(select_tracks(info,[1,3]), 25_000_000_000)[0], 'encode')

    def test_gui_tracks_per_film_and_project_roundtrip(self):
        from main import Window
        app = QApplication.instance() or QApplication([])
        w = Window()
        try:
            for name in ('one.mkv', 'two.mkv'):
                path = str(Path(name).resolve())
                w.media[path] = replace(multi(), path=path)
                w.chapters[path] = [0]
                item = QListWidgetItem(name); item.setData(Qt.UserRole, path); w.movies.addItem(item)
            w.movies.setCurrentRow(0)
            self.assertEqual(w.audio_panel.tracks.count(), 3)
            w.audio_panel.tracks.item(0).setCheckState(Qt.Unchecked)
            w.audio_panel.tracks.item(1).setCheckState(Qt.Checked)
            first = w._selected_path()
            self.assertEqual(w.media[first].selected_audio, (3,))
            data = w._project_data()
            w.movies.setCurrentRow(1)
            self.assertEqual(w.audio_panel.tracks.item(0).checkState(), Qt.Checked)
            w.movies.setCurrentRow(0)
            self.assertEqual(w.audio_panel.tracks.item(1).checkState(), Qt.Checked)
            with tempfile.TemporaryDirectory() as tmp:
                import json
                project = Path(tmp)/'project.dfr.json'
                project.write_text(json.dumps(data), encoding='utf-8')
                with patch('main.QFileDialog.getOpenFileName', return_value=(str(project),'')), patch.object(w,'_queue_scan'):
                    w.open_project()
                self.assertEqual(w.audio_selections[first], [3])
                w._scanned(first, multi(), '')
                self.assertEqual(w.media[first].selected_audio, (3,))
        finally:
            w.hide()
