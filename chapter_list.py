"""Edit chapter names without exposing the timestamp as editable text."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit, QListWidget, QStyledItemDelegate


CHAPTER_NAME_ROLE = Qt.UserRole + 1
CHAPTER_OWNER_ROLE = Qt.UserRole + 2


class ChapterList(QListWidget):
    """Keep F2 editing available independently of the platform's EditKey binding."""
    def keyPressEvent(self, event):
        item = self.currentItem()
        if (event.key() == Qt.Key_F2 and event.modifiers() == Qt.NoModifier
                and self.isEnabled() and item is not None and item.flags() & Qt.ItemIsEditable):
            self.editItem(item)
            event.accept()
            return
        super().keyPressEvent(event)


class ChapterNameDelegate(QStyledItemDelegate):
    renamed = Signal(object, str)

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setObjectName('chapterNameEdit')
        editor.setAccessibleName('Название главы')
        editor.setMaxLength(120)
        return editor

    def setEditorData(self, editor, index):
        editor.setText(index.data(CHAPTER_NAME_ROLE) or '')
        editor.selectAll()

    def setModelData(self, editor, model, index):
        self.renamed.emit(index, editor.text())
