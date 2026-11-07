"""Inspector for episode lists. It edits the existing MenuDesign, not a second scene."""
import copy
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox,
    QSlider, QSpinBox, QPushButton, QCheckBox, QInputDialog, QColorDialog, QLabel, QGroupBox, QSizePolicy,
    QTabWidget, QScrollArea, QFrame, QGridLayout)
from episode_buttons import (STYLE_PRESETS, page_layout, appearance, overlaps)
from disc_menu import button_style, pages, button_id


class ButtonInspector(QWidget):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.syncing = False
        self.numbers = {}
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(10)
        self.selection_title = QLabel('Кнопки эпизодов'); self.selection_title.setObjectName('inspectorHeading')
        box.addWidget(self.selection_title)
        self.selection_detail = QLabel('Выберите кнопку на холсте')
        self.selection_detail.setObjectName('hint'); self.selection_detail.setWordWrap(True)
        box.addWidget(self.selection_detail)
        self.scope = QComboBox()
        for text in ('Выбранные кнопки', 'Все на странице', 'Все в проекте'):
            self.scope.addItem(text)
        self.scope.currentIndexChanged.connect(self.sync)
        self.scope.setAccessibleName('Применить настройки к кнопкам')
        box.addWidget(self.scope)
        self.tabs = QTabWidget(); self.tabs.setObjectName('buttonInspectorTabs'); self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setMinimumHeight(200)
        self.section_layouts = []
        for caption in ('Размеры', 'Стиль', 'Сетка', 'Анимация'):
            page = QWidget(); page_box = QVBoxLayout(page); page_box.setContentsMargins(0, 12, 0, 8); page_box.setSpacing(12)
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); scroll.setWidget(page)
            self.tabs.addTab(scroll, caption); self.section_layouts.append(page_box)
        box.addWidget(self.tabs, 1)

        def group(title, section):
            group = QGroupBox(title); form = QFormLayout(group)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            form.setVerticalSpacing(10)
            self.section_layouts[section].addWidget(group)
            return form

        geometry = group('Геометрия · px', 0)
        for key, title, low, high in [('button_width', 'Ширина', 32, 1620), ('button_height', 'Высота', 32, 540),
                ('padding_x', 'Отступ X', 0, 200), ('padding_y', 'Отступ Y', 0, 200),
                ('font_size', 'Шрифт', 12, 160), ('radius', 'Скругление', 0, 270)]:
            self.add_number(geometry, key, title, low, high)
        reset = QPushButton('Сбросить размеры'); reset.clicked.connect(self.reset_sizes); geometry.addRow(reset)
        self.layout_form = group('Расположение', 2)
        self.mode = QComboBox()
        for text, value in [('Свободное расположение', 'free'), ('Одна колонка', 'column'), ('Сетка', 'grid')]:
            self.mode.addItem(text, value)
        self.mode.currentIndexChanged.connect(lambda: self.change_layout('mode', self.mode.currentData()))
        self.layout_form.addRow('Режим', self.mode)
        self.add_number(self.layout_form, 'columns', 'Колонки', 1, 8)
        self.add_number(self.layout_form, 'gap_x', 'Промежуток X', 0, 300)
        self.add_number(self.layout_form, 'gap_y', 'Промежуток Y', 0, 300)
        self.add_number(self.layout_form, 'snap', 'Шаг сетки', 1, 100)
        self.align = QComboBox()
        for title, value in [('Слева', 'left'), ('По центру', 'center'), ('Справа', 'right')]:
            self.align.addItem(title, value)
        self.align.currentIndexChanged.connect(lambda: self.change_layout('align', self.align.currentData()))
        self.layout_form.addRow('Выравнивание', self.align)
        self.uniform = QCheckBox('Равные размеры')
        self.uniform.toggled.connect(lambda value: self.change_layout('uniform', value))
        self.layout_form.addRow(self.uniform)
        order_row = QHBoxLayout()
        for text, delta in [('Раньше', -1), ('Позже', 1)]:
            button = QPushButton(text); button.clicked.connect(lambda _=False, d=delta: self.move_order(d)); order_row.addWidget(button)
        self.layout_form.addRow(order_row)
        from button_style_card import ButtonStyleCard
        cards = QGridLayout(); cards.setSpacing(8)
        self.style_cards = {}
        for i, (caption, variant) in enumerate((('Контурный', 'outline'), ('Стеклянный', 'glass'), ('Плашка', 'solid'), ('Минималистичный', 'minimal'))):
            card = ButtonStyleCard(caption, variant)
            card.clicked.connect(lambda _=False, key=variant: self.apply_preset(self.preset.findData(key)))
            self.style_cards[variant] = card; cards.addWidget(card, i//2, i%2)
        self.section_layouts[1].addLayout(cards)
        style_form = group('Поверхность', 1)
        self.preset = QComboBox()
        self.preset.setPlaceholderText('Текущее оформление')
        self.preset.activated.connect(self.apply_preset)
        style_form.addRow('Стиль', self.preset)
        for key, title, low, high in [('opacity', 'Заливка %', 0, 100), ('width', 'Контур', 0, 10), ('glow_strength', 'Свечение %', 0, 100)]:
            self.add_number(style_form, key, title, low, high)
        colors = QHBoxLayout()
        self.colors = {}
        for key, label in [('fill', 'Фон'), ('text', 'Текст'), ('border', 'Рамка'), ('highlight', 'Акцент')]:
            button = QPushButton(label); button.setMinimumWidth(0)
            button.clicked.connect(lambda _=False, k=key: self.choose_color(k))
            self.colors[key] = button; colors.addWidget(button)
        style_form.addRow(colors)
        save = QPushButton('Сохранить пресет'); save.clicked.connect(self.save_preset); style_form.addRow(save)
        reset = QPushButton('Сбросить стиль'); reset.clicked.connect(self.reset_appearance); style_form.addRow(reset)
        self.numbered = QCheckBox('Номер эпизода')
        self.numbered.setToolTip('Показывать номер эпизода отдельным блоком перед названием')
        self.numbered.toggled.connect(lambda value: self.change_style({'numbered': value}))
        geometry.addRow(self.numbered)
        self.disabled = QCheckBox('Недоступна')
        self.disabled.setToolTip('Кнопка видна, но исключена из навигации и не запускает действие')
        self.disabled.toggled.connect(lambda value: self.change_style({'disabled': value}))
        style_form.addRow(self.disabled)
        motion = group('Переходы', 3)
        self.animation = QComboBox()
        for text, value in [('Без анимации', 'none'), ('Сдержанная', 'restrained'), ('Кинематографичная', 'cinematic')]:
            self.animation.addItem(text, value)
        self.animation.currentIndexChanged.connect(self.change_animation); motion.addRow(self.animation)
        note = QLabel('Экспорт BD-J: статичные состояния. Переходы и последовательное появление доступны в предпросмотре.')
        note.setWordWrap(True); note.setObjectName('hint'); motion.addRow(note)
        self.undo, self.redo = editor.undo_canvas, editor.redo_canvas
        self.advanced_toggle = QPushButton('Дополнительно'); self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setObjectName('ghost'); self.section_layouts[2].addWidget(self.advanced_toggle)
        self.advanced = QWidget(); self.advanced_layout = QVBoxLayout(self.advanced); self.advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.advanced.hide(); self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.section_layouts[2].addWidget(self.advanced)
        for page_box in self.section_layouts:
            page_box.addStretch()
        for combo in (self.scope, self.mode, self.align, self.preset, self.animation):
            combo.setMinimumWidth(0)
            combo.setMinimumContentsLength(7)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def add_number(self, form, key, title, low, high):
        widget = QWidget(); row = QHBoxLayout(widget); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(5)
        slider = QSlider(Qt.Horizontal); slider.setRange(low, high); slider.setMinimumWidth(35)
        spin = QSpinBox(); spin.setRange(low, high); spin.setFixedWidth(66)
        slider.setAccessibleName(title); spin.setAccessibleName(title + ' — значение')
        slider.valueChanged.connect(spin.setValue); spin.valueChanged.connect(slider.setValue)
        spin.valueChanged.connect(lambda value, k=key: self.change_number(k, value))
        slider.sliderPressed.connect(lambda: setattr(self.editor, '_editing_gesture', True))
        slider.sliderReleased.connect(self.finish_slider)
        row.addWidget(slider, 1); row.addWidget(spin); form.addRow(title, widget)
        self.numbers[key] = (slider, spin)

    def finish_slider(self):
        self.editor._editing_gesture = False
        self.editor.refresh()

    def scope_key(self):
        return f'catalog:{self.editor.catalog_page}' if self.editor.is_catalog() else str(self.editor.page)

    def elements(self):
        return [e for e in self.editor.preview.elements if e['button']]

    def targets(self):
        visible = self.elements()
        if self.scope.currentIndex() == 2:
            design = self.editor.current_design(preview=True)
            keys = [button_id(p, a) for p, entries in enumerate(pages(self.editor.chapters, design)) for _, a in entries]
            from collection_menu import catalog_page
            for p in range((len(self.editor.collection_items)+4)//5):
                _, entries = catalog_page(design, [name for _, name in self.editor.collection_items], p)
                keys.extend(entries.keys)
            return list(dict.fromkeys(keys))
        if self.scope.currentIndex() == 1:
            return [e['id'] for e in visible]
        selected = self.editor.preview.selected_keys or {self.editor.selected_button}
        return [e['id'] for e in visible if e['id'] in selected]

    def sync(self, *_):
        self.syncing = True
        design = self.editor.current_design(preview=True)
        elements = self.elements()
        selected = [e for e in elements if e['id'] in self.editor.preview.selected_keys or e['id'] == self.editor.selected_button]
        self.selection_title.setText('Выбрано кнопок: '+str(len(selected)) if len(selected) > 1 else 'Кнопка эпизода' if selected else 'Кнопки эпизодов')
        detail = selected[0]['shown'] if len(selected) == 1 else 'Ctrl + щелчок — выбрать несколько' if selected else 'Выберите кнопку или примените настройки ко всей странице.'
        self.selection_detail.setText(detail)
        self.tabs.setEnabled(bool(self.targets()))
        target = next((e for e in elements if e['id'] == self.editor.selected_button), elements[0] if elements else None)
        if self.scope.currentIndex() > 0 and elements:
            target = elements[0]
        config = page_layout(design, self.scope_key())
        style = button_style(design, target['id'] if target else 'play')
        rect = target['rect'] if target else None
        from disc_menu import button_radius
        values = dict(style, button_width=rect.width() if rect else config['width'],
                      button_height=rect.height() if rect else config['height'], radius=button_radius(style, rect.height() if rect else 82), **{k: config[k] for k in ('columns', 'gap_x', 'gap_y', 'snap')})
        for key, (slider, spin) in self.numbers.items():
            spin.setValue(values.get(key, 0))
        self.mode.setCurrentIndex(self.mode.findData(config['mode']))
        self.align.setCurrentIndex(self.align.findData(config['align']))
        self.uniform.setChecked(config['uniform'])
        for field in ('columns', 'gap_x', 'gap_y', 'snap'):
            enabled = config['mode'] == 'grid' if field == 'columns' else config['mode'] == 'free' if field == 'snap' else config['mode'] != 'free'
            for widget in self.numbers[field]:
                widget.setEnabled(enabled)
        self.uniform.setEnabled(config['mode'] != 'free')
        self.align.setEnabled(config['mode'] != 'free')
        self.disabled.setChecked(style.get('disabled', False))
        self.numbered.setChecked(style.get('numbered', True))
        self.animation.setCurrentIndex(self.animation.findData(design.button_editor.get('animation', 'restrained')))
        self.preset.clear()
        for text, value in [('Контурный', 'outline'), ('Стеклянный', 'glass'), ('Плашка', 'solid'), ('Минималистичный', 'minimal')]:
            self.preset.addItem(text, value)
        for name in design.button_editor.get('presets', {}):
            self.preset.addItem(name, 'custom:'+name)
        self.preset.setCurrentIndex(self.preset.findData(style.get('variant')))
        for key, card in self.style_cards.items():
            card.setChecked(key == style.get('variant'))
        for key, button in self.colors.items():
            button.setStyleSheet(f'border-bottom: 3px solid {style[key]}; padding: 3px;')
        self.undo.setEnabled(bool(self.editor._undo))
        self.redo.setEnabled(bool(self.editor._redo))
        self.syncing = False

    def change_number(self, key, value):
        if self.syncing:
            return
        if key in ('columns', 'gap_x', 'gap_y', 'snap'):
            self.change_layout(key, value)
        elif key in ('button_width', 'button_height'):
            self.change_size(key, value)
        else:
            self.change_style({key: value})

    def change_style(self, values):
        if self.syncing:
            return
        for key in self.targets():
            self.editor.button_styles.setdefault(key, {}).update(values)
        self.editor.refresh()

    def change_animation(self, *_):
        if not self.syncing:
            self.editor.button_editor['animation'] = self.animation.currentData()
            self.editor.refresh()

    def apply_preset(self, index):
        key = self.preset.itemData(index)
        value = self.editor.button_editor.get('presets', {}).get(key[7:], {}) if key.startswith('custom:') else STYLE_PRESETS[key]
        self.change_style(copy.deepcopy(value))

    def choose_color(self, key):
        targets = self.targets()
        if not targets:
            return
        color = QColorDialog.getColor(QColor(button_style(self.editor.current_design(preview=True), targets[0])[key]), self, 'Цвет кнопок')
        if color.isValid():
            self.change_style({key: color.name()})

    def save_preset(self):
        targets = self.targets()
        if not targets:
            return
        name, ok = QInputDialog.getText(self, 'Сохранить оформление', 'Название пресета (до 60 символов):')
        if ok and name.strip():
            presets = self.editor.button_editor.setdefault('presets', {})
            if len(name.strip()) > 60 or len(presets) >= 100:
                self.editor.caption.setText('Слишком длинное название или достигнут предел пресетов.'); return
            presets[name.strip()] = appearance(button_style(self.editor.current_design(preview=True), targets[0]))
            self.editor.refresh()

    def reset_appearance(self):
        from episode_buttons import APPEARANCE_KEYS
        for key in self.targets():
            style = self.editor.button_styles.setdefault(key, {})
            for field in APPEARANCE_KEYS:
                style.pop(field, None)
        self.editor.refresh()

    def reset_sizes(self):
        before = copy.deepcopy((self.editor.element_layout, self.editor.button_editor, self.editor.button_styles))
        design = self.editor.current_design(preview=True)
        targets = self.targets()
        from disc_menu import page_elements
        elements = self.elements() if self.editor.is_catalog() else [e for p in range(len(pages(self.editor.chapters, design))) for e in page_elements(design, self.editor.chapters, p) if e['button']]
        if self.scope.currentIndex() == 2:
            elements = [e for p in range(len(pages(self.editor.chapters, design))) for e in page_elements(design, self.editor.chapters, p) if e['button']]
            from collection_menu import catalog_page
            for p in range((len(self.editor.collection_items)+4)//5):
                root, entries = catalog_page(design, [name for _, name in self.editor.collection_items], p)
                elements += [e for e in page_elements(root, [0], 0, entries) if e['button']]
        for element in elements:
            r = element['rect']; rect = [r.x(), r.y(), r.width(), r.height()] if hasattr(r, 'x') else list(r)
            if element['id'] in targets:
                rect[2:] = [760, 82]
            self.editor.element_layout[element['id']] = rect
        for key in self.targets():
            style = self.editor.button_styles.setdefault(key, {})
            for field in ('font_size', 'padding_x', 'padding_y', 'radius'):
                style.pop(field, None)
        configs = self.editor.button_editor.setdefault('pages', {})
        for scope in list(configs) if self.scope.currentIndex() == 2 else [self.scope_key()]:
            config = configs.get(scope)
            if not config:
                continue
            if self.scope.currentIndex() == 0:
                config['uniform'] = False
            else:
                config.update(width=760, height=82)
        try:
            check = self.editor.current_design(preview=True)
            for p in range(len(pages(self.editor.chapters, check))):
                page_elements(check, self.editor.chapters, p)
            if page_layout(check, self.scope_key())['mode'] == 'free':
                current = self.elements()
                rects = [check.layout.get(e['id'], list(e['rect'].getRect())) for e in current]
                if any(overlaps(rect, rects[:i]+rects[i+1:]) for i, rect in enumerate(rects)):
                    raise ValueError('Сброс размеров приводит к пересечению кнопок. Сначала освободите место.')
            self.editor.refresh()
        except ValueError as exc:
            self.editor.element_layout, self.editor.button_editor, self.editor.button_styles = before
            self.editor.caption.setText(str(exc)); self.sync()

    def change_layout(self, key, value):
        if self.syncing:
            return
        before = copy.deepcopy(self.editor.button_editor)
        config = page_layout(self.editor.current_design(preview=True), self.scope_key())
        if key == 'mode' and value == 'free':
            for element in self.elements():
                r = element['rect']; self.editor.element_layout[element['id']] = [r.x(), r.y(), r.width(), r.height()]
        config[key] = value
        if key == 'mode' and self.elements():
            first = self.elements()[0]['rect']
            config['width'], config['height'] = first.width(), first.height()
        self.editor.button_editor.setdefault('pages', {})[self.scope_key()] = config
        try:
            self.editor.refresh()
        except ValueError as exc:
            self.editor.button_editor = before
            self.editor.caption.setText(str(exc)); self.sync()

    def change_size(self, key, value):
        targets = self.targets()
        if not targets:
            return
        before = copy.deepcopy((self.editor.element_layout, self.editor.button_editor))
        design = self.editor.current_design(preview=True)
        from disc_menu import page_elements
        elements = self.elements() if self.editor.is_catalog() and self.scope.currentIndex() != 2 else [e for p in range(len(pages(self.editor.chapters, design))) for e in page_elements(design, self.editor.chapters, p) if e['button']]
        if self.scope.currentIndex() == 2 and self.editor.collection_items:
            from collection_menu import catalog_page
            for p in range((len(self.editor.collection_items)+4)//5):
                root, entries = catalog_page(design, [name for _, name in self.editor.collection_items], p)
                elements += [e for e in page_elements(root, [0], 0, entries) if e['button']]
        # Preserve resolved sizes when disabling uniform sizing for an individual cell.
        for element in elements:
            r = element['rect']
            self.editor.element_layout[element['id']] = [r.x(), r.y(), r.width(), r.height()] if hasattr(r, 'x') else list(r)
        for element in elements:
            if element['id'] not in targets:
                continue
            r = element['rect']
            rect = [r.x(), r.y(), r.width(), r.height()] if hasattr(r, 'x') else list(r)
            rect[2 if key == 'button_width' else 3] = value
            if rect[0]+rect[2] > 1920 or rect[1]+rect[3] > 1080:
                self.editor.element_layout, self.editor.button_editor = before
                self.editor.caption.setText('Размер выходит за границы меню.'); self.sync(); return
            self.editor.element_layout[element['id']] = rect
        configs = self.editor.button_editor.setdefault('pages', {})
        scopes = list(configs) if self.scope.currentIndex() == 2 else [self.scope_key()]
        for scope in scopes:
            config = page_layout(design, scope)
            if config['mode'] != 'free':
                if self.scope.currentIndex() == 0:
                    config['uniform'] = False
                else:
                    config['width' if key == 'button_width' else 'height'] = value
                configs[scope] = config
        try:
            # Resolve every affected page before committing the edit.
            check = self.editor.current_design(preview=True)
            for p in range(len(pages(self.editor.chapters, check))):
                page_elements(check, self.editor.chapters, p)
            if self.editor.collection_items:
                from collection_menu import catalog_page
                for p in range((len(self.editor.collection_items)+4)//5):
                    root, entries = catalog_page(check, [name for _, name in self.editor.collection_items], p)
                    page_elements(root, [0], 0, entries)
            if page_layout(check, self.scope_key())['mode'] == 'free':
                rects = [self.editor.element_layout.get(e['id'], [e['rect'].x(), e['rect'].y(), e['rect'].width(), e['rect'].height()]) for e in self.elements()]
                if any(overlaps(rect, rects[:i]+rects[i+1:]) for i, rect in enumerate(rects)):
                    raise ValueError('Кнопки пересекаются. Уменьшите размер или освободите место.')
            self.editor.refresh()
        except ValueError as exc:
            self.editor.element_layout, self.editor.button_editor = before
            self.editor.caption.setText(str(exc)); self.sync()

    def reorder(self, key, target):
        keys = [e['id'] for e in self.elements()]
        if key not in keys:
            return
        keys.remove(key); keys.insert(max(0, min(target, len(keys))), key)
        self.editor.button_editor.setdefault('order', {})[self.scope_key()] = keys
        self.editor.refresh()

    def move_order(self, delta):
        keys = [e['id'] for e in self.elements()]
        if self.editor.selected_button in keys:
            self.reorder(self.editor.selected_button, keys.index(self.editor.selected_button)+delta)
