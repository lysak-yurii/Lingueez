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

"""Small reusable widgets."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QToolButton, QWidget,
)


class ElidedLabel(QLabel):
    """Single-line label that elides its text and exposes it as a tooltip.

    Unlike a plain QLabel it never enforces the full text width as a
    layout minimum, so it can be squeezed without growing the window.
    """

    def __init__(self, parent=None, min_width=24):
        super().__init__(parent)
        self._full = ""
        self._min_width = min_width

    def minimumSizeHint(self):
        return QSize(self._min_width, super().minimumSizeHint().height())

    def sizeHint(self):
        # Preferred width tracks the FULL text (not the currently elided text),
        # so a layout keeps offering room to show the whole word; the small
        # minimumSizeHint still lets it be squeezed. Without this, once the
        # label elides to nothing it would report a ~0 hint and never grow back.
        fm = self.fontMetrics()
        # margin must exceed _refit's 2px so the full text fits without eliding
        width = fm.horizontalAdvance(self._full) + 8
        return QSize(max(self._min_width, width), super().sizeHint().height())

    def set_full_text(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self.updateGeometry()  # sizeHint depends on _full; re-query the layout
        self._refit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refit()

    def _refit(self):
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self._full, Qt.ElideRight, max(0, self.width() - 2)))


class ColorButton(QWidget):
    """Color swatch button opening a QColorDialog; optionally clearable.

    `color()` returns "#rrggbb", or "" when cleared (clearable only).
    """

    def __init__(self, value="", clearable=False, parent=None):
        super().__init__(parent)
        self._color = QColor(str(value))
        self._button = QPushButton()
        self._button.setFixedSize(90, 26)
        self._button.setCursor(Qt.PointingHandCursor)
        self._button.clicked.connect(self._pick)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self._button)
        if clearable:
            clear = QToolButton()
            clear.setText("✕")
            clear.setToolTip("No color")
            clear.clicked.connect(self._clear)
            row.addWidget(clear)
        row.addStretch(1)
        self._refresh()

    def _refresh(self):
        if self._color.isValid():
            name = self._color.name()
            text_color = "#000000" if self._color.lightness() > 127 else "#ffffff"
            self._button.setText(name)
            self._button.setStyleSheet(
                f"background-color: {name}; color: {text_color}; border: 1px solid #888;")
        else:
            self._button.setText("None")
            self._button.setStyleSheet("")

    def _pick(self):
        current = self._color if self._color.isValid() else QColor("#ffffff")
        picked = QColorDialog.getColor(current, self, "Choose Color")
        if picked.isValid():
            self._color = picked
            self._refresh()

    def _clear(self):
        self._color = QColor()
        self._refresh()

    def color(self):
        return self._color.name() if self._color.isValid() else ""


class ColumnPicker(QWidget):
    """Checkbox list of export columns, optionally with a width spinbox each.

    `columns` is [(internal_name, label)]; `exclude_csv` the stored CSV of
    internal names to exclude. Unknown tokens are dropped on save.
    """

    def __init__(self, columns, exclude_csv, width_spins=None, parent=None):
        super().__init__(parent)
        self._columns = list(columns)
        self._spins = width_spins or {}
        self._widths_enabled = True
        self._checks = {}
        excluded = {t.strip() for t in str(exclude_csv).split(',')}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        for i, (internal, label) in enumerate(self._columns):
            check = QCheckBox(label)
            check.setChecked(internal not in excluded)
            self._checks[internal] = check
            if self._spins:
                spin = self._spins[internal]
                check.toggled.connect(lambda _on, c=internal: self._sync_spin(c))
                grid.addWidget(check, i, 0)
                grid.addWidget(spin, i, 1)
                grid.addWidget(QLabel("in"), i, 2)
            else:
                grid.addWidget(check, i % ((len(self._columns) + 1) // 2),
                               i // ((len(self._columns) + 1) // 2))
        grid.setColumnStretch(grid.columnCount(), 1)
        for internal in self._spins:
            self._sync_spin(internal)

    def _sync_spin(self, internal):
        self._spins[internal].setEnabled(
            self._widths_enabled and self._checks[internal].isChecked())

    def set_widths_enabled(self, enabled):
        self._widths_enabled = enabled
        for internal in self._spins:
            self._sync_spin(internal)

    def exclude_csv(self):
        return ",".join(internal for internal, _ in self._columns
                        if not self._checks[internal].isChecked())
