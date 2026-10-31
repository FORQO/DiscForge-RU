"""Image inspection and explicit handoff to the Windows disc-image recorder."""
import os
from pathlib import Path
import subprocess
import sys
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton, QFileDialog, QComboBox, QPlainTextEdit, QMessageBox
from tools.iso_udf_preflight import inspect


class InspectThread(QThread):
    result = Signal(str, bool)

    def __init__(self, filename, profile):
        super().__init__()
        self.filename, self.profile = filename, profile

    def run(self):
        try:
            report = inspect(Path(self.filename), self.profile)
            text = ('Проверка UDF 2.50 пройдена.' if report.valid else 'Образ не прошёл проверку.')
            text += f'\nРазмер: {report.size_bytes / 1e9:.2f} ГБ\n'
            text += '\n'.join(report.errors)
            text += '\nПроверены метаданные ISO. Воспроизведение меню и фильма проверяется отдельно.'
            self.result.emit(text, report.valid)
        except Exception as exc:
            self.result.emit(str(exc), False)


class ImageTools(QWidget):
    def __init__(self, editor=None):
        super().__init__()
        self.worker = None
        self.verified = None
        box = QVBoxLayout(self)
        title = QLabel('Проверить образ и записать диск')
        title.setObjectName('section')
        box.addWidget(title)
        text = QLabel('Сначала откройте ISO в Blu-ray-проигрывателе и проверьте меню, главы и звук. '
                      'Для первой физической проверки удобно использовать перезаписываемый BD-RE.')
        text.setWordWrap(True)
        box.addWidget(text)
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setPlaceholderText('Выберите готовый ISO-образ')
        row.addWidget(self.path, 1)
        browse = QPushButton('Выбрать ISO…')
        browse.clicked.connect(self.choose)
        row.addWidget(browse)
        box.addLayout(row)
        row = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.addItems(['BD25', 'BD50'])
        row.addWidget(self.profile)
        self.check = QPushButton('Проверить ISO')
        self.check.clicked.connect(self.verify)
        row.addWidget(self.check)
        self.vlc = QPushButton('Проверить ISO в VLC')
        self.vlc.clicked.connect(self.open_vlc)
        row.addWidget(self.vlc)
        self.burn = QPushButton('Запись ISO: macOS' if sys.platform == 'darwin' else 'Записать средствами Windows…')
        self.burn.setEnabled(False)
        self.burn.clicked.connect(self.open_burner)
        row.addWidget(self.burn)
        box.addLayout(row)
        from iso_menu_panel import IsoMenuPanel
        self.build_report = QPushButton('Отчёт проверки сборки')
        self.build_report.clicked.connect(self.open_build_report)
        box.addWidget(self.build_report)
        self.menu_panel = IsoMenuPanel(self, editor)
        box.addWidget(self.menu_panel)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText('Здесь появится результат проверки.')
        box.addWidget(self.report, 1)
        note = QLabel('Откроется стандартное окно записи Windows: выберите привод и включите проверку диска после записи. '
                      'DiscForge не стирает носители и не начинает запись автоматически.')
        if sys.platform == 'darwin':
            note.setText('Запись готового образа на Mac доступна через macos_burn.py с проверкой записи. '
                         'Для просмотра меню установите VLC для Apple Silicon; Java входит в комплект.')
        note.setWordWrap(True)
        note.setObjectName('hint')
        box.addWidget(note)
        self.path.textChanged.connect(self.invalidate)
        self.profile.currentTextChanged.connect(self.invalidate)

    def open_build_report(self):
        path = Path(self.path.text()).with_name(Path(self.path.text()).name + '.verification.txt') if self.path.text().strip() else None
        if path and path.is_file():
            self.report.setPlainText('Сохранённый отчёт на момент сборки. Это не повторная проверка текущего ISO.\n\n' + path.read_text(encoding='utf-8-sig'))
        else:
            self.report.setPlainText('Рядом с выбранным ISO нет отчёта новой сборки. Проверка UDF доступна отдельно.')

    def invalidate(self, *_):
        self.verified = None
        self.burn.setEnabled(False)

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Выберите ISO', '', 'ISO (*.iso)')
        if path:
            self.path.setText(path)

    def _identity(self):
        file = Path(self.path.text()).resolve()
        stat = file.stat()
        return str(file), stat.st_size, stat.st_mtime_ns, self.profile.currentText()

    def verify(self):
        if self.is_running():
            return
        try:
            self._checking = self._identity()
        except OSError as exc:
            self.report.setPlainText(str(exc))
            return
        self.invalidate()
        self.check.setEnabled(False)
        self.report.setPlainText('Проверяем структуру UDF…')
        self.worker = InspectThread(self._checking[0], self._checking[3])
        self.worker.result.connect(self.done)
        self.worker.finished.connect(lambda: self.check.setEnabled(True))
        self.worker.start()

    def done(self, message, valid):
        self.report.setPlainText(message)
        try:
            if valid and self._identity() == self._checking:
                self.verified = self._checking
                self.burn.setEnabled(sys.platform == 'win32')
        except OSError:
            self.invalidate()

    def open_burner(self):
        try:
            if not self.verified or self._identity() != self.verified:
                self.invalidate()
                raise ValueError('Образ изменился. Повторите проверку.')
            executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/isoburn.exe'
            subprocess.Popen([str(executable), self.verified[0]])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Запись образа', str(exc))

    def open_vlc(self):
        from vlc_preview import find_vlc, launch_iso
        if not self.path.text().strip():
            self.choose()
        if not self.path.text().strip():
            return
        vlc = find_vlc()
        if vlc is None:
            vlc, _ = QFileDialog.getOpenFileName(self, 'Выберите VLC.app' if sys.platform == 'darwin' else 'Выберите VLC 64-bit (vlc.exe)', '',
                                                'Приложения (*.app);;Все файлы (*)' if sys.platform == 'darwin' else 'VLC (vlc.exe)')
            if not vlc:
                return
        try:
            launch_iso(self.path.text(), vlc)
            self.report.appendPlainText('ISO открыт в отдельном VLC с меню Blu-ray. '
                                        'Проверьте запуск фильма, главы и возврат в меню.')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Проверить ISO в VLC', str(exc))

    def is_running(self):
        return bool(self.worker and self.worker.isRunning()) or self.menu_panel.is_running()
