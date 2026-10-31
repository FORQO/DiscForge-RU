"""Studio shell styling and the export sheet; authoring remains in core.py."""
from pathlib import Path
from PySide6.QtCore import Qt, QSize, QRect
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
                              QLineEdit, QPushButton, QComboBox, QFileDialog, QMessageBox,
                              QStyledItemDelegate, QStyle)


def studio_style(theme='graphite'):
    dark = theme == 'blue'
    bg, panel, side = ('#1b1c20', '#232429', '#202126') if dark else ('#f5f6f8', '#ffffff', '#eceef2')
    fg, muted, line = ('#f0f1f4', '#a5a8b2', '#383a43') if dark else ('#222631', '#666d7a', '#dce0e7')
    selected = '#3a3028' if dark else '#faeadd'
    hover = '#30323a' if dark else '#e9ecf2'
    check_icon = (Path(__file__).parent / 'assets/icons/check.svg').as_posix()
    chevron_icon = (Path(__file__).parent / 'assets/icons/chevron.svg').as_posix()
    return f'''
QWidget {{ background: {bg}; color: {fg}; font-size: 13px; }}
QMainWindow, QDialog {{ background: {bg}; }}
QLabel {{ background: transparent; }}
QWidget#studioHeader, QWidget#studioFooter {{ background: {panel}; }}
QWidget#studioHeader {{ border-bottom: 1px solid {line}; }}
QWidget#studioFooter {{ border-top: 1px solid {line}; }}
QWidget#studioLibrary {{ background: {side}; border-right: 1px solid {line}; }}
QLabel#studioBrand {{ font-size: 17px; font-weight: 600; }}
QLabel#studioHeading {{ font-size: 26px; font-weight: 600; }}
QLabel#studioSection {{ color: {muted}; font-size: 11px; font-weight: 600; }}
QLabel#hint, QLabel#tag, QLabel#details, QLabel#studioStatus {{ color: {muted}; font-size: 12px; }}
QLabel#details {{ padding: 6px 0; }}
QLabel#hint {{ padding: 4px 0; }}
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QFontComboBox {{
 background: {panel}; border: 1px solid {line}; border-radius: 6px; padding: 6px 8px;
 selection-background-color: #d8792b; selection-color: #ffffff; min-height: 18px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: #d8792b; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {muted}; background: {side}; }}
QComboBox {{ padding-right: 22px; }}
QComboBox::down-arrow {{ image: url("{chevron_icon}"); width: 12px; height: 12px; }}
QComboBox::drop-down {{ width: 20px; border: none; }}
QComboBox QAbstractItemView {{ background: {panel}; selection-background-color: {selected}; selection-color: {fg}; }}
QListWidget, QTableWidget {{ background: {panel}; border: 1px solid {line}; border-radius: 7px; gridline-color: {line}; }}
QListWidget#studioMovies {{ background: {side}; border: 0; outline: 0; }}
QListWidget::item {{ padding: 9px 8px; margin: 2px 0; border-radius: 6px; }}
QListWidget::item:selected {{ background: {selected}; color: {fg}; }}
QListWidget::item:hover {{ background: {hover}; }}
QGroupBox {{ border: 0; border-top: 1px solid {line}; margin-top: 15px; padding: 20px 4px 8px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 4px; padding: 0 6px 0 0; }}
QPushButton, QToolButton {{ background: {panel}; border: 1px solid {line}; padding: 6px 10px; border-radius: 6px; min-height: 18px; }}
QPushButton:hover, QToolButton:hover {{ background: {hover}; }}
QPushButton:pressed, QPushButton:checked, QToolButton:checked {{ background: {selected}; border-color: #d8792b; }}
QPushButton:disabled {{ background: {side}; color: {muted}; border-color: {line}; }}
QPushButton#primary {{ background: #cf7024; border-color: #cf7024; color: #ffffff; font-weight: 600; padding: 8px 18px; }}
QPushButton#primary:hover {{ background: #bb6019; }}
QPushButton#primary:disabled {{ background: {side}; border-color: {line}; color: {muted}; }}
QPushButton#ghost {{ border-color: transparent; background: transparent; }}
QPushButton#ghost:hover {{ background: {hover}; }}
QTabWidget::pane {{ border: 0; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {muted}; border: 0; border-bottom: 2px solid transparent; padding: 10px 12px; margin-right: 3px; }}
QTabBar::tab:selected {{ color: {fg}; border-bottom: 2px solid #d8792b; }}
QTabBar::tab:hover {{ background: {hover}; }}
QTabWidget#inspectorTabs QTabBar::tab {{ padding: 8px 6px; font-size: 11px; margin: 0; }}
QScrollArea {{ border: 0; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {line}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QSplitter::handle {{ background: {line}; }}
QProgressBar {{ background: {side}; border: 0; border-radius: 3px; text-align: center; font-size: 11px; }}
QProgressBar::chunk {{ background: #d8792b; border-radius: 3px; }}
QProgressBar#capacityMeter {{ background: {line}; max-height: 5px; min-height: 5px; }}
QHeaderView::section {{ background: {side}; color: {muted}; border: 0; padding: 6px; font-size: 11px; }}
QCheckBox {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {line}; border-radius: 4px; background: {panel}; }}
QCheckBox::indicator:checked {{ background: #d8792b; border-color: #d8792b; image: url("{check_icon}"); }}
QMenu {{ background: {panel}; border: 1px solid {line}; padding: 5px; }}
QMenu::item {{ padding: 6px 22px; }}
QMenu::item:selected {{ background: {selected}; }}
QStatusBar {{ background: {bg}; color: {muted}; font-size: 11px; }}
QToolTip {{ background: {panel}; color: {fg}; border: 1px solid {line}; padding: 6px; }}
'''


class ExportDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle('Создание Blu-ray')
        self.setMinimumWidth(500)
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(16)
        title = QLabel('Создание Blu-ray'); title.setObjectName('studioHeading')
        layout.addWidget(title)
        description = QLabel(f'Фильмов: {len(window.media)} · ' +
                             ('Общий диск' if window.disc_mode.currentIndex() == 0 else 'Отдельный диск для каждого фильма'))
        description.setObjectName('hint'); layout.addWidget(description)
        form = QFormLayout(); form.setSpacing(12)
        self.profile = QComboBox(); self.profile.addItems(['BD25', 'BD50']); self.profile.setCurrentText(window.profile.currentText())
        self.mode = QComboBox(); self.mode.addItems(['ISO-образ', 'Папка BDMV']); self.mode.setCurrentIndex(window.output_mode.currentIndex())
        form.addRow('Носитель', self.profile); form.addRow('Результат', self.mode)
        self.folder = QLineEdit(window.output_folder.text()); self.folder.setAccessibleName('Папка результата')
        row = QHBoxLayout(); row.addWidget(self.folder, 1)
        choose = QPushButton('Выбрать…'); choose.clicked.connect(self.choose_folder); row.addWidget(choose)
        form.addRow('Сохранить в', row); layout.addLayout(form)
        notice = QLabel('Готовые этапы сохраняются для возобновления. Исходные видео остаются на месте.'); notice.setWordWrap(True); notice.setObjectName('hint'); layout.addWidget(notice)
        buttons = QHBoxLayout(); buttons.addStretch()
        cancel = QPushButton('Отмена'); cancel.clicked.connect(self.reject); buttons.addWidget(cancel)
        self.create = QPushButton('Начать сборку'); self.create.setObjectName('primary'); self.create.clicked.connect(self.accept); self.create.setDefault(True); buttons.addWidget(self.create)
        layout.addLayout(buttons)

    def choose_folder(self):
        selected = QFileDialog.getExistingDirectory(self, 'Папка результата', self.folder.text())
        if selected:
            self.folder.setText(selected)

    def accept(self):
        if not self.folder.text().strip():
            QMessageBox.warning(self, 'Папка результата', 'Выберите папку для готового диска.')
            return
        super().accept()



class MovieDelegate(QStyledItemDelegate):
    """Two-line library rows with stable source identity and native selection."""
    def __init__(self, window):
        super().__init__(window.movies)
        self.window = window

    def sizeHint(self, option, index):
        return QSize(180, 62)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        dark = self.window.ui_theme == 'blue'
        rect = option.rect.adjusted(0, 2, -2, -2)
        selected = bool(option.state & QStyle.State_Selected)
        if selected or option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor('#3a3028' if dark else '#faeadd') if selected else QColor('#30323a' if dark else '#e1e5ec'))
            painter.drawRoundedRect(rect, 7, 7)
        badge = QRect(rect.x()+8, rect.y()+9, 30, 38)
        painter.setPen(Qt.NoPen); painter.setBrush(QColor('#3a3d45' if dark else '#d6dce5')); painter.drawRoundedRect(badge, 5, 5)
        painter.setPen(QColor('#e1e4e9' if dark else '#596574'))
        painter.drawText(badge, Qt.AlignCenter, f'{index.row()+1:02d}')
        title = str(index.data() or '').lstrip('✅⏳❌ ')
        text_rect = QRect(rect.x()+47, rect.y()+8, max(20, rect.width()-53), 22)
        painter.setPen(QColor('#f0f1f4' if dark else '#222631'))
        painter.drawText(text_rect, Qt.AlignVCenter, QFontMetrics(option.font).elidedText(title, Qt.ElideRight, text_rect.width()))
        info = self.window.media.get(index.data(Qt.UserRole))
        if info:
            duration = int(info.duration)
            detail = f'{duration//3600}:{duration//60%60:02d}:{duration%60:02d} · {info.height}p'
        else:
            detail = 'Не удалось прочитать' if str(index.data()).startswith('❌') else 'Анализ файла…'
        font = option.font; font.setPointSizeF(max(9, font.pointSizeF()-1)); painter.setFont(font)
        painter.setPen(QColor('#a5a8b2' if dark else '#666d7a'))
        painter.drawText(text_rect.translated(0, 22), Qt.AlignVCenter, QFontMetrics(font).elidedText(detail, Qt.ElideRight, text_rect.width()))
        painter.restore()
