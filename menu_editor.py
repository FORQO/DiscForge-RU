"""Live menu preview, using the same page images as the on-disc Xlet."""
from PySide6.QtCore import Qt, Signal, QSize, QRect, QEvent
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QLineEdit, QFileDialog, QMessageBox, QSlider, QColorDialog, QSpinBox, QLayout, QFontComboBox)
from disc_menu import (MenuDesign, render_page, pages, import_background, TEXT, BUTTONS,
                       button_id, button_label_key, button_style, page_elements, resolve_action)
from menu_layout import text_style
import copy
import uuid


from menu_preview import MenuPreview


class MenuEditor(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.chapters = [0.0]
        self.page = self.focus = 0
        self.background = ''
        self.labels = {}
        self.hidden_buttons = []
        self.hidden_text = []
        self.button_styles = {}
        self.element_layout = {}
        self.text_styles = {}
        self.custom_buttons = []
        self.history = []
        self.deleted_history = []
        self.selected_button = ''
        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        layout.setSpacing(6)
        self.enabled = QCheckBox('Интерактивное меню на диске · BD-J')
        self.enabled.setChecked(True)
        layout.addWidget(self.enabled)
        fields = QHBoxLayout()
        self.title = QLineEdit('Мой Blu-ray')
        self.title.setMaxLength(80)
        self.title.setPlaceholderText('Название на обложке меню')
        fields.addWidget(self.title, 1)
        self.theme = QComboBox()
        self.theme.addItem('Полночь', 'midnight')
        self.theme.addItem('Графит', 'graphite')
        self.theme.addItem('Кинотеатр · прежняя тема', 'cinema')
        fields.addWidget(self.theme)
        self.language = QComboBox()
        self.language.addItem('Русский', 'ru')
        self.language.addItem('English', 'en')
        fields.addWidget(self.language)
        layout.addLayout(fields)
        background_row = QHBoxLayout()
        choose = QPushButton('Выбрать фон…')
        choose.clicked.connect(self.choose_background)
        background_row.addWidget(choose)
        clear = QPushButton('Убрать фон')
        clear.clicked.connect(self.clear_background)
        background_row.addWidget(clear)
        self.fit = QComboBox()
        self.fit.addItem('Заполнить экран', 'cover')
        self.fit.addItem('Изображение целиком', 'contain')
        background_row.addWidget(self.fit)
        self.dim = QSlider(Qt.Horizontal)
        self.dim.setRange(0, 80)
        self.dim.setValue(40)
        self.dim.setMaximumWidth(140)
        self.dim.setAccessibleName('Затемнение фона')
        self.dim_label = QLabel('Затемнение: 40%')
        background_row.addWidget(self.dim_label)
        background_row.addWidget(self.dim)
        background_row.addStretch()
        layout.addLayout(background_row)
        edit_row = QHBoxLayout()
        self.label_key = QComboBox()
        self.label_key.setMinimumWidth(160)
        self.label_value = QLineEdit()
        self.label_value.setMaxLength(120)
        self.label_value.setPlaceholderText('Своя подпись; пусто — стандартная')
        edit_row.addWidget(QLabel('Текст / кнопка:'))
        edit_row.addWidget(self.label_key)
        edit_row.addWidget(self.label_value, 1)
        reset = QPushButton('Сбросить подпись')
        reset.clicked.connect(self.label_value.clear)
        edit_row.addWidget(reset)
        layout.addLayout(edit_row)
        self.style_panel = QWidget()
        style_layout = QVBoxLayout(self.style_panel)
        style_layout.setContentsMargins(0, 0, 0, 0)
        shape_row = QHBoxLayout()
        self.shape = QComboBox()
        for text, value in [('Скруглённая', 'rounded'), ('Прямоугольная', 'rectangle'), ('Капсула', 'pill')]:
            self.shape.addItem(text, value)
        shape_row.addWidget(QLabel('Форма:'))
        shape_row.addWidget(self.shape)
        self.border_width = QSpinBox()
        self.border_width.setRange(0, 10)
        self.font_size = QSpinBox()
        self.font_size.setRange(12, 160)
        self.opacity = QSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix('%')
        for label, widget in [('Обводка:', self.border_width), ('Шрифт:', self.font_size), ('Заливка:', self.opacity)]:
            shape_row.addWidget(QLabel(label))
            shape_row.addWidget(widget)
        shape_row.addStretch()
        style_layout.addLayout(shape_row)
        colors = QHBoxLayout()
        self.color_buttons = {}
        for name, label in [('fill', 'Цвет фона'), ('border', 'Обводка'), ('text', 'Текст'), ('highlight', 'Выделение')]:
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, k=name: self.choose_color(k))
            self.color_buttons[name] = button
            colors.addWidget(button)
        reset_style = QPushButton('Сбросить стиль')
        reset_style.clicked.connect(self.reset_style)
        colors.addWidget(reset_style)
        style_layout.addLayout(colors)
        font_row = QHBoxLayout()
        self.font_family = QFontComboBox()
        self.font_family.setMaximumWidth(230)
        self.bold = QCheckBox('Жирный')
        self.italic = QCheckBox('Курсив')
        font_row.addWidget(QLabel('Гарнитура:'))
        font_row.addWidget(self.font_family)
        font_row.addWidget(self.bold)
        font_row.addWidget(self.italic)
        self.action_selector = QComboBox()
        self.action_selector.setMinimumWidth(175)
        font_row.addWidget(QLabel('Действие:'))
        font_row.addWidget(self.action_selector, 1)
        style_layout.addLayout(font_row)
        layout.addWidget(self.style_panel)
        self.style_panel.setEnabled(False)
        self.shape.currentIndexChanged.connect(self.style_changed)
        self.border_width.valueChanged.connect(self.style_changed)
        self.font_size.valueChanged.connect(self.style_changed)
        self.opacity.valueChanged.connect(self.style_changed)
        self.font_family.currentFontChanged.connect(self.style_changed)
        self.bold.toggled.connect(self.style_changed)
        self.italic.toggled.connect(self.style_changed)
        self.action_selector.currentIndexChanged.connect(self.change_action)
        tools_row = QHBoxLayout()
        add = QPushButton('+ Добавить кнопку')
        add.clicked.connect(self.add_button)
        tools_row.addWidget(add)
        self.alignment = QComboBox()
        self.alignment.addItems(['По левому краю', 'По центру меню', 'По правому краю', 'Равные интервалы по вертикали'])
        tools_row.addWidget(self.alignment)
        align = QPushButton('Выровнять кнопки')
        align.clicked.connect(self.align_buttons)
        tools_row.addWidget(align)
        self.magnet = QCheckBox('Прилипание')
        self.magnet.setChecked(True)
        tools_row.addWidget(self.magnet)
        layout.addLayout(tools_row)
        self.preview = MenuPreview()
        self.preview.setObjectName('preview')
        layout.addWidget(self.preview, 1)
        self.preview.selected.connect(self.preview_selection)
        self.preview.text_committed.connect(self.inline_text)
        self.preview.delete_requested.connect(self.delete_button)
        self.preview.geometry_changed.connect(self.change_geometry)
        self.magnet.toggled.connect(lambda enabled: setattr(self.preview, 'snapping', enabled))
        remote = QHBoxLayout()
        remote.addStretch()
        self.back_button = QPushButton('Назад')
        self.back_button.clicked.connect(lambda: self.press('back'))
        remote.addWidget(self.back_button)
        for caption, key in [('Вверх', 'up'), ('Вниз', 'down'), ('OK', 'enter'), ('Главное меню', 'home')]:
            button = QPushButton(caption)
            button.clicked.connect(lambda _=False, k=key: self.press(k))
            remote.addWidget(button)
        restore = QPushButton('Вернуть удалённый элемент')
        restore.clicked.connect(self.restore_button)
        remote.addWidget(restore)
        self.fullscreen_button = QPushButton('На весь экран · F11')
        self.fullscreen_button.clicked.connect(self.show_fullscreen)
        remote.addWidget(self.fullscreen_button)
        from PySide6.QtGui import QShortcut, QKeySequence
        self.fullscreen_shortcut = QShortcut(QKeySequence('F11'), self)
        self.fullscreen_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.fullscreen_shortcut.activated.connect(self.show_fullscreen)
        remote.addStretch()
        layout.addLayout(remote)
        self.caption = QLabel('Клик — выбрать; перетаскивание — переместить; двойной клик — изменить текст. Del — удалить элемент. Маркеры меняют размер, Alt отключает прилипание.')
        self.caption.setWordWrap(True)
        self.caption.setObjectName('hint')
        layout.addWidget(self.caption)
        self.title.textChanged.connect(self.refresh)
        self.theme.currentIndexChanged.connect(self.refresh)
        self.language.currentIndexChanged.connect(self.language_changed)
        self.fit.currentIndexChanged.connect(self.refresh)
        self.dim.valueChanged.connect(self.refresh)
        self.label_key.currentIndexChanged.connect(self.sync_label)
        self.label_value.textEdited.connect(self.edit_label)
        reset.clicked.connect(self.edit_label)
        self.enabled.toggled.connect(self.changed)
        self.populate_labels()
        self.refresh()

    def show_fullscreen(self):
        from fullscreen_preview import FullscreenPreview
        self.preview.commit_edit()
        self.fullscreen = FullscreenPreview(self)
        self.fullscreen.showFullScreen()
        self.fullscreen.setFocus()

    def design(self):
        self.preview.commit_edit()
        return self.current_design() if self.enabled.isChecked() else None

    def current_design(self, preview=False):
        return MenuDesign(self.title.text().strip() or ('Blu-ray' if preview else ''),
                          self.theme.currentData(), self.language.currentData(), self.background,
                          self.fit.currentData(), self.dim.value(), self.labels,
                          self.hidden_buttons, self.button_styles, self.element_layout, self.text_styles, self.custom_buttons, self.hidden_text)

    def settings(self):
        self.preview.commit_edit()
        data = self.current_design().to_dict()
        data.pop('title')
        return dict(data, enabled=self.enabled.isChecked())

    def apply_design(self, design):
        self.preview.edit_key = None
        self.preview.inline.hide()
        # Block controls until the complete validated design is installed.
        controls = (self.title, self.theme, self.language, self.fit, self.dim)
        for control in controls:
            control.blockSignals(True)
        self.title.setText(design.title)
        self.theme.setCurrentIndex(self.theme.findData(design.theme))
        self.language.setCurrentIndex(self.language.findData(design.language))
        self.fit.setCurrentIndex(self.fit.findData(design.background_fit))
        self.dim.setValue(design.dim)
        self.background, self.labels = design.background, dict(design.labels)
        self.hidden_buttons = list(design.hidden_buttons)
        self.hidden_text = list(design.hidden_text)
        self.button_styles = {k: dict(v) for k, v in design.button_styles.items()}
        self.element_layout = copy.deepcopy(design.layout)
        self.text_styles = copy.deepcopy(design.text_styles)
        self.custom_buttons = copy.deepcopy(design.custom_buttons)
        self.history = []
        self.deleted_history = []
        self.select_button('')
        for control in controls:
            control.blockSignals(False)
        self.page = self.focus = 0
        self.populate_labels()
        self.refresh()

    def choose_background(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Фон меню', '',
                                             'Изображения (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            try:
                self.set_background(path)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, 'Фон меню', str(exc))

    def set_background(self, path):
        self.background = import_background(path)
        self.refresh()

    def clear_background(self):
        self.background = ''
        self.refresh()

    def populate_labels(self):
        selected = self.label_key.currentData()
        self.label_key.blockSignals(True)
        self.label_key.clear()
        for key, name in [('play', 'Смотреть фильм'), ('title', 'Название фильма'), ('chapters', 'Выбор главы'),
                          ('home', 'Главное меню'), ('previous', 'Предыдущие главы'),
                          ('next', 'Следующие главы'), ('subtitle', 'Подзаголовок'),
                          ('brand', 'Верхняя надпись'), ('hint', 'Подсказка внизу')]:
            self.label_key.addItem(name, key)
        for i in range(len(self.chapters)):
            self.label_key.addItem(f'Название главы {i + 1}', f'chapter:{i}')
        for i, button in enumerate(self.custom_buttons):
            self.label_key.addItem(f'Своя кнопка {i+1}', button['id'])
        self.label_key.setCurrentIndex(max(0, self.label_key.findData(selected)))
        self.label_key.blockSignals(False)
        self.sync_label()

    def sync_label(self, *_):
        key = self.label_key.currentData()
        if key is None:
            return
        self.label_value.setMaxLength(80 if key == 'title' else 120)
        if key == 'title':
            self.label_value.setText(self.title.text())
            self.label_value.setPlaceholderText('Название фильма')
            return
        lang = self.language.currentData()
        self.label_value.setText(self.labels.get(lang + ':' + key, ''))
        self.label_value.setPlaceholderText(TEXT[lang].get(key, f'{TEXT[lang]["chapter"]} {int(key.split(":")[-1])+1}' if key.startswith('chapter:') else ''))

    def edit_label(self, *_):
        if self.label_key.currentData() == 'title':
            self.title.setText(self.label_value.text().strip() or 'Мой Blu-ray')
            return
        key = self.language.currentData() + ':' + self.label_key.currentData()
        value = self.label_value.text().strip()
        if value:
            self.labels[key] = value
        else:
            self.labels.pop(key, None)
        self.refresh()

    def language_changed(self, *_):
        self.preview.edit_key = None
        self.preview.inline.hide()
        self.sync_label()
        self.refresh()

    def set_chapters(self, chapters):
        self.preview.commit_edit()
        self.chapters = list(chapters)[:999] or [0.0]
        self.page = self.focus = 0
        self.history = []
        self.select_button('')
        self.populate_labels()
        self.refresh()

    def refresh(self, *_):
        design = self.current_design(preview=True)
        self.dim_label.setText(f'Затемнение: {self.dim.value()}%')
        graph = pages(self.chapters, design)
        self.page = min(self.page, len(graph)-1)
        self.focus = min(self.focus, max(0, len(graph[self.page])-1))
        self.back_button.setEnabled(bool(self.history))
        image = render_page(design, self.chapters, self.page, self.focus)
        self.preview.image = image
        elements = page_elements(design, self.chapters, self.page)
        for element in elements:
            rect = QRect(*element['rect'])
            element['rect'] = rect
            style = element['style']
            font = QFont(style['font_family'])
            font.setPixelSize(style['font_size'])
            font.setBold(style['bold'])
            font.setItalic(style['italic'])
            metrics = QFontMetrics(font)
            inset = min(28, rect.width()//10) if element['button'] else 0
            element['text_rect'] = QRect(rect.x()+inset, rect.y()+(rect.height()-metrics.height())//2,
                              min(metrics.horizontalAdvance(element['shown'])+10, rect.width()-inset), metrics.height()).intersected(rect)
        self.preview.elements = elements
        self.preview.update()
        self.changed.emit()

    def press(self, key):
        self.preview.commit_edit()
        entries = pages(self.chapters, self.current_design(preview=True))[self.page]
        if key == 'back':
            if self.history:
                self.page, self.focus = self.history.pop()
                self.select_button('')
        elif key == 'home':
            if self.page:
                self.history.append((self.page, self.focus))
            self.page = self.focus = 0
            self.select_button('')
        elif key in ('up', 'down'):
            if entries:
                self.focus = (self.focus + (1 if key == 'down' else -1)) % len(entries)
        elif entries:
            action = resolve_action(self.current_design(preview=True), entries[self.focus][1])
            kind, value = action.split(':')
            if kind == 'page':
                if int(value) >= len(pages(self.chapters, self.current_design(preview=True))):
                    self.caption.setText('Страница больше не существует. Выберите другое действие кнопки.')
                    return
                self.history.append((self.page, self.focus))
                self.page, self.focus = int(value), 0
                self.select_button('')
            else:
                self.caption.setText(f'На плеере начнётся воспроизведение главы {int(value) + 1}.')
        self.refresh()

    def inline_text(self, key, value):
        if key == 'title':
            if value:
                self.title.setText(value)
        else:
            name = self.language.currentData() + ':' + key
            if value:
                self.labels[name] = value
            else:
                self.labels.pop(name, None)
            self.label_key.setCurrentIndex(self.label_key.findData(key))
            self.sync_label()
        self.refresh()

    def select_button(self, key):
        self.selected_button = key
        if hasattr(self, 'preview'):
            self.preview.selection = key
        self.style_panel.setEnabled(bool(key))
        if not key:
            return
        is_text = key.startswith('text:')
        style = text_style(self.current_design(preview=True), key[5:]) if is_text else button_style(self.current_design(preview=True), key)
        for widget in (self.shape, self.border_width, self.opacity):
            widget.setEnabled(not is_text)
        for name, button in self.color_buttons.items():
            button.setEnabled(not is_text or name == 'text')
        controls = (self.shape, self.border_width, self.font_size, self.opacity, self.font_family, self.bold, self.italic)
        for widget in controls:
            widget.blockSignals(True)
        self.shape.setCurrentIndex(self.shape.findData(style.get('shape', 'rounded')))
        self.border_width.setValue(style.get('width', 1))
        self.font_size.setValue(style['font_size'])
        self.opacity.setValue(style.get('opacity', 100))
        self.font_family.setCurrentFont(QFont(style['font_family']))
        self.font_family.setEditText(style['font_family'])
        self.bold.setChecked(style['bold'])
        self.italic.setChecked(style['italic'])
        for widget in controls:
            widget.blockSignals(False)
        for name, button in self.color_buttons.items():
            button.setStyleSheet(f'border-bottom: 4px solid {style.get(name, "#54667b")};')
        label_key = ('chapters' if key == 'text:chapter_heading' else key[5:]) if is_text else button_label_key(key)
        self.label_key.setCurrentIndex(self.label_key.findData(label_key))
        self.populate_actions()

    def preview_selection(self, key):
        self.select_button(key)
        entries = pages(self.chapters, self.current_design(preview=True))[self.page]
        for i, (_, action) in enumerate(entries):
            if button_id(self.page, action) == key:
                self.focus = i
        self.refresh()

    def style_changed(self, *_):
        if not self.selected_button:
            return
        is_text = self.selected_button.startswith('text:')
        style = self.text_styles.setdefault(self.selected_button[5:], {}) if is_text else self.button_styles.setdefault(self.selected_button, {})
        style.update(font_size=self.font_size.value(), font_family=self.font_family.currentFont().family(), bold=self.bold.isChecked(), italic=self.italic.isChecked())
        if not is_text:
            style.update(shape=self.shape.currentData(), width=self.border_width.value(), opacity=self.opacity.value())
        self.refresh()

    def choose_color(self, name):
        if not self.selected_button:
            return
        is_text = self.selected_button.startswith('text:')
        style = text_style(self.current_design(preview=True), self.selected_button[5:]) if is_text else button_style(self.current_design(preview=True), self.selected_button)
        color = QColorDialog.getColor(QColor(style[name]), self, 'Цвет элемента')
        if color.isValid():
            target = self.text_styles.setdefault(self.selected_button[5:], {}) if is_text else self.button_styles.setdefault(self.selected_button, {})
            target[name] = color.name()
            self.select_button(self.selected_button)
            self.refresh()

    def reset_style(self):
        if self.selected_button.startswith('text:'):
            self.text_styles.pop(self.selected_button[5:], None)
        else:
            self.button_styles.pop(self.selected_button, None)
        self.select_button(self.selected_button)
        self.refresh()

    def delete_button(self):
        if self.selected_button.startswith('text:'):
            key = self.selected_button[5:]
            if key not in self.hidden_text:
                self.hidden_text.append(key)
                self.deleted_history.append(self.selected_button)
                self.select_button('')
                self.caption.setText('Текст удалён. Его можно вернуть вместе с оформлением.')
                self.refresh()
            return
        if self.selected_button and not self.selected_button.startswith('text:') and self.selected_button not in self.hidden_buttons:
            self.hidden_buttons.append(self.selected_button)
            self.deleted_history.append(self.selected_button)
            self.select_button('')
            self.caption.setText('Кнопка удалена. «Вернуть удалённую кнопку» восстанавливает её вместе со стилем.')
            self.refresh()

    def restore_button(self):
        key = self.deleted_history.pop() if self.deleted_history else (self.hidden_buttons[-1] if self.hidden_buttons else 'text:'+self.hidden_text[-1] if self.hidden_text else None)
        if key and key.startswith('text:'):
            if key[5:] in self.hidden_text:
                self.hidden_text.remove(key[5:])
                self.refresh()
            return
        if key and key in self.hidden_buttons:
            design = self.current_design(preview=True)
            from dataclasses import replace
            try:
                pages(self.chapters, replace(design, hidden_buttons=[k for k in self.hidden_buttons if k != key]))
            except ValueError as exc:
                self.caption.setText(str(exc))
                return
            self.hidden_buttons.remove(key)
            self.refresh()

    def change_geometry(self, key, rect, font_size=0):
        self.element_layout[key] = list(rect)
        if font_size and key.startswith('text:'):
            self.text_styles.setdefault(key[5:], {})['font_size'] = font_size
            self.font_size.blockSignals(True)
            self.font_size.setValue(font_size)
            self.font_size.blockSignals(False)
        self.refresh()

    def add_button(self):
        self.preview.commit_edit()
        if len(self.custom_buttons) >= 500:
            self.caption.setText('Достигнут предел дополнительных кнопок проекта (500).')
            return
        if len(pages(self.chapters, self.current_design(preview=True))[self.page]) >= 16:
            self.caption.setText('На странице уже 16 кнопок. Удалите лишнюю перед добавлением.')
            return
        key = 'custom:'+uuid.uuid4().hex[:12]
        self.custom_buttons.append(dict(id=key, page=self.page, action='play:0'))
        self.labels[self.language.currentData()+':'+key] = 'Новая кнопка' if self.language.currentData() == 'ru' else 'New button'
        existing = [e['rect'] for e in self.preview.elements]
        rect = QRect(1100, 720, 500, 90)
        for y in range(400, 940, 110):
            for x in (150, 1050):
                candidate = QRect(x, y, 500, 90)
                if not any(candidate.intersects(other) for other in existing):
                    rect = candidate
                    break
            else:
                continue
            break
        self.element_layout[key] = [rect.x(), rect.y(), rect.width(), rect.height()]
        self.populate_labels()
        self.preview_selection(key)
        self.caption.setText('Кнопка добавлена. Выберите действие, измените подпись и перетащите на нужное место.')

    def populate_actions(self):
        self.action_selector.blockSignals(True)
        self.action_selector.clear()
        self.action_selector.addItem('Смотреть с начала', 'play:0')
        for i, sec in enumerate(self.chapters[1:], 1):
            self.action_selector.addItem(f'Глава {i+1} · {int(sec)//3600:02}:{int(sec)%3600//60:02}:{int(sec)%60:02}', f'play:{i}')
        for page in range(len(pages(self.chapters, self.current_design(preview=True)))):
            self.action_selector.addItem('Главное меню' if page == 0 else f'Страница глав {page}', f'page:{page}')
        custom = next((b for b in self.custom_buttons if b['id'] == self.selected_button), None)
        self.action_selector.setEnabled(custom is not None)
        if custom:
            self.action_selector.setCurrentIndex(self.action_selector.findData(custom['action']))
        self.action_selector.blockSignals(False)

    def change_action(self, *_):
        for button in self.custom_buttons:
            if button['id'] == self.selected_button and self.action_selector.currentData():
                button['action'] = self.action_selector.currentData()
        self.refresh()

    def align_buttons(self):
        elements = [e for e in self.preview.elements if e['button']]
        if not elements:
            return
        mode = self.alignment.currentIndex()
        if mode == 3:
            elements.sort(key=lambda e: e['rect'].y())
            total = sum(e['rect'].height() for e in elements)
            if total > 1040:
                self.caption.setText('Кнопки не помещаются по высоте. Сначала уменьшите их размер.')
                return
            gap = min(30, (1040-total)/max(1, len(elements)-1))
            y = (1080-total-gap*(len(elements)-1))/2
        for e in elements:
            r = QRect(e['rect'])
            if mode == 0:
                r.moveLeft(150)
                if r.right() >= 1920:
                    r.moveLeft(1920-r.width())
            elif mode == 1:
                r.moveLeft((1920-r.width())//2)
            elif mode == 2:
                r.moveLeft(max(0, 1770-r.width()))
            else:
                r.moveTop(round(y))
                y += r.height()+gap
            self.element_layout[e['id']] = [r.x(), r.y(), r.width(), r.height()]
        self.refresh()

    def retime_chapters(self, old, new, moved=None):
        """Keep chapter labels, geometry and custom jumps attached to their times."""
        from dataclasses import replace
        self.preview.commit_edit()
        moved = moved or {}
        mapping = {i: new.index(moved.get(t, t)) for i, t in enumerate(old) if moved.get(t, t) in new}
        def remap_key(key):
            if key.startswith('chapter:'):
                i = int(key.split(':')[1])
                return 'chapter:'+str(mapping[i]) if i in mapping else None
            return key
        design = self.current_design(preview=True)
        labels = {}
        for key, value in design.labels.items():
            lang, name = key.split(':', 1)
            mapped = remap_key(name)
            if mapped:
                labels[lang+':'+mapped] = value
        styles = {remap_key(k): v for k, v in design.button_styles.items() if remap_key(k)}
        layout = {remap_key(k): v for k, v in design.layout.items() if remap_key(k)}
        hidden = [remap_key(k) for k in design.hidden_buttons if remap_key(k)]
        custom = copy.deepcopy(design.custom_buttons)
        count = 1 + (len(new)+4)//5 if len(new) > 1 else 1
        for button in custom:
            button['page'] = min(button['page'], count-1)
            kind, value = button['action'].split(':')
            button['action'] = f'play:{mapping.get(int(value), 0)}' if kind == 'play' else f'page:{min(int(value), count-1)}'
        updated = replace(design, labels=labels, button_styles=styles, layout=layout, hidden_buttons=hidden, custom_buttons=custom)
        pages(new, updated)  # Fail before changing any state if a merged page is full.
        self.chapters = list(new)
        self.apply_design(updated)
