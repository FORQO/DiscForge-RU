"""Capture the existing Studio shell before/after on synthetic episode data."""
import argparse
import os
from pathlib import Path
import sys
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--enhanced', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    from PySide6.QtWidgets import QApplication, QListWidgetItem, QGroupBox
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtTest import QTest
    from main import Window
    from core import MediaInfo
    from smart_copy import CopyReport
    app = QApplication([]); app.setStyle('Fusion')
    with patch('main.load_settings', return_value={}), patch('main.save_settings'):
        window = Window(); window.resize(1530, 980); window.set_theme('blue'); window.show()
        for i, title in enumerate(('Эпизод 01 · Ночной город', 'Эпизод 02 · Последний поезд',
                                    'Эпизод 03 · По ту сторону света', 'Эпизод 04 · Возвращение')):
            path = str((Path('Demo') / ('episode'+str(i)+'.mkv')).resolve())
            window.movie_names[path] = title
            item = QListWidgetItem(title); item.setData(Qt.UserRole, path); window.movies.addItem(item)
            info = MediaInfo(path, 5400, 3000000000, 'h264', 1920, 1080, '24/1', '', 'ac3', 2, 1, 0,
                copy_report=CopyReport(video_ok=True, audio_ok=True, video_bytes=2800000000,
                                      video_max_bps=10000000, audio_results={1: {'ok': True, 'bytes': 200000000}}),
                audio_streams=({'index': 1, 'codec_name': 'ac3', 'language': 'rus', 'channels': 2, 'sample_rate': '48000', 'bit_rate': '384000'},),
                selected_audio=(1,), source_chapters=(0., 900., 1800.))
            window._scanned(path, info, '')
        window.movies.setCurrentRow(0)
        window.menu_title.setText('Город после полуночи'); window._refresh_buttons()
        editor = window.menu_editor
        window.tabs.setCurrentWidget(editor)
        if args.enhanced:
            panel = editor.button_inspector
            panel.scope.setCurrentIndex(1)
            panel.change_size('button_width', 650)
            panel.change_size('button_height', 92)
            panel.change_style({'radius': 18, 'padding_x': 32, 'font_size': 30})
            panel.change_layout('mode', 'grid')
            panel.apply_preset(panel.preset.findData('glass'))
            editor.preview_selection('catalog:movie:0')
        app.processEvents(); QTest.qWait(350); app.processEvents()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.output), 'JPG', 90):
            raise RuntimeError('Could not save screenshot')
        if args.enhanced:
            scroll = editor.inspector_tabs.widget(2)
            group = next(g for g in panel.findChildren(QGroupBox) if g.title() == 'Оформление кнопок')
            scroll.verticalScrollBar().setValue(group.mapTo(scroll.widget(), QPoint()).y()-8)
            app.processEvents()
            window.grab().save(str(args.output.with_stem(args.output.stem+'-styles')), 'JPG', 90)
            editor.preview_mode.setChecked(True); QTest.qWait(550)
            window.grab().save(str(args.output.with_stem(args.output.stem+'-preview')), 'JPG', 90)
        window.capacity_clock.stop(); window.hide()


if __name__ == '__main__':
    main()
