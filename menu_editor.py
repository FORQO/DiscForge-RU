"""Live menu preview, using the same page images as the on-disc Xlet."""
from PySide6.QtCore import Qt, Signal, QSize, QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QCheckBox, QLineEdit
from disc_menu import MenuDesign, render_page, pages


class MenuPreview(QWidget):
    def __init__(self):
        super().__init__()
        self.image = None
        self.setMinimumSize(420, 236)

    def paintEvent(self, event):
        if self.image is None:
            return
        size = self.image.size().scaled(self.size(), Qt.KeepAspectRatio)
        target = QRect((self.width()-size.width())//2, (self.height()-size.height())//2, size.width(), size.height())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(target, self.image)
        painter.end()


class MenuEditor(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.chapters = [0.0]
        self.page = self.focus = 0
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self.enabled = QCheckBox('Интерактивное меню на диске · BD-J (экспериментально)')
        self.enabled.setChecked(True)
        layout.addWidget(self.enabled)
        fields = QHBoxLayout()
        self.title = QLineEdit('Мой Blu-ray')
        self.title.setMaxLength(80)
        self.title.setPlaceholderText('Название на обложке меню')
        fields.addWidget(self.title, 1)
        self.theme = QComboBox()
        self.theme.addItem('Полночь', 'midnight')
        self.theme.addItem('Кинотеатр', 'cinema')
        fields.addWidget(self.theme)
        layout.addLayout(fields)
        self.preview = MenuPreview()
        self.preview.setObjectName('preview')
        layout.addWidget(self.preview, 1)
        remote = QHBoxLayout()
        remote.addStretch()
        for caption, key in [('↑', 'up'), ('↓', 'down'), ('OK', 'enter'), ('Главное меню', 'home')]:
            button = QPushButton(caption)
            button.clicked.connect(lambda _=False, k=key: self.press(k))
            remote.addWidget(button)
        remote.addStretch()
        layout.addLayout(remote)
        self.caption = QLabel('Проверьте кнопки пультом под предпросмотром.')
        self.caption.setWordWrap(True)
        self.caption.setObjectName('hint')
        layout.addWidget(self.caption)
        note = QLabel('На диске: просмотр фильма, выбор главы, возврат через Pop-up Menu / Back. '
                      'Предпросмотр показывает оформление и переходы; совместимость проверяется отдельно на плеере.')
        note.setWordWrap(True)
        note.setObjectName('hint')
        layout.addWidget(note)
        self.title.textChanged.connect(self.refresh)
        self.theme.currentIndexChanged.connect(self.refresh)
        self.enabled.toggled.connect(self.changed)
        self.refresh()

    def design(self):
        return MenuDesign(self.title.text().strip(), self.theme.currentData()) if self.enabled.isChecked() else None

    def set_chapters(self, chapters):
        self.chapters = list(chapters)[:999] or [0.0]
        self.page = self.focus = 0
        self.refresh()

    def refresh(self, *_):
        design = MenuDesign(self.title.text().strip() or 'Мой Blu-ray', self.theme.currentData())
        image = render_page(design, self.chapters, self.page, self.focus)
        self.preview.image = image
        self.preview.update()
        self.changed.emit()

    def press(self, key):
        entries = pages(self.chapters)[self.page]
        if key == 'home':
            self.page = self.focus = 0
        elif key in ('up', 'down'):
            self.focus = (self.focus + (1 if key == 'down' else -1)) % len(entries)
        else:
            action = entries[self.focus][1]
            kind, value = action.split(':')
            if kind == 'page':
                self.page, self.focus = int(value), 0
            else:
                self.caption.setText(f'На плеере начнётся воспроизведение главы {int(value) + 1}.')
        self.refresh()
