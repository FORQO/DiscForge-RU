"""Menu-only ISO operations with progress and cancellation."""
from pathlib import Path
import threading
import time
from PySide6.QtCore import QThread, Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QProgressBar, QFileDialog, QMessageBox
from iso_menu_update import update_iso, mounted_iso, open_menu


class MenuIsoJob(QThread):
    message = Signal(str)
    progress = Signal(float)
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, source, destination=None, design=None):
        super().__init__()
        self.source, self.destination, self.design = source, destination, design
        self.cancel_event = threading.Event()

    def run(self):
        try:
            if self.destination is None:
                with mounted_iso(self.source) as root:
                    chapters, _, design = open_menu(root)
                self.result.emit((chapters, design))
            else:
                output = update_iso(self.source, self.destination, self.design, self.cancel_event,
                                    self.message.emit, self.progress.emit)
                self.result.emit(output)
        except Exception as exc:
            self.failed.emit(str(exc))


class IsoMenuPanel(QWidget):
    edit_requested = Signal()
    loading_design = Signal()

    def __init__(self, image_tools, editor):
        super().__init__()
        self.owner, self.editor = image_tools, editor
        self.worker = None
        self.fraction = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel('Меню готового ISO DiscForge: видео и звук копируются без обработки, главы сохраняются. '
                      'Результат — новый ISO; требуется место примерно как для исходного образа. '
                      'Исправление обводки работает и без проекта. Для замены используется меню из вкладки «Меню».')
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.load = QPushButton('Загрузить меню из ISO')
        self.repair = QPushButton('Исправить обводку в ISO…')
        self.replace = QPushButton('Сохранить текущее меню в ISO…')
        self.load.clicked.connect(lambda: self.start('load'))
        self.repair.clicked.connect(lambda: self.start('repair'))
        self.replace.clicked.connect(lambda: self.start('replace'))
        for button in (self.load, self.repair, self.replace):
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFormat('%p%')
        row.addWidget(self.progress, 1)
        self.cancel = QPushButton('Отмена обновления')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_job)
        row.addWidget(self.cancel)
        layout.addLayout(row)
        self.status = QLabel('Обновление ISO не запущено.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)

    def is_running(self):
        return bool(self.worker and self.worker.isRunning())

    def start(self, mode):
        if self.owner.is_running():
            return
        if not self.owner.path.text().strip():
            self.owner.choose()
        source = Path(self.owner.path.text().strip())
        if not source.is_file() or source.suffix.lower() != '.iso':
            QMessageBox.warning(self, 'Меню ISO', 'Выберите существующий ISO в поле выше.')
            return
        design, destination = None, None
        try:
            if mode == 'replace':
                if self.editor is None:
                    raise ValueError('Редактор меню недоступен.')
                design = self.editor.design()
                if design is None:
                    raise ValueError('Включите интерактивное меню во вкладке «Меню».')
                # Freeze the design while the editor remains usable.
                from disc_menu import MenuDesign
                import copy
                design = MenuDesign.from_dict(design.title, copy.deepcopy(design.to_dict()))
            if mode != 'load':
                destination, _ = QFileDialog.getSaveFileName(self, 'Сохранить обновлённый ISO',
                    str(source.with_name(source.stem + '_menu.iso')), 'ISO (*.iso)')
                if not destination:
                    return
                if not destination.lower().endswith('.iso'):
                    destination += '.iso'
                if Path(destination).exists() or Path(destination).resolve() == source.resolve():
                    raise ValueError('Выберите новое имя: существующие образы не перезаписываются.')
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, 'Меню ISO', str(exc))
            return
        self.worker = MenuIsoJob(source, destination, design)
        self.worker.message.connect(self.owner.report.appendPlainText)
        self.worker.progress.connect(self.advance)
        self.worker.result.connect(self.done)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.started, self.fraction = time.monotonic(), 0
        self.progress.setValue(0)
        for button in (self.load, self.repair, self.replace, self.owner.check):
            button.setEnabled(False)
        self.owner.path.setEnabled(False)
        self.cancel.setEnabled(destination is not None)
        self.timer.start(1000)
        self.tick()
        self.worker.start()

    def advance(self, fraction):
        self.fraction = fraction
        self.progress.setValue(round(1000*fraction))
        self.tick()

    def tick(self):
        elapsed = max(0, time.monotonic()-self.started)
        def clock(seconds):
            n = int(seconds)
            return f'{n//3600:02}:{n//60%60:02}:{n%60:02}'
        remaining = clock(elapsed*(1-self.fraction)/self.fraction) if self.fraction > .01 else 'расчёт…'
        suffix = ' · отмена…' if self.worker.cancel_event.is_set() else ''
        self.status.setText(f'Прошло: {clock(elapsed)} · Осталось примерно: {remaining}{suffix}')

    def cancel_job(self):
        if self.is_running():
            self.worker.cancel_event.set()
            self.cancel.setEnabled(False)

    def done(self, result):
        self.timer.stop()
        if isinstance(result, tuple):
            chapters, design = result
            if design is None:
                self.status.setText('В старом ISO нет редактируемых настроек. Обводку можно исправить без проекта.')
                QMessageBox.information(self, 'Старое меню', 'В этом ISO сохранены готовые изображения меню, но нет исходных настроек. '
                    'Для исправления линий нажмите «Исправить обводку в ISO…». Для изменения текста или оформления '
                    'откройте сохранённый проект либо создайте меню во вкладке «Меню» и сохраните его в новый ISO.')
            elif self.editor is not None:
                self.loading_design.emit()
                self.editor.set_chapters(chapters)
                self.editor.apply_design(design)
                self.editor.enabled.setChecked(True)
                self.status.setText('Меню загружено. После редактирования нажмите «Сохранить текущее меню в ISO…».')
                self.edit_requested.emit()
        else:
            self.owner.path.setText(str(result))
            self.progress.setValue(1000)
            self.status.setText('Готово. Обновлённый ISO выбран выше — можно проверить его в VLC.')

    def failed(self, message):
        self.timer.stop()
        self.status.setText(message)
        self.owner.report.appendPlainText(message)

    def finished(self):
        self.timer.stop()
        for button in (self.load, self.repair, self.replace, self.owner.check):
            button.setEnabled(True)
        self.owner.path.setEnabled(True)
        self.cancel.setEnabled(False)
