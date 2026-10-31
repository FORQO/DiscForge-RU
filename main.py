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
from PySide6.QtGui import QAction, QActionGroup, QIcon, QPixmap, QColor, QFont, QDesktopServices, QKeySequence
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSpinBox, QSplitter, QStatusBar, QTabWidget, QVBoxLayout,
    QWidget, QScrollArea, QSizePolicy, QStyle, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QStackedWidget, QDialog,
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
from source_chapters import titles_for_chapters, is_chapter_label
from gpu_monitor import GpuMonitor
from collection_titles import movie_title, normalize_names, validate_title
from bitrate_advisor import recommend as recommend_bitrate, analyze_bitrate, planned_rate, normalize_rates
from video_treatment import enhancement_for, project_enhancements

VERSION = '1.5.0'
LOGO = Path(__file__).parent / 'assets/brand/diskforge-logo.png'
ICON = Path(__file__).parent / 'assets/brand/diskforge.ico'


class MovieList(QListWidget):
    """Internal reordering and external video drops share the same list."""
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self.window().dragEnterEvent(event)
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            self.window().dragEnterEvent(event)
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if not self.window()._movies_editable():
            event.ignore()
        elif event.mimeData().hasUrls():
            self.window().dropEvent(event)
        else:
            super().dropEvent(event)


def timecode(seconds: float) -> str:
    n = round(seconds)
    hours, minutes = divmod(n, 3600)
    minutes, secs = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{secs:02}'


def parse_timecode(code: str) -> float:
    parts = code.strip().split(':')
    if len(parts) != 3:
        raise ValueError('Формат времени: ЧЧ:ММ:СС.ммм')
    import math
    h, m = (int(p) for p in parts[:2])
    s = float(parts[2])
    if not math.isfinite(s) or min(h, m, s) < 0 or m > 59 or s >= 60:
        raise ValueError('Недопустимое время')
    return float(h * 3600 + m * 60 + s)


