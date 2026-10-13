"""Clean, aspect-correct fullscreen menu simulation."""
from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QDialog, QPushButton, QHBoxLayout, QVBoxLayout, QWidget


class FullscreenPreview(QDialog):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setWindowTitle('Предварительный просмотр — Esc / F11: выход')
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)
        layout.addStretch()
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.addStretch()
        for label, action in [('Вверх', 'up'), ('Вниз', 'down'), ('OK', 'enter'), ('Назад', 'back'), ('Главное меню', 'home')]:
            button = QPushButton(label)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, key=action: self.navigate(key))
            row.addWidget(button)
        close = QPushButton('Закрыть · Esc / F11')
        close.clicked.connect(self.close)
        close.setFocusPolicy(Qt.NoFocus)
        row.addWidget(close)
        row.addStretch()
        layout.addWidget(bar)
        self.bar = bar
        editor.changed.connect(self.update)

    def navigate(self, key):
        self.editor.press(key)
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_F11):
            self.close()
            return
        action = {Qt.Key_Up: 'up', Qt.Key_Left: 'up', Qt.Key_Down: 'down', Qt.Key_Right: 'down',
                  Qt.Key_Return: 'enter', Qt.Key_Enter: 'enter', Qt.Key_Backspace: 'back', Qt.Key_Home: 'home'}.get(event.key())
        if action:
            self.navigate(action)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('black'))
        height = max(1, self.bar.y() - 6)
        size = QSize(1920, 1080).scaled(QSize(self.width(), height), Qt.KeepAspectRatio)
        target = QRect((self.width()-size.width())//2, (height-size.height())//2, size.width(), size.height())
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(target, self.editor.preview.image)
        painter.end()
