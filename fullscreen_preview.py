"""Clean, aspect-correct fullscreen menu simulation."""
from PySide6.QtCore import Qt, QRect, QSize, QMargins
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QDialog, QPushButton, QGridLayout, QVBoxLayout, QWidget, QLayout


class FullscreenPreview(QDialog):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setWindowTitle('Предварительный просмотр — Esc / F11: выход')
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.StrongFocus)
        layout = QVBoxLayout(self)
        # A fullscreen window must fit the screen even when the controls need to wrap.
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        layout.addStretch()
        bar = QWidget()
        row = QGridLayout(bar)
        row.setAlignment(Qt.AlignCenter)
        self._buttons = []
        for label, action in [('Вверх', 'up'), ('Вниз', 'down'), ('Влево', 'left'), ('Вправо', 'right'),
                              ('OK', 'enter'), ('Назад', 'back'), ('Главное меню', 'home')]:
            button = QPushButton(label)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda checked=False, key=action: self.navigate(key))
            row.addWidget(button, 0, len(self._buttons))
            self._buttons.append(button)
        close = QPushButton('Закрыть · Esc / F11')
        close.clicked.connect(self.close)
        close.setFocusPolicy(Qt.NoFocus)
        row.addWidget(close, 0, len(self._buttons))
        self._buttons.append(close)
        self._columns = len(self._buttons)
        layout.addWidget(bar)
        self.bar = bar
        self._base_margins = layout.contentsMargins()
        self._screen = None
        self._tracking_screen = False
        editor.changed.connect(self.update)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._tracking_screen and self.windowHandle():
            self.windowHandle().screenChanged.connect(self._screen_changed)
            self._tracking_screen = True
        self._screen_changed(self.screen())

    def _screen_changed(self, screen):
        if screen is not self._screen:
            if self._screen is not None:
                try:
                    self._screen.availableGeometryChanged.disconnect(self._update_safe_area)
                    self._screen.geometryChanged.disconnect(self._update_safe_area)
                except RuntimeError:  # The previous monitor may have been disconnected.
                    pass
            self._screen = screen
            if screen is not None:
                screen.availableGeometryChanged.connect(self._update_safe_area)
                screen.geometryChanged.connect(self._update_safe_area)
        self._update_safe_area()

    def _available_rect(self):
        screen = self.screen()
        if screen is None:
            return self.rect()
        available = screen.availableGeometry()
        return self.rect().intersected(QRect(self.mapFromGlobal(available.topLeft()), available.size()))

    def _update_safe_area(self, *_):
        if not self.isVisible():
            return
        safe = self._available_rect()
        if safe.isEmpty():
            return
        base = self._base_margins
        margins = QMargins(safe.left() + base.left(), safe.top() + base.top(),
                           self.width() - safe.right() - 1 + base.right(),
                           self.height() - safe.bottom() - 1 + base.bottom())
        changed = self.layout().contentsMargins() != margins
        if changed:
            self.layout().setContentsMargins(margins)
        row = self.bar.layout()
        padding = row.contentsMargins()
        available_width = safe.width() - base.left() - base.right() - padding.left() - padding.right()
        for columns in (8, 4, 2, 1):
            widths = [0] * columns
            for index, button in enumerate(self._buttons):
                widths[index % columns] = max(widths[index % columns], button.minimumSizeHint().width())
            if sum(widths) + row.horizontalSpacing() * (columns - 1) <= available_width:
                break
        if columns != self._columns:
            for button in self._buttons:
                row.removeWidget(button)
            for index, button in enumerate(self._buttons):
                row.addWidget(button, index // columns, index % columns)
            self._columns = columns
            changed = True
        if changed:
            self.layout().activate()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_safe_area()

    def image_rect(self):
        safe = self._available_rect()
        height = max(1, self.bar.y() - 6 - safe.top())
        size = QSize(1920, 1080).scaled(QSize(safe.width(), height), Qt.KeepAspectRatio)
        return QRect(safe.x() + (safe.width() - size.width()) // 2,
                     safe.y() + (height - size.height()) // 2, size.width(), size.height())

    def navigate(self, key):
        self.editor.press(key)
        self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_F11):
            self.close()
            return
        action = {Qt.Key_Up: 'up', Qt.Key_Left: 'left', Qt.Key_Down: 'down', Qt.Key_Right: 'right',
                  Qt.Key_Return: 'enter', Qt.Key_Enter: 'enter', Qt.Key_Backspace: 'back', Qt.Key_Home: 'home'}.get(event.key())
        if action:
            self.navigate(action)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('black'))
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(self.image_rect(), self.editor.preview.image)
        painter.end()
