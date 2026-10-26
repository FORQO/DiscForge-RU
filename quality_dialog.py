"""Video-only trial encoder and synchronized, full-resolution comparison viewer."""
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QTime
from PySide6.QtGui import QImage, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QSpinBox, QTimeEdit, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QProgressBar, QSlider, QGraphicsView, QGraphicsScene,
    QPlainTextEdit, QMessageBox, QWidget, QCheckBox)

from quality_trial import (TrialPlan, FPS, WIDTH, HEIGHT, suggested_rates,
                           comparison_command, ensure_source)
from quality_scenes import timecode
from quality_search import run_quality_search


class TrialWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)
    message = Signal(str)
    progress = Signal(int)

    def __init__(self, info, plan, config, directory, parent=None):
        super().__init__(parent)
        self.args = info, plan, config, directory
        self.cancel = threading.Event()

    def run(self):
        try:
            from quality_cache import fingerprint, make_record
            info, plan, config, _ = self.args
            identity = fingerprint(info, plan, config, self.cancel, self.message.emit,
                                   lambda p: self.progress.emit(round(p*.1)))
            result = run_quality_search(*self.args, self.cancel, self.message.emit,
                               lambda value: self.progress.emit(round(10+value*.9)))
            ensure_source(info.path, identity['source']['stamp'])
            result['_project_record'] = make_record(result, plan, identity)
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class RestoreWorker(TrialWorker):
    def run(self):
        try:
            from quality_cache import restore_record
            info, plan, config, record = self.args
            self.completed.emit(restore_record(record, info, plan, config, self.cancel,
                self.message.emit, lambda p: self.progress.emit(round(p))))
        except Exception as exc:
            self.failed.emit(str(exc))


class FrameWorker(QThread):
    frame_ready = Signal(object, int)
    failed = Signal(str)

    def __init__(self, command, frame, play, parent=None):
        super().__init__(parent)
        self.command, self.frame, self.play = command, frame, play
        self.cancel = threading.Event()
        self.ack = threading.Event()
        self.lock = threading.Lock()
        self.process = None

    def stop(self):
        self.cancel.set()
        self.ack.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.kill()
                except OSError:
                    pass

    def run(self):
        process = None
        try:
            with tempfile.TemporaryFile() as errors:
                with self.lock:
                    if self.cancel.is_set():
                        return
                    process = self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=errors,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                size = WIDTH*2*HEIGHT*3
                count = 0
                while not self.cancel.is_set():
                    # Buffered read returns a complete frame or EOF; stop() kills a
                    # blocked decoder. Only one unacknowledged frame can be queued.
                    data = process.stdout.read(size)
                    if not data:
                        break
                    if len(data) != size:
                        raise RuntimeError('Декодер вернул неполный кадр.')
                    started = time.monotonic()
                    image = QImage(data, WIDTH*2, HEIGHT, WIDTH*2*3, QImage.Format_RGB888).copy()
                    self.ack.clear()
                    self.frame_ready.emit(image, self.frame+count)
                    while not self.cancel.is_set() and not self.ack.wait(.1):
                        pass
                    count += 1
                    if not self.play:
                        break
                    self.cancel.wait(max(0, 1/FPS-(time.monotonic()-started)))
                if not self.cancel.is_set():
                    code = process.wait(timeout=10)
                    if code or not count:
                        errors.seek(0)
                        raise RuntimeError('Не удалось показать сравнение. '+errors.read(2000).decode('utf-8', 'replace'))
        except Exception as exc:
            if not self.cancel.is_set():
                self.failed.emit(str(exc))
        finally:
            with self.lock:
                if process:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    process.stdout.close()
                self.process = None


class FrameView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = self.scene().addPixmap(QPixmap())
        self.fit = True
        self.setMinimumSize(200, 170)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(Qt.black)

    def set_image(self, image):
        self.item.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self.item.boundingRect())
        self.update_zoom()

    def update_zoom(self):
        if self.fit and not self.item.pixmap().isNull():
            self.fitInView(self.item.boundingRect(), Qt.KeepAspectRatio)
        else:
            self.resetTransform()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_zoom()


