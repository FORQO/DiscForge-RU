"""Per-movie soft subtitle selection and external SRT / SUP import."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QFileDialog, QComboBox, QLineEdit, QMessageBox)
from subtitles import embedded_tracks, external_track, SUPPORTED_CODECS, validate_selection


def identity(track):
    return (track['source'], track['index'], track['path'])


class SubtitlePanel(QWidget):
    selection_changed = Signal(list)

    def __init__(self):
        super().__init__()
        self.info = None
        self.external = {}
        box = QVBoxLayout(self)
        self.heading = QLabel()
        box.addWidget(self.heading)
        note = QLabel('Отметьте субтитры из фильма или добавьте файлы SRT / SUP (PGS).\n'
                      'На диске появится меню «Субтитры» с выбором дорожки и пунктом «Выключить». '
                      'Изначально субтитры выключены. Текст преобразуется в PGS без изменения видео. '
                      'Оформление ASS/SSA упрощается; VobSub не поддерживается.')
        note.setWordWrap(True)
        box.addWidget(note)
        self.tracks = QListWidget()
        self.tracks.itemChanged.connect(self._changed)
        self.tracks.currentItemChanged.connect(self._selected)
        box.addWidget(self.tracks, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton('+ Добавить SRT / SUP…')
        self.add_button.clicked.connect(self._add)
        self.remove_button = QPushButton('Удалить внешний файл')
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        box.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel('Язык:'))
        self.language = QComboBox()
        for name, code in [('Русский', 'rus'), ('English', 'eng'), ('Українська', 'ukr'),
                           ('Deutsch', 'deu'), ('Français', 'fra'), ('Español', 'spa'),
                           ('Italiano', 'ita'), ('日本語', 'jpn'), ('Не указан', 'und')]:
            self.language.addItem(name, code)
        self.language.currentIndexChanged.connect(self._metadata)
        row.addWidget(self.language)
        self.title = QLineEdit()
        self.title.setMaxLength(80)
        self.title.setPlaceholderText('Название дорожки в меню диска')
        self.title.editingFinished.connect(self._metadata)
        row.addWidget(self.title, 1)
        box.addLayout(row)
        self.set_media(None)

    def set_media(self, info):
        self.info = info
        self.tracks.blockSignals(True)
        self.tracks.clear()
        self.heading.setText('Субтитры · '+info.name if info else 'Сначала добавьте и проанализируйте фильм.')
        self.add_button.setEnabled(info is not None)
        if info:
            selected = {identity(t): t for t in info.selected_subtitles}
            catalog = self.external.setdefault(info.path, {})
            catalog.update({identity(t): t for t in info.selected_subtitles if t['source'] == 'external'})
            for t in embedded_tracks(info) + list(catalog.values()):
                t = selected.get(identity(t), t)
                self._append(t, identity(t) in selected)
        self.tracks.blockSignals(False)
        self._selected()

    def _append(self, track, checked=True):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, dict(track))
        supported = track['codec'] in SUPPORTED_CODECS
        if supported:
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        else:
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        self._label(item)
        self.tracks.addItem(item)
        return item

    def _label(self, item):
        t = item.data(Qt.UserRole)
        source = 'Файл' if t['source'] == 'external' else 'Поток '+str(t['index'])
        item.setText(f"{source} · {t['language'].upper()} · {t['codec'].upper()} · {t['title']}" +
                     (' · не поддерживается' if t['codec'] not in SUPPORTED_CODECS else ''))
        item.setToolTip(t['path'])

    def _selected(self, *_):
        item = self.tracks.currentItem()
        valid = item is not None and bool(item.flags() & Qt.ItemIsEnabled)
        self.language.setEnabled(valid)
        self.title.setEnabled(valid)
        self.remove_button.setEnabled(valid and item.data(Qt.UserRole)['source'] == 'external')
        self.language.blockSignals(True)
        if valid:
            t = item.data(Qt.UserRole)
            index = self.language.findData(t['language'])
            if index < 0:
                self.language.addItem(t['language'].upper(), t['language'])
                index = self.language.count()-1
            self.language.setCurrentIndex(index)
            self.title.setText(t['title'])
        else:
            self.title.clear()
        self.language.blockSignals(False)

    def _metadata(self, *_):
        item = self.tracks.currentItem()
        if not item or not self.title.isEnabled():
            return
        t = dict(item.data(Qt.UserRole))
        t.update(language=self.language.currentData(), title=' '.join(self.title.text().split())[:80])
        self.tracks.blockSignals(True)
        item.setData(Qt.UserRole, t)
        self._label(item)
        self.tracks.blockSignals(False)
        if self.info and t['source'] == 'external':
            self.external[self.info.path][identity(t)] = t
        self._changed()

    def _changed(self, *_):
        selection = [self.tracks.item(i).data(Qt.UserRole) for i in range(self.tracks.count())
                     if self.tracks.item(i).checkState() == Qt.Checked]
        try:
            validate_selection(selection)
        except ValueError as exc:
            QMessageBox.warning(self, 'Субтитры', str(exc))
            self.set_media(self.info)
            return
        self.selection_changed.emit(selection)

    def _add(self):
        if not self.info:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, 'Добавить субтитры', '', 'Субтитры (*.srt *.sup)')
        for path in paths:
            try:
                t = external_track(path)
                if identity(t) in self.external[self.info.path]:
                    continue
                self.external[self.info.path][identity(t)] = t
                self.tracks.blockSignals(True)
                item = self._append(t)
                self.tracks.blockSignals(False)
                self.tracks.setCurrentItem(item)
            except ValueError as exc:
                QMessageBox.warning(self, 'Субтитры', str(exc))
        self._changed()

    def _remove(self):
        item = self.tracks.currentItem()
        if item and item.data(Qt.UserRole)['source'] == 'external':
            self.external[self.info.path].pop(identity(item.data(Qt.UserRole)), None)
            self.tracks.takeItem(self.tracks.row(item))
            self._changed()
