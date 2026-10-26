"""Live menu preview, using the same page images as the on-disc Xlet."""
from PySide6.QtCore import Qt, Signal, QSize, QRect, QEvent
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics, QIcon
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QLineEdit, QFileDialog, QMessageBox, QSlider, QColorDialog, QSpinBox, QLayout, QFontComboBox, QSplitter, QScrollArea, QGroupBox, QFormLayout, QGridLayout, QFrame)
from disc_menu import (MenuDesign, render_page, pages, import_background, TEXT, BUTTONS,
                       button_id, button_label_key, button_style, page_elements, resolve_action)
from menu_layout import text_style
from menu_navigation import nearest_button
import copy
import uuid
from dataclasses import replace
from collection_menu import catalog_page


from menu_preview import MenuPreview


class MenuEditor(QWidget):
    changed = Signal()
    movie_requested = Signal(str)
    movie_renamed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.chapters = [0.0]
        self.chapter_titles = []
        self.audio_tracks = []
        self.subtitle_tracks = []
        self.page = self.focus = 0
        self.background = ''
        self.labels = {}
        self.hidden_buttons = []
        self.hidden_text = []
        self.logo_visible = True
        self.button_styles = {}
        self.element_layout = {}
        self.text_styles = {}
        self.custom_buttons = []
        self.history = []
        self.deleted_history = []
        self.selected_button = ''
        self.collection_items = []
        self.collection_enabled = False
        self.collection_root = True
        self.catalog_page = 0
        self.movie_path = None
        self.movie_name = ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(8)
        layout.addWidget(self.splitter)
        canvas = QWidget()
        canvas_layout = QVBoxLayout(canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(6)
        self.context_selector = QComboBox()
        self.context_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.context_selector.setMinimumContentsLength(20)
        self.context_selector.setAccessibleName('Просмотр меню общего диска или фильма')
        self.context_selector.currentIndexChanged.connect(self._context_changed)
        self.context_selector.hide()
        canvas_layout.addWidget(self.context_selector)
        self.preview = MenuPreview()
        self.preview.setObjectName('preview')
        canvas_layout.addWidget(self.preview, 1)
        remote = QHBoxLayout()
        remote.addStretch()
        self.back_button = QPushButton('Назад')
        self.back_button.clicked.connect(lambda: self.press('back'))
        remote.addWidget(self.back_button)
        self.remote_buttons = {}
        for caption, key in [('Вверх', 'up'), ('Вниз', 'down'), ('Влево', 'left'), ('Вправо', 'right'),
                             ('OK', 'enter'), ('Главное меню', 'home')]:
            button = QPushButton(caption)
            button.setAccessibleName({'up': 'Вверх', 'down': 'Вниз'}.get(key, caption))
            button.clicked.connect(lambda _=False, k=key: self.press(k))
            remote.addWidget(button)
            self.remote_buttons[key] = button
        canvas_layout.addLayout(remote)
        self.fullscreen_button = QPushButton()
        self.fullscreen_button.setIcon(QIcon(str(Path(__file__).parent / 'assets/icons/fullscreen.svg')))
        self.fullscreen_button.setIconSize(QSize(21, 21))
        self.fullscreen_button.setFixedSize(34, 34)
        self.fullscreen_button.setStyleSheet('padding: 0px;')
        self.fullscreen_button.setToolTip('На весь экран · F11')
        self.fullscreen_button.setAccessibleName('На весь экран')
        self.fullscreen_button.clicked.connect(self.show_fullscreen)
        remote.addWidget(self.fullscreen_button)
        remote.addStretch()
        self.caption = QLabel('Клик — выбрать · Двойной клик — изменить текст · Del — удалить. Перетаскивайте элементы и маркеры размера; Alt отключает прилипание.')
        self.caption.setWordWrap(True)
        self.caption.setObjectName('hint')
        canvas_layout.addWidget(self.caption)
        self.splitter.addWidget(canvas)

        self.inspector = QScrollArea()
        self.inspector.setObjectName('menuInspector')
        self.inspector.setAccessibleName('Настройки меню')
        self.inspector.setWidgetResizable(True)
        self.inspector.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inspector.setFrameShape(QFrame.NoFrame)
        self.inspector.setMinimumWidth(300)
        self.inspector.setMaximumWidth(440)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 8, 0)
        panel_layout.setSpacing(10)

        def section(title):
            group = QGroupBox(title)
            box = QVBoxLayout(group)
            box.setSpacing(8)
            panel_layout.addWidget(group)
            return box

        general = section('Меню диска')
        self.enabled = QCheckBox('Интерактивное меню · BD-J')
        self.enabled.setChecked(True)
        general.addWidget(self.enabled)
        self.title = QLineEdit('Мой Blu-ray')
        self.title.setMaxLength(80)
        self.title.setPlaceholderText('Название на обложке меню')
        general.addWidget(self.title)
        self.theme = QComboBox()
        self.theme.addItem('Кино · Янтарь', 'amber')
        self.theme.addItem('Кино · Сапфир', 'sapphire')
        self.theme.addItem('Полночь', 'midnight')
        self.theme.addItem('Графит', 'graphite')
        self.theme.addItem('Кинотеатр', 'cinema')
        self.theme.setCurrentIndex(self.theme.findData('amber'))
        self.language = QComboBox()
        self.language.addItem('Русский', 'ru')
        self.language.addItem('English', 'en')
        general_form = QFormLayout()
        general_form.addRow('Тема', self.theme)
        general_form.addRow('Язык', self.language)
        general.addLayout(general_form)

        background = section('Фон')
        background_row = QHBoxLayout()
        choose = QPushButton('Выбрать…')
        choose.clicked.connect(self.choose_background)
        background_row.addWidget(choose)
        clear = QPushButton('Убрать фон')
        clear.clicked.connect(self.clear_background)
        background_row.addWidget(clear)
        background.addLayout(background_row)
        self.fit = QComboBox()
        self.fit.addItem('Заполнить экран', 'cover')
        self.fit.addItem('Изображение целиком', 'contain')
        background.addWidget(self.fit)
        self.dim = QSlider(Qt.Horizontal)
        self.dim.setRange(0, 80)
        self.dim.setValue(40)
        self.dim.setAccessibleName('Затемнение фона')
        self.dim_label = QLabel('Затемнение: 40%')
        background.addWidget(self.dim_label)
        background.addWidget(self.dim)

        element = section('Выбранный элемент')
        self.label_key = QComboBox()
        self.label_key.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.label_key.setMinimumContentsLength(15)
        element.addWidget(self.label_key)
        self.label_value = QLineEdit()
        self.label_value.setMaxLength(120)
        self.label_value.setPlaceholderText('Своя подпись; пусто — стандартная')
        element.addWidget(self.label_value)
        reset = QPushButton('Сбросить подпись')
        self.reset_label = reset
        reset.clicked.connect(self.label_value.clear)
        reset.clicked.connect(self.edit_label)
        element.addWidget(reset)
        self.style_panel = QWidget()
        style_layout = QVBoxLayout(self.style_panel)
        style_layout.setContentsMargins(0, 0, 0, 0)
        self.shape = QComboBox()
        for text, value in [('Скруглённая', 'rounded'), ('Прямоугольная', 'rectangle'), ('Капсула', 'pill')]:
            self.shape.addItem(text, value)
        self.border_width = QSpinBox()
        self.border_width.setRange(0, 10)
        self.font_size = QSpinBox()
        self.font_size.setRange(12, 160)
        self.opacity = QSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix('%')
        self.font_family = QFontComboBox()
        self.font_family.setMinimumWidth(0)
        self.font_family.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.font_family.setMinimumContentsLength(9)
        form = QFormLayout()
        form.addRow('Гарнитура', self.font_family)
        form.addRow('Размер', self.font_size)
        self.bold = QCheckBox('Жирный')
        self.italic = QCheckBox('Курсив')
        font_row = QHBoxLayout()
        font_row.addWidget(self.bold)
        font_row.addWidget(self.italic)
        form.addRow(font_row)
        form.addRow('Форма', self.shape)
        form.addRow('Обводка', self.border_width)
        form.addRow('Заливка', self.opacity)
        style_layout.addLayout(form)
        colors = QGridLayout()
        self.color_buttons = {}
        for i, (name, label) in enumerate([('fill', 'Цвет фона'), ('border', 'Обводка'), ('text', 'Текст'), ('highlight', 'Выделение')]):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, k=name: self.choose_color(k))
            self.color_buttons[name] = button
            colors.addWidget(button, i // 2, i % 2)
        style_layout.addLayout(colors)
        reset_style = QPushButton('Сбросить стиль')
        reset_style.clicked.connect(self.reset_style)
        style_layout.addWidget(reset_style)
        self.action_selector = QComboBox()
        self.action_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.action_selector.setMinimumContentsLength(15)
        style_layout.addWidget(QLabel('Действие кнопки'))
        style_layout.addWidget(self.action_selector)
        element.addWidget(self.style_panel)
        self.style_panel.setEnabled(False)
        self.shape.currentIndexChanged.connect(self.style_changed)
        self.border_width.valueChanged.connect(self.style_changed)
        self.font_size.valueChanged.connect(self.style_changed)
        self.opacity.valueChanged.connect(self.style_changed)
        self.font_family.currentFontChanged.connect(self.style_changed)
        self.bold.toggled.connect(self.style_changed)
        self.italic.toggled.connect(self.style_changed)
        self.action_selector.currentIndexChanged.connect(self.change_action)

        tools = section('Кнопки и выравнивание')
        add = QPushButton('+ Добавить кнопку')
        add.clicked.connect(self.add_button)
        tools.addWidget(add)
        self.alignment = QComboBox()
        self.alignment.addItems(['По левому краю', 'По центру меню', 'По правому краю', 'Равные интервалы по вертикали'])
        self.alignment.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.alignment.setMinimumContentsLength(15)
        tools.addWidget(self.alignment)
        align = QPushButton('Выровнять кнопки')
        align.clicked.connect(self.align_buttons)
        tools.addWidget(align)
        self.magnet = QCheckBox('Прилипание к направляющим')
        self.magnet.setChecked(True)
        tools.addWidget(self.magnet)
        restore = QPushButton('Вернуть удалённый элемент')
        restore.clicked.connect(self.restore_button)
        tools.addWidget(restore)
        panel_layout.addStretch()
        self.inspector.setWidget(panel)
        self.splitter.addWidget(self.inspector)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([1000, 330])
        self.preview.selected.connect(self.preview_selection)
        self.preview.text_committed.connect(self.inline_text)
        self.preview.delete_requested.connect(self.delete_button)
        self.preview.geometry_changed.connect(self.change_geometry)
        self.preview.navigate.connect(self.press)
        self.magnet.toggled.connect(lambda enabled: setattr(self.preview, 'snapping', enabled))
        from PySide6.QtGui import QShortcut, QKeySequence
        self.fullscreen_shortcut = QShortcut(QKeySequence('F11'), self)
        self.fullscreen_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.fullscreen_shortcut.activated.connect(self.show_fullscreen)
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

    def set_collection(self, items, enabled, selected_path):
        """Keep source identities out of the shared menu design and saved style."""
        previous = (self.collection_items, self.collection_enabled, self.movie_path)
        self.collection_items = list(items)
        if enabled and not self.collection_enabled:
            self.collection_root = True
        self.collection_enabled = enabled
        self.movie_path = selected_path
        self.movie_name = dict(items).get(selected_path, '')
        if not self.movie_name:
            self.collection_root = True
        self.catalog_page = min(self.catalog_page, max(0, (len(items)-1)//5))
        self.context_selector.blockSignals(True)
        self.context_selector.clear()
        self.context_selector.addItem(f'Общий диск · фильмов: {len(items)}', None)
        for path, name in items:
            self.context_selector.addItem('Фильм: ' + name, path)
            self.context_selector.setItemData(self.context_selector.count()-1, path, Qt.ToolTipRole)
        self.context_selector.setCurrentIndex(0 if self.collection_root else max(0, self.context_selector.findData(selected_path)))
        self.context_selector.blockSignals(False)
        self.context_selector.setVisible(enabled)
        if previous != (self.collection_items, self.collection_enabled, self.movie_path):
            self.select_button('')
            if self.is_catalog():
                self.caption.setText('Кнопки фильмов создаются автоматически. Выберите кнопку и нажмите OK, чтобы открыть меню этого фильма и его главы.')
        if previous[1:] != (self.collection_enabled, self.movie_path):
            self.populate_labels()
        if not self.label_value.hasFocus():
            self.sync_label()
        self.refresh()

    def _context_changed(self, index):
        self.preview.commit_edit()
        if index <= 0:
            self.show_catalog()
        else:
            self.open_movie(self.context_selector.itemData(index))

    def show_catalog(self):
        self.collection_root = True
        self.page = self.focus = 0
        self.history = []
        self.select_button('')
        self.caption.setText('Кнопки фильмов создаются автоматически. Выберите кнопку и нажмите OK, чтобы открыть меню этого фильма и его главы.')
        self.context_selector.blockSignals(True)
        self.context_selector.setCurrentIndex(0)
        self.context_selector.blockSignals(False)
        self.populate_labels()
        self.sync_label()
        self.refresh()

    def open_movie(self, path):
        if path not in dict(self.collection_items):
            return
        self.collection_root = False
        self.page = self.focus = 0
        self.history = []
        self.select_button('')
        self.movie_requested.emit(path)
        self.caption.setText(f'Меню фильма «{self.movie_name}». «Выбор главы» открывает только его сцены; «Назад» — предыдущий уровень меню.')
        self.populate_labels()
        self.sync_label()
        self.refresh()

    def display_design(self):
        design = self.current_design(preview=True)
        return replace(design, title=self.movie_name) if self.collection_enabled and not self.collection_root and self.movie_name else design

    def is_catalog(self):
        return self.collection_enabled and self.collection_root

    def design(self):
        self.preview.commit_edit()
        return self.current_design() if self.enabled.isChecked() else None

    def current_design(self, preview=False):
        return MenuDesign(self.title.text().strip() or ('Blu-ray' if preview else ''),
                          self.theme.currentData(), self.language.currentData(), self.background,
                          self.fit.currentData(), self.dim.value(), self.labels,
                          self.hidden_buttons, self.button_styles, self.element_layout, self.text_styles, self.custom_buttons, self.hidden_text, self.audio_tracks, self.subtitle_tracks, self.chapter_titles, self.logo_visible)

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
        self.logo_visible = design.logo_visible
        self.audio_tracks = copy.deepcopy(design.audio_tracks)
        self.subtitle_tracks = copy.deepcopy(design.subtitle_tracks)
        self.chapter_titles = list(design.chapter_titles)
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
                          ('subtitles', 'Субтитры'), ('sub:0', 'Субтитры: выключить'), ('sub_previous', 'Субтитры: предыдущие'), ('sub_next', 'Субтитры: следующие'), ('sub_back', 'Субтитры: назад'), ('audio', 'Аудио'), ('audio_back', 'Аудио: назад'),
                          ('audio_previous', 'Аудио: предыдущие дорожки'), ('audio_next', 'Аудио: следующие дорожки'),
                          ('home', 'Главное меню'), ('previous', 'Предыдущие главы'),
                          ('next', 'Следующие главы'), ('subtitle', 'Подзаголовок'),
                          ('brand', 'Верхняя надпись'), ('hint', 'Подсказка внизу')]:
            if self.is_catalog() and key not in ('title', 'brand', 'hint'):
                continue
            self.label_key.addItem(name, key)
        for i in range(0 if self.is_catalog() else len(self.chapters)):
            self.label_key.addItem(f'Название главы {i + 1}', f'chapter:{i}')
        for i in range(0 if self.is_catalog() else len(self.subtitle_tracks)):
            self.label_key.addItem(f'Название субтитров {i+1}', f'sub:{i+1}')
        for i in range(0 if self.is_catalog() else len(self.audio_tracks)):
            self.label_key.addItem(f'Название аудиодорожки {i+1}', f'audio:{i+1}')
        for i, button in enumerate([] if self.is_catalog() else self.custom_buttons):
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
            self.label_value.setText(self.movie_name if self.collection_enabled and not self.collection_root else self.title.text())
            self.label_value.setPlaceholderText('Название фильма')
            return
        lang = self.language.currentData()
        self.label_value.setText(self.labels.get(lang + ':' + key, ''))
        self.label_value.setPlaceholderText(self.current_design(preview=True).text(key) or TEXT[lang].get(key, f'{TEXT[lang]["chapter"]} {int(key.split(":")[-1])+1}' if key.startswith('chapter:') else ''))

    def edit_label(self, *_):
        if self.label_key.currentData() is None:
            return
        if self.label_key.currentData() == 'title':
            self.inline_text('title', self.label_value.text().strip() or 'Мой Blu-ray')
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

    def set_audio(self, info):
        from disc_audio import menu_tracks
        self.preview.commit_edit()
        self.audio_tracks = menu_tracks(info)
        self.page = self.focus = 0
        self.history = []
        self.select_button('')
        self.populate_labels()
        self.refresh()

    def set_subtitles(self, info):
        from subtitles import menu_subtitles
        self.preview.commit_edit()
        self.subtitle_tracks = menu_subtitles(info)
        self.page = self.focus = 0
        self.history = []
        self.select_button('')
        self.populate_labels()
        self.refresh()

    def set_chapters(self, chapters, titles=None):
        self.preview.commit_edit()
        self.chapters = list(chapters)[:999] or [0.0]
        self.chapter_titles = list(titles or [])
        self.page = self.focus = 0
        self.history = []
        self.select_button('')
        self.populate_labels()
        self.refresh()

    def refresh(self, *_):
        design = self.display_design()
        self.dim_label.setText(f'Затемнение: {self.dim.value()}%')
        if self.is_catalog():
            design, entries = catalog_page(design, [name for _, name in self.collection_items], self.catalog_page)
            self.focus = min(self.focus, max(0, len(entries)-1))
            image = render_page(design, [0.0], 0, self.focus, entries_override=entries)
            elements = page_elements(design, [0.0], 0, entries)
            for element in elements:
                if element['button']:
                    action = entries[element['index']][1]
                    element.update(id='catalog:'+action, key=action, locked_geometry=True, locked_text=not action.startswith('movie:'))
                elif element['id'] == 'text:subtitle':
                    element['locked_text'] = True
            self.back_button.setEnabled(self.catalog_page > 0)
        else:
            graph = pages(self.chapters, design)
            self.page = min(self.page, len(graph)-1)
            self.focus = min(self.focus, max(0, len(graph[self.page])-1))
            self.back_button.setEnabled(bool(self.history) or self.collection_enabled)
            image = render_page(design, self.chapters, self.page, self.focus)
            elements = page_elements(design, self.chapters, self.page)
        self.preview.image = image
        for element in elements:
            rect = QRect(*element['rect'])
            element['rect'] = rect
            if element.get('image'):
                element['text_rect'] = QRect()
                continue
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

    def move_focus(self, direction):
        buttons = sorted((e for e in self.preview.elements if e['button']), key=lambda e: e['index'])
        rects = [(r.x(), r.y(), r.width(), r.height()) for r in (e['rect'] for e in buttons)]
        self.focus = nearest_button(rects, self.focus, direction)

    def press(self, key):
        self.preview.commit_edit()
        if self.is_catalog():
            _, entries = catalog_page(self.current_design(preview=True), [name for _, name in self.collection_items], self.catalog_page)
            if key in ('up', 'down', 'left', 'right') and entries:
                self.move_focus(key)
            elif key in ('back', 'home'):
                self.catalog_page = max(0, self.catalog_page-1) if key == 'back' else 0
                self.focus = 0
            elif key in ('enter', 'ok') and entries:
                kind, value = entries[self.focus][1].split(':')
                if kind == 'movie':
                    self.open_movie(self.collection_items[int(value)][0])
                    return
                self.catalog_page, self.focus = int(value), 0
            self.refresh()
            return
        if self.collection_enabled and (key == 'home' or key == 'back' and not self.history):
            self.show_catalog()
            return
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
        elif key in ('up', 'down', 'left', 'right'):
            if entries:
                self.move_focus(key)
        elif key in ('enter', 'ok') and entries:
            action = resolve_action(self.current_design(preview=True), entries[self.focus][1])
            kind, value = action.split(':')
            if kind == 'page':
                if int(value) >= len(pages(self.chapters, self.current_design(preview=True))):
                    self.caption.setText('Страница больше не существует. Выберите другое действие кнопки.')
                    return
                self.history.append((self.page, self.focus))
                self.page, self.focus = int(value), 0
                self.select_button('')
            elif kind == 'sub':
                from disc_audio import track_label
                self.caption.setText('Субтитры: ' + ('выключены' if value == '0' else track_label(self.subtitle_tracks[int(value)-1], int(value), self.language.currentData())))
                self.page = self.focus = 0
                self.select_button('')
            elif kind == 'audio':
                from disc_audio import track_label
                self.caption.setText('На плеере будет выбрана дорожка: ' + track_label(self.audio_tracks[int(value)-1], int(value), self.language.currentData()))
                self.page = self.focus = 0
                self.select_button('')
            else:
                owner = f' «{self.movie_name}»' if self.collection_enabled else ''
                name = self.display_design().text(f'chapter:{value}') or f'Глава {int(value)+1}'
                self.caption.setText(f'На плеере начнётся воспроизведение главы {int(value)+1}: {name} · фильм{owner} · с {self.chapters[int(value)]:g} с.')
        self.refresh()

    def inline_text(self, key, value):
        if key.startswith('movie:'):
            index = int(key.split(':')[1])
            if 0 <= index < len(self.collection_items):
                self.movie_renamed.emit(self.collection_items[index][0], value)
            return
        if key == 'title':
            if self.collection_enabled and not self.collection_root:
                self.movie_renamed.emit(self.movie_path, value)
                return
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
        fixed_label = key == 'logo' or key.startswith('catalog:') or (self.is_catalog() and key == 'text:subtitle')
        self.label_key.setEnabled(not fixed_label)
        self.label_value.setEnabled(not fixed_label)
        self.reset_label.setEnabled(not fixed_label)
        if key.startswith('catalog:'):
            self.style_panel.setEnabled(False)
            self.label_value.setEnabled(False)
            self.caption.setText('Кнопка фильма: OK — открыть его меню; двойной клик по названию — переименовать. Порядок и состав задаются списком фильмов слева.')
            return
        if key == 'logo':
            self.style_panel.setEnabled(False)
            self.caption.setText('Логотип · перетащите или измените размер. Del — удалить, «Вернуть удалённый элемент» — восстановить.')
            return
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
        previous = self.selected_button
        self.select_button(key)
        if key and key != previous:
            self.inspector.verticalScrollBar().setValue(self.label_key.parentWidget().y())
        if self.is_catalog():
            _, entries = catalog_page(self.current_design(preview=True), [name for _, name in self.collection_items], self.catalog_page)
            for i, (_, action) in enumerate(entries):
                if 'catalog:'+action == key:
                    self.focus = i
            self.refresh()
            return
        entries = pages(self.chapters, self.current_design(preview=True))[self.page]
        for i, (_, action) in enumerate(entries):
            if button_id(self.page, action) == key:
                self.focus = i
        self.refresh()

    def style_changed(self, *_):
        if not self.selected_button or self.selected_button.startswith('catalog:'):
            return
        is_text = self.selected_button.startswith('text:')
        style = self.text_styles.setdefault(self.selected_button[5:], {}) if is_text else self.button_styles.setdefault(self.selected_button, {})
        style.update(font_size=self.font_size.value(), font_family=self.font_family.currentFont().family(), bold=self.bold.isChecked(), italic=self.italic.isChecked())
        if not is_text:
            style.update(shape=self.shape.currentData(), width=self.border_width.value(), opacity=self.opacity.value())
        self.refresh()

    def choose_color(self, name):
        if not self.selected_button or self.selected_button.startswith('catalog:'):
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
        if self.selected_button.startswith('catalog:'):
            self.caption.setText('Чтобы убрать фильм и его кнопку с общего диска, выберите этот фильм в списке слева и нажмите «Удалить».')
            return
        if self.selected_button == 'logo':
            self.logo_visible = False
            self.deleted_history.append('logo')
            self.select_button('')
            self.caption.setText('Логотип удалён со всех страниц меню. Его можно вернуть вместе с положением и размером.')
            self.refresh()
            return
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
        key = self.deleted_history.pop() if self.deleted_history else ('logo' if not self.logo_visible else self.hidden_buttons[-1] if self.hidden_buttons else 'text:'+self.hidden_text[-1] if self.hidden_text else None)
        if key == 'logo':
            self.logo_visible = True
            self.refresh()
            return
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
        if key.startswith('catalog:'):
            return
        self.element_layout[key] = list(rect)
        if font_size and key.startswith('text:'):
            self.text_styles.setdefault(key[5:], {})['font_size'] = font_size
            self.font_size.blockSignals(True)
            self.font_size.setValue(font_size)
            self.font_size.blockSignals(False)
        self.refresh()

    def add_button(self):
        self.preview.commit_edit()
        if self.is_catalog():
            self.caption.setText('Кнопки фильмов добавляются автоматически: нажмите «Добавить видео» слева. Свои кнопки можно добавлять в меню конкретного фильма.')
            return
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
            from disc_menu import audio_page_start
            first = audio_page_start(self.chapters)
            name = 'Главное меню' if page == 0 else f'Аудио · страница {page-first+1}' if page >= first else f'Страница глав {page}'
            from disc_menu import subtitle_page_start
            sub_first = subtitle_page_start(self.chapters, self.current_design(preview=True))
            if page >= sub_first:
                name = f'Субтитры · страница {page-sub_first+1}'
            self.action_selector.addItem(name, f'page:{page}')
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
        if self.is_catalog():
            self.caption.setText('Кнопки общего диска выровнены автоматически. Их порядок задаётся списком фильмов слева.')
            return
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
        from disc_menu import audio_page_start
        old_audio_start, new_audio_start = audio_page_start(old), audio_page_start(new)
        def remap_page(page):
            if page == 0:
                return 0
            if page >= old_audio_start:
                return page + new_audio_start - old_audio_start
            return min(page, new_audio_start - 1)
        for button in custom:
            button['page'] = remap_page(button['page'])
            kind, value = button['action'].split(':')
            if kind == 'play':
                button['action'] = f'play:{mapping.get(int(value), 0)}'
            elif kind == 'page':
                button['action'] = f'page:{remap_page(int(value))}'
        titles = [''] * len(new)
        for before, after in mapping.items():
            if before < len(design.chapter_titles):
                titles[after] = design.chapter_titles[before]
        updated = replace(design, labels=labels, button_styles=styles, layout=layout, hidden_buttons=hidden, custom_buttons=custom, chapter_titles=titles)
        pages(new, updated)  # Fail before changing any state if a merged page is full.
        self.chapters = list(new)
        self.apply_design(updated)
