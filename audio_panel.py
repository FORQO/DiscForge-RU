"""Per-film audio track selection after probing."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QComboBox
from audio_selection import available_tracks, selected_tracks, language


class AudioPanel(QWidget):
    selection_changed = Signal(list)
    policy_changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self.info = None
        box = QVBoxLayout(self)
        self.heading = QLabel('Сначала добавьте фильм и дождитесь анализа.')
        self.heading.setWordWrap(True)
        box.addWidget(self.heading)
        note = QLabel('Отметьте дорожки, которые войдут в ISO. По умолчанию выбрана первая.\n'
                      'В меню диска появится раздел «Аудио» для выбора языка или озвучки пультом.\n'
                      'Без отметок фильм будет без звука. Проверенные AC-3, DTS core и LPCM сохраняются без потерь. '
                      'TrueHD/Atmos 48–96 кГц копируются без потерь с отдельным совместимым слоем AC-3. '
                      'Если проверка TrueHD не пройдена, сборка остановится: качество не снижается автоматически.\n'
                      'Выберите строку ниже, чтобы настроить обработку этой дорожки.')
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
        self.policy = QComboBox()
        self.policy.addItems(['Автоматически: сохранить, если проверка пройдена', 'Только копирование — остановиться при несовместимости', 'Преобразовать в AC-3'])
        box.addWidget(self.policy)
        self.tracks.currentItemChanged.connect(self._current)
        self.policy.currentIndexChanged.connect(self._policy)
        self.policy.setEnabled(False)

    def _current(self, *_):
        item = self.tracks.currentItem()
        self.policy.blockSignals(True)
        self.policy.setEnabled(item is not None)
        mode = self.info.audio_modes.get(str(item.data(Qt.UserRole)), 'auto') if item and self.info else 'auto'
        self.policy.setCurrentIndex(('auto', 'copy', 'ac3').index(mode))
        self.policy.blockSignals(False)

    def _policy(self, index):
        item = self.tracks.currentItem()
        if item and self.info:
            modes = dict(self.info.audio_modes)
            modes[str(item.data(Qt.UserRole))] = ('auto', 'copy', 'ac3')[index]
            from dataclasses import replace
            self.info = replace(self.info, audio_modes=modes)
            self.policy_changed.emit(modes)

    def set_media(self, info):
        self.info = info
        self.tracks.blockSignals(True)
        try:
            self.tracks.clear()
            self.heading.setText(f'Аудио · {info.name}' if info else 'Сначала добавьте фильм и дождитесь анализа.')
            chosen = {t.get('index', 0) for t in selected_tracks(info)} if info else set()
            for number, track in enumerate(available_tracks(info) if info else [], 1):
                tags = track.get('tags') or {}
                title = str(tags.get('title', '')).replace('\n', ' ').replace('\r', ' ')
                from truehd_audio import label
                text = (f"{number:02} · {language(track).upper()} · {label(track)} · "
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
        self._current()

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