def chapter_timecode(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    seconds, ms = divmod(milliseconds, 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}.{ms:03}'


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
                info = replace(info, copy_report=report)
                self.message.emit('Подбираем битрейт видео: ' + Path(path).name)
                analysis = analyze_bitrate(info, self.probe, self.cancel_event)
                self.result.emit(path, replace(info, video_analysis=analysis), '')
            except Exception as exc:  # user-supplied corrupt media must not crash UI
                self.result.emit(path, None, str(exc))


class BuildThread(QThread):
    message = Signal(str)
    progress_changed = Signal(int)
    timing_changed = Signal(str, float)
    verification_changed = Signal(object)
    item_started = Signal(str)
    item_done = Signal(str, str)
    failed = Signal(str, str)
    finished_all = Signal(bool)

    def __init__(self, jobs: list[tuple[MediaInfo, list[float]]], config: dict, profile: str,
                 as_iso: bool, nvenc: bool, menu: MenuDesign | None = None, auto=False, fast=True, processing_mode="encode", chapter_names=None):
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
        import copy
        self.chapter_names = copy.deepcopy(chapter_names)
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    def menu_for(self, media, chapters):
        if self.menu is None or self.chapter_names is None:
            return self.menu
        names = self.chapter_names.get(media.path, {})
        labels = {k: v for k, v in self.menu.labels.items() if not is_chapter_label(k)}
        labels.update(names.get('labels', {}))
        return replace(self.menu, labels=labels, chapter_titles=names.get('titles', titles_for_chapters(media, chapters)))

    def run(self) -> None:
        all_successful = True
        try:
            prepared = []
            selected_modes = []
            self.timing_changed.emit('Проверка режима обработки', -1)
            for media, chapters in self.jobs:
                requested = 'encode' if enhancement_for(self.config, media.path) else self.processing_mode
                if self.config.get('collection'):
                    from disc_capacity import video_mode
                    requested = video_mode(media, requested, 10**18)
                media, mode, reason = prepare_processing(media, requested, self.profile,
                    self.config.get('ffprobe', 'ffprobe'), self.config['ffmpeg'], self.cancel_event, self.message.emit)
                self.message.emit(media.name + ': ' + reason)
                prepared.append((media,chapters))
                selected_modes.append(mode)
            self.jobs = prepared
            if sys.platform == 'darwin':
                self.nvenc = False
            if self.auto and 'encode' in selected_modes and sys.platform != 'darwin':
                self.timing_changed.emit('Проверка видеокарты', -1)
                self.nvenc = nvenc_available(self.config['ffmpeg'], self.cancel_event, self.message.emit)
            if self.config.get('collection'):
                from disc_collection import create_collection
                target = create_collection(self.jobs, self.config, self.profile, self.as_iso, self.nvenc,
                    self.cancel_event, self.message.emit, lambda v: self.progress_changed.emit(int(v)), self.menu_for,
                    bitrate=self.config.get('collection_bitrate', 12_000_000), modes=selected_modes, fast=self.fast,
                    verification=self.verification_changed.emit, timing=self.timing_changed.emit)
                for media, _ in self.jobs:
                    self.item_done.emit(media.path, str(target))
                self.finished_all.emit(True)
                return
            plans = []
            for (media,_), mode in zip(self.jobs,selected_modes):
                count = len(selected_tracks(media))
                rate = self.config.get('video_rates', {}).get(str(Path(media.path).resolve()))
                stream_size = (media.copy_report.video_bytes + media.copy_report.for_selection(media).audio_bytes) if mode != 'encode' else media.duration*((rate if rate is not None else bitrate_plan(media.duration,self.profile,count))+640000*count)/8
                plan = stage_plan(media.duration, stream_size, self.as_iso, True, self.nvenc)
                plan['verify'] = max(2, stream_size * (3 if self.as_iso else 1) / 100_000_000)
                if mode != 'encode':
                    plan['encode'] = max(2,stream_size/100_000_000, media.duration/50 if mode=='video_copy' else 0)
                elif self.config.get('quiet_rate', 0):
                    plan['encode'] = max(plan['encode'], media.duration/self.config['quiet_rate'])
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
            def verification(event):
                self.verification_changed.emit(event)
                if event and event['scope'] == 'iso-final':
                    fraction = event['done']/event['total'] if event['total'] else 0
                    remaining = estimate.update(i-1, 'verify', min(.999, fraction))
                    if i == len(self.jobs):
                        remaining = event['remaining'] if event['state'] != 'waiting' else -1
                    self.timing_changed.emit(f"{i}/{len(self.jobs)} · {event['phase']}", remaining)
            try:
                target = create_bluray(
                    media, self.profile, self.config['output'], self.config['ffmpeg'],
                    self.config['tsmuxer'], self.as_iso, self.nvenc,
                    self.cancel_event, self.message.emit, progress,
                    chapters=chapters,
                    menu=self.menu_for(media, chapters), fast=self.fast, observer=timing, processing_mode=selected_modes[i-1], ffprobe=self.config.get("ffprobe", "ffprobe"),
                    resilient=True, gpu_frames=self.config.get('gpu_frames', True),
                    enhance=enhancement_for(self.config, media.path), quiet_rate=self.config.get('quiet_rate', 0.0),
                    verification=verification,
                    video_bitrate=self.config.get('video_rates', {}).get(str(Path(media.path).resolve())),
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
        self.setFont(QApplication.font())
        self.setWindowIcon(QIcon(str(ICON)))
        self.ui_theme = 'graphite'
        self.setWindowTitle('DiskForge Studio · ' + VERSION)
        self.resize(1380, 850)
        self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)
        self.media: dict[str, MediaInfo] = {}
        self.chapters: dict[str, list[float]] = {}
        self.chapter_names = {}
        self.movie_names = {}
        self.bitrate_overrides = {}
        self.quality_records = {}
        self.video_enhancements = {}
        self._chapter_owner = None
        self.audio_selections: dict[str, list[int]] = {}
        self.audio_modes = {}
        self._close_after_build = False
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
        self.tabs.currentChanged.connect(self._chapter_tab_changed)
        self._restore()
        self._refresh_buttons()
        self.capacity_clock = QTimer(self)
        self.capacity_clock.setInterval(1000)
        self.capacity_clock.timeout.connect(self._update_capacity)
        self.capacity_clock.start()
        for box in (self.disc_mode, self.profile, self.processing_mode):
            box.currentIndexChanged.connect(self._update_capacity)
        self.disc_mode.currentIndexChanged.connect(self._update_recommendation)
        self.disc_mode.currentIndexChanged.connect(self._sync_collection)
        self.collection_bitrate.valueChanged.connect(self._update_capacity)
        self.bitrate_policy.currentIndexChanged.connect(self._bitrate_settings_changed)
        self.custom_bitrate.toggled.connect(self._custom_bitrate_changed)
        self.movie_bitrate.valueChanged.connect(self._custom_bitrate_changed)
        self.processing_mode.currentIndexChanged.connect(self._update_recommendation)
        self.collection_bitrate.valueChanged.connect(self._update_recommendation)

    def _build_ui(self) -> None:
        host = QWidget()
        self.setCentralWidget(host)
        root = QVBoxLayout(host)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)
        header_widget = QWidget(); header_widget.setObjectName('studioHeader')
        header = QHBoxLayout(header_widget)
        header.setContentsMargins(20, 12, 20, 12)
        brand = QLabel('DiskForge'); brand.setObjectName('studioBrand')
        header.addWidget(brand)
        self.project_heading = QLabel('Новый проект'); self.project_heading.setObjectName('studioStatus')
        header.addSpacing(14); header.addWidget(self.project_heading); header.addStretch()
        save_project = QPushButton('Сохранить проект'); save_project.setObjectName('ghost')
        save_project.clicked.connect(self.save_project); header.addWidget(save_project)
        root.addWidget(header_widget)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        self.workspace_splitter = splitter
        root.addWidget(splitter, 1)
        left = QWidget(); left.setObjectName('studioLibrary')
        left.setMinimumWidth(210); left.setMaximumWidth(290)
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(14, 18, 14, 14)
        left_box.setSpacing(10)
        self.library_heading = QLabel('ФИЛЬМЫ'); self.library_heading.setObjectName('studioSection')
        left_box.addWidget(self.library_heading)
        self.movies = MovieList()
        self.movies.setObjectName('studioMovies')
        from studio_ui import MovieDelegate
        self.movies.setItemDelegate(MovieDelegate(self))
        self.movies.setSpacing(3)
        self.movies.setTextElideMode(Qt.ElideMiddle)
        self.movies.setDragDropMode(QListWidget.InternalMove)
        self.movies.setDefaultDropAction(Qt.MoveAction)
        self.movies.setToolTip('Порядок в списке — порядок фильмов в меню диска. Перетаскивайте строки или используйте стрелки.')
        self.movies.currentItemChanged.connect(lambda *_: self._select_media())
        self.movies.model().rowsMoved.connect(lambda *_: self._refresh_buttons())
        left_box.addWidget(self.movies, 1)
        left_box.addWidget(QLabel('Название фильма в меню'))
        self.movie_title_edit = QLineEdit()
        self.movie_title_edit.setMaxLength(80)
        self.movie_title_edit.setClearButtonEnabled(True)
        self.movie_title_edit.setToolTip('До 80 символов. Очистите поле, чтобы вернуть имя файла. Сам файл не переименовывается.')
        self.movie_title_edit.textEdited.connect(self._rename_movie)
        self.movies.itemDoubleClicked.connect(lambda *_: self._focus_movie_title())
        left_box.addWidget(self.movie_title_edit)
        order_row = QHBoxLayout()
        order_row.addWidget(QLabel('Порядок'), 1)
        self.movie_up = QPushButton('↑')
        self.movie_down = QPushButton('↓')
        for button, direction, caption in ((self.movie_up, -1, 'Переместить фильм вверх'), (self.movie_down, 1, 'Переместить фильм вниз')):
            button.setFixedWidth(36)
            button.setToolTip(caption)
            button.setAccessibleName(caption)
            button.clicked.connect(lambda checked=False, d=direction: self._move_movie(d))
            order_row.addWidget(button)
        left_box.addLayout(order_row)
        line = QHBoxLayout()
        self.add_button = QPushButton('+ Добавить')
        self.add_button.clicked.connect(self.add_media)
        self.remove_button = QPushButton('Удалить')
        self.remove_button.clicked.connect(self.remove_media)
        line.addWidget(self.add_button, 1)
        line.addWidget(self.remove_button)
        left_box.addLayout(line)
        self.disc_mode = QComboBox()
        self.disc_mode.addItems(['Общий диск', 'Отдельные диски'])
        left_box.addWidget(self.disc_mode)
        self.capacity_bar = QProgressBar()
        self.capacity_bar.setRange(0, 25000)
        self.capacity_bar.setObjectName('capacityMeter')
        self.capacity_bar.setTextVisible(False)
        self.capacity_summary = QLabel('BD25 · 0 / 25 ГБ')
        self.capacity_summary.setObjectName('studioStatus')
        left_box.addWidget(self.capacity_summary)
        left_box.addWidget(self.capacity_bar)
        self.capacity_label = QLabel()
        self.capacity_label.setWordWrap(True)
        self.capacity_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.capacity_label.hide()
        capacity_details = QPushButton('Расчёт места'); capacity_details.setObjectName('ghost')
        capacity_details.clicked.connect(lambda: QMessageBox.information(self, 'Заполнение диска', self.capacity_label.text()))
        left_box.addWidget(capacity_details)
        self.details = QLabel('Перетащите видео в окно или нажмите «Добавить видео».')
        self.details.setObjectName('details')
        self.details.setWordWrap(True)
        self.details.setMinimumHeight(0)
        self.details.setMaximumHeight(85)
        self.details.hide()
        file_details = QPushButton('Сведения о файле'); file_details.setObjectName('ghost')
        file_details.clicked.connect(lambda: QMessageBox.information(self, 'Выбранный фильм', self.details.text()))
        left_box.addWidget(file_details)
        splitter.addWidget(left)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.studio_stack = QStackedWidget()
        self.studio_stack.addWidget(self.tabs)
        welcome = QWidget(); welcome_layout = QVBoxLayout(welcome)
        welcome_layout.setContentsMargins(50, 40, 50, 40)
        welcome_layout.addStretch()
        welcome_title = QLabel('Ваша коллекция начинается здесь'); welcome_title.setObjectName('studioHeading'); welcome_title.setWordWrap(True)
        welcome_layout.addWidget(welcome_title)
        welcome_hint = QLabel('Добавьте фильмы, выберите дорожки и оформите меню Blu-ray.\nВидео можно перетащить прямо в окно.'); welcome_hint.setObjectName('hint'); welcome_hint.setWordWrap(True)
        welcome_layout.addWidget(welcome_hint)
        welcome_actions = QHBoxLayout()
        self.welcome_add = QPushButton('Добавить видео…'); self.welcome_add.setObjectName('primary'); self.welcome_add.clicked.connect(self.add_media)
        welcome_actions.addWidget(self.welcome_add); welcome_actions.addStretch(); welcome_layout.addLayout(welcome_actions)
        existing = QPushButton('Открыть готовый ISO'); existing.setObjectName('ghost')
        existing.clicked.connect(lambda: (self.tabs.setCurrentIndex(5), self._studio_update(), self.image_tools.choose()))
        welcome_layout.addWidget(existing, 0, Qt.AlignLeft)
        early_menu = QPushButton('Оформить меню заранее'); early_menu.setObjectName('ghost')
        early_menu.clicked.connect(self._open_empty_menu)
        welcome_layout.addWidget(early_menu, 0, Qt.AlignLeft)
        welcome_layout.addStretch()
        self.studio_stack.addWidget(welcome)
        splitter.addWidget(self.studio_stack)
        self.tabs.currentChanged.connect(lambda *_: self._studio_update())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 1150])

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
        self.processing_mode.addItems(['Авто — по анализу фильма', 'Без перекодирования видео и звука', 'Копировать видео, обработать звук', 'Перекодировать видео · звук по настройкам'])
        form.addRow('Обработка фильма:', self.processing_mode)
        self.bitrate_policy = QComboBox()
        self.bitrate_policy.addItem('Авто — отдельно для каждого фильма', 'auto')
        self.bitrate_policy.addItem('Ручной битрейт', 'manual')
        form.addRow('Битрейт при перекодировании:', self.bitrate_policy)
        self.collection_bitrate = QSpinBox()
        self.collection_bitrate.setRange(5, 28)
        self.collection_bitrate.setValue(12)
        self.collection_bitrate.setSuffix(' Мбит/с')
        self.collection_bitrate.setToolTip('Для общего диска при перекодировании. Копируемые потоки сохраняют исходный размер. Битрейт не уменьшается автоматически при добавлении фильмов.')
        form.addRow('Общий ручной битрейт:', self.collection_bitrate)
        self.custom_bitrate = QCheckBox('Задать битрейт для выбранного фильма')
        self.movie_bitrate = QDoubleSpinBox()
        self.movie_bitrate.setRange(5, 28)
        self.movie_bitrate.setDecimals(1)
        self.movie_bitrate.setSingleStep(.5)
        self.movie_bitrate.setSuffix(' Мбит/с')
        form.addRow(self.custom_bitrate, self.movie_bitrate)
        self.recommendation = QLabel('Добавьте фильм: программа проверит возможность сборки без потери качества.')
        self.recommendation.setWordWrap(True)
        self.recommendation.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.recommendation.setObjectName('hint')
        self.apply_recommendation = QPushButton('Применить автоподбор ко всем фильмам')
        self.apply_recommendation.setToolTip('Копировать совместимое видео, автоматически подобрать битрейт остальных фильмов, сбросить индивидуальные битрейты и выбрать сбалансированное кодирование.')
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
        self.encode_speed.setCurrentIndex(1)
        self.encode_speed.setToolTip('Быстро: NVENC p3 / x264 fast. Сбалансированно: p5 / medium — лучше детализация при том же битрейте.')
        form.addRow('Скорость:', self.encode_speed)
        self.gpu_frames = QCheckBox('NVDEC + обработка кадров на GPU, если поддерживается')
        self.gpu_frames.setChecked(True)
        self.gpu_frames.setToolTip('Перед сборкой проверяется реальный файл. При несовместимости фильтры работают на CPU, кодер — NVENC.')
        if sys.platform == 'darwin':
            self.encoder.model().item(1).setEnabled(False)
            self.encoder.setItemText(2, 'Авто — CPU x264 на Mac')
            self.gpu_frames.setEnabled(False)
            self.gpu_frames.setToolTip('NVIDIA CUDA недоступна на Mac. Используется CPU x264.')
        form.addRow('', self.gpu_frames)
        self.enhance_video = QCheckBox('Бережное улучшение выбранного фильма · без AI')
        self.enhance_video.setToolTip('Только для фильма, выбранного слева. Требует его перекодирования. '
            'Декодирование и фильтры работают на CPU; NVENC можно оставить включённым. Остальные фильмы не меняются.')
        form.addRow('', self.enhance_video)
        self.quiet_mode = QComboBox()
        for caption, rate in [('Выключен · максимальная скорость', 0.0),
                              ('Тихий · 0,5× длительности видео', 0.5),
                              ('Тихий · 1× длительности видео', 1.0),
                              ('Умеренный · 2× длительности видео', 2.0)]:
            self.quiet_mode.addItem(caption, rate)
        self.quiet_mode.setToolTip('Лимит темпа обработки при перекодировании и в пробах. '
            '1×: час видео обрабатывается не быстрее примерно часа; 0,5× — двух часов. '
            'Не ограничивает мощность видеокарты и не гарантирует её температуру. Копирование не замедляется.')
        form.addRow('Тихий режим:', self.quiet_mode)
        self.enhance_video.toggled.connect(self._treatment_changed)
        treatment_note = QLabel('Улучшение — только для выбранного фильма; фильтры CPU, кодер выбранный. '
            'В режиме «Авто» остальные совместимые фильмы копируются без перекодирования. '
            'Тихий режим общий для всей очереди.')
        treatment_note.setWordWrap(True)
        treatment_note.setObjectName('hint')
        form.addRow('', treatment_note)
        self.quality_trial_button = QPushButton('Проба качества…')
        self.quality_trial_button.setToolTip('Закодировать короткую сцену с разными битрейтами, сравнить кадры и применить выбранный битрейт к этому фильму.')
        self.quality_trial_button.clicked.connect(self._quality_trial)
        form.addRow('', self.quality_trial_button)
        resume_note = QLabel('Завершённые этапы сохраняются автоматически. После ошибки откройте последнюю сборку и нажмите «Создать ISO…».')
        resume_note.setWordWrap(True)
        form.addRow('', resume_note)
        build_box.addWidget(profile_box)
        plan_box = QGroupBox('План диска · оценка после обработки')
        plan_layout = QVBoxLayout(plan_box)
        self.bitrate_table = QTableWidget(0, 5)
        self.bitrate_table.setHorizontalHeaderLabels(['Фильм', 'Видео исходника', 'Обработка', 'Видео на диске', 'Потоки, ГБ'])
        self.bitrate_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.bitrate_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.bitrate_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.bitrate_table.verticalHeader().hide()
        self.bitrate_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.bitrate_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.bitrate_table.setMinimumHeight(85)
        self.bitrate_table.setMaximumHeight(220)
        self.bitrate_table.itemClicked.connect(lambda item: self._request_movie(self.bitrate_table.item(item.row(), 0).data(Qt.UserRole)))
        plan_layout.addWidget(self.bitrate_table)
        plan_note = QLabel('Копирование сохраняет видео без потерь. Автобитрейт — оценка для перекодирования, а не гарантия исходного качества. '
                           'В строках учтены видео, выбранный звук и субтитры; меню и общий резерв добавлены на шкале слева. Добавление фильма не уменьшает битрейт остальных.')
        plan_note.setWordWrap(True)
        plan_layout.addWidget(plan_note)
        build_box.insertWidget(0, plan_box)

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
        path_box.setVisible(False)
        tools_toggle = QPushButton('Пути инструментов и папка результата'); tools_toggle.setObjectName('ghost'); tools_toggle.setCheckable(True)
        tools_toggle.toggled.connect(path_box.setVisible)
        build_box.addWidget(tools_toggle)
        build_box.addWidget(path_box)
        info = QLabel('Совместимое видео можно сохранить. При перекодировании: H.264 1080p 23,976 кадр/с. Обработка каждой аудиодорожки выбирается в разделе «Аудио». '
                      'Выберите аудио и субтитры в соответствующих разделах. '
                      'Сначала проверьте ISO на программном проигрывателе и бытовом плеере.')
        info.setWordWrap(True)
        info.setObjectName('hint')
        build_box.addWidget(info)
        build_box.addStretch()
        build_scroll = QScrollArea()
        build_scroll.setWidgetResizable(True)
        build_scroll.setWidget(build_tab)
        self.tabs.addTab(build_scroll, 'Подготовка')
        self.audio_panel = AudioPanel()
        self.audio_panel.selection_changed.connect(self._audio_changed)
        self.audio_panel.policy_changed.connect(self._audio_policy_changed)
        self.tabs.addTab(self.audio_panel, 'Аудио')

        self.subtitle_panel = SubtitlePanel()
        self.subtitle_panel.selection_changed.connect(self._subtitles_changed)
        self.tabs.addTab(self.subtitle_panel, 'Субтитры')

        chapter_tab = QWidget()
        chapter_box = QVBoxLayout(chapter_tab)
        chapter_help = QLabel('Главы — это закладки внутри одного фильма. Кнопка главы в меню запускает фильм с указанного времени.\n'
                             'Готовые главы импортируются при анализе. Можно добавить свою главу, изменить время или вернуть главы из файла.')
        chapter_help.setWordWrap(True)
        chapter_box.addWidget(chapter_help)
        self.chapter_movie = QComboBox()
        self.chapter_movie.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.chapter_movie.setMinimumContentsLength(20)
        self.chapter_movie.setAccessibleName('Фильм, которому принадлежат главы')
        self.chapter_movie.currentIndexChanged.connect(lambda *_: self._request_movie(self.chapter_movie.currentData()))
        chapter_box.addWidget(QLabel('Главы фильма:'))
        chapter_box.addWidget(self.chapter_movie)
        self.chapter_status = QLabel('Сначала добавьте фильм и выберите его слева.')
        chapter_box.addWidget(self.chapter_status)
        self.chapter_list = QListWidget()
        self.chapter_list.currentItemChanged.connect(self.select_chapter_time)
        self.chapter_list.itemDoubleClicked.connect(lambda *_: self.chapter_time.setFocus())
        chapter_box.addWidget(self.chapter_list, 1)
        chapter_edit = QHBoxLayout()
        chapter_edit.addWidget(QLabel('Начало (чч:мм:сс.ммм):'))
        self.chapter_time = QLineEdit('00:00:00.000')
        self.chapter_time.setInputMask('00:00:00.000;_')
        self.chapter_time.setMaximumWidth(145)
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
        self.import_chapters_button = QPushButton('Вернуть главы из файла')
        self.import_chapters_button.setToolTip('Заменить текущие тайминги главами, найденными при анализе фильма.')
        self.import_chapters_button.clicked.connect(self.restore_source_chapters)
        chapter_box.addWidget(self.import_chapters_button)
        note = QLabel('Глава 1 — начало фильма, 00:00:00. Её нельзя удалить или сдвинуть. '
                      'Добавленные главы автоматически появляются в меню «Выбор главы». Удаление главы не вырезает видео.')
        note.setObjectName('hint')
        note.setWordWrap(True)
        chapter_box.addWidget(note)
        self.tabs.addTab(chapter_tab, 'Главы')

        self.menu_editor = MenuEditor()
        self.menu_editor.movie_requested.connect(self._request_movie)
        self.menu_editor.movie_renamed.connect(self._rename_movie_path)
        self.menu_title = self.menu_editor.title
        self.menu_title.textChanged.connect(self.project_heading.setText)
        self.tabs.addTab(self.menu_editor, 'Меню')
        self.image_tools = ImageTools(self.menu_editor)
        self.image_tools.menu_panel.loading_design.connect(self._detach_chapter_names)
        self.image_tools.menu_panel.edit_requested.connect(lambda: self.tabs.setCurrentWidget(self.menu_editor))
        self.tabs.addTab(self.image_tools, 'Проверка ISO')

        self.log_panel = QWidget()
        log_box = QVBoxLayout(self.log_panel); log_box.setContentsMargins(16, 8, 16, 8)
        logbar = QHBoxLayout(); logbar.addWidget(QLabel('Журнал')); logbar.addStretch()
        clear = QPushButton('Очистить'); clear.setObjectName('ghost'); clear.clicked.connect(lambda: self.log.clear()); logbar.addWidget(clear)
        log_box.addLayout(logbar)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(120); self.log.setPlaceholderText('События сборки появятся здесь.')
        log_box.addWidget(self.log); self.log_panel.hide(); root.addWidget(self.log_panel)
        # Keep the shared folder field for project compatibility; export edits it.
        self.output_folder = QLineEdit(self); self.output_folder.hide()
        self.output_folder.setAccessibleName('Папка готового ISO')
        self.output_folder.textChanged.connect(self.path_fields['output'].setText)
        self.path_fields['output'].textChanged.connect(self.output_folder.setText)
        self.output_browse = QPushButton('Выбрать папку…', self); self.output_browse.hide()
        self.output_browse.clicked.connect(lambda: self._choose_path('output'))
        self.timing_panel = QWidget(); timing_row = QHBoxLayout(self.timing_panel)
        timing_row.setContentsMargins(18, 4, 18, 4)
        self.elapsed_label = QLabel('Прошло: 00:00:00')
        self.remaining_label = QLabel('Осталось ≈ —')
        self.stage_label = QLabel('Ожидание')
        timing_row.addWidget(self.elapsed_label); timing_row.addWidget(self.remaining_label); timing_row.addStretch(); timing_row.addWidget(self.stage_label)
        self.timing_panel.hide(); root.addWidget(self.timing_panel)
        from verification_panel import VerificationPanel
        self.verification_panel = VerificationPanel(self)
        root.addWidget(self.verification_panel)
        self.gpu_monitor = GpuMonitor(self)
        self.gpu_monitor.temperature_warning.connect(self._message)
        root.addWidget(self.gpu_monitor)
        if sys.platform == 'darwin':
            self.gpu_monitor.hide()
        footer_widget = QWidget(); footer_widget.setObjectName('studioFooter')
        footer = QHBoxLayout(footer_widget); footer.setContentsMargins(16, 10, 16, 10)
        self.studio_status = QLabel('Добавьте фильмы'); self.studio_status.setObjectName('studioStatus')
        footer.addWidget(self.studio_status)
        self.log_toggle = QPushButton('Журнал'); self.log_toggle.setObjectName('ghost'); self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self.log_panel.setVisible); footer.addWidget(self.log_toggle)
        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setValue(0); self.progress.setMaximumWidth(260); self.progress.hide()
        footer.addWidget(self.progress, 1); footer.addStretch()
        open_output = QPushButton('Результаты'); open_output.setObjectName('ghost')
        open_output.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.path_fields['output'].text())))
        footer.addWidget(open_output)
        self.cancel_button = QPushButton('Отмена'); self.cancel_button.clicked.connect(self.cancel_build); self.cancel_button.hide(); footer.addWidget(self.cancel_button)
        self.build_button = QPushButton('Создать ISO…'); self.build_button.setObjectName('primary'); self.build_button.clicked.connect(self.show_export_dialog); footer.addWidget(self.build_button)
        root.addWidget(footer_widget)
        self.setStatusBar(QStatusBar()); self.statusBar().hide()
        self.output_mode.currentIndexChanged.connect(lambda *_: self._studio_update())

        project = self.menuBar().addMenu('Проект')
        for text, callback in [('Новый', self.new_project), ('Открыть проект…', self.open_project),
                               ('Сохранить проект…', self.save_project)]:
            action = QAction(text, self)
            action.triggered.connect(callback)
            action.setShortcut({'Новый': QKeySequence.New, 'Открыть проект…': QKeySequence.Open,
                                'Сохранить проект…': QKeySequence.Save}[text])
            project.addAction(action)
        theme_menu = self.menuBar().addMenu('Темы')
        project.addAction('Открыть последнюю сборку', self.open_last_build)
        project.addAction('Открыть папку кэша этапов', self.open_build_cache)
        self.theme_actions = {}
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        for label, key in [('Студия · светлая', 'graphite'), ('Студия · тёмная', 'blue')]:
            action = QAction(label, self, checkable=True)
            self.theme_group.addAction(action)
            self.theme_actions[key] = action
            theme_menu.addAction(action)
            action.triggered.connect(lambda checked=False, key=key: self.set_theme(key, save=True))
        help_menu = self.menuBar().addMenu('Справка')
        about = QAction('О программе', self)
        about.triggered.connect(self.about)
        help_menu.addAction(about)
        if sys.platform == 'win32':
            explorer = self.menuBar().addMenu('Проводник')
            explorer.addAction('Добавить «Открыть Blu-ray в VLC»', lambda: self.configure_explorer(True))
            explorer.addAction('Удалить пункт из Проводника', lambda: self.configure_explorer(False))

    def _open_empty_menu(self):
        self._allow_empty_editor = True
        self.tabs.setCurrentWidget(self.menu_editor)
        self._studio_update()

    def _studio_update(self):
        if not hasattr(self, 'studio_status'):
            return
        scanning = bool(self.scan_thread and self.scan_thread.isRunning())
        busy = bool(self.build_thread and self.build_thread.isRunning())
        count = self.movies.count()
        show_editor = count > 0 or self.tabs.currentIndex() == 5 or getattr(self, '_allow_empty_editor', False)
        self.studio_stack.setCurrentIndex(0 if show_editor else 1)
        self.library_heading.setText(f'ФИЛЬМЫ  ·  {count}')
        self.studio_status.setText('Создание диска…' if busy else 'Анализ файлов…' if scanning else getattr(self, '_studio_result', '') or (f'Фильмов: {len(self.media)}' if count else 'Добавьте фильмы'))
        self.welcome_add.setEnabled(not scanning and not busy)
        self.timing_panel.setVisible(busy or self.build_started is not None)
        self.progress.setVisible(busy or self.build_started is not None)
        self.cancel_button.setVisible(busy or scanning)
        self.build_button.setText('Создать ISO…' if self.output_mode.currentIndex() == 0 else 'Создать BDMV…')

    def show_export_dialog(self):
        if not self.media or not self.build_button.isEnabled():
            return
        from studio_ui import ExportDialog
        dialog = ExportDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.profile.setCurrentText(dialog.profile.currentText())
            self.output_mode.setCurrentIndex(dialog.mode.currentIndex())
            self.output_folder.setText(dialog.folder.text().strip())
            self.start_build(confirmed=True)

    def configure_explorer(self, enabled):
        from explorer_integration import install, uninstall
        try:
            (install if enabled else uninstall)()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Проводник', str(exc))
            return
        QMessageBox.information(self, 'Проводник',
            'Пункт «Открыть Blu-ray в VLC» добавлен для дисков и ISO.\n'
            'В Windows 11 он может находиться в «Показать дополнительные параметры».\n'
            'После перемещения папки DiskForge добавьте пункт заново.' if enabled
            else 'Пункт DiskForge удалён из контекстного меню.')

    def _restore(self) -> None:
        settings = load_settings()
        for key, val in settings.items():
            if key in self.path_fields:
                self.path_fields[key].setText(val)
        self.set_theme(settings.get('ui_theme', 'graphite'))

    def set_theme(self, key, save=False):
        self.ui_theme = key if key in self.theme_actions else 'graphite'
        app = QApplication.instance()
        stylesheet = interface_style(self.ui_theme)
        if app.styleSheet() != stylesheet:
            app.setStyleSheet(stylesheet)
        self.theme_actions[self.ui_theme].setChecked(True)
        if save:
            self._save_config()

    def _config(self) -> dict[str, str]:
        return dict({key: edit.text().strip() for key, edit in self.path_fields.items()}, ui_theme=self.ui_theme,
                    video_enhancements=dict(self.video_enhancements), quiet_rate=self.quiet_mode.currentData())

    def _requested_mode(self, info):
        return 'encode' if enhancement_for({'video_enhancements': self.video_enhancements}, info.path) else MODES[self.processing_mode.currentIndex()]

    def _trial_config(self, info):
        config = self._config()
        config['enhance'] = enhancement_for(config, info.path)
        return config

    def _sync_treatment_controls(self):
        path = self._selected_path()
        self.enhance_video.blockSignals(True)
        self.enhance_video.setChecked(bool(path and enhancement_for({'video_enhancements': self.video_enhancements}, path)))
        self.enhance_video.blockSignals(False)
        self.enhance_video.setEnabled(path in self.media and self._movies_editable())

    def _treatment_changed(self, *_):
        path = self._selected_path()
        if path not in self.media or not self._movies_editable():
            self._sync_treatment_controls()
            return
        path = str(Path(path).resolve())
        if self.enhance_video.isChecked():
            self.video_enhancements[path] = True
        else:
            self.video_enhancements.pop(path, None)
        self._update_recommendation()
        self._update_capacity()

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
        if self.disc_mode.currentIndex() == 0:
            self.menu_editor.show_catalog()
        for path in fresh:
            item = QListWidgetItem('⏳  ' + movie_title(path, self.movie_names))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
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
        if info:
            info = replace(info, audio_modes=dict(self.audio_modes.get(path, {})))
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
                item.setText(('✅  ' if info else '❌  ') + movie_title(path, self.movie_names))
                item.setToolTip(path + ('\n' + error if error else ''))
                break
        if info:
            self.media[path] = info
            if path not in self.chapters:
                self.chapters[path] = list(info.source_chapters) or [0.0]
                if info.source_chapters:
                    self._message(f'Главы из файла импортированы: {len(info.source_chapters)}. Они доступны в меню диска.')
            names = self.chapter_names.setdefault(path, {'labels': {}})
            names.setdefault('titles', titles_for_chapters(info, self.chapters[path]))
            for notice in info.chapter_notices:
                self._message(notice)
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
        self.apply_recommendation.setEnabled(enabled and self._movies_editable())
        self._sync_bitrate_controls()
        if not enabled:
            self.recommendation.setText('Добавьте фильм для анализа совместимости.')
            return
        advice = recommend_bitrate(info, 10**18 if self.disc_mode.currentIndex() == 0 else CAPACITIES[self.profile.currentText()])
        from disc_capacity import video_mode
        mode = video_mode(info, self._requested_mode(info), 10**18 if self.disc_mode.currentIndex() == 0 else CAPACITIES[self.profile.currentText()])
        if mode == 'encode':
            rate = self._video_rates().get(str(Path(info.path).resolve()))
            current = f'Текущий план: перекодирование {rate/1e6:g} Мбит/с.' if rate else 'Текущий план: прежний расчёт под ёмкость отдельного диска.'
            if enhancement_for({'video_enhancements': self.video_enhancements}, info.path):
                current += ' Бережное улучшение включено только для этого фильма.'
        else:
            current = 'Текущий план: копировать видео без изменения битрейта; звук — по выбранным настройкам.'
        self.recommendation.setText(advice.explanation + '\n\n' + current)

    def _use_recommendation(self):
        if not self._movies_editable():
            return
        self.bitrate_overrides.clear()
        self.processing_mode.setCurrentIndex(0)
        self.bitrate_policy.setCurrentIndex(0)
        self.encode_speed.setCurrentIndex(1)
        self._update_recommendation()
        self._update_capacity()

    def _video_rates(self):
        common = self.disc_mode.currentIndex() == 0
        policy = self.bitrate_policy.currentData()
        if policy == 'manual' and not common:
            return dict(self.bitrate_overrides)
        return {str(Path(info.path).resolve()): planned_rate(info, policy, self.bitrate_overrides, self.collection_bitrate.value()*1_000_000)
                for info in self.media.values()}

    def _sync_bitrate_controls(self):
        self._sync_treatment_controls()
        info = self.media.get(self._selected_path())
        path = str(Path(info.path).resolve()) if info else None
        self.custom_bitrate.blockSignals(True)
        self.movie_bitrate.blockSignals(True)
        self.custom_bitrate.setChecked(path in self.bitrate_overrides)
        if info:
            self.movie_bitrate.setValue(planned_rate(info, self.bitrate_policy.currentData(), self.bitrate_overrides, self.collection_bitrate.value()*1_000_000)/1e6)
        self.custom_bitrate.blockSignals(False)
        self.movie_bitrate.blockSignals(False)
        from disc_capacity import video_mode
        encodes = info is not None and video_mode(info, self._requested_mode(info),
            10**18 if self.disc_mode.currentIndex() == 0 else CAPACITIES[self.profile.currentText()]) == 'encode'
        self.custom_bitrate.setEnabled(encodes and self._movies_editable())
        self.movie_bitrate.setEnabled(encodes and self._movies_editable() and self.custom_bitrate.isChecked())
        self.quality_trial_button.setEnabled(info is not None and not info.is_hdr and self._movies_editable())

    def _quality_trial(self):
        info = self.media.get(self._selected_path())
        if not info or not self._movies_editable():
            return
        from quality_dialog import QualityDialog
        from disc_capacity import video_mode
        copying = video_mode(info, self._requested_mode(info),
            10**18 if self.disc_mode.currentIndex() == 0 else CAPACITIES[self.profile.currentText()]) != 'encode'
        rate = planned_rate(info, self.bitrate_policy.currentData(), self.bitrate_overrides, self.collection_bitrate.value()*1_000_000)
        dialog = QualityDialog(info, self._trial_config(info), rate, self.encoder.currentIndex(),
                               self.encode_speed.currentIndex() == 0, self.gpu_frames.isChecked(), copying, self,
                               saved_record=self.quality_records.get(str(Path(info.path).resolve())))
        self.quality_dialog = dialog
        try:
            if dialog.exec() == QualityDialog.Accepted and dialog.selected_rate is not None:
                # The dialog is modal: source identity and encoder controls cannot
                # change behind it. Bind the result to the original film, not a row.
                if self.media.get(info.path) != info:
                    self._message('Фильм изменился. Повторите пробу перед применением.')
                    return
                self.bitrate_overrides[str(Path(info.path).resolve())] = dialog.selected_rate
                self._bitrate_settings_changed()
                self._message(f'Проба качества: {info.name} — {dialog.selected_rate/1e6:g} Мбит/с для перекодирования.' +
                              (' Сейчас выбран режим копирования; исходное видео сохраняется без потерь.' if copying else ''))
        finally:
            if isinstance(dialog.saved_record, dict):
                self.quality_records[str(Path(info.path).resolve())] = dialog.saved_record
                self._message('Результаты проб сохранены в текущем проекте. Сохраните проект, чтобы открыть их позже.')
            self.quality_dialog = None
            dialog.deleteLater()

    def _bitrate_settings_changed(self, *_):
        self._update_recommendation()
        self._update_capacity()

    def _custom_bitrate_changed(self, *_):
        path = self._selected_path()
        if path in self.media and self._movies_editable():
            path = str(Path(path).resolve())
            if self.custom_bitrate.isChecked():
                self.bitrate_overrides[path] = round(self.movie_bitrate.value()*1_000_000)
            else:
                self.bitrate_overrides.pop(path, None)
        self._bitrate_settings_changed()

    def _update_bitrate_table(self):
        from disc_capacity import estimate
        rates = self._video_rates()
        rows = []
        common = self.disc_mode.currentIndex() == 0
        for i in range(self.movies.count()):
            path = self.movies.item(i).data(Qt.UserRole)
            info = self.media.get(path)
            if not info:
                continue
            advice = recommend_bitrate(info, 10**18 if common else CAPACITIES[self.profile.currentText()])
            budget = estimate([(info, self.chapters.get(path, [0.]))], self.profile.currentText(), MODES[self.processing_mode.currentIndex()],
                              collection=common, bitrate=self.collection_bitrate.value()*1_000_000, rates=rates, menu=False,
                              enhancements=self.video_enhancements)
            mode = budget.titles[0][2]
            name = movie_title(path, self.movie_names)
            rate = rates.get(str(Path(path).resolve()))
            source = f'{advice.source_bps/1e6:.2f} Мбит/с' if advice.source_bps else 'Неизвестно'
            if advice.source_method == 'samples':
                source = '≈ '+source
            target = f'{rate/1e6:g} Мбит/с' if rate else 'По ёмкости'
            if mode != 'encode':
                target = 'Исходное'
            treatment = 'Улучшение + перекодирование' if enhancement_for({'video_enhancements': self.video_enhancements}, path) else 'Перекодирование' if mode == 'encode' else 'Копия видео'
            rows.append((path, name, source, treatment, target,
                         f'{(budget.video+budget.audio+budget.subtitles)/1e9:.2f}', advice.explanation))
        if getattr(self, '_bitrate_rows', None) == rows:
            return
        self._bitrate_rows = rows
        self.bitrate_table.setRowCount(len(rows))
        self.bitrate_table.setFixedHeight(min(200, max(85, self.bitrate_table.horizontalHeader().height() + 31*len(rows) + 4)))
        for row, values in enumerate(rows):
            path, *cells, explanation = values
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, path)
                item.setToolTip(path if col == 0 else explanation)
                self.bitrate_table.setItem(row, col, item)

    def _audio_policy_changed(self, modes):
        item = self.movies.currentItem()
        if item and item.data(Qt.UserRole) in self.media:
            path = item.data(Qt.UserRole)
            self.audio_modes[path] = dict(modes)
            self.media[path] = replace(self.media[path], audio_modes=dict(modes))
            self._message('Обработка аудиодорожек обновлена.')

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

    def _save_chapter_names(self):
        self.menu_editor.preview.commit_edit()
        if self._chapter_owner in self.media:
            self.chapter_names[self._chapter_owner] = {
                'titles': list(self.menu_editor.chapter_titles),
                'labels': {k: v for k, v in self.menu_editor.labels.items() if is_chapter_label(k)}}

    def _detach_chapter_names(self):
        self._save_chapter_names()
        self._chapter_owner = None
        self.menu_editor.set_collection([], False, None)

    def _chapter_tab_changed(self, index):
        if index == 3 and self._chapter_owner is not None:
            self._select_media()

    def _select_media(self) -> None:
        self._save_chapter_names()
        self._chapter_owner = None
        path = self._selected_path()
        self.movie_title_edit.setText(self.movie_names.get(path, ''))
        self.movie_title_edit.setPlaceholderText(Path(path).stem[:80] if path else 'Выберите фильм')
        info = self.media.get(path or '')
        names = self.chapter_names.get(path, {})
        self.menu_editor.labels = {k: v for k, v in self.menu_editor.labels.items() if not is_chapter_label(k)}
        self.menu_editor.labels.update(names.get('labels', {}))
        self.audio_panel.set_media(info)
        self.subtitle_panel.set_media(info)
        self.menu_editor.set_subtitles(info)
        self.menu_editor.set_audio(info)
        self.menu_editor.set_chapters(self.chapters.get(path, [0.0]), names.get('titles', []))
        self._chapter_owner = path if info else None
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
            source_note = f'В файле найдено глав: {len(info.source_chapters)}.' if info.source_chapters else 'Готовых глав в файле нет — добавьте их вручную или по интервалу.'
            self.chapter_status.setText(f'Фильм: {movie_title(path, self.movie_names)} · Длительность: {timecode(info.duration)} · Глав: {len(self.chapters.get(path, [0]))}\n{source_note}')
            self.chapter_status.setWordWrap(True)
            for index, t in enumerate(self.chapters.get(path, [0.0]), 1):
                name = self.menu_editor.current_design(preview=True).text(f'chapter:{index-1}') or f'Глава {index}'
                item = QListWidgetItem(f'{name}   ·   {chapter_timecode(t)}' + ('   ·   Начало фильма' if t == 0 else ''))
                item.setData(Qt.UserRole, t)
                item.setToolTip(f'Фильм: {movie_title(path, self.movie_names)}\n{path}\nНачало сцены: {chapter_timecode(t)}')
                self.chapter_list.addItem(item)
        self._update_recommendation()
        self._refresh_buttons()

    def restore_source_chapters(self) -> None:
        path = self._selected_path()
        info = self.media.get(path)
        if info and info.source_chapters:
            if self.apply_chapters(path, list(info.source_chapters)):
                self.menu_editor.chapter_titles = titles_for_chapters(info, self.chapters[path])
                self._select_media()

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
            self.chapter_time.setText(chapter_timecode(float(item.data(Qt.UserRole))))
        if hasattr(self, 'build_button'):
            self._refresh_buttons()

    def apply_chapters(self, path, new, moved=None):
        try:
            validate_chapters(new, self.media[path].duration)
            self.menu_editor.retime_chapters(self.chapters[path], new, moved)
        except (ValueError, DiscForgeError) as exc:
            QMessageBox.warning(self, 'Главы', str(exc))
            return False
        self.chapters[path] = new
        self._select_media()
        return True

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

    def _movies_editable(self):
        return not ((self.scan_thread and self.scan_thread.isRunning()) or
                    (self.build_thread and self.build_thread.isRunning()))

    def _focus_movie_title(self):
        if self.movie_title_edit.isEnabled():
            self.movie_title_edit.setFocus()
            self.movie_title_edit.selectAll()

    def _rename_movie(self, text):
        path = self._selected_path()
        self._rename_movie_path(path, text, update_field=False)

    def _rename_movie_path(self, path, text, update_field=True):
        if not path or not self._movies_editable():
            return
        try:
            title = validate_title(text)
        except ValueError:
            self.movie_title_edit.setText(self.movie_names.get(path, ''))
            return
        if title:
            self.movie_names[path] = title
        else:
            self.movie_names.pop(path, None)
        for i in range(self.movies.count()):
            item = self.movies.item(i)
            if item.data(Qt.UserRole) == path:
                prefix = '✅  ' if path in self.media else '❌  '
                item.setText(prefix + movie_title(path, self.movie_names))
                break
        if update_field and path == self._selected_path():
            self.movie_title_edit.setText(self.movie_names.get(path, ''))
        self._sync_collection()

    def _request_movie(self, path):
        if not path:
            return
        for i in range(self.movies.count()):
            if self.movies.item(i).data(Qt.UserRole) == path:
                if self.movies.currentRow() == i:
                    self._select_media()
                else:
                    self.movies.setCurrentRow(i)
                return

    def _sync_collection(self, *_):
        items = [(item.data(Qt.UserRole), movie_title(item.data(Qt.UserRole), self.movie_names))
                 for item in (self.movies.item(i) for i in range(self.movies.count()))
                 if item.data(Qt.UserRole) in self.media]
        path = self._selected_path()
        self.chapter_movie.blockSignals(True)
        self.chapter_movie.clear()
        for source, title in items:
            self.chapter_movie.addItem(title, source)
            self.chapter_movie.setItemData(self.chapter_movie.count()-1, source, Qt.ToolTipRole)
        self.chapter_movie.setCurrentIndex(self.chapter_movie.findData(path))
        self.chapter_movie.blockSignals(False)
        self.menu_editor.set_collection(items, self.disc_mode.currentIndex() == 0, path)
        info = self.media.get(path)
        if info:
            title = movie_title(path, self.movie_names)
            self.chapter_status.setText(f'Фильм: {title} · Длительность: {timecode(info.duration)} · Глав: {len(self.chapters.get(path, [0]))}\n'
                                       'Все главы ниже относятся только к этому фильму. Для другого фильма выберите его выше.')
            for i in range(self.chapter_list.count()):
                item = self.chapter_list.item(i)
                item.setToolTip(f'Фильм: {title}\n{path}\nНачало сцены: {chapter_timecode(item.data(Qt.UserRole))}')

    def _move_movie(self, direction):
        row = self.movies.currentRow()
        target = row + direction
        if not self._movies_editable() or row < 0 or not 0 <= target < self.movies.count():
            return
        self._save_chapter_names()
        self.movies.blockSignals(True)
        try:
            item = self.movies.takeItem(row)
            self.movies.insertItem(target, item)
            self.movies.setCurrentItem(item)
        finally:
            self.movies.blockSignals(False)
        self._select_media()
        self._refresh_buttons()

    def remove_media(self) -> None:
        row = self.movies.currentRow()
        if row < 0:
            return
        item = self.movies.takeItem(row)
        path = item.data(Qt.UserRole)
        self.media.pop(path, None)
        self.chapters.pop(path, None)
        self.chapter_names.pop(path, None)
        self.movie_names.pop(path, None)
        self.bitrate_overrides.pop(str(Path(path).resolve()), None)
        self.quality_records.pop(str(Path(path).resolve()), None)
        self.video_enhancements.pop(str(Path(path).resolve()), None)
        self.audio_selections.pop(path, None)
        self.subtitle_selections.pop(path, None)
        self.subtitle_panel.external.pop(path, None)
        self._select_media()
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self._update_capacity()
        scanning = bool(self.scan_thread and self.scan_thread.isRunning())
        busy = bool(self.build_thread and self.build_thread.isRunning())
        editable = self._selected_path() in self.media and not scanning and not busy
        can_order = not scanning and not busy and self.movies.currentItem() is not None
        self.movie_title_edit.setEnabled(can_order)
        self.movie_up.setEnabled(can_order and self.movies.currentRow() > 0)
        self.movie_down.setEnabled(can_order and self.movies.currentRow() < self.movies.count()-1)
        self.movies.setDragEnabled(not scanning and not busy)
        self.audio_panel.setEnabled(editable)
        self.subtitle_panel.setEnabled(editable)
        self.add_chapter_button.setEnabled(editable)
        self.auto_chapter_button.setEnabled(editable)
        info = self.media.get(self._selected_path())
        self.apply_recommendation.setEnabled(editable and bool(info and info.copy_report))
        self.import_chapters_button.setEnabled(editable and bool(info and info.source_chapters))
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
        self.disc_mode.setEnabled(not busy)
        self.add_button.setEnabled(not scanning and not busy)
        self.remove_button.setEnabled(not scanning and not busy and self.movies.currentItem() is not None)
        self.statusBar().showMessage(
            f'Файлов: {len(self.media)}  |  Сканирование: {"да" if scanning else "нет"}  |  '
            f'Сборка: {"идёт" if busy else "нет"}')
        self._sync_collection()
        self._sync_bitrate_controls()
        self._studio_update()

    def _message(self, value: str) -> None:
        self.log.appendPlainText(value)
        if value.startswith('ОШИБКА'):
            self.log_toggle.setChecked(True)

    def _capacity_budgets(self):
        from disc_capacity import estimate
        jobs = [(self.media[self.movies.item(i).data(Qt.UserRole)], self.chapters.get(self.movies.item(i).data(Qt.UserRole), [0.0]))
                for i in range(self.movies.count()) if self.movies.item(i).data(Qt.UserRole) in self.media]
        common = self.disc_mode.currentIndex() == 0
        groups = [jobs] if common else [[job] for job in jobs]
        return [estimate(group, self.profile.currentText(), MODES[self.processing_mode.currentIndex()],
                         collection=common, bitrate=self.collection_bitrate.value()*1_000_000,
                         menu=self.menu_editor.enabled.isChecked(), rates=self._video_rates(),
                         enhancements=self.video_enhancements) for group in groups]

    def _update_capacity(self):
        if not hasattr(self, 'menu_editor'):
            return
        self.collection_bitrate.setEnabled(self.disc_mode.currentIndex() == 0 and self.bitrate_policy.currentData() == 'manual' and self._movies_editable())
        try:
            self._update_bitrate_table()
            budgets = self._capacity_budgets()
            worst = max(budgets, key=lambda b: b.total) if budgets else None
            maximum = CAPACITIES[self.profile.currentText()]//1_000_000
            self.capacity_bar.setMaximum(maximum)
            self.capacity_bar.setValue(min(maximum, round(worst.total/1_000_000)) if worst else 0)
            self.capacity_bar.setFormat(f'{worst.total/1e9:.2f} / {maximum/1000:g} ГБ' if worst else f'0 / {maximum/1000:g} ГБ')
            text = worst.explanation() if worst else 'Добавьте фильмы для расчёта заполнения.'
            if self.disc_mode.currentIndex() == 1 and worst:
                text = 'Отдельные диски · показан самый заполненный\n'+text
            if self.scan_thread and self.scan_thread.isRunning():
                text += '\nАнализ идёт: ещё не проверенные файлы пока не учтены.'
            self.capacity_label.setText(text)
            self.capacity_summary.setText(self.profile.currentText() + ' · ' + self.capacity_bar.format())
            self.capacity_summary.setToolTip(text)
            color = '#e66b60' if worst and worst.remaining < 0 else '#e7aa53' if worst and worst.warning else ('#a5a8b2' if self.ui_theme == 'blue' else '#666d7a')
            self.capacity_label.setStyleSheet('color: '+color)
            fill = '#993f37' if worst and worst.remaining < 0 else '#965b21' if worst and worst.warning else '#805a36'
            self.capacity_bar.setStyleSheet('QProgressBar::chunk { background: '+fill+'; }')
            self.capacity_bar.setToolTip('\n'.join(f'{name}: ≈ {size/1e9:.2f} ГБ потоков' for b in budgets for name, size, _ in b.titles))
        except (ValueError, OSError, DiscForgeError) as exc:
            self.capacity_label.setText(str(exc))
            self.capacity_bar.setValue(0)
            self.capacity_bar.setFormat('Расчёт недоступен')

    def _build_video_description(self, info, config):
        from disc_capacity import video_mode
        mode = video_mode(info, self._requested_mode(info), 10**18 if config['collection'] else CAPACITIES[self.profile.currentText()])
        if mode != 'encode':
            return 'копирование видео без потерь; звук по настройкам'
        rate = config.get('video_rates', {}).get(str(Path(info.path).resolve()))
        prefix = 'бережное улучшение + ' if enhancement_for(config, info.path) else ''
        return prefix + (f'перекодирование видео {rate/1e6:g} Мбит/с; возможны потери качества' if rate else 'перекодирование видео с расчётом под ёмкость диска')

    def start_build(self, *, confirmed=False) -> None:
        if not self.media:
            return
        try:
            from disc_capacity import require_fit
            for budget in self._capacity_budgets():
                require_fit(budget)
        except (ValueError, OSError, DiscForgeError) as exc:
            QMessageBox.warning(self, 'Диск не вмещает выбранные файлы', str(exc))
            return
        self._save_chapter_names()
        config = self._config()
        config['gpu_frames'] = self.gpu_frames.isChecked()
        config['collection'] = self.disc_mode.currentIndex() == 0
        config['movie_names'] = dict(self.movie_names)
        config['collection_bitrate'] = self.collection_bitrate.value()*1_000_000
        config['video_rates'] = self._video_rates()
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
            if config['collection'] and menu is None:
                raise ValueError('Включите меню: оно нужно для выбора фильма на общем диске.')
        except ValueError as exc:
            QMessageBox.warning(self, 'Название меню', str(exc))
            return
        if not confirmed:
            response = QMessageBox.question(self, 'Создание Blu-ray',
                f'Дисков: {1 if config["collection"] else len(jobs)} · Фильмов: {len(jobs)} · {self.profile.currentText()} · {self.output_mode.currentText()}\n'
                + ('Меню: смотреть фильм и выбирать главы.\n' if menu else 'Без интерактивного меню.\n')
                + 'Режим: ' + self.processing_mode.currentText() + '\n'
                + '\n'.join(movie_title(info.path, self.movie_names) + ': ' + self._build_video_description(info, config) for info, _ in jobs) + '\n'
                + 'Выбранные субтитры войдут в ISO отдельными дорожками. Исходники сохраняются.\n\nНачать сборку?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if response != QMessageBox.Yes:
                return
        try:
            write_json(Path(config['output']) / '.diskforge-cache/last-project.dfr.json', self._project_data())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Не удалось сохранить сборку', str(exc))
            return
        self.progress.setValue(0)
        self.build_thread = BuildThread(jobs, config, self.profile.currentText(),
                                        self.output_mode.currentIndex() == 0,
                                        self.encoder.currentIndex() == 1, menu, auto=self.encoder.currentIndex() == 2, fast=self.encode_speed.currentIndex() == 0, processing_mode=MODES[self.processing_mode.currentIndex()], chapter_names=self.chapter_names)
        self._studio_result = ''
        self.build_started = time.monotonic()
        self._verification_update(None)
        self.eta_remaining = -1
        self.stage_label.setText("Подготовка")
        self._tick_build_time()
        self.build_clock.start()
        self.gpu_monitor.start()
        self.build_thread.timing_changed.connect(self._timing_update)
        self.build_thread.verification_changed.connect(self._verification_update)
        self.build_thread.message.connect(self._message)
        self.build_thread.progress_changed.connect(self.progress.setValue)
        self.build_thread.item_started.connect(lambda p: self._message('Начинаем: ' + Path(p).name))
        self.build_thread.item_done.connect(self._item_done)
        self.build_thread.failed.connect(lambda p, err: self._message('ОШИБКА ' + Path(p).name + ': ' + err))
        self.build_thread.finished_all.connect(self._build_finished)
        self.build_thread.finished.connect(self._refresh_buttons)
        self.build_thread.finished.connect(self.gpu_monitor.stop)
        self.build_thread.finished.connect(self._finish_deferred_close)
        self.build_thread.start()
        self._refresh_buttons()

    def _verification_update(self, event):
        self.verification_panel.update_progress(event)
        self.progress.setFormat('%p% · проверка ISO' if event and event['scope'] == 'iso-final' else '%p%')

    def _timing_update(self, stage, remaining):
        self.stage_label.setText(stage)
        self.eta_remaining = remaining
        self.eta_updated = time.monotonic()
        self._tick_build_time()

    def _finish_deferred_close(self):
        if self._close_after_build:
            self.close()

    def open_build_cache(self):
        path = Path(self.output_folder.text().strip()) / '.diskforge-cache'
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def open_last_build(self):
        path = Path(self.output_folder.text().strip()) / '.diskforge-cache/last-project.dfr.json'
        if path.is_file():
            self._load_project(str(path))
        else:
            QMessageBox.information(self, 'Возобновление', 'В выбранной выходной папке пока нет сохранённой сборки.')

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
        self.gpu_monitor.stop()
        self.verification_panel.stop(success)
        self.progress.setFormat('%p%')
        self.stage_label.setText('Готово' if success else 'Остановлено / ошибка')
        self.remaining_label.setText('Осталось: 00:00:00' if success else 'Осталось: —')
        self._message('Очередь завершена.' if success else 'Очередь завершилась с ошибками или была отменена.')
        self._studio_result = 'Готово' if success else 'Сборка остановлена · смотрите журнал'
        self.studio_status.setText(self._studio_result)
        if not success:
            self.log_toggle.setChecked(True)

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
        self._save_chapter_names()
        return {'format_version': 1, 'title': self.menu_title.text(),
                'menu': self.menu_editor.settings(),
                'files': [self.movies.item(i).data(Qt.UserRole) for i in range(self.movies.count())],
                'movie_names': dict(self.movie_names),
                'bitrate_policy': self.bitrate_policy.currentData(), 'bitrate_overrides': dict(self.bitrate_overrides),
                'quality_trials': dict(self.quality_records),
                'video_enhancements': dict(self.video_enhancements), 'quiet_rate': self.quiet_mode.currentData(),
                'subtitles': self.subtitle_selections, 'audio': self.audio_selections, 'audio_modes': self.audio_modes, 'gpu_frames': self.gpu_frames.isChecked(), 'chapters': self.chapters, 'chapter_names': self.chapter_names, 'profile': self.profile.currentText(),
                'collection': self.disc_mode.currentIndex() == 0, 'collection_bitrate': self.collection_bitrate.value(),
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
        self._load_project(path)

    def _load_project(self, path):
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
            loaded_modes = data.get('audio_modes', {})
            if (not isinstance(loaded_modes, dict) or any(not isinstance(p, str) or not isinstance(m, dict)
                    or any(not isinstance(k, str) or not k.isdigit() or v not in ('auto', 'copy', 'ac3') for k, v in m.items())
                    for p, m in loaded_modes.items())):
                raise ValueError('Неверные режимы аудиодорожек')
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
                if p not in raw:
                    continue  # No saved edit: use chapters found by the scanner.
                v = raw[p]
                if not isinstance(v, list):
                    raise ValueError('Неверный список глав')
                validate_chapters(v)
                loaded_chapters[normalized] = [float(x) for x in v]
            raw_names = data.get('chapter_names', {})
            if not isinstance(raw_names, dict):
                raise ValueError('Неверный раздел названий глав')
            loaded_names = {}
            for p in paths:
                normalized = str(Path(p).resolve())
                names = raw_names.get(p, raw_names.get(normalized))
                if names is None:
                    # Legacy projects kept chapter edits in the common menu.
                    loaded_names[normalized] = {'labels': {k: v for k, v in design.labels.items() if is_chapter_label(k)}}
                    continue
                if not isinstance(names, dict) or 'labels' not in names or set(names) - {'titles', 'labels'}:
                    raise ValueError('Неверные названия глав')
                checked = MenuDesign('Проверка', chapter_titles=names.get('titles', []), labels=names['labels'])
                if any(not is_chapter_label(k) for k in checked.labels):
                    raise ValueError('В названиях глав найдены посторонние подписи')
                if normalized in loaded_chapters and len(checked.chapter_titles) > len(loaded_chapters[normalized]):
                    raise ValueError('Названий больше, чем глав')
                loaded_names[normalized] = {'labels': checked.labels}
                if 'titles' in names:
                    loaded_names[normalized]['titles'] = checked.chapter_titles
            loaded_movie_names = normalize_names(data.get('movie_names', {}), normalized_paths)
            loaded_rates = normalize_rates(data.get('bitrate_overrides', {}), normalized_paths)
            loaded_enhancements = project_enhancements(data, normalized_paths)
            from quality_cache import load_records
            loaded_quality, skipped_quality = load_records(data.get('quality_trials'), normalized_paths)
            loaded_policy = data.get('bitrate_policy', 'manual')
            if loaded_policy not in ('auto', 'manual'):
                raise ValueError('Неизвестный режим подбора битрейта.')
            if not self.new_project():
                return
            self.menu_editor.apply_design(design)
            self.menu_editor.enabled.setChecked(menu['enabled'])
            self.profile.setCurrentText(profile)
            self.disc_mode.setCurrentIndex(0 if data.get('collection') is True else 1)
            rate = data.get('collection_bitrate', 12)
            self.collection_bitrate.setValue(rate if type(rate) is int and 5 <= rate <= 28 else 12)
            self.bitrate_policy.setCurrentIndex(self.bitrate_policy.findData(loaded_policy))
            self.bitrate_overrides.update(loaded_rates)
            self.quality_records.update(loaded_quality)
            self.video_enhancements.update(loaded_enhancements)
            self.encoder.setCurrentIndex(data.get('encoder') if data.get('encoder') in (0, 1, 2) else 2)
            self.encode_speed.setCurrentIndex(1 if data.get('encode_speed') == 1 else 0)
            mode = data.get('processing_mode', 'auto')
            if 'video_enhancements' not in data and data.get('enhance') is True:
                mode = 'auto'  # Previous global improvement forced encode; per-film flags now do that.
            self.processing_mode.setCurrentIndex(MODES.index(mode) if mode in MODES else 0)
            from video_treatment import QUIET_RATES
            quiet = data.get('quiet_rate', 0.0)
            self.quiet_mode.setCurrentIndex(self.quiet_mode.findData(quiet) if type(quiet) in (int, float) and quiet in QUIET_RATES else 0)
            if isinstance(data.get('output'), str):
                self.output_folder.setText(data['output'])
            self.output_mode.setCurrentIndex(0 if data.get('as_iso', True) else 1)
            self.chapters.update(loaded_chapters)
            self.chapter_names.update(loaded_names)
            self.movie_names.update(loaded_movie_names)
            self.audio_selections.update(loaded_audio)
            self.audio_modes.update({str(Path(p).resolve()): m for p, m in loaded_modes.items()})
            self.gpu_frames.setChecked(data.get('gpu_frames', True) is True)
            self.subtitle_selections.update(loaded_subtitles)
            self._queue_scan(normalized_paths)
            self._message('Проект открыт: ' + str(path))
            if skipped_quality:
                self._message(f'Повреждённые отчёты проб пропущены: {skipped_quality}. Остальной проект загружен.')
        except (OSError, ValueError, TypeError, OverflowError, DiscForgeError) as exc:
            QMessageBox.warning(self, 'Ошибка проекта', str(exc))

    def new_project(self) -> bool:
        if ((self.build_thread and self.build_thread.isRunning()) or
                (self.scan_thread and self.scan_thread.isRunning())):
            QMessageBox.warning(self, 'Операция выполняется', 'Дождитесь завершения текущей операции.')
            return False
        self._chapter_owner = None
        self.movies.clear()
        self.media.clear()
        self._allow_empty_editor = False
        self._studio_result = ''
        self.build_started = None
        self.menu_editor.preview_mode.setChecked(False)
        self.chapters.clear()
        self.chapter_names.clear()
        self.movie_names.clear()
        self.bitrate_overrides.clear()
        self.audio_selections.clear()
        self.quality_records.clear()
        self.video_enhancements.clear()
        self.audio_modes.clear()
        self.subtitle_selections.clear()
        self.subtitle_panel.external.clear()
        self.chapter_list.clear()
        self.menu_editor.apply_design(MenuDesign('Моя коллекция Blu-ray'))
        self.menu_editor.enabled.setChecked(True)
        self.enhance_video.setChecked(False)
        self.quiet_mode.setCurrentIndex(0)
        self.processing_mode.setCurrentIndex(0)
        self.disc_mode.setCurrentIndex(0)
        self.collection_bitrate.setValue(12)
        self.bitrate_policy.setCurrentIndex(0)
        self.encode_speed.setCurrentIndex(1)
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
        trial = getattr(self, 'quality_dialog', None)
        if trial is not None:
            # A modal dialog can still be open when the parent receives a close
            # request. Never destroy a child QThread while FFmpeg is running.
            if not getattr(trial, '_parent_closing', False):
                trial._parent_closing = True
                trial.finished.connect(lambda _=0: QTimer.singleShot(0, self.close))
            trial.reject()
            event.ignore()
            return
        if self.build_thread and self.build_thread.isRunning():
            if not self._close_after_build and QMessageBox.question(self, 'Сохранить этапы и выйти?',
                    'Текущий незавершённый этап будет остановлен. Готовые этапы и проект останутся в выходной папке. '
                    'После запуска выберите «Проект → Открыть последнюю сборку».',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                self._close_after_build = True
                self.cancel_build()
            event.ignore()
            return
        if self.image_tools.is_running() or (self.build_thread and self.build_thread.isRunning()) or (
                self.scan_thread and self.scan_thread.isRunning()):
            QMessageBox.warning(self, 'Операция выполняется', 'Сначала завершите или отмените текущую операцию.')
            event.ignore()
            return
        self._save_config()
        self.gpu_monitor.shutdown()
        self.capacity_clock.stop()
        super().closeEvent(event)


from studio_ui import studio_style

STYLE = studio_style('blue')


def interface_style(theme):
    return studio_style(theme)


def main() -> int:
    # Packaging smoke test: exercise the same window and resources offscreen.
    smoke_report = None
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        smoke_report = Path(sys.argv[2])
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    if sys.platform != 'darwin':
        app.setFont(QFont('Segoe UI', 10))
    if len(sys.argv) > 1 and sys.argv[1] in ('--open-bluray', '--install-explorer', '--remove-explorer'):
        try:
            if sys.argv[1] == '--open-bluray':
                if len(sys.argv) != 3:
                    raise ValueError('Не указан диск Blu-ray или ISO-образ.')
                from vlc_preview import launch_bluray
                launch_bluray(sys.argv[2])
            else:
                from explorer_integration import install, uninstall
                (install if sys.argv[1] == '--install-explorer' else uninstall)()
            return 0
        except (OSError, ValueError) as exc:
            QMessageBox.critical(None, 'DiskForge — Blu-ray в VLC', str(exc))
            return 1
    w = Window()
    if smoke_report is not None:
        w.resize(1380, 850)
        w.show()
        app.processEvents()
        resources = Path(__file__).parent
        checks = {'window': w.tabs.count() == 6,
                  'menu_image': not w.menu_editor.preview.image.isNull(),
                  'xlet': (resources / 'assets/bdj/menu.jar').is_file(),
                  'windows_iso': sys.platform != 'win32' or (resources / 'tools/windows_iso.ps1').is_file()}
        if getattr(sys, 'frozen', False) and sys.platform == 'win32':
            from vlc_preview import app_root, pe_machine
            java = app_root() / 'runtime/java'
            checks['private_java'] = ((java / 'lib/rt.jar').is_file() and
                                      (java / 'bin/server/jvm.dll').is_file() and
                                      pe_machine(java / 'bin/server/jvm.dll') == 0x8664)
        if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
            from settings import find_binary
            from vlc_preview import macos_java
            java, jvm = macos_java()
            checks['private_java'] = jvm.is_file() and (java / 'lib/rt.jar').is_file()
            for tool in ('ffmpeg', 'ffprobe', 'tsmuxer', 'discforge-udf'):
                checks[tool] = Path(find_binary(tool)).is_file()
        checks['vlc_button'] = w.image_tools.vlc.text() == 'Проверить ISO в VLC'
        checks['verification_progress'] = w.verification_panel.isHidden() and not w.verification_panel.timer.isActive()
        checks['iso_menu_update'] = w.image_tools.menu_panel.repair.isEnabled()
        checks['fullscreen_preview'] = w.menu_editor.fullscreen_button.isEnabled()
        checks['fullscreen_icon'] = not w.menu_editor.fullscreen_button.icon().isNull() and not w.menu_editor.fullscreen_button.text()
        from disc_menu import theme_background
        checks['cinematic_themes'] = all(not theme_background(name).isNull() for name in ('amber', 'sapphire'))
        checks['removable_menu_logo'] = hasattr(w.menu_editor.current_design(), 'logo_visible')
        checks['disc_capacity'] = w.capacity_bar.maximum() == CAPACITIES[w.profile.currentText()]//1_000_000
        checks['collection_builder'] = w.disc_mode.count() == 2
        checks['bitrate_advisor'] = w.bitrate_policy.currentData() == 'auto' and w.bitrate_table.columnCount() == 5
        checks['individual_bitrate'] = w.movie_bitrate.minimum() == 5 and w.movie_bitrate.maximum() == 28
        from quality_dialog import QualityDialog
        trial = QualityDialog(MediaInfo('self-test.mkv', 60, 1, 'h264', 1920, 1080,
                                        '24000/1001', '', '', 0, 0, 0), w._config(), 8_000_000, parent=w)
        checks['quality_trial'] = (len(trial.rates) == 3 and trial.table.columnCount() == 7
                                   and not trial.apply_button.isEnabled() and not w.quality_trial_button.isEnabled())
        checks['quality_metrics'] = hasattr(trial, 'advice_label') and not trial.pick_recommended.isEnabled()
        checks['quality_scenes'] = (trial._plan().automatic and trial.scene_count.value() == 3
                                    and not trial.start.isEnabled() and not trial.weak_scene.isEnabled())
        checks['quality_search'] = trial.retry_rates.isChecked() and trial._plan().retry_rates
        checks['quality_project'] = hasattr(trial, 'saved_record') and 'quality_trials' in w._project_data()
        checks['remote_four_arrows'] = all(key in w.menu_editor.remote_buttons for key in ('up', 'down', 'left', 'right'))
        from video_treatment import frame_filter
        checks['gentle_video'] = hasattr(w, 'enhance_video') and 'hqdn3d=' in frame_filter()
        checks['film_enhancement'] = 'video_enhancements' in w._project_data() and not w.video_enhancements
        checks['quiet_mode'] = w.quiet_mode.count() == 4 and w._config()['quiet_rate'] == 0
        checks['before_after'] = trial.comparison_mode.count() == 3
        from menu_navigation import nearest_button
        checks['spatial_navigation'] = nearest_button([(0, 0, 100, 100), (200, 0, 100, 100)], 0, 'right') == 1 and nearest_button([(0, 0, 100, 100)], 0, 'down') == 0
        trial.reject()
        trial.deleteLater()
        checks['collection_titles'] = w.movie_title_edit.maxLength() == 80
        checks['collection_order'] = w.movies.dragDropMode() == QListWidget.InternalMove and w.movie_up is not None
        checks['collection_preview'] = hasattr(w.menu_editor, 'context_selector') and callable(w.menu_editor.show_catalog)
        checks['chapter_movie_selector'] = w.chapter_movie.accessibleName() == 'Фильм, которому принадлежат главы'
        from PySide6.QtGui import QImage
        logo = QImage(str(LOGO))
        checks['transparent_logo'] = logo.hasAlphaChannel() and logo.pixelColor(0, 0).alpha() == 0
        checks['disc_icon'] = not QIcon(str(ICON)).isNull()
        checks['menu_sidebar'] = w.menu_editor.splitter.widget(1) is w.menu_editor.inspector
        checks['studio_empty_state'] = w.studio_stack.currentIndex() == 1 and w.log_panel.isHidden()
        checks['studio_inspector'] = w.menu_editor.inspector_tabs.count() == 3
        checks['studio_icons'] = all((resources / 'assets/icons' / name).is_file() for name in ('check.svg', 'chevron.svg'))
        from studio_ui import ExportDialog
        export = ExportDialog(w)
        checks['studio_export'] = export.folder.text() == w.output_folder.text() and export.mode.count() == 2
        export.deleteLater()
        checks['source_chapters'] = probe_media is not None and hasattr(MediaInfo, 'source_chapters')
        checks['gpu_monitor'] = hasattr(w, 'gpu_monitor') and not w.gpu_monitor.timer.isActive()
        checks['gpu_heat_warning'] = w.gpu_monitor.heat_warning.isHidden()
        checks['gpu_frames_option'] = w.gpu_frames.isChecked()
        checks['per_track_audio_policy'] = w.audio_panel.policy.count() == 3
        checks['build_verification_report'] = w.image_tools.build_report.isEnabled()
        checks['resume_last_project'] = callable(w.open_last_build)
        write_json(smoke_report, {'version': VERSION, 'checks': checks, 'passed': all(checks.values())})
        # Do not save user settings from a packaging test.
        w.hide()
        return 0 if all(checks.values()) else 1
    w.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
