"""Interactive canvas; pointer coordinates are mapped through the letterboxed image."""
from PySide6.QtCore import Qt, Signal, QSize, QRect, QEvent, QPointF, QTimer
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QLineEdit, QApplication
from menu_layout import snap_rect
import copy
import time


class MenuPreview(QWidget):
    selected = Signal(str)
    text_committed = Signal(str, str)
    delete_requested = Signal()
    geometry_changed = Signal(str, list, int)
    navigate = Signal(str)
    gesture_finished = Signal()
    reorder_requested = Signal(str, int)
    focus_requested = Signal(int)

    def __init__(self):
        super().__init__()
        self.image = None
        self.setMinimumSize(300, 169)
        self.view_only = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.elements = []
        self.selection = ''
        self.selected_keys = set()
        self.edit_key = None
        self.drag = None
        self.guides = []
        self.snapping = True
        self.scene_background = None
        self.layout_config = {'mode': 'free', 'snap': 16}
        self.animation_mode = 'restrained'
        self.scene_scope = None
        self.focus_index = -1
        self._focus_levels = {}
        self._positions = {}
        self._from_positions = {}
        self._motion_start = self._entrance_start = self._pressed_start = 0
        self._pressed_id = ''
        self._last_tick = time.monotonic()
        self._pending_click = None
        self.timer = QTimer(self); self.timer.setInterval(16); self.timer.timeout.connect(self._tick)
        self.inline = QLineEdit(self)
        self.inline.hide()
        self.inline.setMaxLength(120)
        self.inline.installEventFilter(self)
        self.inline.editingFinished.connect(self.commit_edit)

    def target_rect(self):
        size = QSize(1920, 1080).scaled(self.size(), Qt.KeepAspectRatio)
        return QRect((self.width()-size.width())//2, (self.height()-size.height())//2, size.width(), size.height())

    def display_rect(self, rect):
        target = self.target_rect()
        return QRect(target.x()+round(rect.x()*target.width()/1920),
                     target.y()+round(rect.y()*target.height()/1080),
                     round(rect.width()*target.width()/1920), round(rect.height()*target.height()/1080))

    def canvas_point(self, point):
        rect = self.target_rect()
        return QPointF((point.x()-rect.x())*1920/rect.width(), (point.y()-rect.y())*1080/rect.height())

    def handles(self, element):
        if element.get('locked_geometry'):
            return {}
        r = self.display_rect(element['rect'])
        return {key: QRect(x-5, y-5, 10, 10) for key, x, y in (
            ('nw', r.left(), r.top()), ('n', r.center().x(), r.top()), ('ne', r.right(), r.top()),
            ('w', r.left(), r.center().y()), ('e', r.right(), r.center().y()),
            ('sw', r.left(), r.bottom()), ('s', r.center().x(), r.bottom()), ('se', r.right(), r.bottom()))}

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self.commit_edit()
        self.setFocus()
        point = event.position()
        element = next((e for e in self.elements if e['id'] == self.selection), None)
        edge = ''
        if element:
            edge = next((k for k, r in self.handles(element).items() if r.contains(point.toPoint())), '')
        if not edge:
            pos = self.canvas_point(point)
            element = next((e for e in reversed(self.elements) if e['rect'].contains(pos.toPoint())), None)
        if element is None:
            self.selection = ''
            self.selected_keys.clear()
            self.selected.emit('')
            self.update()
            return
        self.selection = element['id']
        if event.modifiers() & Qt.ControlModifier and not self.view_only:
            if self.selection in self.selected_keys:
                self.selected_keys.remove(self.selection)
            else:
                self.selected_keys.add(self.selection)
        else:
            self.selected_keys = {self.selection}
        self.selected.emit(self.selection)
        if self.view_only:
            if element.get('button'):
                if not element['style'].get('disabled'):
                    self._pending_click = element['id']
            self.selection = ''
            self.update()
            return
        self.drag = None if element.get('locked_geometry') else dict(element=copy.deepcopy(element), start=point, rect=QRect(element['rect']), edge=edge, moved=False, ghost=None)
        self.update()

    def mouseMoveEvent(self, event):
        if self.drag is None:
            if self.view_only:
                pos = self.canvas_point(event.position()).toPoint()
                hover = next((e for e in self.elements if e['button'] and e['rect'].contains(pos) and not e['style'].get('disabled')), None)
                if hover and hover['index'] != self.focus_index:
                    self.focus_requested.emit(hover['index'])
            selected = next((e for e in self.elements if e['id'] == self.selection), None)
            edge = next((k for k, r in self.handles(selected).items() if r.contains(event.position().toPoint())), '') if selected else ''
            cursor = Qt.SizeFDiagCursor if edge in ('nw', 'se') else Qt.SizeBDiagCursor if edge in ('ne', 'sw') else Qt.SizeHorCursor if edge in ('w', 'e') else Qt.SizeVerCursor if edge else Qt.ArrowCursor
            self.setCursor(cursor)
            return
        delta = event.position()-self.drag['start']
        if not self.drag['moved'] and abs(delta.x())+abs(delta.y()) < QApplication.startDragDistance():
            return
        self.drag['moved'] = True
        self.setCursor(Qt.ClosedHandCursor)
        factor = 1920/self.target_rect().width()
        dx, dy = round(delta.x()*factor), round(delta.y()*factor)
        r = self.drag['rect']
        x, y, w, h = r.x(), r.y(), r.width(), r.height()
        edge = self.drag['edge']
        if not edge:
            x, y = max(0, min(1920-w, x+dx)), max(0, min(1080-h, y+dy))
        else:
            if 'e' in edge:
                w = max(32, min(1920-x, w+dx))
            if 's' in edge:
                h = max(32, min(1080-y, h+dy))
            if 'w' in edge:
                x = max(0, min(r.x()+r.width()-32, x+dx))
                w = r.x()+r.width()-x
            if 'n' in edge:
                y = max(0, min(r.y()+r.height()-32, y+dy))
                h = r.y()+r.height()-y
        others = [[e['rect'].x(), e['rect'].y(), e['rect'].width(), e['rect'].height()]
                  for e in self.elements if e['id'] != self.selection]
        rect = [x, y, w, h]
        self.drag['ghost'] = rect
        if self.drag['element'].get('button') and not edge and self.layout_config['mode'] != 'free':
            pos = self.canvas_point(event.position())
            buttons = [e for e in self.elements if e['button']]
            target = min(buttons, key=lambda e: (e['rect'].center().x()-pos.x())**2 + (e['rect'].center().y()-pos.y())**2)
            if target['id'] != self.selection:
                self.reorder_requested.emit(self.selection, target['index'])
            self.update()
            return
        if self.snapping and not event.modifiers() & Qt.AltModifier:
            if self.scene_scope in getattr(self, 'explicit_scopes', set()) and not edge:
                step = self.layout_config['snap']
                rect[0], rect[1] = round(rect[0]/step)*step, round(rect[1]/step)*step
            rect, self.guides = snap_rect(rect, others, threshold=6*factor, resize_edges=edge)
        else:
            self.guides = []
        size = 0
        if edge and self.selection.startswith('text:'):
            size = max(12, min(160, round(self.drag['element']['style']['font_size']*rect[3]/r.height())))
        self.geometry_changed.emit(self.selection, rect, size)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.view_only and self._pending_click:
            point = self.canvas_point(event.position()).toPoint()
            element = next((e for e in self.elements if e['id'] == self._pending_click), None)
            if element and element['rect'].contains(point):
                self.flash_pressed()
                self.navigate.emit('enter')
        self._pending_click = None
        moved = self.drag and self.drag['moved']
        self.drag = None
        self.guides = []
        self.setCursor(Qt.ArrowCursor)
        if moved:
            self.gesture_finished.emit()
        self.update()

    def mouseDoubleClickEvent(self, event):
        if self.view_only:
            return
        if event.button() != Qt.LeftButton:
            return
        self.commit_edit()
        point = self.canvas_point(event.position()).toPoint()
        element = next((e for e in reversed(self.elements) if e['rect'].contains(point)), None)
        if element and element.get('image'):
            return
        element = next((e for e in reversed(self.elements) if e['text_rect'].contains(point)), None)
        if element is None or element.get('locked_text'):
            return
        self.selection = element['id']
        self.selected.emit(self.selection)
        self.drag = None
        self.guides = []
        self.edit_key = element['key']
        self.original_text = element['text']
        self.inline.setMaxLength(80 if self.edit_key == 'title' or self.edit_key.startswith('movie:') else 120)
        self.inline.setText(element['text'])
        rect = self.display_rect(element['rect'])
        rect.setHeight(max(28, rect.height()))
        self.inline.setGeometry(rect)
        self.inline.show()
        self.inline.setFocus()
        self.inline.selectAll()
        self.update()

    def commit_edit(self):
        if self.edit_key is None:
            return
        key, value = self.edit_key, self.inline.text().strip()
        self.edit_key = None
        self.inline.hide()
        self.text_committed.emit(key, value)

    def eventFilter(self, source, event):
        if source is self.inline and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                self.edit_key = None
                self.inline.hide()
                self.setFocus()
                return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.commit_edit()
                self.setFocus()
                return True
        return super().eventFilter(source, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete and self.selection and not self.view_only:
            self.delete_requested.emit()
            return
        action = {Qt.Key_Up: 'up', Qt.Key_Down: 'down', Qt.Key_Left: 'left', Qt.Key_Right: 'right',
                  Qt.Key_Return: 'enter', Qt.Key_Enter: 'enter', Qt.Key_Backspace: 'back', Qt.Key_Home: 'home'}.get(event.key())
        if action:
            self.navigate.emit(action)
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        self.commit_edit()
        super().resizeEvent(event)

    def paintEvent(self, event):
        if self.image is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        target = self.target_rect()
        painter.drawImage(target, self.scene_background if self.scene_background is not None else self.image)
        if self.scene_background is not None:
            from disc_menu import paint_button, paint_focus
            from episode_buttons import ANIMATIONS
            duration, stagger = ANIMATIONS[self.animation_mode]
            painter.save(); painter.translate(target.x(), target.y()); painter.scale(target.width()/1920, target.height()/1080)
            painter.setRenderHint(QPainter.Antialiasing)
            now = time.monotonic()
            progress = 1 if not duration else min(1, (now-self._motion_start)*1000/duration)
            eased = 1-(1-progress)**3
            for e in self.elements:
                if not e['button']:
                    continue
                element = dict(e)
                rect = e['rect']; end = [rect.x(), rect.y(), rect.width(), rect.height()]
                start = self._from_positions.get(e['id'], end)
                live = [round(a+(b-a)*eased) for a, b in zip(start, end)]
                self._positions[e['id']] = live
                element['rect'] = live
                reveal = 1 if not duration or not self.view_only else max(0, min(1, ((now-self._entrance_start)*1000-e['index']*stagger)/duration))
                painter.save(); painter.setOpacity(reveal)
                if self.drag and self.drag['moved'] and self.drag['element']['id'] == e['id']:
                    painter.setOpacity(reveal*0.25)
                paint_button(painter, element)
                level = self._focus_levels.get(e['id'], 0)
                if self._pressed_id == e['id'] and now-self._pressed_start < 0.12:
                    level *= 0.35
                paint_focus(painter, element, level)
                painter.restore()
            if self.drag and self.drag['moved'] and self.drag['ghost']:
                ghost = dict(self.drag['element'], rect=self.drag['ghost'])
                painter.save(); painter.setOpacity(0.72); paint_button(painter, ghost); paint_focus(painter, ghost); painter.restore()
                if self.layout_config['mode'] != 'free':
                    slot = next((e['rect'] for e in self.elements if e['id'] == self.selection), None)
                    if slot:
                        painter.setPen(QPen(QColor('#ff9f38'), 4)); painter.drawLine(slot.topLeft(), slot.bottomLeft())
            painter.restore()
        for element in self.elements:
            if not self.view_only and (element['id'] == self.selection or element['id'] in self.selected_keys):
                painter.setPen(QPen(QColor('#ffce70'), 1, Qt.DashLine))
                painter.drawRect(self.display_rect(element['rect']))
                painter.setBrush(QColor('#ffce70'))
                for rect in self.handles(element).values():
                    painter.drawRect(rect)
        target = self.target_rect()
        painter.setPen(QPen(QColor('#65e6f5'), 1, Qt.DashLine))
        for axis, value in self.guides:
            if axis == 0:
                x = target.x()+round(value*target.width()/1920)
                painter.drawLine(x, target.top(), x, target.bottom())
            else:
                y = target.y()+round(value*target.height()/1080)
                painter.drawLine(target.left(), y, target.right(), y)
        painter.end()

    def set_scene(self, background, focus, scope, animation, config):
        self.scene_background = background
        self.focus_index = focus
        self.animation_mode = animation
        self.layout_config = config
        now = time.monotonic()
        if not self.timer.isActive():
            self._last_tick = now
        targets = {e['id']: [e['rect'].x(), e['rect'].y(), e['rect'].width(), e['rect'].height()] for e in self.elements if e['button']}
        if targets != getattr(self, '_targets', {}):
            self._from_positions = copy.deepcopy(self._positions)
            self._motion_start = now
            self._targets = targets
        if self.scene_scope != scope:
            self.scene_scope = scope
            self.start_entrance()
        if animation == 'none':
            self._focus_levels = {e['id']: float(e['index'] == focus) for e in self.elements if e['button']}
        self.timer.start()

    def start_entrance(self):
        self._entrance_start = time.monotonic()
        self.timer.start()

    def flash_pressed(self):
        self._pressed_id = next((e['id'] for e in self.elements if e['button'] and e['index'] == self.focus_index), '')
        self._pressed_start = time.monotonic()
        self.timer.start()

    def _tick(self):
        from episode_buttons import ANIMATIONS
        now = time.monotonic()
        duration, stagger = ANIMATIONS[self.animation_mode]
        step = 1 if not duration else min(1, (now-self._last_tick)*1000/duration)
        self._last_tick = now
        active = False
        for e in self.elements:
            if not e['button']:
                continue
            target = float(e['index'] == self.focus_index and not e['style'].get('disabled'))
            level = self._focus_levels.get(e['id'], 0)
            level = min(target, level+step) if target > level else max(target, level-step)
            self._focus_levels[e['id']] = level
            active |= level != target
        active |= bool(duration and (now-self._motion_start)*1000 < duration)
        button_indices = [e['index'] for e in self.elements if e['button']]
        active |= bool(duration and self.view_only and button_indices and
                       (now-self._entrance_start)*1000 < duration+max(button_indices)*stagger)
        active |= now-self._pressed_start < 0.12
        self.update()
        if not active:
            self.timer.stop()
