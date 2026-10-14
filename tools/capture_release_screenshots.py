"""Capture the real UI with explicit demo metadata, without user files or encoding."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
from pathlib import Path
from unittest.mock import patch
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QListWidgetItem
from core import MediaInfo
from smart_copy import CopyReport
from main import Window, interface_style

def main():
    app = QApplication([])
    app.setStyle('Fusion')
    app.setStyleSheet(interface_style('graphite'))
    app.setFont(QFont('Segoe UI', 10))
    with patch('main.load_settings', return_value={}):
        w = Window()
    titles = ['Северный маршрут', 'Серия 1 — Отправление', 'Серия 2 — Новый горизонт']
    for i, title in enumerate(titles):
        path = str(Path('C:/Media')/f'demo-{i+1}.mkv')
        tracks = ({'index': 1, 'codec_name': 'ac3', 'sample_rate': '48000', 'channels': 6, 'bit_rate': '640000', 'tags': {'language': 'rus', 'title': 'Русский · основная озвучка'}},
                  {'index': 2, 'codec_name': 'aac', 'sample_rate': '48000', 'channels': 2, 'tags': {'language': 'eng', 'title': 'English · original'}})
        duration = 7200 if i == 0 else 2700
        report = CopyReport(video_ok=i==0, audio_ok=True, video_bytes=7_200_000_000 if i==0 else 0,
                            video_max_bps=12_000_000, reasons=['размер кадра требует преобразования'] if i==1 else ['видеокодек HEVC требует преобразования'],
                            audio_results={1: {'ok': True, 'bytes': duration*80000}, 2: {'ok': False, 'bytes': duration*24000, 'reason': 'AAC'}})
        info = MediaInfo(path, duration, 8_000_000_000 if i==0 else 1_200_000_000, 'hevc' if i==2 else 'h264',
                         1024 if i==1 else 1920, 426 if i==1 else 1080, '24000/1001', '', 'ac3', 6, 2, 0,
                         audio_streams=tracks, selected_audio=(1,), copy_report=report,
                         video_details={'index': 0, 'bit_rate': '2400000' if i==1 else '4000000'},
                         source_chapters=(0., 180., 620., 1250.), source_chapter_titles=('Начало пути', 'Первая встреча', 'Новый поворот', 'Возвращение'))
        w.movie_names[path] = title
        item=QListWidgetItem(title)
        item.setData(Qt.UserRole,path)
        w.movies.addItem(item)
        w._scanned(path,info,'')
    w.menu_editor.title.setText('Киноклуб · Избранное')
    w.output_folder.setText('C:/Blu-ray')
    w.log.clear()
    w.log.appendPlainText('Демонстрационный проект · три фильма, независимые главы и аудиодорожки.')
    w.resize(1600,1050)
    w.show()
    out=root/'docs/images'
    out.mkdir(exist_ok=True)
    def capture(name):
        app.processEvents()
        assert w.grab().save(str(out/name))
    w.movies.setCurrentRow(1)
    w.tabs.setCurrentIndex(0)
    capture('interface-plan-1.4.png')
    w.tabs.setCurrentIndex(4)
    w.menu_editor.show_catalog()
    capture('interface-menu-1.4.png')
    w.menu_editor.preview_selection('catalog:movie:1')
    w.menu_editor.press('enter')
    w.menu_editor.press('down')
    w.menu_editor.press('enter')
    capture('interface-chapters-1.4.png')
    w.tabs.setCurrentIndex(1)
    capture('interface-audio-1.4.png')
    w.capacity_clock.stop()
    w.hide()
    print('Captured four UI screenshots using demo metadata.')

if __name__ == '__main__':
    main()
