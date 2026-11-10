"""Studio numeric controls: a precise value above a matching range slider."""
from PySide6.QtCore import Qt, QEvent, QTime
from PySide6.QtWidgets import (QWidget, QGridLayout, QLabel, QSlider, QSpinBox,
                              QDoubleSpinBox, QTimeEdit, QSizePolicy, QStyle, QStyleOptionSpinBox)


def step_buttons_hidden(number):
    option = QStyleOptionSpinBox()
    number.initStyleOption(option)
    return all(number.style().subControlRect(QStyle.CC_SpinBox, option, part, number).isEmpty()
               for part in (QStyle.SC_SpinBoxUp, QStyle.SC_SpinBoxDown))


class _SliderEdit:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Qt 6.12 collapses the edit area to 1px with NoButtons + styled borders.
        # Hide the subcontrols instead, retaining native typing/keyboard stepping.
        self.setStyleSheet('QAbstractSpinBox::up-button, QAbstractSpinBox::down-button '
                          '{ width: 0px; height: 0px; border: none; } '
                          'QAbstractSpinBox::up-arrow, QAbstractSpinBox::down-arrow '
                          '{ image: none; width: 0px; height: 0px; }')
        self.setAlignment(Qt.AlignRight)

    def _sync_control(self):
        control = getattr(self, 'slider_control', None)
        if control is not None:
            control.sync()

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.EnabledChange, QEvent.FontChange, QEvent.StyleChange):
            self._sync_control()
        return result

    def setReadOnly(self, value):
        super().setReadOnly(value)
        self._sync_control()


class _NumberEdit(_SliderEdit):
    # Keep project/selection restores synchronized even inside QSignalBlocker.
    def setValue(self, value):
        super().setValue(value)
        self._sync_control()

    def setRange(self, low, high):
        super().setRange(low, high)
        self._sync_control()

    def setMinimum(self, value):
        super().setMinimum(value)
        self._sync_control()

    def setMaximum(self, value):
        super().setMaximum(value)
        self._sync_control()

    def setSingleStep(self, value):
        super().setSingleStep(value)
        self._sync_control()

    def setSuffix(self, value):
        super().setSuffix(value)
        self._sync_control()

    def setPrefix(self, value):
        super().setPrefix(value)
        self._sync_control()


class IntegerEdit(_NumberEdit, QSpinBox):
    pass


class DecimalEdit(_NumberEdit, QDoubleSpinBox):
    def setDecimals(self, value):
        super().setDecimals(value)
        self._sync_control()


class TimeEdit(_SliderEdit, QTimeEdit):
    def setTime(self, value):
        super().setTime(value)
        self._sync_control()

    def setTimeRange(self, low, high):
        super().setTimeRange(low, high)
        self._sync_control()

    def setMinimumTime(self, value):
        super().setMinimumTime(value)
        self._sync_control()

    def setMaximumTime(self, value):
        super().setMaximumTime(value)
        self._sync_control()

    def setDisplayFormat(self, value):
        super().setDisplayFormat(value)
        self._sync_control()


class SliderValue(QWidget):
    def __init__(self, number, label, parent=None, *, actions=()):
        super().__init__(parent)
        self.number = number
        self.actions = tuple(actions)
        self.label = QLabel(label) if isinstance(label, str) else label
        if isinstance(self.label, QLabel):
            self.label.setWordWrap(True)
            self.label.setBuddy(number)
        self.label.setMinimumWidth(0)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimumWidth(0)
        self.slider.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(3)
        layout.setColumnStretch(0, 1)
        layout.addWidget(self.label, 0, 0)
        layout.addWidget(number, 0, 1)
        for column, action in enumerate(self.actions, 2):
            layout.addWidget(action, 0, column)
        layout.addWidget(self.slider, 1, 0, 1, 2 + len(self.actions))
        title = self.label.text().replace('&', '').rstrip(':')
        self.slider.setAccessibleName(title)
        number.setAccessibleName(title + ' — значение')
        self.slider.setToolTip(number.toolTip())
        self._syncing = False
        number.slider_control = self
        signal = number.timeChanged if isinstance(number, QTimeEdit) else number.valueChanged
        signal.connect(self.sync)
        self.slider.valueChanged.connect(self._set_number)
        self.sync()

    def sync(self, *_):
        if self._syncing:
            return
        self._syncing = True
        previous = self.slider.blockSignals(True)
        try:
            number = self.number
            if isinstance(number, QTimeEdit):
                low = number.minimumTime().msecsSinceStartOfDay() // 1000
                high = number.maximumTime().msecsSinceStartOfDay() // 1000
                value = number.time().msecsSinceStartOfDay() // 1000
                step = 1
            else:
                factor = 10 ** number.decimals() if isinstance(number, QDoubleSpinBox) else 1
                low, high = round(number.minimum() * factor), round(number.maximum() * factor)
                value = round(number.value() * factor)
                step = max(1, round(number.singleStep() * factor))
            self.slider.setRange(low, high)
            self.slider.setSingleStep(step)
            self.slider.setPageStep(max(step, (high - low) // 10))
            self.slider.setValue(value)
            self.slider.setEnabled(number.isEnabled() and not number.isReadOnly())
            if isinstance(self.label, QLabel):
                self.label.setEnabled(number.isEnabled())
            samples = [number.text()]
            if isinstance(number, QTimeEdit):
                samples.extend(time.toString(number.displayFormat())
                               for time in (number.minimumTime(), number.maximumTime()))
            else:
                samples.extend(number.prefix() + number.textFromValue(value) + number.suffix()
                               for value in (number.minimum(), number.maximum()))
            text_width = max(number.fontMetrics().horizontalAdvance(text) for text in samples)
            width = max(68, number.minimumSizeHint().width(), text_width + 24)
            if number.minimumWidth() != width or number.maximumWidth() != width:
                number.setFixedWidth(width)
            for action in self.actions:
                action.setFixedHeight(number.sizeHint().height())
        finally:
            self.slider.blockSignals(previous)
            self._syncing = False

    def _set_number(self, position):
        if isinstance(self.number, QTimeEdit):
            self.number.setTime(QTime(0, 0).addSecs(position))
        elif isinstance(self.number, QDoubleSpinBox):
            self.number.setValue(position / (10 ** self.number.decimals()))
        else:
            self.number.setValue(position)
