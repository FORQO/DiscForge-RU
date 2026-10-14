"""Read-only NVIDIA telemetry, polled asynchronously while a build is active."""
import csv
import io
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


QUERY = ['--query-gpu=index,name,temperature.gpu,utilization.gpu,utilization.encoder',
         '--format=csv,noheader,nounits']
HOT_TEMPERATURE = 85
COOLED_TEMPERATURE = 80


@dataclass(frozen=True)
class GpuReading:
    index: int
    name: str
    temperature: int | None
    gpu: int | None
    encoder: int | None


def parse_readings(output):
    readings = []
    seen = set()
    def number(value, maximum):
        value = value.strip()
        if not value.isascii() or not value.isdecimal():
            return None
        result = int(value)
        return result if 0 <= result <= maximum else None
    for row in csv.reader(io.StringIO(output), skipinitialspace=True):
        if len(row) != 5:
            continue
        index = number(row[0], 1023)
        name = ' '.join(row[1].split())[:120]
        if index is None or index in seen or not name:
            continue
        seen.add(index)
        readings.append(GpuReading(index, name, number(row[2], 150),
                                   number(row[3], 100), number(row[4], 100)))
    return readings


def find_nvidia_smi():
    found = shutil.which('nvidia-smi')
    if found:
        return found
    if os.name == 'nt':
        for env, suffix in [('SystemRoot', 'System32/nvidia-smi.exe'),
                            ('ProgramW6432', 'NVIDIA Corporation/NVSMI/nvidia-smi.exe'),
                            ('ProgramFiles', 'NVIDIA Corporation/NVSMI/nvidia-smi.exe')]:
            base = os.environ.get(env)
            if base and (Path(base)/suffix).is_file():
                return str(Path(base)/suffix)
    return None


