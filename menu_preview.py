"""Interactive canvas; pointer coordinates are mapped through the letterboxed image."""
from PySide6.QtCore import Qt, Signal, QSize, QRect, QEvent, QPointF
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget, QLineEdit, QApplication
from menu_layout import snap_rect


class MenuPreview(QWidget):
    selected = Signal(str)
    text_committed = Signal(str, str)
    delete_requested = Signal()
    geometry_changed = Signal(str, list, int)
    navigate = Signal(str)

    def __init__(self):
        super().__init__()
        self.image = None
        self.setMinimumSize(300, 169)
        self.view_only = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.elements = []
        self.selection = ''
        self.edit_key = None
        self.drag = None
        self.guides = []
        self.snapping = True
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
            self.selected.emit('')
            self.update()
            return
        self.selection = element['id']
        self.selected.emit(self.selection)
        if self.view_only:
            if element.get('button'):
                self.navigate.emit('enter')
            self.selection = ''
            self.update()
            return
        self.drag = None if element.get('locked_geometry') else dict(element=element, start=point, rect=QRect(element['rect']), edge=edge, moved=False)
        self.update()

    def mouseMoveEvent(self, event):
        if self.drag is None:
            selected = next((e for e in self.elements if e['id'] == self.selection), None)
            edge = next((k for k, r in self.handles(selected).items() if r.contains(event.position().toPoint())), '') if selected else ''
            cursor = Qt.SizeFDiagCursor if edge in ('nw', 'se') else Qt.SizeBDiagCursor if edge in ('ne', 'sw') else Qt.SizeHorCursor if edge in ('w', 'e') else Qt.SizeVerCursor if edge else Qt.ArrowCursor
            self.setCursor(cursor)
            return
        delta = event.position()-self.drag['start']
        if not self.drag['moved'] and abs(delta.x())+abs(delta.y()) < QApplication.startDragDistance():
            return
        self.drag['moved'] = True
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
        if self.snapping and not event.modifiers() & Qt.AltModifier:
            rect, self.guides = snap_rect(rect, others, threshold=6*factor, resize_edges=edge)
        else:
            self.guides = []
        size = 0
        if edge and self.selection.startswith('text:'):
            size = max(12, min(160, round(self.drag['element']['style']['font_size']*rect[3]/r.height())))
        self.geometry_changed.emit(self.selection, rect, size)
        self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.guides = []
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
        painter.drawImage(self.target_rect(), self.image)
        for element in self.elements:
            if element['id'] == self.selection:
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
