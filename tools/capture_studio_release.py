"""Capture the release UI on synthetic movie metadata without touching user settings."""
import argparse
import os
from pathlib import Path
import sys
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'docs/images')
    args = parser.parse_args()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QListWidgetItem
    from PySide6.QtTest import QTest
    from core import MediaInfo
    from main import Window, STYLE
    from smart_copy import CopyReport
    app = QApplication([]); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    app.setFont(QFont('Segoe UI', 10))
    args.output.mkdir(parents=True, exist_ok=True)
    with patch('main.load_settings', return_value={}), patch('main.save_settings'):
        window = Window(); window.resize(1530, 980); window.set_theme('blue'); window.show()
        titles = ('Ночной город', 'Последний поезд', 'Встреча', 'Обратный путь', 'Рассвет')
        for i, title in enumerate(titles):
            path = str((ROOT/'Demo'/f'episode-{i+1:02}.mkv').resolve())
            name = f'Эпизод {i+1:02} · {title}'
            window.movie_names[path] = name
            item = QListWidgetItem(name); item.setData(Qt.UserRole, path); window.movies.addItem(item)
            info = MediaInfo(path, 3240, 2400000000, 'h264', 1920, 1080, '24000/1001', '', 'ac3', 2, 1, 0,
                copy_report=CopyReport(video_ok=True, audio_ok=True, video_bytes=2250000000,
                    video_max_bps=10000000, audio_results={1:{'ok':True,'bytes':150000000}}),
                audio_streams=({'index':1,'codec_name':'ac3','language':'rus','channels':2,
                    'sample_rate':'48000','bit_rate':'384000'},), selected_audio=(1,),
                source_chapters=(0.,480.,960.,1440.,1920.,2700.),
                source_chapter_titles=('Начало','Прибытие','Разговор','Дорога','Решение','Финал'))
            window._scanned(path, info, '')
        window.movies.setCurrentRow(0)
        window.menu_title.setText('После полуночи'); window._refresh_buttons()
        editor = window.menu_editor
        window.tabs.setCurrentWidget(editor)
        editor.button_styles['catalog:movie:0'] = dict(variant='glass', fill='#251d15',
            border='#cf8d48', opacity=76, radius=18, font_size=40, padding_x=28, width=2)
        editor.element_layout['catalog:movie:0'] = [150,400,600,90]
        editor.refresh(); editor.select_button('catalog:movie:0')
        editor.button_inspector.apply_all_button.click()
        editor.inspector_tabs.setCurrentIndex(2)

        def capture(name):
            window.movie_title_edit.setCursorPosition(0)
            app.processEvents(); QTest.qWait(300)
            target = args.output/f'studio-1.5.1-{name}.jpg'
            if not window.grab().save(str(target), 'JPG', 88):
                raise RuntimeError(str(target))
            print(target, flush=True)

        capture('menu')
        window.set_theme('graphite'); capture('light')
        window.set_theme('blue')
        editor.preview_mode.setChecked(True); capture('preview')
        editor.preview_mode.setChecked(False)
        window.tabs.setCurrentIndex(3)
        window.rename_chapter(window.chapter_list.indexFromItem(window.chapter_list.item(1)), 'Встреча на вокзале')
        capture('chapters')
        window.tabs.setCurrentIndex(0); capture('plan')
        window.close()


if __name__ == '__main__':
    main()