class GpuMonitor(QWidget):
    temperature_warning = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active = False
        self.program = None
        self.readings = []
        self.timed_out = False
        self.accept_result = False
        self.hot_cards = {}
        self.missing_temperatures = set()
        self.status = QLabel('Видеокарта · показания появятся во время сборки')
        self.values = QLabel('Температура: —    GPU: —    Кодирование NVENC: —')
        for label in (self.status, self.values):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
        self.values.setAccessibleName('Температура и загрузка видеокарты')
        self.heat_warning = QLabel()
        self.heat_warning.setTextFormat(Qt.PlainText)
        self.heat_warning.setWordWrap(True)
        self.heat_warning.setAccessibleName('Предупреждение о высокой температуре GPU')
        self.heat_warning.setStyleSheet('QLabel { background: #522727; color: #fff0e8; '
                                       'border: 2px solid #f18a65; border-radius: 5px; '
                                       'padding: 8px; font-weight: bold; }')
        self.heat_warning.hide()
        self.setToolTip('Показания всех видеокарт NVIDIA, обновление каждые 2 секунды.\n'
                        'Температура — датчик GPU, не Hot Spot. GPU и NVENC — разные показатели нагрузки.\n'
                        'Учитывается работа всех программ, а не только DiskForge.\n'
                        '«—» означает, что драйвер не предоставляет показание.\n'
                        'Предупреждение от 85 °C, сброс при 80 °C и ниже. Это порог программы, '
                        'а не температурный предел всех видеокарт. Сборка автоматически не останавливается.')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(2)
        layout.addWidget(self.status)
        layout.addWidget(self.values)
        layout.addWidget(self.heat_warning)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.setInterval(1500)
        self.deadline.timeout.connect(self._timeout)
        self.process = QProcess(self)
        # QProcess launches asynchronously without a shell or visible console.
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

    def start(self):
        self.stop()
        self.active = True
        self.readings = []
        self.hot_cards.clear()
        self.missing_temperatures.clear()
        self.heat_warning.hide()
        self.program = find_nvidia_smi()
        if not self.program:
            self._unavailable('Мониторинг NVIDIA недоступен: видеокарта или её драйвер не найдены')
            return
        self.status.setText('Видеокарта · получение показаний…')
        self.values.setText('Температура: —    GPU: —    Кодирование NVENC: —')
        self.timer.start()
        self.poll()

    def poll(self):
        if not self.active or not self.program or self.process.state() != QProcess.NotRunning:
            return
        self.timed_out = False
        self.accept_result = True
        self.deadline.start()
        self.process.start(self.program, QUERY)

    def _timeout(self):
        self.timed_out = True
        if self.active:
            self._unavailable('Видеокарта · драйвер не ответил; повторяем опрос…')
        self.process.kill()

    def _error(self, error):
        if error == QProcess.FailedToStart:
            self.deadline.stop()
            if self.active:
                self._unavailable('Видеокарта · не удалось получить данные драйвера NVIDIA')

    def _finished(self, code, exit_status):
        self.deadline.stop()
        raw = bytes(self.process.readAllStandardOutput())
        self.process.readAllStandardError()
        if not self.active or not self.accept_result or self.timed_out:
            return
        if code != 0 or exit_status != QProcess.NormalExit or len(raw) > 65536:
            self._unavailable('Видеокарта · данные NVIDIA временно недоступны')
            return
        readings = parse_readings(raw.decode('utf-8', errors='replace'))
        if not readings:
            self._unavailable('Видеокарта · драйвер не вернул показания NVIDIA')
            return
        self.readings = readings
        def show(value, suffix):
            return '—' if value is None else f'{value}{suffix}'
        self.values.setText('\n'.join(
            f'GPU {r.index} · {r.name}    Температура: {show(r.temperature, " °C")}    '
            f'GPU: {show(r.gpu, "%")}    Кодирование NVENC: {show(r.encoder, "%")}'
            for r in readings))
        self.status.setText('Видеокарта · обновляется каждые 2 с')
        self._update_heat(readings)

    def _update_heat(self, readings):
        if not self.active:
            return
        newly_hot = []
        available = set()
        for reading in readings:
            key = (reading.index, reading.name)
            if reading.temperature is None:
                continue
            available.add(key)
            if reading.temperature <= COOLED_TEMPERATURE:
                self.hot_cards.pop(key, None)
            elif reading.temperature >= HOT_TEMPERATURE or key in self.hot_cards:
                if key not in self.hot_cards:
                    newly_hot.append(reading)
                self.hot_cards[key] = reading
        # A lost sensor is not evidence that a hot card has cooled down.
        self.missing_temperatures = set(self.hot_cards) - available
        self._render_heat()
        if newly_hot:
            message = 'Высокая температура GPU: ' + '; '.join(
                f'GPU {r.index} · {r.name}: {r.temperature} °C' for r in newly_hot)
            self.temperature_warning.emit(message)
            QApplication.beep()
            QApplication.alert(self.window(), 5000)

    def _render_heat(self):
        if not self.hot_cards:
            self.heat_warning.hide()
            return
        lines = []
        for key, reading in self.hot_cards.items():
            value = f'GPU {reading.index} · {reading.name}: {reading.temperature} °C'
            if key in self.missing_temperatures:
                value += ' — последнее значение; датчик недоступен'
            lines.append(value)
        if self.active:
            title = 'ВЫСОКАЯ ТЕМПЕРАТУРА · порог 85 °C'
            advice = 'Проверьте охлаждение. При необходимости отмените сборку кнопкой «Отмена». '
            advice += 'Предупреждение снимется при 80 °C и ниже.'
        else:
            title = 'ПРИ СБОРКЕ БЫЛА ВЫСОКАЯ ТЕМПЕРАТУРА'
            advice = 'Опрос остановлен; показаны последние измерения, текущая температура неизвестна.'
        self.heat_warning.setText(title + '\n' + '\n'.join(lines) + '\n' + advice)
        self.heat_warning.show()

    def _unavailable(self, message):
        self.readings = []
        self.status.setText(message)
        self.values.setText('Температура: —    GPU: —    Кодирование NVENC: —')
        self._update_heat([])

    def stop(self):
        was_active = self.active
        self.active = False
        self.accept_result = False
        self.timer.stop()
        self.deadline.stop()
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
        if self.readings:
            self.status.setText('Видеокарта · последние показания, опрос остановлен')
        elif was_active:
            self.status.setText('Видеокарта · опрос остановлен, показаний нет')
        self._render_heat()

    def shutdown(self):
        self.stop()
        # Only on window close: reap the already killed helper before Qt destroys it.
        if self.process.state() != QProcess.NotRunning:
            self.process.waitForFinished(1000)
