"""Per-film audio track selection after probing."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton
from audio_selection import available_tracks, selected_tracks, language


class AudioPanel(QWidget):
    selection_changed = Signal(list)

    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self)
        self.heading = QLabel('Сначала добавьте фильм и дождитесь анализа.')
        self.heading.setWordWrap(True)
        box.addWidget(self.heading)
        note = QLabel('Отметьте дорожки, которые войдут в ISO. По умолчанию выбрана первая.\n'
                      'Без отметок фильм будет без звука. Совместимые AC-3 копируются без потерь; '
                      'остальные выбранные дорожки преобразуются в AC-3 48 кГц (до 5.1).')
        note.setWordWrap(True)
        box.addWidget(note)
        self.tracks = QListWidget()
        self.tracks.itemChanged.connect(self._changed)
        box.addWidget(self.tracks, 1)
        row = QHBoxLayout()
        for title, checked in [('Выбрать все', True), ('Снять все', False)]:
            button = QPushButton(title)
            button.clicked.connect(lambda _=False, state=checked: self._check_all(state))
            row.addWidget(button)
        row.addStretch()
        box.addLayout(row)

    def set_media(self, info):
        self.tracks.blockSignals(True)
        try:
            self.tracks.clear()
            self.heading.setText(f'Аудио · {info.name}' if info else 'Сначала добавьте фильм и дождитесь анализа.')
            chosen = {t.get('index', 0) for t in selected_tracks(info)} if info else set()
            for number, track in enumerate(available_tracks(info) if info else [], 1):
                tags = track.get('tags') or {}
                title = str(tags.get('title', '')).replace('\n', ' ').replace('\r', ' ')
                text = (f"{number:02} · {language(track).upper()} · {track.get('codec_name', '?').upper()} · "
                        f"{track.get('channels', '?')} кан. · {track.get('sample_rate', '?')} Гц")
                if title:
                    text += ' · ' + title
                item = QListWidgetItem(text)
                index = track.get('index', 0)
                item.setData(Qt.UserRole, index)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if index in chosen else Qt.Unchecked)
                self.tracks.addItem(item)
            if info and not available_tracks(info):
                self.heading.setText(f'Аудио · {info.name} · звуковых дорожек нет')
        finally:
            self.tracks.blockSignals(False)

    def _changed(self, *_):
        self.selection_changed.emit([self.tracks.item(i).data(Qt.UserRole)
                                     for i in range(self.tracks.count())
                                     if self.tracks.item(i).checkState() == Qt.Checked])

    def _check_all(self, checked):
        self.tracks.blockSignals(True)
        for i in range(self.tracks.count()):
            self.tracks.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.tracks.blockSignals(False)
        self._changed()
