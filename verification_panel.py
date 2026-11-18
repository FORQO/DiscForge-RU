"""Compact verification status, separate from the overall build percentage."""
from studio_locale import tr
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QProgressBar


def duration(seconds):
    seconds = max(0, round(seconds))
    return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'


class VerificationPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        layout.setSpacing(3)
        self.title = QLabel()
        self.file = QLabel()
        self.stats = QLabel()
        for label in (self.title, self.file, self.stats):
            label.setTextFormat(Qt.PlainText)
            layout.addWidget(label)
        self.file.setMinimumWidth(0)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(14)
        self.bar.setAccessibleName(tr('Прогресс проверки файлов'))
        layout.addWidget(self.bar)
        self.event = None
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.hide()

    def update_progress(self, event):
        if event is None:
            self.timer.stop()
            self.event = None
            self.hide()
            return
        self.event = dict(event)
        self.updated = time.monotonic()
        self.show()
        self.timer.start()
        self.refresh()

    def refresh(self):
        if not self.event:
            return
        e = self.event
        self.title.setText(e['phase'])
        path = e['file']
        self.file.setToolTip(path)
        self.file.setText(self.file.fontMetrics().elidedText(
            tr('Файл {0}/{1}: {2}', e['index'], e['count'], path) if path else tr('Подготовка доступа к образу…'),
            Qt.ElideMiddle, max(100, self.width())))
        waiting = e['state'] == 'waiting'
        self.bar.setRange(0, 0 if waiting else 1000)
        if not waiting:
            fraction = e['done']/e['total'] if e['total'] else 0
            self.bar.setValue(1000 if e['complete'] else min(999, int(fraction*1000)))
            self.bar.setFormat(f"{100 if e['complete'] else min(99.9, fraction*100):.1f}%")
        stale = time.monotonic()-self.updated > 3
        speed = tr('{0} МБ/с', format(e['speed'] / 1000000.0, f'.1f')) if e['speed'] and not stale and not waiting else tr('скорость уточняется')
        eta = duration(e['remaining']) if e['remaining'] >= 0 and not stale and not waiting else tr('рассчитывается…')
        scope = tr('До конца проверки ISO') if e['scope'] == 'iso-final' else tr('До конца этого этапа')
        self.stats.setText(tr('Прочитано {0} / {1} ГБ · {2} · {3} ≈ {4}', format(e['done'] / 1000000000.0, f'.2f'), format(e['total'] / 1000000000.0, f'.2f'), speed, scope, eta))
        if e['complete']:
            self.stats.setText(tr('Чтение завершено · {0} ГБ', format(e['done'] / 1000000000.0, f'.2f')))
            self.timer.stop()

    def stop(self, success):
        self.timer.stop()
        if self.event and not success:
            self.title.setText(tr('Проверка остановлена'))
            self.stats.setText(tr('Осталось: — · Подробности в журнале работы'))
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
                self.bar.setValue(0)
