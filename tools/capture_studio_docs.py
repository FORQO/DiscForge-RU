"""Capture the real Studio UI with synthetic demo data; never save user settings."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem
from core import MediaInfo
from smart_copy import CopyReport
from main import Window
from studio_ui import ExportDialog


def main():
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    destination = ROOT / 'docs/images'
    destination.mkdir(exist_ok=True)
    with patch('main.load_settings', return_value={}), patch('main.save_settings'):
        window = Window()
        window.resize(1380, 850)
        window.set_theme('graphite')
        window.show()
        for title in ('Ночной город', 'Путешествие к морю'):
            path = str(Path('/Demo') / (title + '.mkv'))
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, path)
            window.movies.addItem(item)
            tracks = ({'index': 1, 'codec_name': 'ac3', 'sample_rate': '48000',
                       'channels': 2, 'bit_rate': '384000'},)
            report = CopyReport(video_ok=True, audio_ok=True, video_bytes=6_000_000_000,
                                video_max_bps=10_000_000,
                                audio_results={1: {'ok': True, 'bytes': 259200000}})
            info = MediaInfo(path, 5400, 6259200000, 'h264', 1920, 1080, '24/1', '',
                             'ac3', 2, 1, 0, copy_report=report,
                             audio_streams=tracks, selected_audio=(1,),
                             source_chapters=(0., 900., 1800., 2700., 3600., 4500.))
            window._scanned(path, info, '')
        window.movies.setCurrentRow(0)
        window.menu_title.setText('Киновечер')
        window._refresh_buttons()

        def capture(name, widget=window):
            app.processEvents()
            if not widget.grab().save(str(destination / ('studio-' + name + '.jpg')), 'JPG', 85):
                raise RuntimeError('Screenshot save failed: ' + name)

        window.tabs.setCurrentWidget(window.menu_editor)
        capture('menu')
        window.set_theme('blue')
        capture('dark')
        window.set_theme('graphite')
        window.tabs.setCurrentIndex(0)
        capture('plan')
        window.tabs.setCurrentIndex(3)
        capture('chapters')
        window.output_folder.setText('Blu-ray Output')
        dialog = ExportDialog(window)
        dialog.show()
        capture('export', dialog)
        dialog.hide()
        window.capacity_clock.stop()
        window.hide()


if __name__ == '__main__':
    main()
