"""Keyboard-accessible style swatches drawn with the actual disc renderer."""
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import QPushButton, QSizePolicy
from episode_buttons import STYLE_PRESETS


class ButtonStyleCard(QPushButton):
    def __init__(self, title, variant, parent=None):
        super().__init__(title, parent)
        self.variant = variant
        self.setCheckable(True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(82)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName('Стиль: '+title)
        self.setToolTip(title)

    def sizeHint(self):
        return QSize(132, 82)

    def minimumSizeHint(self):
        return QSize(80, 82)

    def paintEvent(self, event):
        from disc_menu import paint_button
        dark = self.palette().window().color().lightness() < 128
        painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else .45)
        bounds = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setBrush(QColor('#24282f' if self.underMouse() and dark else '#1b2027' if dark else '#ffffff'))
        painter.setPen(QPen(QColor('#e79743' if self.isChecked() or self.hasFocus() else '#353d48' if dark else '#dce1e8'), 1.4 if self.isChecked() else 1))
        painter.drawRoundedRect(bounds, 10, 10)
        style = dict(shape='rounded', fill='#141c26', border='#8b95a3', text='#edf1f7',
                     highlight='#f6a34c', width=1, font_size=12, opacity=0,
                     font_family='Noto Sans', bold=False, italic=False, radius=6,
                     padding_x=9, padding_y=0)
        style.update(STYLE_PRESETS[self.variant])
        paint_button(painter, dict(rect=[12, 12, max(30, self.width()-24), 29], style=style, shown='Эпизод'))
        font = QFont(self.font()); font.setPixelSize(11); painter.setFont(font)
        painter.setPen(QColor('#f1b76d' if self.isChecked() else '#b7c0ce' if dark else '#465262'))
        text = QFontMetrics(font).elidedText(self.text(), Qt.ElideRight, self.width()-16)
        painter.drawText(self.rect().adjusted(8, 48, -8, -6), Qt.AlignCenter, text)
        painter.end()
