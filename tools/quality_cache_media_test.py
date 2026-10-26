"""Encode real trials once, save/reopen a project, restore scores without encoding."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QListWidgetItem
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt
from core import probe_media
from quality_dialog import QualityDialog
from main import Window, interface_style
from settings import write_json
from quality_trial_media_test import wait


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = (args.output/("run-'проект, "+str(time.time_ns()))).resolve()
    output.mkdir(parents=True)
    config = {name: str((args.tools/(name+'.exe')).resolve()) for name in ('ffmpeg','ffprobe','tsMuxeR')}
    config['tsmuxer'] = config.pop('tsMuxeR')
    config.update(output=str(output), ui_theme='graphite')
    source = output/'фильм.mkv'
    subprocess.run([config['ffmpeg'], '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'testsrc2=s=640x360:r=25:d=4', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
        '-pix_fmt', 'yuv420p', str(source)], check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    info = probe_media(str(source), config['ffprobe'])
    app = QApplication.instance() or QApplication([])
    from disc_menu import initialize_fonts
    initialize_fonts()
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    app.setStyleSheet(interface_style('graphite'))
    dialog = QualityDialog(info, config, 8_000_000, encoder=0)
    dialog.mode.setCurrentIndex(1)
    dialog.start.setTime(dialog.start.minimumTime())
    dialog.duration.setValue(1)
    dialog.retry_rates.setChecked(False)
    dialog._begin()
    temporary = Path(dialog.temporary.name)
    dialog.show()
    wait(app, lambda: not dialog._busy() and dialog.result is not None, seconds=180)
    assert dialog.saved_record and dialog.saved_record['scenes'][0]['variants'][0]['quality']['vmaf']
    dialog.reject()
    assert not temporary.exists()
    record = dialog.saved_record
    dialog.deleteLater()
    app.processEvents()
    with patch('main.load_settings', return_value=config):
        window = Window()
    item = QListWidgetItem(info.name)
    item.setData(Qt.UserRole, info.path)
    window.movies.addItem(item)
    window._scanned(info.path, info, '')
    window.encoder.setCurrentIndex(0)
    window.encode_speed.setCurrentIndex(1)
    window.quality_records[info.path] = record
    project = output/'saved.dfr.json'
    write_json(project, window._project_data())
    assert project.stat().st_size < 100_000
    window.new_project()
    window._load_project(str(project))
    wait(app, lambda: not (window.scan_thread and window.scan_thread.isRunning()) and info.path in window.media)
    loaded = window.media[info.path]
    restored = QualityDialog(loaded, config, 12_000_000, encoder=0, saved_record=window.quality_records[info.path])
    restored.show()
    with patch('quality_dialog.run_quality_search', side_effect=AssertionError('Unexpected encoding')):
        wait(app, lambda: restored.result is not None and not restored._busy())
    assert restored.result['archived'] and restored.apply_button.isEnabled()
    assert restored.table.rowCount() == 3 and not restored.play.isEnabled()
    restored.grab().save(str(output/'restored-quality.png'))
    restored._apply()
    assert restored.selected_rate in record['plan']['rates']
    restored.deleteLater()
    window.resize(1500, 950)
    window.tabs.setCurrentIndex(4) if hasattr(window, 'tabs') else None
    window.menu_editor.show()
    window.menu_editor.resize(1300, 800)
    window.menu_editor.remote_buttons['right'].click()
    window.menu_editor.remote_buttons['left'].click()
    app.processEvents()
    window.menu_editor.grab().save(str(output/'menu-controls.png'))
    window.capacity_clock.stop()
    window.deleteLater()
    app.processEvents()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original
    write_json(output/'RESULT.json', dict(passed=True, saved_project=str(project),
        source_sha256=original, temp_videos_removed=True, restored_without_encoding=True,
        applied_rate=restored.selected_rate, project_bytes=project.stat().st_size))
    print('PASS: '+str(output), flush=True)


if __name__ == '__main__':
    main()
