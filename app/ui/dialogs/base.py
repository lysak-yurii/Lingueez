"""Frameless dialog base with an integrated title bar matching the app."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.ui import icons, theme
from app.ui.titlebar import DragArea, FramelessResizer


class FramelessDialog(QDialog):
    """QDialog with client-side decorations: draggable header + close button.

    Subclasses build their UI in ``self.content_layout``.
    """

    def __init__(self, parent=None, title=""):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.colors = theme.current_colors()

        # Edge resizing (the filter dies with the dialog, removing itself)
        self._resizer = FramelessResizer(self)
        QApplication.instance().installEventFilter(self._resizer)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = DragArea(objectName="DialogTitleBar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(18, 8, 8, 8)
        self._title_label = QLabel(title, objectName="DialogTitle")
        bl.addWidget(self._title_label)
        bl.addStretch(1)
        close_btn = QPushButton(objectName="winBtnClose")
        close_btn.setIcon(icons.icon("x", self.colors["text_dim"], 15))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.setFocusPolicy(Qt.NoFocus)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        bl.addWidget(close_btn)
        outer.addWidget(bar)

        body = QWidget()
        self.content_layout = QVBoxLayout(body)
        self.content_layout.setContentsMargins(20, 18, 20, 16)
        self.content_layout.setSpacing(12)
        outer.addWidget(body, 1)

        super().setWindowTitle(title)

    def setWindowTitle(self, title):
        super().setWindowTitle(title)
        self._title_label.setText(title)


class _InputDialog(FramelessDialog):
    """One-field prompt used by the ask_int/ask_item/ask_text helpers."""

    def __init__(self, parent, title, label, editor):
        super().__init__(parent, title=title)
        self.setMinimumWidth(380)
        prompt = QLabel(label)
        prompt.setWordWrap(True)
        self.content_layout.addWidget(prompt)
        self.content_layout.addWidget(editor)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton("OK", objectName="primaryButton")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        self.content_layout.addLayout(row)
        editor.setFocus()


def ask_int(parent, title, label, value=0, minimum=0, maximum=2147483647):
    """Frameless replacement for QInputDialog.getInt(). Returns (value, ok)."""
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    dialog = _InputDialog(parent, title, label, spin)
    spin.selectAll()
    ok = dialog.exec() == QDialog.Accepted
    return spin.value(), ok


def ask_item(parent, title, label, items, current=0, editable=False):
    """Frameless replacement for QInputDialog.getItem(). Returns (text, ok)."""
    combo = QComboBox()
    combo.addItems(list(items))
    combo.setEditable(editable)
    combo.setCurrentIndex(current)
    dialog = _InputDialog(parent, title, label, combo)
    ok = dialog.exec() == QDialog.Accepted
    return combo.currentText(), ok


def ask_text(parent, title, label, text=""):
    """Frameless replacement for QInputDialog.getText(). Returns (text, ok)."""
    edit = QLineEdit(text)
    dialog = _InputDialog(parent, title, label, edit)
    edit.returnPressed.connect(dialog.accept)
    ok = dialog.exec() == QDialog.Accepted
    return edit.text(), ok
