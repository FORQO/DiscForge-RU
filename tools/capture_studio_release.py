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
    parser.add_argument('--language', choices=['en', 'ru'], default='en')
    args = parser.parse_args()
    from studio_locale import configure_language
    from studio_ui import ExportDialog
    from quality_dialog import QualityDialog
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QListWidgetItem
    from PySide6.QtTest import QTest
    from core import MediaInfo
    from main import Window, STYLE
    from smart_copy import CopyReport
    app = QApplication([]); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    configure_language(app, args.language)
    if sys.platform != 'darwin':
        app.setFont(QFont('Segoe UI', 10))
    english = args.language == 'en'
    demo = Path('/Users/Shared/DiskForge Demo' if sys.platform == 'darwin' else 'C:/DiskForge Demo')
    args.output.mkdir(parents=True, exist_ok=True)
    with patch('main.load_settings', return_value={}), patch('main.save_settings'):
        window = Window(); window.resize(1530, 980); window.set_theme('blue'); window.show()
        def capture(name, widget=window):
            app.processEvents()
            QTest.qWait(120)
            target = args.output / f'{name}.jpg'
            if not widget.grab().save(str(target), 'JPG', 88):
                raise RuntimeError(str(target))
            print(target.name, flush=True)

        window.output_folder.setText(str(demo / 'Output'))
        capture('welcome')
        titles = ('Night City', 'The Last Train', 'The Meeting', 'The Way Home', 'Dawn') if english else ('Ночной город', 'Последний поезд', 'Встреча', 'Обратный путь', 'Рассвет')
        for i, title in enumerate(titles):
            path = str(demo/f'episode-{i+1:02}.mkv')
            name = f'Episode {i+1:02} · {title}' if english else f'Эпизод {i+1:02} · {title}'
            window.movie_names[path] = name
            item = QListWidgetItem(name); item.setData(Qt.UserRole, path); window.movies.addItem(item)
            info = MediaInfo(path, 3240, 2400000000, 'h264', 1920, 1080, '24000/1001', '', 'ac3', 2, 1, 0,
                copy_report=CopyReport(video_ok=True, audio_ok=True, video_bytes=2250000000,
                    video_max_bps=10000000, audio_results={1:{'ok':True,'bytes':150000000}}),
                audio_streams=({'index':1,'codec_name':'ac3','language':('eng' if english else 'rus'),'channels':2,
                    'sample_rate':'48000','bit_rate':'384000'},), selected_audio=(1,),
                source_chapters=(0.,480.,960.,1440.,1920.,2700.),
                source_chapter_titles=('Opening','Arrival','Conversation','On the Road','The Decision','Finale') if english else ('Начало','Прибытие','Разговор','Дорога','Решение','Финал'))
            window._scanned(path, info, '')
        window.movies.setCurrentRow(0)
        window.menu_title.setText('After Midnight' if english else 'После полуночи'); window._refresh_buttons()
        editor = window.menu_editor
        window.tabs.setCurrentWidget(editor)
        editor.button_styles['catalog:movie:0'] = dict(variant='glass', fill='#251d15',
            border='#cf8d48', opacity=76, radius=18, font_size=40, padding_x=28, width=2)
        editor.element_layout['catalog:movie:0'] = [150,400,600,90]
        editor.refresh(); editor.select_button('catalog:movie:0')
        editor.button_inspector.apply_all_button.click()
        editor.inspector_tabs.setCurrentIndex(2)

        capture('menu')
        window.set_theme('graphite'); capture('light')
        window.set_theme('blue')
        editor.preview_mode.setChecked(True); capture('preview')
        editor.preview_mode.setChecked(False)
        editor.inspector_tabs.setCurrentIndex(0); capture('appearance')
        editor.inspector_tabs.setCurrentIndex(1); capture('object')
        window.tabs.setCurrentIndex(3)
        window.rename_chapter(window.chapter_list.indexFromItem(window.chapter_list.item(1)), 'Meeting at the Station' if english else 'Встреча на вокзале')
        capture('chapters')
        window.tabs.setCurrentIndex(0); capture('plan')
        for index, name in ((1, 'audio'), (2, 'subtitles'), (5, 'verify-iso')):
            window.tabs.setCurrentIndex(index)
            capture(name)
        dialog = ExportDialog(window)
        dialog.show(); capture('export', dialog); dialog.close()
        quality = QualityDialog(window.media[next(iter(window.media))], window._config(), 8_000_000, parent=window)
        quality.show(); capture('quality', quality); quality.close()
        window.capacity_clock.stop()
        window.hide()


if __name__ == '__main__':
    main()
