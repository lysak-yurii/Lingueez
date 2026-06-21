# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Additional terms under AGPL-3.0 section 7 apply to this program; see the
# NOTICE file distributed with this source for details.
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Frameless dialog base with an integrated title bar matching the app."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from app.i18n import tr
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
        cancel = QPushButton(tr("Cancel"))
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        ok = QPushButton(tr("OK"), objectName="primaryButton")
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


def quiet_frame(colors):
    """A single soft, rounded container that holds a short list of ``quiet_row``s —
    the shared 'quiet frame' used by the sync dialogs. Returns ``(frame, vlayout)``;
    add rows to the layout, then add the frame to your content layout."""
    frame = QFrame(objectName="QuietFrame")
    frame.setStyleSheet(
        f"#QuietFrame{{background:{colors['surface_alt']};"
        f" border:1px solid {colors['border']}; border-radius:10px;}}")
    v = QVBoxLayout(frame)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    return frame, v


def quiet_row(colors, primary, trailing="", *, leading="·", icon=None, divider=True):
    """One row for a ``quiet_frame``: an optional leading glyph (icon name) or text
    bullet, the primary text, and a right-aligned dim trailing label. A hairline
    divider sits on the bottom edge — pass ``divider=False`` for the last row so it
    doesn't double up with the frame border."""
    row = QFrame(objectName="QuietRow")
    row.setStyleSheet(
        f"#QuietRow{{border-bottom:1px solid {colors['border']};}}" if divider
        else "#QuietRow{border:none;}")
    h = QHBoxLayout(row)
    h.setContentsMargins(14, 11, 14, 11)
    h.setSpacing(11)
    if icon:
        glyph = QLabel()
        glyph.setPixmap(icons.icon(icon, colors["text_dim"], 15).pixmap(QSize(15, 15)))
        h.addWidget(glyph, 0, Qt.AlignVCenter)
    elif leading:
        dot = QLabel(leading)
        dot.setStyleSheet(f"color:{colors['text_dim']};")
        h.addWidget(dot, 0, Qt.AlignVCenter)
    label = QLabel(str(primary))
    label.setStyleSheet(f"color:{colors['text']}; font-size:13px;")
    h.addWidget(label, 1)
    if str(trailing) != "":
        tl = QLabel(str(trailing))
        tl.setStyleSheet(f"color:{colors['text_dim']}; font-size:11.5px;")
        h.addWidget(tl, 0, Qt.AlignVCenter)
    return row


def confirm(parent, title, message, *, ok_text=None, cancel_text=None,
            danger=False, rows=None):
    """Themed (frameless) yes/no confirmation — a drop-in for QMessageBox.question
    that matches the app instead of the OS's native chrome + giant icon.

    *rows*: optional summary shown as a tidy grid under the message — each item is
    ``(label, value)`` or ``(icon_name, label, value)`` (icon from ``app.ui.icons``),
    e.g. an Upload/Download/Remove summary. Returns True on accept.
    """
    dialog = FramelessDialog(parent, title=title)
    dialog.setMinimumWidth(360)
    if message:
        text = QLabel(message)
        text.setWordWrap(True)
        dialog.content_layout.addWidget(text)
    if rows:
        frame, v = quiet_frame(dialog.colors)
        last = len(rows) - 1
        for i, item in enumerate(rows):
            icon_name, label, value = item if len(item) == 3 else (None, *item)
            v.addWidget(quiet_row(dialog.colors, label, value, icon=icon_name,
                                  leading=None, divider=(i < last)))
        dialog.content_layout.addWidget(frame)

    row = QHBoxLayout()
    row.addStretch(1)
    # Escape '&' so it shows literally instead of becoming a Qt mnemonic.
    cancel = QPushButton((cancel_text or tr("Cancel")).replace("&", "&&"))
    cancel.setCursor(Qt.PointingHandCursor)
    cancel.clicked.connect(dialog.reject)
    row.addWidget(cancel)
    ok = QPushButton((ok_text or tr("OK")).replace("&", "&&"),
                     objectName="dangerButton" if danger else "primaryButton")
    ok.setCursor(Qt.PointingHandCursor)
    ok.setDefault(True)
    ok.clicked.connect(dialog.accept)
    row.addWidget(ok)
    dialog.content_layout.addLayout(row)
    return dialog.exec() == QDialog.Accepted
