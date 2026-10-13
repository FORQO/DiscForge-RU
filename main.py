"""DiscForge RU: Blu-ray authoring desktop application, requires PySide6 and external tools.

Creates chapter-enabled BDMV/ISO with customizable BD-J menus.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from dataclasses import replace

from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPixmap, QColor, QFont, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QStatusBar, QTabWidget, QVBoxLayout,
    QWidget, QScrollArea,
)

from core import (CAPACITIES, SUPPORTED, DiscForgeError, MediaInfo,
                  create_bluray, probe_media, validate_chapters, nvenc_available, bitrate_plan, prepare_processing)
from smart_copy import analyze_copy, MODES
from build_timing import BuildEstimate, stage_plan
from settings import load as load_settings, save as save_settings, write_json
from menu_editor import MenuEditor
from disc_menu import MenuDesign, initialize_fonts
from image_tools import ImageTools
from audio_panel import AudioPanel
from subtitle_panel import SubtitlePanel
from subtitles import select_subtitles, validate_selection
from audio_selection import selected_tracks, select_tracks

VERSION = '1.2.0'
LOGO = Path(__file__).parent / 'assets/brand/diskforge-logo.png'


def timecode(seconds: float) -> str:
    n = round(seconds)
    hours, minutes = divmod(n, 3600)
    minutes, secs = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{secs:02}'


def parse_timecode(code: str) -> float:
    parts = code.strip().split(':')
    if len(parts) != 3:
        raise ValueError('Формат времени: ЧЧ:ММ:СС')
    h, m, s = (int(p) for p in parts)
    if min(h, m, s) < 0 or m > 59 or s > 59:
        raise ValueError('Недопустимое время')
    return float(h * 3600 + m * 60 + s)


class ScanThread(QThread):
    result = Signal(str, object, str)
    message = Signal(str)

    def __init__(self, paths: list[str], probe: str, ffmpeg="ffmpeg"):
        super().__init__()
        self.paths = paths
        self.probe = probe
        self.ffmpeg = ffmpeg
        self.cancel_event = threading.Event()

    def run(self) -> None:
        for path in self.paths:
            try:
                if self.cancel_event.is_set():
                    self.result.emit(path, None, 'Анализ отменён.')
                    continue
                info = probe_media(path, self.probe)
                self.message.emit('Проверяем потоки: ' + Path(path).name + ' (без декодирования; может потребоваться чтение всего файла)…')
                report = analyze_copy(info, self.probe, self.ffmpeg, self.cancel_event)
                self.result.emit(path, replace(info, copy_report=report), '')
            except Exception as exc:  # user-supplied corrupt media must not crash UI
                self.result.emit(path, None, str(exc))


class BuildThread(QThread):
    message = Signal(str)
    progress_changed = Signal(int)
    timing_changed = Signal(str, float)
    item_started = Signal(str)
    item_done = Signal(str, str)
    failed = Signal(str, str)
    finished_all = Signal(bool)

    def __init__(self, jobs: list[tuple[MediaInfo, list[float]]], config: dict, profile: str,
                 as_iso: bool, nvenc: bool, menu: MenuDesign | None = None, auto=False, fast=True, processing_mode="encode"):
        super().__init__()
        self.jobs = jobs
        self.config = config
        self.profile = profile
        self.as_iso = as_iso
        self.nvenc = nvenc
        self.auto = auto
        self.processing_mode = processing_mode
        self.fast = fast
        self.menu = menu
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        all_successful = True
        try:
            prepared = []
            selected_modes = []
            self.timing_changed.emit('Проверка режима обработки', -1)
            for media, chapters in self.jobs:
                media, mode, reason = prepare_processing(media, self.processing_mode, self.profile,
                    self.config.get('ffprobe', 'ffprobe'), self.config['ffmpeg'], self.cancel_event, self.message.emit)
                self.message.emit(media.name + ': ' + reason)
                prepared.append((media,chapters))
                selected_modes.append(mode)
            self.jobs = prepared
            if self.auto and 'encode' in selected_modes:
                self.timing_changed.emit('Проверка видеокарты', -1)
                self.nvenc = nvenc_available(self.config['ffmpeg'], self.cancel_event, self.message.emit)
            plans = []
            for (media,_), mode in zip(self.jobs,selected_modes):
                count = len(selected_tracks(media))
                stream_size = (media.copy_report.video_bytes + media.copy_report.for_selection(media).audio_bytes) if mode != 'encode' else media.duration*(bitrate_plan(media.duration,self.profile,count)+640000*count)/8
                plan = stage_plan(media.duration, stream_size, self.as_iso, self.menu is not None, self.nvenc)
                if mode != 'encode':
                    plan['encode'] = max(2,stream_size/100_000_000, media.duration/50 if mode=='video_copy' else 0)
                plans.append(plan)
        except Exception as exc:
            self.message.emit(str(exc))
            self.finished_all.emit(False)
            return
        estimate = BuildEstimate(plans)
        total_duration = sum(m.duration for m, _ in self.jobs)
        done_duration = 0
        names = {'encode': 'Кодирование видео и звука', 'mux': 'Сборка Blu-ray',
                 'menu': 'Меню и проверка BDMV', 'iso': 'Создание ISO', 'verify': 'Проверка результата'}
        for i, (media, chapters) in enumerate(self.jobs, 1):
            if self.cancel_event.is_set():
                all_successful = False
                break
            self.item_started.emit(media.path)
            self.message.emit(f'[{i}/{len(self.jobs)}] {media.name}')
            def progress(value):
                self.progress_changed.emit(min(99, int((done_duration+media.duration*value/100)/total_duration*100)))
            def timing(stage, fraction):
                label = ('Копирование потоков' if selected_modes[i-1]=='copy' else 'Копирование видео + AC-3') if stage=='encode' and selected_modes[i-1]!='encode' else names[stage]
                self.timing_changed.emit(f'{i}/{len(self.jobs)} · {label}', estimate.update(i-1, stage, fraction))
            try:
                target = create_bluray(
                    media, self.profile, self.config['output'], self.config['ffmpeg'],
                    self.config['tsmuxer'], self.as_iso, self.nvenc,
                    self.cancel_event, self.message.emit, progress,
                    chapters=chapters,
                    menu=self.menu, fast=self.fast, observer=timing, processing_mode=selected_modes[i-1], ffprobe=self.config.get("ffprobe", "ffprobe"),
                )
                self.item_done.emit(media.path, str(target))
            except Exception as exc:
                all_successful = False
                self.failed.emit(media.path, str(exc))
                if self.cancel_event.is_set():
                    break
            done_duration += media.duration
        if all_successful and not self.cancel_event.is_set():
            self.progress_changed.emit(100)
        self.finished_all.emit(all_successful and not self.cancel_event.is_set())


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        initialize_fonts()
        self.setFont(QFont('Noto Sans', 10))
        self.setWindowIcon(QIcon(str(LOGO)))
        self.ui_theme = 'graphite'
        self.setWindowTitle('DiskForge — мастер Blu-ray | ' + VERSION)
        self.resize(1190, 760)
        self.setMinimumSize(920, 620)
        self.setAcceptDrops(True)
        self.media: dict[str, MediaInfo] = {}
        self.chapters: dict[str, list[float]] = {}
        self.audio_selections: dict[str, list[int]] = {}
        self.subtitle_selections = {}
        self.scan_thread: ScanThread | None = None
        self.build_thread: BuildThread | None = None
        self.build_started = None
        self.eta_remaining = -1
        self.eta_updated = 0
        self.build_clock = QTimer(self)
        self.build_clock.setInterval(1000)
        self.build_clock.timeout.connect(self._tick_build_time)
        self._build_ui()
        self._restore()
        self._refresh_buttons()

    def _build_ui(self) -> None:
        host = QWidget()
        self.setCentralWidget(host)
        root = QVBoxLayout(host)
        root.setSpacing(12)
        root.setContentsMargins(18, 16, 18, 12)

        header = QHBoxLayout()
        heading = QLabel()
        heading.setPixmap(QPixmap(str(LOGO)).scaled(120, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        heading.setAccessibleName('DiskForge')
        heading.setTextFormat(Qt.RichText)
        heading.setObjectName('brand')
        header.addWidget(heading)
        header.addStretch()
        tag = QLabel('БЕСПЛАТНО  ·  OPEN SOURCE  ·  ' + VERSION)
        tag.setObjectName('tag')
        header.addWidget(tag)
        root.addLayout(header)

        warning = QLabel('Добавьте фильм, выберите звук, настройте главы и меню. Перед записью '
                         'проверьте ISO в VLC и совместимость с вашим Blu-ray-плеером.')
        warning.setWordWrap(True)
        warning.setObjectName('warning')
        root.addWidget(warning)

        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 4, 0)
        left_box.addWidget(QLabel('ФИЛЬМЫ · КАЖДЫЙ НА ОТДЕЛЬНОМ ДИСКЕ'))
        self.movies = QListWidget()
        self.movies.currentItemChanged.connect(lambda *_: self._select_media())
        left_box.addWidget(self.movies, 1)
        line = QHBoxLayout()
        self.add_button = QPushButton('+ Добавить видео')
        self.add_button.clicked.connect(self.add_media)
        self.remove_button = QPushButton('Удалить')
        self.remove_button.clicked.connect(self.remove_media)
        line.addWidget(self.add_button, 1)
        line.addWidget(self.remove_button)
        left_box.addLayout(line)
        self.details = QLabel('Перетащите видео в окно или нажмите «Добавить видео».')
        self.details.setObjectName('details')
        self.details.setWordWrap(True)
        self.details.setMinimumHeight(90)
        left_box.addWidget(self.details)
        splitter.addWidget(left)

        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setSizes([450, 690])

        build_tab = QWidget()
        build_box = QVBoxLayout(build_tab)
        build_box.setSpacing(12)
        profile_box = QGroupBox('Параметры Blu-ray')
        form = QFormLayout(profile_box)
        self.profile = QComboBox()
        self.profile.addItems(list(CAPACITIES))
        self.profile.setCurrentText('BD25')
        form.addRow('Ёмкость носителя:', self.profile)
        self.output_mode = QComboBox()
        self.output_mode.addItems(['ISO-образ', 'Папка BDMV'])
        form.addRow('Формат результата:', self.output_mode)
        self.processing_mode = QComboBox()
        self.processing_mode.addItems(['Авто — по анализу фильма', 'Без перекодирования видео и звука', 'Копировать видео, звук → AC-3', 'Перекодировать видео и звук'])
        form.addRow('Обработка фильма:', self.processing_mode)
        self.recommendation = QLabel('Добавьте фильм: программа проверит возможность сборки без потери качества.')
        self.recommendation.setWordWrap(True)
        self.recommendation.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.recommendation.setObjectName('hint')
        self.apply_recommendation = QPushButton('Использовать рекомендацию')
        self.apply_recommendation.clicked.connect(self._use_recommendation)
        form.addRow(self.recommendation)
        form.addRow(self.apply_recommendation)
        self.profile.currentTextChanged.connect(self._update_recommendation)

        self.encoder = QComboBox()
        self.encoder.addItems(['CPU x264 — надёжнее', 'NVIDIA NVENC — быстрее, экспериментально', 'Авто — NVIDIA при наличии, иначе CPU'])
        self.encoder.setCurrentIndex(2)
        self.encoder.setToolTip('Используется только при перекодировании видео. При копировании видеокарта не нужна.')
        form.addRow('Видеокодер:', self.encoder)
        self.encode_speed = QComboBox()
        self.encode_speed.addItems(['Быстро', 'Сбалансированно'])
        self.encode_speed.setToolTip('Быстро: NVENC p3 / x264 fast. Сбалансированно: p5 / medium — лучше детализация при том же битрейте.')
        form.addRow('Скорость:', self.encode_speed)
        build_box.addWidget(profile_box)

        path_box = QGroupBox('Инструменты и выходная папка')
        paths = QFormLayout(path_box)
        self.path_fields: dict[str, QLineEdit] = {}
        for key, label in [
            ('ffmpeg', 'ffmpeg:'), ('ffprobe', 'ffprobe:'),
            ('tsmuxer', 'tsMuxer:'), ('output', 'Готовые диски:'),
        ]:
            edit = QLineEdit()
            self.path_fields[key] = edit
            row = QHBoxLayout()
            row.addWidget(edit, 1)
            browse = QPushButton('…')
            browse.setFixedWidth(36)
            browse.clicked.connect(lambda _=False, k=key: self._choose_path(k))
            row.addWidget(browse)
            paths.addRow(label, row)
        build_box.addWidget(path_box)
        info = QLabel('Без перекодирования сохраняются исходное видео и выбранные совместимые аудиодорожки. При полном перекодировании: H.264 1080p 23,976 кадр/с + AC-3. '
                      'Выберите аудио и субтитры в соответствующих разделах. '
                      'Сначала проверьте ISO на программном проигрывателе и бытовом плеере.')
        info.setWordWrap(True)
        info.setObjectName('hint')
        build_box.addWidget(info)
        build_box.addStretch()
        build_scroll = QScrollArea()
        build_scroll.setWidgetResizable(True)
        build_scroll.setWidget(build_tab)
        self.tabs.addTab(build_scroll, '1 · Подготовка')
        self.audio_panel = AudioPanel()
        self.audio_panel.selection_changed.connect(self._audio_changed)
        self.tabs.addTab(self.audio_panel, '2 · Аудио')

        self.subtitle_panel = SubtitlePanel()
        self.subtitle_panel.selection_changed.connect(self._subtitles_changed)
        self.tabs.addTab(self.subtitle_panel, '3 · Субтитры')

        chapter_tab = QWidget()
        chapter_box = QVBoxLayout(chapter_tab)
        chapter_help = QLabel('Главы — это закладки внутри одного фильма. Кнопка главы в меню запускает фильм с указанного времени.\n'
                             'Введите время начала и нажмите «Добавить главу». Для изменения выберите главу в списке, задайте новое время и нажмите «Изменить время».')
        chapter_help.setWordWrap(True)
        chapter_box.addWidget(chapter_help)
        self.chapter_status = QLabel('Сначала добавьте фильм и выберите его слева.')
        chapter_box.addWidget(self.chapter_status)
        self.chapter_list = QListWidget()
        self.chapter_list.currentItemChanged.connect(self.select_chapter_time)
        self.chapter_list.itemDoubleClicked.connect(lambda *_: self.chapter_time.setFocus())
        chapter_box.addWidget(self.chapter_list, 1)
        chapter_edit = QHBoxLayout()
        chapter_edit.addWidget(QLabel('Начало (чч:мм:сс):'))
        self.chapter_time = QLineEdit('00:00:00')
        self.chapter_time.setInputMask('00:00:00;_')
        self.chapter_time.setMaximumWidth(110)
        self.chapter_time.setAccessibleName('Время начала главы')
        chapter_edit.addWidget(self.chapter_time)
        self.add_chapter_button = QPushButton('+ Добавить главу')
        self.add_chapter_button.clicked.connect(self.add_chapter)
        chapter_edit.addWidget(self.add_chapter_button)
        self.edit_chapter_button = QPushButton('Изменить время')
        self.edit_chapter_button.clicked.connect(self.edit_chapter)
        chapter_edit.addWidget(self.edit_chapter_button)
        chapter_box.addLayout(chapter_edit)
        chapter_actions = QHBoxLayout()
        self.auto_interval = QSpinBox()
        self.auto_interval.setRange(1, 60)
        self.auto_interval.setValue(10)
        self.auto_interval.setSuffix(' мин')
        chapter_actions.addWidget(self.auto_interval)
        self.auto_chapter_button = QPushButton('Разбить по интервалу')
        self.auto_chapter_button.clicked.connect(self.auto_chapters)
        chapter_actions.addWidget(self.auto_chapter_button)
        self.delete_chapter_button = QPushButton('Удалить главу')
        self.delete_chapter_button.clicked.connect(self.remove_chapter)
        chapter_actions.addWidget(self.delete_chapter_button)
        chapter_box.addLayout(chapter_actions)
        note = QLabel('Глава 1 — начало фильма, 00:00:00. Её нельзя удалить или сдвинуть. '
                      'Добавленные главы автоматически появляются в меню «Выбор главы». Удаление главы не вырезает видео.')
        note.setObjectName('hint')
        note.setWordWrap(True)
        chapter_box.addWidget(note)
        self.tabs.addTab(chapter_tab, '4 · Главы')

        self.menu_editor = MenuEditor()
        self.menu_title = self.menu_editor.title
        menu_scroll = QScrollArea()
        menu_scroll.setWidgetResizable(True)
        menu_scroll.setWidget(self.menu_editor)
        self.tabs.addTab(menu_scroll, '5 · Меню')
        self.image_tools = ImageTools(self.menu_editor)
        self.image_tools.menu_panel.edit_requested.connect(lambda: self.tabs.setCurrentWidget(menu_scroll))
        self.tabs.addTab(self.image_tools, '6 · Запись ISO')

        logbar = QHBoxLayout()
        logbar.addWidget(QLabel('ЖУРНАЛ РАБОТЫ'))
        logbar.addStretch()
        clear = QPushButton('Очистить')
        clear.clicked.connect(lambda: self.log.clear())
        logbar.addWidget(clear)
        preview_iso = QPushButton('Проверить ISO в VLC')
        preview_iso.clicked.connect(self.image_tools.open_vlc)
        logbar.addWidget(preview_iso)
        open_output = QPushButton('Открыть результаты')
        open_output.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.path_fields['output'].text())))
        logbar.addWidget(open_output)
        root.addLayout(logbar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(125)
        self.log.setPlaceholderText('Ожидание действий…')
        root.addWidget(self.log)
        destination_row = QHBoxLayout()
        destination_row.addWidget(QLabel('Сохранить ISO / BDMV в:'))
        self.output_folder = QLineEdit()
        self.output_folder.setAccessibleName('Папка готового ISO')
        self.output_folder.textChanged.connect(self.path_fields['output'].setText)
        self.path_fields['output'].textChanged.connect(self.output_folder.setText)
        destination_row.addWidget(self.output_folder, 1)
        self.output_browse = QPushButton('Выбрать папку…')
        self.output_browse.clicked.connect(lambda: self._choose_path('output'))
        destination_row.addWidget(self.output_browse)
        root.addLayout(destination_row)
        timing_row = QHBoxLayout()
        self.elapsed_label = QLabel('Прошло: 00:00:00')
        self.remaining_label = QLabel('Осталось ≈ —')
        self.stage_label = QLabel('Ожидание')
        timing_row.addWidget(self.elapsed_label)
        timing_row.addWidget(self.remaining_label)
        timing_row.addStretch()
        timing_row.addWidget(self.stage_label)
        root.addLayout(timing_row)
        footer = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        footer.addWidget(self.progress, 1)
        self.cancel_button = QPushButton('Отмена')
        self.cancel_button.clicked.connect(self.cancel_build)
        footer.addWidget(self.cancel_button)
        self.build_button = QPushButton('СОЗДАТЬ BLU-RAY')
        self.build_button.setObjectName('primary')
        self.build_button.clicked.connect(self.start_build)
        footer.addWidget(self.build_button)
        root.addLayout(footer)
        self.setStatusBar(QStatusBar())

        project = self.menuBar().addMenu('Проект')
        for text, callback in [('Новый', self.new_project), ('Открыть проект…', self.open_project),
                               ('Сохранить проект…', self.save_project)]:
            action = QAction(text, self)
            action.triggered.connect(callback)
            project.addAction(action)
        theme_menu = self.menuBar().addMenu('Темы')
        self.theme_actions = {}
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        for label, key in [('Графит · DiskForge', 'graphite'), ('Синяя · классическая', 'blue')]:
            action = QAction(label, self, checkable=True)
            self.theme_group.addAction(action)
            self.theme_actions[key] = action
            theme_menu.addAction(action)
            action.triggered.connect(lambda checked=False, key=key: self.set_theme(key, save=True))
        help_menu = self.menuBar().addMenu('Справка')
        about = QAction('О программе', self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)

    def _restore(self) -> None:
        settings = load_settings()
        for key, val in settings.items():
            if key in self.path_fields:
                self.path_fields[key].setText(val)
        self.set_theme(settings.get('ui_theme', 'graphite'))

    def set_theme(self, key, save=False):
        self.ui_theme = key if key in self.theme_actions else 'graphite'
        QApplication.instance().setStyleSheet(interface_style(self.ui_theme))
        self.theme_actions[self.ui_theme].setChecked(True)
        if save:
            self._save_config()

    def _config(self) -> dict[str, str]:
        return dict({key: edit.text().strip() for key, edit in self.path_fields.items()}, ui_theme=self.ui_theme)

    def _save_config(self) -> None:
        try:
            save_settings(self._config())
        except OSError as exc:
            self._message('Не удалось сохранить настройки: ' + str(exc))

    def _choose_path(self, key: str) -> None:
        if key == 'output':
            path = QFileDialog.getExistingDirectory(self, 'Куда сохранять готовый ISO / BDMV?', self.path_fields['output'].text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, 'Путь к ' + key,
                          filter=('Все файлы (*)' if sys.platform == 'darwin' else 'Исполняемые файлы (*.exe);;Все файлы (*)'))
        if path:
            self.path_fields[key].setText(path)
            self._save_config()

    def add_media(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, 'Выберите видео', '',
                    'Видео (*.mkv *.mp4 *.mov *.m2ts *.mts *.ts *.avi);;Все файлы (*)')
        self._queue_scan(paths)

    def _queue_scan(self, paths: list[str]) -> None:
        paths = [str(Path(p).resolve()) for p in paths if Path(p).suffix.lower() in SUPPORTED]
        existing = {self.movies.item(i).data(Qt.UserRole) for i in range(self.movies.count())}
        fresh = [p for p in dict.fromkeys(paths) if p not in existing]
        if not fresh:
            return
        if self.scan_thread and self.scan_thread.isRunning():
            QMessageBox.information(self, 'Идёт анализ', 'Дождитесь окончания анализа предыдущих файлов.')
            return
        for path in fresh:
            item = QListWidgetItem('⏳  ' + Path(path).name)
            item.setData(Qt.UserRole, path)
            self.movies.addItem(item)
        self.scan_thread = ScanThread(fresh, self.path_fields['ffprobe'].text().strip(), self.path_fields['ffmpeg'].text().strip())
        self.scan_thread.message.connect(self._message)
        self.scan_thread.result.connect(self._scanned)
        self.scan_thread.finished.connect(self._refresh_buttons)
        self.scan_thread.start()
        if self.movies.currentRow() < 0:
            self.movies.setCurrentRow(0)
        self._refresh_buttons()
        self._message(f'Анализируем {len(fresh)} файлов…')

    def _scanned(self, path: str, info: MediaInfo | None, error: str) -> None:
        if info and path in self.audio_selections:
            try:
                info = select_tracks(info, self.audio_selections[path])
            except ValueError as exc:
                info, error = None, str(exc)
        if info and path in self.subtitle_selections:
            try:
                info = select_subtitles(info, self.subtitle_selections[path])
            except ValueError as exc:
                info, error = None, str(exc)
        for i in range(self.movies.count()):
            item = self.movies.item(i)
            if item.data(Qt.UserRole) == path:
                item.setText(('✅  ' if info else '❌  ') + Path(path).name)
                item.setToolTip(error if error else path)
                break
        if info:
            self.media[path] = info
            self.chapters.setdefault(path, [0.0])
            self._message(f'OK: {info.name} — {timecode(info.duration)}; {info.width}×{info.height}')
        else:
            self._message(f'ОШИБКА {Path(path).name}: {error}')
        self._select_media()
        self._refresh_buttons()

    def _selected_path(self) -> str | None:
        item = self.movies.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _update_recommendation(self):
        info = self.media.get(self._selected_path() or '')
        enabled = info is not None and info.copy_report is not None
        self.apply_recommendation.setEnabled(enabled)
        if not enabled:
            self.recommendation.setText('Добавьте фильм для анализа совместимости.')
            return
        mode, reason = info.copy_report.recommendation(info, CAPACITIES[self.profile.currentText()])
        note = '\nАнализ консервативный; совместимость на конкретном плеере не гарантируется. Дорожки выбираются в разделе «Аудио»; субтитры — в разделе «Субтитры».'
        self.recommendation.setText(reason+note)

    def _use_recommendation(self):
        # Auto handles every film separately, even in a mixed queue.
        self.processing_mode.setCurrentIndex(0)
        self._update_recommendation()

    def _audio_changed(self, indices):
        path = self._selected_path()
        info = self.media.get(path)
        if info is None:
            return
        try:
            self.media[path] = select_tracks(info, indices)
        except ValueError as exc:
            QMessageBox.warning(self, 'Аудиодорожки', str(exc))
            self.audio_panel.set_media(info)
            return
        self.audio_selections[path] = list(indices)
        self.menu_editor.set_audio(self.media[path])
        self._update_recommendation()

    def _subtitles_changed(self, items):
        path = self._selected_path()
        info = self.media.get(path)
        if info is None:
            return
        try:
            self.media[path] = select_subtitles(info, items)
        except ValueError as exc:
            QMessageBox.warning(self, 'Субтитры', str(exc))
            self.subtitle_panel.set_media(info)
            return
        self.subtitle_selections[path] = list(items)
        self.subtitle_panel.info = self.media[path]
        self.menu_editor.set_subtitles(self.media[path])

    def _select_media(self) -> None:
        path = self._selected_path()
        info = self.media.get(path or '')
        self.audio_panel.set_media(info)
        self.subtitle_panel.set_media(info)
        self.menu_editor.set_subtitles(info)
        self.menu_editor.set_audio(info)
        self.menu_editor.set_chapters(self.chapters.get(path, [0.0]))
        self.chapter_list.clear()
        if not info:
            self.chapter_status.setText('Сначала добавьте фильм и выберите его слева.')
            self.details.setText('Выберите успешно проанализированный фильм.')
        else:
            self.details.setText(
                f'{info.name}\n{timecode(info.duration)}  •  {info.size/1e9:.2f} ГБ  •  '
                f'{info.width}×{info.height}  •  {info.video_codec.upper()}\n'
                f'Аудиодорожек: {info.audio_tracks}; субтитров: {info.subtitle_tracks}; '
                f'HDR: {"да (UHD-модуль ещё не готов)" if info.is_hdr else "нет"}'
            )
            self.chapter_status.setText(f'Фильм: {info.name} · Длительность: {timecode(info.duration)} · Глав: {len(self.chapters.get(path, [0]))}')
            for index, t in enumerate(self.chapters.get(path, [0.0]), 1):
                item = QListWidgetItem(f'Глава {index}   ·   {timecode(t)}' + ('   ·   Начало фильма' if t == 0 else ''))
                item.setData(Qt.UserRole, t)
                self.chapter_list.addItem(item)
        self._update_recommendation()
        self._refresh_buttons()

    def auto_chapters(self) -> None:
        path = self._selected_path()
        info = self.media.get(path or '')
        if not info:
            return
        interval = self.auto_interval.value() * 60
        new = [0.0] + [float(i) for i in range(interval, int(info.duration), interval)]
        self.apply_chapters(path, new)

    def add_chapter(self) -> None:
        path = self._selected_path()
        info = self.media.get(path or '')
        if not info:
            return
        try:
            sec = parse_timecode(self.chapter_time.text())
            if sec >= info.duration or sec in self.chapters[path]:
                raise ValueError('Глава уже существует или находится за концом видео')
        except ValueError as exc:
            QMessageBox.warning(self, 'Некорректное время', str(exc))
            return
        self.apply_chapters(path, sorted([*self.chapters[path], sec]))

    def select_chapter_time(self, item, *_):
        if item:
            self.chapter_time.setText(timecode(float(item.data(Qt.UserRole))))
        if hasattr(self, 'build_button'):
            self._refresh_buttons()

    def apply_chapters(self, path, new, moved=None):
        try:
            validate_chapters(new, self.media[path].duration)
            self.menu_editor.retime_chapters(self.chapters[path], new, moved)
        except (ValueError, DiscForgeError) as exc:
            QMessageBox.warning(self, 'Главы', str(exc))
            return
        self.chapters[path] = new
        self._select_media()

    def edit_chapter(self):
        path = self._selected_path()
        item = self.chapter_list.currentItem()
        if not item or path not in self.media:
            return
        old = float(item.data(Qt.UserRole))
        if old == 0:
            QMessageBox.information(self, 'Начало фильма', 'Первая глава должна оставаться на 00:00:00.')
            return
        try:
            sec = parse_timecode(self.chapter_time.text())
            if sec == old:
                return
            if sec >= self.media[path].duration or sec in self.chapters[path]:
                raise ValueError('Укажите уникальное время внутри фильма.')
        except ValueError as exc:
            QMessageBox.warning(self, 'Некорректное время', str(exc))
            return
        new = sorted(sec if t == old else t for t in self.chapters[path])
        self.apply_chapters(path, new, {old: sec})

    def remove_chapter(self) -> None:
        path = self._selected_path()
        item = self.chapter_list.currentItem()
        if not path or not item:
            return
        sec = float(item.data(Qt.UserRole))
        if sec == 0:
            QMessageBox.information(self, 'Начало фильма', 'Главу 00:00:00 удалять нельзя.')
            return
        self.apply_chapters(path, [t for t in self.chapters[path] if t != sec])

    def remove_media(self) -> None:
        row = self.movies.currentRow()
        if row < 0:
            return
        item = self.movies.takeItem(row)
        path = item.data(Qt.UserRole)
        self.media.pop(path, None)
        self.chapters.pop(path, None)
        self.audio_selections.pop(path, None)
        self.subtitle_selections.pop(path, None)
        self.subtitle_panel.external.pop(path, None)
        self._select_media()
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        scanning = bool(self.scan_thread and self.scan_thread.isRunning())
        busy = bool(self.build_thread and self.build_thread.isRunning())
        editable = self._selected_path() in self.media and not scanning and not busy
        self.audio_panel.setEnabled(editable)
        self.subtitle_panel.setEnabled(editable)
        self.add_chapter_button.setEnabled(editable)
        self.auto_chapter_button.setEnabled(editable)
        self.chapter_time.setEnabled(editable)
        selected = self.chapter_list.currentItem()
        can_change = editable and selected is not None and selected.data(Qt.UserRole) != 0
        self.edit_chapter_button.setEnabled(can_change)
        self.delete_chapter_button.setEnabled(can_change)
        self.build_button.setEnabled(bool(self.media) and not scanning and not busy)
        self.cancel_button.setEnabled(busy or scanning)
        self.output_folder.setEnabled(not busy)
        self.output_browse.setEnabled(not busy)
        self.tabs.widget(0).setEnabled(not busy)
        self.add_button.setEnabled(not scanning and not busy)
        self.remove_button.setEnabled(not scanning and not busy and self.movies.currentItem() is not None)
        self.statusBar().showMessage(
            f'Файлов: {len(self.media)}  |  Сканирование: {"да" if scanning else "нет"}  |  '
            f'Сборка: {"идёт" if busy else "нет"}')

    def _message(self, value: str) -> None:
        self.log.appendPlainText(value)

    def start_build(self) -> None:
        if not self.media:
            return
        config = self._config()
        self._save_config()
        if not config['output']:
            QMessageBox.warning(self, 'Выходная папка', 'Укажите выходную папку.')
            return
        jobs = []
        for i in range(self.movies.count()):
            path = self.movies.item(i).data(Qt.UserRole)
            if path in self.media:
                jobs.append((self.media[path], list(self.chapters[path])))
        bad = [info.name for info, _ in jobs if info.is_hdr]
        if bad:
            QMessageBox.warning(self, 'HDR пока не поддерживается',
                                'Нельзя создавать обычный BD из HDR без правильного тонмаппинга.\n' + '\n'.join(bad))
            return
        try:
            menu = self.menu_editor.design()
        except ValueError as exc:
            QMessageBox.warning(self, 'Название меню', str(exc))
            return
        response = QMessageBox.question(self, 'Создание Blu-ray',
            f'Дисков: {len(jobs)} · {self.profile.currentText()} · {self.output_mode.currentText()}\n'
            + ('Меню: смотреть фильм и выбирать главы.\n' if menu else 'Без интерактивного меню.\n')
            + 'Режим: ' + self.processing_mode.currentText() + '\n'
            + '\n'.join(info.name + ': ' + (info.copy_report.recommendation(info, CAPACITIES[self.profile.currentText()])[1] if info.copy_report else 'анализ при сборке') for info, _ in jobs) + '\n'
            + 'Выбранные субтитры войдут в ISO отдельными дорожками. Исходники сохраняются.\n\nНачать сборку?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if response != QMessageBox.Yes:
            return
        self.progress.setValue(0)
        self.build_thread = BuildThread(jobs, config, self.profile.currentText(),
                                        self.output_mode.currentIndex() == 0,
                                        self.encoder.currentIndex() == 1, menu, auto=self.encoder.currentIndex() == 2, fast=self.encode_speed.currentIndex() == 0, processing_mode=MODES[self.processing_mode.currentIndex()])
        self.build_started = time.monotonic()
        self.eta_remaining = -1
        self.stage_label.setText("Подготовка")
        self._tick_build_time()
        self.build_clock.start()
        self.build_thread.timing_changed.connect(self._timing_update)
        self.build_thread.message.connect(self._message)
        self.build_thread.progress_changed.connect(self.progress.setValue)
        self.build_thread.item_started.connect(lambda p: self._message('Начинаем: ' + Path(p).name))
        self.build_thread.item_done.connect(self._item_done)
        self.build_thread.failed.connect(lambda p, err: self._message('ОШИБКА ' + Path(p).name + ': ' + err))
        self.build_thread.finished_all.connect(self._build_finished)
        self.build_thread.finished.connect(self._refresh_buttons)
        self.build_thread.start()
        self._refresh_buttons()

    def _timing_update(self, stage, remaining):
        self.stage_label.setText(stage)
        self.eta_remaining = remaining
        self.eta_updated = time.monotonic()
        self._tick_build_time()

    def _tick_build_time(self):
        if self.build_started is None:
            return
        now = time.monotonic()
        self.elapsed_label.setText('Прошло: ' + timecode(now-self.build_started))
        if self.eta_remaining < 0:
            self.remaining_label.setText('Осталось ≈ рассчитывается…')
        elif now-self.eta_updated > 15:
            self.remaining_label.setText('Осталось ≈ уточняется…')
        else:
            self.remaining_label.setText('Осталось ≈ ' + timecode(max(1, self.eta_remaining-(now-self.eta_updated))))

    def _build_finished(self, success: bool) -> None:
        self._tick_build_time()
        self.build_clock.stop()
        self.stage_label.setText('Готово' if success else 'Остановлено / ошибка')
        self.remaining_label.setText('Осталось: 00:00:00' if success else 'Осталось: —')
        self._message('Очередь завершена.' if success else 'Очередь завершилась с ошибками или была отменена.')

    def _item_done(self, path: str, destination: str) -> None:
        self._message('ГОТОВО: ' + destination)
        if destination.lower().endswith('.iso'):
            self.image_tools.path.setText(destination)
            self.image_tools.profile.setCurrentText(self.build_thread.profile)

    def cancel_build(self) -> None:
        if self.build_thread and self.build_thread.isRunning():
            self._message('Запрошена отмена операции…')
            self.build_thread.cancel()
        elif self.scan_thread and self.scan_thread.isRunning():
            self._message("Запрошена отмена анализа…")
            self.scan_thread.cancel_event.set()

    def _project_data(self) -> dict:
        self.menu_editor.preview.commit_edit()
        return {'format_version': 1, 'title': self.menu_title.text(),
                'menu': self.menu_editor.settings(),
                'files': [self.movies.item(i).data(Qt.UserRole) for i in range(self.movies.count())],
                'subtitles': self.subtitle_selections, 'audio': self.audio_selections, 'chapters': self.chapters, 'profile': self.profile.currentText(),
                'encoder': self.encoder.currentIndex(), 'encode_speed': self.encode_speed.currentIndex(), 'processing_mode': MODES[self.processing_mode.currentIndex()], 'output': self.output_folder.text().strip(), 'as_iso': self.output_mode.currentIndex() == 0}

    def save_project(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, 'Сохранить проект', 'DiscForge_project.dfr.json',
                                             'DiscForge RU (*.dfr.json)')
        if path:
            try:
                write_json(Path(path), self._project_data())
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, 'Ошибка сохранения', str(exc))
            else:
                self._message('Проект сохранён: ' + path)

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Открыть проект', '', 'DiscForge RU (*.dfr.json)')
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('format_version') != 1 or not isinstance(data.get('files'), list):
                raise ValueError('Неизвестный формат проекта')
            paths = [p for p in data['files'] if isinstance(p, str)]
            raw = data.get('chapters', {})
            if not isinstance(raw, dict):
                raise ValueError('Неверный раздел глав')
            # Validate everything before discarding the current project.
            profile = data.get('profile', 'BD25')
            if not isinstance(profile, str) or profile not in CAPACITIES:
                raise ValueError('Неизвестный профиль диска')
            title = data.get('title', 'Мой Blu-ray')
            menu = data.get('menu', {'enabled': False, 'theme': 'midnight'})
            if not isinstance(menu, dict) or not isinstance(menu.get('enabled'), bool):
                raise ValueError('Некорректные настройки меню')
            design = MenuDesign.from_dict(title, menu)
            if design.background:
                from disc_menu import background_image
                background_image(design.background)
            audio = data.get('audio', {})
            if not isinstance(audio, dict):
                raise ValueError('Неверный раздел аудио')
            loaded_audio = {}
            for p, indices in audio.items():
                if (not isinstance(p, str) or not isinstance(indices, list) or len(indices) > 32 or
                        any(type(i) is not int or i < 0 for i in indices) or len(indices) != len(set(indices))):
                    raise ValueError('Неверный выбор аудиодорожек')
                loaded_audio[str(Path(p).resolve())] = indices
            raw_subtitles = data.get('subtitles', {})
            if not isinstance(raw_subtitles, dict):
                raise ValueError('Неверный раздел субтитров')
            loaded_subtitles = {str(Path(p).resolve()): validate_selection(items) for p, items in raw_subtitles.items()}
            loaded_chapters = {}
            normalized_paths = []
            for p in paths:
                normalized = str(Path(p).resolve())
                normalized_paths.append(normalized)
                v = raw.get(p, [0.0])
                if not isinstance(v, list):
                    raise ValueError('Неверный список глав')
                validate_chapters(v)
                loaded_chapters[normalized] = [float(x) for x in v]
            if not self.new_project():
                return
            self.menu_editor.apply_design(design)
            self.menu_editor.enabled.setChecked(menu['enabled'])
            self.profile.setCurrentText(profile)
            self.encoder.setCurrentIndex(data.get('encoder') if data.get('encoder') in (0, 1, 2) else 2)
            self.encode_speed.setCurrentIndex(1 if data.get('encode_speed') == 1 else 0)
            mode = data.get('processing_mode', 'auto')
            self.processing_mode.setCurrentIndex(MODES.index(mode) if mode in MODES else 0)
            if isinstance(data.get('output'), str):
                self.output_folder.setText(data['output'])
            self.output_mode.setCurrentIndex(0 if data.get('as_iso', True) else 1)
            self.chapters.update(loaded_chapters)
            self.audio_selections.update(loaded_audio)
            self.subtitle_selections.update(loaded_subtitles)
            self._queue_scan(normalized_paths)
            self._message('Проект открыт: ' + path)
        except (OSError, ValueError, TypeError, OverflowError, DiscForgeError) as exc:
            QMessageBox.warning(self, 'Ошибка проекта', str(exc))

    def new_project(self) -> bool:
        if ((self.build_thread and self.build_thread.isRunning()) or
                (self.scan_thread and self.scan_thread.isRunning())):
            QMessageBox.warning(self, 'Операция выполняется', 'Дождитесь завершения текущей операции.')
            return False
        self.movies.clear()
        self.media.clear()
        self.chapters.clear()
        self.audio_selections.clear()
        self.subtitle_selections.clear()
        self.subtitle_panel.external.clear()
        self.chapter_list.clear()
        self.menu_editor.apply_design(MenuDesign('Моя коллекция Blu-ray'))
        self.menu_editor.enabled.setChecked(True)
        self.processing_mode.setCurrentIndex(0)
        self._select_media()
        return True

    def about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle('О DiskForge')
        box.setIconPixmap(QPixmap(str(LOGO)).scaled(192, 144, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        box.setText(f'DiskForge {VERSION} · MIT\n\nBlu-ray ISO, аудио и субтитры с выбором пультом, главы и редактируемое меню BD-J.')
        box.exec()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() and not (self.build_thread and self.build_thread.isRunning()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if not (self.build_thread and self.build_thread.isRunning()):
            self._queue_scan([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        if self.image_tools.is_running() or (self.build_thread and self.build_thread.isRunning()) or (
                self.scan_thread and self.scan_thread.isRunning()):
            QMessageBox.warning(self, 'Операция выполняется', 'Сначала завершите или отмените текущую операцию.')
            event.ignore()
            return
        self._save_config()
        super().closeEvent(event)


STYLE = '''
QWidget { background: #101724; color: #e3eaf6; font-size: 12px; }
QMainWindow, QTabWidget::pane { background: #101724; }
QLabel#brand { font-size: 26px; font-weight: 800; }
QLabel#tag { color: #95a3bb; font-size: 11px; letter-spacing: 1px; }
QLabel#section { font-size: 16px; font-weight: 700; }
QLabel#warning { background: #302925; border: 1px solid #80623c; color: #ffcf91; border-radius: 7px; padding: 10px; }
QLabel#details { background: #192234; border-radius: 7px; padding: 10px; color: #b9c9e0; }
QLabel#hint { color: #9bacc3; padding: 7px; }
QLineEdit, QListWidget, QPlainTextEdit, QComboBox, QSpinBox { background: #182337; border: 1px solid #34445e; border-radius: 6px; padding: 7px; selection-background-color: #3268b4; }
QGroupBox { border: 1px solid #33435a; border-radius: 9px; margin-top: 13px; padding: 15px 9px 9px 9px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 7px; }
QPushButton { background: #273952; border: 1px solid #3b5573; padding: 8px 13px; border-radius: 6px; }
QPushButton:hover { background: #355170; }
QPushButton:disabled { background: #1b2636; color: #62718a; border-color: #28354a; }
QPushButton#primary { background: #2478db; border-color: #2478db; font-weight: 700; color: #fff; }
QPushButton#primary:hover { background: #3491ff; }
QTabBar::tab { background: #1d2a3d; padding: 10px 16px; border: 1px solid #304259; }
QTabBar::tab:selected { background: #2b527f; }
QProgressBar { background: #1d293b; border: 1px solid #30435a; border-radius: 5px; text-align: center; }
QProgressBar::chunk { background: #3287e5; border-radius: 5px; }
QStatusBar { color: #9fb1c9; }
'''


def interface_style(theme):
    if theme == 'blue':
        return STYLE
    palette = {'#101724':'#202123', '#e3eaf6':'#eeeef0', '#95a3bb':'#abaeb3',
        '#192234':'#292b2e', '#b9c9e0':'#c8c9cc', '#9bacc3':'#b5b6bb',
        '#182337':'#282a2d', '#34445e':'#484b50', '#3268b4':'#825329',
        '#33435a':'#45484d', '#273952':'#383b40', '#3b5573':'#56595e',
        '#355170':'#494d52', '#1b2636':'#292b2e', '#62718a':'#777b80',
        '#28354a':'#3b3e43', '#2478db':'#e58a35', '#3491ff':'#f3a24f',
        '#1d2a3d':'#2d2f33', '#304259':'#45484d', '#2b527f':'#5f4632',
        '#1d293b':'#2b2d31', '#30435a':'#484b50', '#3287e5':'#ba6d2a', '#9fb1c9':'#b8bbc0'}
    value = STYLE
    for old, new in palette.items():
        value = value.replace(old, new)
    return value + '\nQPushButton#primary { color: #1c1c1c; }\nQMenu::item:selected { background: #825329; }'


def main() -> int:
    # Packaging smoke test: exercise the same window and resources offscreen.
    smoke_report = None
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        smoke_report = Path(sys.argv[2])
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setFont(QFont('Segoe UI', 10))
    w = Window()
    if smoke_report is not None:
        w.resize(1280, 880)
        w.show()
        app.processEvents()
        resources = Path(__file__).parent
        checks = {'window': w.tabs.count() == 6,
                  'menu_image': not w.menu_editor.preview.image.isNull(),
                  'xlet': (resources / 'assets/bdj/menu.jar').is_file(),
                  'windows_iso': sys.platform != 'win32' or (resources / 'tools/windows_iso.ps1').is_file()}
        if getattr(sys, 'frozen', False):
            from vlc_preview import app_root, pe_machine
            java = app_root() / 'runtime/java'
            checks['private_java'] = ((java / 'lib/rt.jar').is_file() and
                                      (java / 'bin/server/jvm.dll').is_file() and
                                      pe_machine(java / 'bin/server/jvm.dll') == 0x8664)
        checks['vlc_button'] = w.image_tools.vlc.text() == 'Проверить ISO в VLC'
        checks['iso_menu_update'] = w.image_tools.menu_panel.repair.isEnabled()
        checks['fullscreen_preview'] = w.menu_editor.fullscreen_button.isEnabled()
        write_json(smoke_report, {'version': VERSION, 'checks': checks, 'passed': all(checks.values())})
        # Do not save user settings from a packaging test.
        w.hide()
        return 0 if all(checks.values()) else 1
    w.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