class QualityDialog(QDialog):
    def __init__(self, info, config, rate, encoder=2, fast=False, gpu_frames=True,
                 copying=False, parent=None, *, saved_record=None):
        super().__init__(parent)
        self.info, self.config = info, dict(config)
        self.encoder, self.fast, self.gpu_frames = encoder, fast, gpu_frames
        self.enhance = self.config.get('enhance', False)
        self.quiet_rate = self.config.get('quiet_rate', 0.0)
        self.result = self.worker = self.frame_worker = self.temporary = None
        self.result_plan = None
        self.batch_result = None
        self.selected_rate = None
        self.close_pending = False
        self.saved_record = saved_record
        self.restoring = False
        self.setWindowTitle('Проба качества · '+info.name)
        self.resize(1320, 880)
        self.setMinimumSize(900, 700)
        root = QVBoxLayout(self)
        title = QLabel('Подберите битрейт по качеству сложных сцен')
        title.setStyleSheet('font-size: 18px; font-weight: 600;')
        root.addWidget(title)
        note = QLabel('Автоматический режим просматривает до 24 фрагментов и выбирает сцены с движением, деталями и тенями. '
                      'Общий битрейт должен пройти проверку каждой сцены. Это выборочная оценка, не гарантия качества всего фильма.')
        note.setWordWrap(True)
        root.addWidget(note)
        treatment = QLabel(('Бережное улучшение включено: CPU-фильтры + выбранный кодер. '
            'VMAF/SSIM оценивают потери сжатия ПОСЛЕ обработки, а не пользу улучшения. '
            'Само улучшение оценивайте по сравнению до/после в движении. ' if self.enhance else 'Улучшение выключено. ') +
            (f'Тихий режим: {self.quiet_rate:g}×, пробы будут создаваться медленнее.' if self.quiet_rate else 'Тихий режим выключен.') +
            ' Эти настройки меняются в разделе «Подготовка».')
        treatment.setWordWrap(True)
        root.addWidget(treatment)
        self.controls = QWidget()
        controls_layout = QVBoxLayout(self.controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        mode_row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(['Автоматически · сложные сцены', 'Вручную · один фрагмент'])
        mode_row.addWidget(self.mode)
        mode_row.addWidget(QLabel('Количество сцен:'))
        self.scene_count = QSpinBox()
        self.scene_count.setRange(2, 5)
        self.scene_count.setValue(3)
        mode_row.addWidget(self.scene_count)
        self.retry_rates = QCheckBox('Автоподбор, если пробы не подошли')
        self.retry_rates.setChecked(True)
        self.retry_rates.setToolTip('До 8 дополнительных раундов на тех же сценах. '
            'После подходящего результата — уточнение с шагом до 0,5 Мбит/с. '
            'Предел — 28 Мбит/с или меньше с учётом звука. Старые пробы не кодируются заново.')
        mode_row.addWidget(self.retry_rates)
        mode_row.addStretch()
        controls_layout.addLayout(mode_row)
        row = QHBoxLayout()
        controls_layout.addLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel('Начало:'))
        self.start = QTimeEdit(QTime(0, 0))
        self.start.setDisplayFormat('HH:mm:ss')
        self.start.setMaximumTime(QTime(0, 0).addSecs(min(86399, max(0, int(info.duration)-1))))
        self.start.setTime(QTime(0, 0).addSecs(min(86399, int(info.duration*.5))))
        row.addWidget(self.start)
        self.scene_preset = QComboBox()
        self.scene_preset.addItems(['Выбрать сцену…', 'Начало · 15%', 'Середина · 50%', 'Конец · 85%'])
        self.scene_preset.activated.connect(self._scene)
        row.addWidget(self.scene_preset)
        row.addWidget(QLabel('Длина:'))
        self.duration = QSpinBox()
        self.duration.setRange(1, 30)
        self.duration.setValue(min(10, max(1, int(info.duration))))
        self.duration.setSuffix(' с')
        row.addWidget(self.duration)
        row.addStretch()
        row.addWidget(QLabel('Мбит/с:'))
        self.rates = []
        for value in suggested_rates(rate):
            spin = QDoubleSpinBox()
            spin.setRange(5, 28)
            spin.setDecimals(1)
            spin.setSingleStep(.5)
            spin.setValue(value/1e6)
            spin.valueChanged.connect(self._refresh_apply)
            self.rates.append(spin)
            row.addWidget(spin)
        self.start.timeChanged.connect(self._refresh_apply)
        self.duration.valueChanged.connect(self._refresh_apply)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.scene_count.valueChanged.connect(self._refresh_apply)
        self.retry_rates.toggled.connect(self._refresh_apply)
        root.addWidget(self.controls)
        encoding = ['CPU x264', 'NVIDIA NVENC', 'Авто: NVIDIA / CPU'][encoder]
        root.addWidget(QLabel(f'Настройки сборки: {encoding} · {"быстро" if fast else "сбалансированно"}. '
                              'Проба содержит только видео, без звука, субтитров и меню.'))
        actions = QHBoxLayout()
        self.encode = QPushButton('Создать пробы')
        self.encode.clicked.connect(self._begin)
        actions.addWidget(self.encode)
        self.cancel_button = QPushButton('Остановить')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        actions.addWidget(self.cancel_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        actions.addWidget(self.progress, 1)
        root.addLayout(actions)
        scene_row = QHBoxLayout()
        scene_row.addWidget(QLabel('Просмотр сцены:'))
        self.scene_results = QComboBox()
        self.scene_results.setEnabled(False)
        self.scene_results.currentIndexChanged.connect(self._result_scene_changed)
        scene_row.addWidget(self.scene_results, 1)
        self.weak_scene = QPushButton('Самая слабая сцена')
        self.weak_scene.setToolTip('Открыть сцену и кадр с худшей оценкой для выбранного битрейта.')
        self.weak_scene.setEnabled(False)
        self.weak_scene.clicked.connect(self._weak_scene)
        scene_row.addWidget(self.weak_scene)
        root.addLayout(scene_row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['Битрейт видео', 'Размер пробы', 'Видео фильма',
                                              'VMAF · среднее / P5', 'SSIM · среднее / P5', 'Кодирование', 'Оценка'])
        self.table.horizontalHeaderItem(2).setToolTip('Приблизительный размер видео всего фильма по целевому битрейту; без аудио, субтитров и меню.')
        self.table.horizontalHeaderItem(3).setToolTip('Баллы VMAF, не процент сохранённого качества. P5 — нижний 5-й процентиль кадров. Чем выше, тем лучше.')
        self.table.horizontalHeaderItem(4).setToolTip('SSIM от −1 до 1. P5 — нижний 5-й процентиль. В подсказке строки есть минимум и номер худшего кадра.')
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setMaximumHeight(128)
        self.table.itemSelectionChanged.connect(self._variant_changed)
        root.addWidget(self.table)
        recommendation_row = QHBoxLayout()
        self.advice_label = QLabel('После кодирования программа автоматически оценит VMAF и SSIM для всех кадров пробы.')
        self.advice_label.setWordWrap(True)
        self.advice_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.advice_label.setToolTip('Пороги DiskForge: VMAF среднее ≥ 95, P5 ≥ 90, минимум ≥ 85; '
            'SSIM среднее ≥ 0,990, P5 ≥ 0,970, минимум ≥ 0,950. '
            'Среди прошедших — минимальный битрейт с отставанием от лучших средних/P5 не более 1/2 баллов VMAF. '
            'Это эвристика для выбранного фрагмента, не гарантия отсутствия потерь.')
        recommendation_row.addWidget(self.advice_label, 1)
        self.pick_recommended = QPushButton('Выбрать рекомендацию')
        self.pick_recommended.setEnabled(False)
        self.pick_recommended.clicked.connect(self._select_recommended)
        recommendation_row.addWidget(self.pick_recommended)
        root.addLayout(recommendation_row)
        self.archive_note = QLabel('Оценки восстановлены из проекта. Временные видеопробы не сохраняются; '
            'для просмотра кадров создайте новые пробы. Битрейт можно применить без нового подбора.')
        self.archive_note.setWordWrap(True)
        self.archive_note.hide()
        root.addWidget(self.archive_note)
        labels = QHBoxLayout()
        self.reference_label = QLabel('Исходник в формате диска · без потерь')
        labels.addWidget(self.reference_label, 1)
        self.variant_label = QLabel('Выберите готовый вариант')
        labels.addWidget(self.variant_label, 1)
        root.addLayout(labels)
        self.comparison_mode = QComboBox()
        self.comparison_mode.addItems(['До / после улучшения · без потерь от кодера',
                                      'До / готовая проба', 'После улучшения / готовая проба · потери сжатия'])
        self.comparison_mode.setVisible(self.enhance)
        self.comparison_mode.currentIndexChanged.connect(self._variant_changed)
        root.addWidget(self.comparison_mode)
        views = QHBoxLayout()
        self.left, self.right = FrameView(), FrameView()
        views.addWidget(self.left)
        views.addWidget(self.right)
        root.addLayout(views, 1)
        for a, b in ((self.left, self.right), (self.right, self.left)):
            a.horizontalScrollBar().valueChanged.connect(b.horizontalScrollBar().setValue)
            a.verticalScrollBar().valueChanged.connect(b.verticalScrollBar().setValue)
        transport = QHBoxLayout()
        self.play = QPushButton('▶')
        self.play.setCheckable(True)
        self.play.setToolTip('Синхронное воспроизведение без звука; скорость зависит от декодирования')
        self.play.toggled.connect(self._play_changed)
        transport.addWidget(self.play)
        self.previous, self.next = QPushButton('‹'), QPushButton('›')
        self.previous.setToolTip('Предыдущий кадр')
        self.next.setToolTip('Следующий кадр')
        self.previous.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        transport.addWidget(self.previous)
        transport.addWidget(self.next)
        self.worst = QPushButton('Слабый кадр')
        self.worst.setToolTip('Перейти к кадру с минимальным VMAF выбранной пробы; при отсутствии VMAF — с минимальным SSIM.')
        self.worst.clicked.connect(self._worst_frame)
        transport.addWidget(self.worst)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.valueChanged.connect(self._seek)
        transport.addWidget(self.slider, 1)
        self.frame_label = QLabel('Кадр —')
        self.frame_label.setMinimumWidth(175)
        transport.addWidget(self.frame_label)
        self.zoom = QComboBox()
        self.zoom.addItems(['Вписать', '100% · детали'])
        self.zoom.setToolTip('В масштабе 100% перетаскивайте изображение: обе стороны перемещаются вместе.')
        self.zoom.currentIndexChanged.connect(self._zoom)
        transport.addWidget(self.zoom)
        root.addLayout(transport)
        self.status = QLabel('Пробы временные: при закрытии окна они удаляются. Исходный фильм не изменяется.')
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(65)
        self.log.setMaximumBlockCount(100)
        root.addWidget(self.log)
        bottom = QHBoxLayout()
        footnote = QLabel(('Сейчас видео копируется без потерь. Выбранный битрейт будет сохранён для режима перекодирования. '
                          if copying else '') +
                         'Размер всего фильма рассчитан по целевому битрейту; это оценка видео, не размер ISO. '
                         'Звук, субтитры и меню учитываются на общей шкале диска после применения.')
        footnote.setWordWrap(True)
        bottom.addWidget(footnote, 1)
        self.apply_button = QPushButton('Применить битрейт к фильму')
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply)
        bottom.addWidget(self.apply_button)
        close = QPushButton('Закрыть')
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        root.addLayout(bottom)
        self.seek_timer = QTimer(self)
        self.seek_timer.setSingleShot(True)
        self.seek_timer.setInterval(180)
        self.seek_timer.timeout.connect(self._show_frame)
        self._enable_view(False)
        self._mode_changed()
        if saved_record:
            QTimer.singleShot(0, self._restore_saved)

    def _restore_saved(self):
        if self.close_pending or self._busy():
            return
        from quality_cache import validate_record
        try:
            record = validate_record(self.saved_record)
            data = record['plan']
            if (data['encoder'], data['fast'], data['gpu_frames'], data['enhance'], data['quiet_rate']) != (self.encoder, self.fast, self.gpu_frames, self.enhance, self.quiet_rate):
                raise ValueError('Настройки кодера изменились. Для актуальных оценок создайте новые пробы.')
            self.mode.setCurrentIndex(0 if data['automatic'] else 1)
            self.start.setTime(QTime(0, 0).addSecs(int(data['start'])))
            self.duration.setValue(int(data['seconds']))
            self.scene_count.setValue(data['scene_count'])
            self.retry_rates.setChecked(data['retry_rates'])
            for index, widget in enumerate(self.rates):
                widget.setValue(data['rates'][min(index, len(data['rates'])-1)]/1e6)
            self.result_plan = self._plan()
            self.result_plan.validate(self.info)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            self.status.setText('Сохранённые пробы устарели: '+str(exc))
            return
        self.restoring = True
        self.controls.setEnabled(False)
        self.encode.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.status.setText('Проверяем сохранённые результаты…')
        self.worker = RestoreWorker(self.info, self.result_plan, self.config, record, self)
        self.worker.message.connect(self._message)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _plan(self):
        start = 0 if self.mode.currentIndex() == 0 else QTime(0, 0).secsTo(self.start.time())
        return TrialPlan(start, self.duration.value(), tuple(sorted({round(s.value()*1e6) for s in self.rates})),
                         self.encoder, self.fast, self.gpu_frames, self.mode.currentIndex() == 0,
                         self.scene_count.value(), self.retry_rates.isChecked(), self.enhance, self.quiet_rate)

    def _mode_changed(self, *_):
        automatic = self.mode.currentIndex() == 0
        self.start.setEnabled(not automatic)
        self.scene_preset.setEnabled(not automatic)
        self.scene_count.setEnabled(automatic)
        self._refresh_apply()

    def _scene(self, index):
        if index:
            self.start.setTime(QTime(0, 0).addSecs(min(86399, int(self.info.duration*(.15, .5, .85)[index-1]))))

    def _busy(self):
        return bool(self.worker and self.worker.isRunning())

    def _enable_view(self, enabled):
        for widget in (self.play, self.previous, self.next, self.slider, self.zoom, self.comparison_mode):
            widget.setEnabled(enabled)
        self.worst.setEnabled(enabled and self._worst_index() is not None)

    def _cleanup(self):
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None

    def _begin(self):
        if self._busy():
            return
        try:
            plan = self._plan()
            plan.validate(self.info)
            output = Path(self.config['output']).expanduser()
            output.mkdir(parents=True, exist_ok=True)
            self._stop_frames()
            self._cleanup()
            self.temporary = tempfile.TemporaryDirectory(prefix='DiskForge-trial-', dir=output)
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, 'Не удалось начать пробу', str(exc))
            return
        self.result = None
        self.restoring = False
        self.archive_note.hide()
        self.left.show()
        self.right.show()
        self.batch_result = None
        self.scene_results.blockSignals(True)
        self.scene_results.clear()
        self.scene_results.blockSignals(False)
        self.scene_results.setEnabled(False)
        self.weak_scene.setEnabled(False)
        self.result_plan = plan
        self.table.setRowCount(0)
        for view in (self.left, self.right):
            view.set_image(QImage())
        self._enable_view(False)
        self.apply_button.setEnabled(False)
        self.pick_recommended.setEnabled(False)
        self.advice_label.setText('Кодирование, затем автоматическая оценка всех кадров: VMAF и SSIM…')
        self.controls.setEnabled(False)
        self.encode.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.log.clear()
        self.status.setText('Подготавливаем пробу…')
        self.worker = TrialWorker(self.info, plan, self.config, self.temporary.name, self)
        self.worker.message.connect(self._message)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _cancel(self):
        if self._busy():
            self.worker.cancel.set()
            self.cancel_button.setEnabled(False)
            self.status.setText('Останавливаем обработку…')

    def _message(self, message):
        self.log.appendPlainText(message)
        self.status.setText(message if len(message) <= 320 else message[:317]+'…')

    def _completed(self, result):
        if self.close_pending or self.worker.cancel.is_set():
            return
        if isinstance(result.get('_project_record'), dict):
            self.saved_record = result['_project_record']
        archived = result.get('archived', False)
        self.archive_note.setVisible(archived)
        self.left.setVisible(not archived)
        self.right.setVisible(not archived)
        self.batch_result = result if result.get('scenes') else None
        self.scene_results.blockSignals(True)
        self.scene_results.clear()
        scenes = result.get('scenes', [result])
        for i, scene in enumerate(scenes):
            self.scene_results.addItem(f"{i+1}. {timecode(scene.get('start', 0))} · {scene['duration']:.1f} с · "
                + scene.get('selection', {}).get('reason', 'выбрана вручную'))
        self.scene_results.setCurrentIndex(0)
        self.scene_results.blockSignals(False)
        self.scene_results.setEnabled(len(scenes) > 1)
        self._display_result(dict(scenes[0], recommendation=result.get('recommendation', {})))
        if archived and self.saved_record.get('selected_rate'):
            rate = self.saved_record['selected_rate']
            self.table.selectRow(next(i for i, v in enumerate(scenes[0]['variants']) if v['bitrate'] == rate))

    def _display_result(self, result, preferred=None):
        self._pause()
        self._stop_frames()
        self.result = result
        advice = result.get('recommendation', {})
        recommended = advice.get('bitrate')
        self.table.horizontalHeaderItem(6).setText('Итог по сценам' if self.batch_result else 'Оценка')
        self.advice_label.setText(advice.get('explanation', 'Метрики для этой пробы не рассчитаны. Сравните её вручную.'))
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setRowCount(len(result['variants']))
        for row, variant in enumerate(result['variants']):
            quality = variant.get('quality', {})
            metric_text = lambda name, digits: (f"{quality[name]['mean']:.{digits}f} / {quality[name]['p5']:.{digits}f}"
                                               if quality.get(name) else 'Недоступно')
            label = 'Рекомендуется' if variant['bitrate'] == recommended else (
                'Проходит пороги' if variant['bitrate'] in advice.get('eligible', []) else
                ('Не во всех сценах' if self.batch_result else 'Не проходит'))
            if advice.get('status') == 'provisional' and variant['bitrate'] == recommended:
                label = 'Предварительно'
            if advice.get('status') == 'unavailable' or not advice:
                label = 'Нет оценки'
            values = (f"{variant['bitrate']/1e6:g} Мбит/с", f"{variant['bytes']/1e6:.2f} МБ",
                      f"{variant['film_video_bytes']/1e9:.2f} ГБ", metric_text('vmaf', 2),
                      metric_text('ssim', 4), f"{variant['seconds']:.1f} с", label)
            details = [advice.get('explanation', '')]
            if self.batch_result and advice.get('per_scene'):
                local_advice = advice['per_scene'][self.scene_results.currentIndex()]
                details.append('Открытая сцена: '+('проходит пороги и сравнение с лучшими пробами.'
                    if variant['bitrate'] in local_advice['eligible'] else 'не проходит критерии.'))
            for method in ('vmaf', 'ssim'):
                if quality.get(method):
                    m = quality[method]
                    details.append(f"{method.upper()}: минимум {m['minimum']:.4f}, кадр {m['worst_frame']+1}, оценено {m['frames']} кадров.")
            details.extend(quality.get('warnings', []))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip('\n'.join(details))
                if variant['bitrate'] == recommended:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setForeground(QColor('#a5d6ac'))
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setRange(0, result['frames']-1)
        self.slider.setValue(0)
        self.slider.blockSignals(False)
        self.status.setText(f"{result['encoder']} · {result['preset']} · {result['duration']:.2f} с. "
                            'Метрики в таблице — для открытой сцены; рекомендация — для всей проверки.')
        preferred = preferred or recommended or advice.get('best_bitrate')
        self.table.selectRow(next((i for i, v in enumerate(result['variants']) if v['bitrate'] == preferred), 0))

    def _result_scene_changed(self, index):
        if not self.batch_result or index < 0:
            return
        rate = None
        if self.result and self.table.currentRow() >= 0:
            rate = self.result['variants'][self.table.currentRow()]['bitrate']
        scene = self.batch_result['scenes'][index]
        self._display_result(dict(scene, recommendation=self.batch_result['recommendation']), rate)

    def _weak_scene(self):
        if not self.batch_result or not self.result or self.table.currentRow() < 0:
            return
        rate = self.result['variants'][self.table.currentRow()]['bitrate']
        method = self.batch_result['recommendation'].get('method')
        if not method:
            return
        candidates = []
        for i, scene in enumerate(self.batch_result['scenes']):
            variant = next(v for v in scene['variants'] if v['bitrate'] == rate)
            metric = variant.get('quality', {}).get(method)
            if metric:
                candidates.append((metric['minimum'], i, metric['worst_frame']))
        if candidates:
            _, index, frame = min(candidates)
            self.scene_results.setCurrentIndex(index)
            self._pause()
            self.slider.setValue(frame)
            self._show_frame()

    def _failed(self, error):
        self.result = None
        self.batch_result = None
        self.advice_label.setText('Оценка не завершена. Для рекомендации повторите пробу.')
        self.status.setText('Проверка отменена.' if self.worker.cancel.is_set() else
                           ('Сохранённые пробы не применены: ' if self.restoring else 'Ошибка: ')+error)
        self.log.appendPlainText(error)

    def _finished(self):
        self.controls.setEnabled(True)
        self.encode.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._refresh_apply()
        if not self.result:
            self._cleanup()
        if self.close_pending:
            self.reject()

    def _refresh_apply(self, *_):
        valid = bool(self.result and self._plan() == self.result_plan and self.table.currentRow() >= 0 and not self._busy())
        self.apply_button.setEnabled(valid)
        self.pick_recommended.setEnabled(valid and bool(self.result.get('recommendation', {}).get('bitrate')))
        self.weak_scene.setEnabled(bool(self.batch_result and self.batch_result['recommendation'].get('method')
                                       and self.result and not self.result.get('archived')))
        if self.result and self._plan() != self.result_plan:
            self.status.setText('Параметры изменены. Создайте новые пробы перед применением; на экране — предыдущая проба.')

    def _select_recommended(self):
        self._refresh_apply()
        if not self.pick_recommended.isEnabled():
            return
        rate = self.result['recommendation']['bitrate']
        self.table.selectRow(next(i for i, v in enumerate(self.result['variants']) if v['bitrate'] == rate))

    def _worst_index(self):
        if not self.result or self.table.currentRow() < 0:
            return None
        quality = self.result['variants'][self.table.currentRow()].get('quality', {})
        metric = quality.get('vmaf') or quality.get('ssim')
        return metric.get('worst_frame') if metric else None

    def _worst_frame(self):
        index = self._worst_index()
        if index is not None:
            self._pause()
            self.slider.setValue(index)
            self._show_frame()

    def _variant_changed(self, *_):
        if not self.result or self.table.currentRow() < 0:
            return
        self._pause()
        self._enable_view(not self.result.get('archived'))
        variant = self.result['variants'][self.table.currentRow()]
        self.variant_label.setText(f"Результат · {variant['bitrate']/1e6:g} Мбит/с")
        self._refresh_apply()
        self._show_frame()

    def _pause(self):
        self.play.blockSignals(True)
        self.play.setChecked(False)
        self.play.setText('▶')
        self.play.blockSignals(False)

    def _stop_frames(self):
        self.seek_timer.stop()
        if self.frame_worker:
            self.frame_worker.stop()
            if not self.frame_worker.wait(5000):
                raise RuntimeError('Декодер ещё останавливается. Повторите действие через несколько секунд.')
            self.frame_worker.deleteLater()
            self.frame_worker = None

    def _show_frame(self):
        if not self.result or self.result.get('archived') or self.table.currentRow() < 0 or self.close_pending:
            return
        try:
            self._stop_frames()
            variant = self.result['variants'][self.table.currentRow()]
            frame = self.slider.value()
            reference, after = self.result['reference'], variant['path']
            self.reference_label.setText('Исходник в формате диска · без потерь')
            self.variant_label.setText(f"Готовая проба · {variant['bitrate']/1e6:g} Мбит/с")
            if self.result.get('enhance'):
                mode = self.comparison_mode.currentIndex()
                reference = self.result['before'] if mode in (0, 1) else self.result['reference']
                after = self.result['reference'] if mode == 0 else variant['path']
                self.reference_label.setText('До улучшения · обычное масштабирование' if mode in (0, 1) else 'После улучшения · без потерь от кодера')
                if mode == 0:
                    self.variant_label.setText('После улучшения · без потерь от кодера')
            command = comparison_command(self.config['ffmpeg'], reference, after, frame, self.play.isChecked())
            worker = self.frame_worker = FrameWorker(command, frame, self.play.isChecked(), self)
            worker.frame_ready.connect(lambda image, index, w=worker: self._frame(w, image, index))
            worker.failed.connect(lambda error, w=worker: self._frame_error(w, error))
            worker.finished.connect(lambda w=worker: self._play_finished(w))
            worker.start()
        except RuntimeError as exc:
            self.status.setText(str(exc))
            self._pause()

    def _frame(self, worker, image, index):
        try:
            if worker is self.frame_worker and not worker.cancel.is_set():
                self.left.set_image(image.copy(0, 0, WIDTH, HEIGHT))
                self.right.set_image(image.copy(WIDTH, 0, WIDTH, HEIGHT))
                self.slider.blockSignals(True)
                self.slider.setValue(index)
                self.slider.blockSignals(False)
                self.frame_label.setText(f'Кадр {index+1}/{self.result["frames"]} · {index/FPS:.2f} с')
        finally:
            worker.ack.set()

    def _frame_error(self, worker, error):
        if worker is self.frame_worker:
            self.status.setText(error)
            self._pause()

    def _play_finished(self, worker):
        if worker is self.frame_worker:
            self._pause()

    def _seek(self, *_):
        self._pause()
        if self.frame_worker:
            self.frame_worker.stop()
        self.seek_timer.start()

    def _step(self, amount):
        self._pause()
        self.slider.setValue(self.slider.value()+amount)

    def _play_changed(self, playing):
        self.play.setText('Ⅱ' if playing else '▶')
        if playing and self.slider.value() == self.slider.maximum():
            self.slider.blockSignals(True)
            self.slider.setValue(0)
            self.slider.blockSignals(False)
        self._show_frame()

    def _zoom(self, index):
        for view in (self.left, self.right):
            view.fit = index == 0
            view.update_zoom()

    def _apply(self):
        self._refresh_apply()
        if not self.apply_button.isEnabled():
            return
        try:
            ensure_source(self.info.path, self.result['source'])
            self._stop_frames()
            self._cleanup()
        except (ValueError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, 'Проба устарела или недоступна', str(exc))
            return
        self.selected_rate = self.result['variants'][self.table.currentRow()]['bitrate']
        if isinstance(self.saved_record, dict):
            self.saved_record['selected_rate'] = self.selected_rate
        super().accept()

    def reject(self):
        self.close_pending = True
        if self._busy():
            self._cancel()
            return
        try:
            self._stop_frames()
            self._cleanup()
        except (OSError, RuntimeError) as exc:
            self.close_pending = False
            self.status.setText(str(exc))
            return
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()
