"""Edit chapter names without exposing the timestamp as editable text."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit, QStyledItemDelegate


CHAPTER_NAME_ROLE = Qt.UserRole + 1
CHAPTER_OWNER_ROLE = Qt.UserRole + 2


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
