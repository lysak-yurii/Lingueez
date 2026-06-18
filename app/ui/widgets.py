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
from PySide6.QtCore import (
    QEasingCurve, QEvent, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLayout,
    QLineEdit, QMenu, QPushButton, QSizePolicy, QStyle, QStyleOptionComboBox,
    QToolButton, QWidget,
)

from app.i18n import tr
from app.ui import icons


class SearchField(QWidget):
    """Search input that, in compact mode, collapses to a round icon button and
    expands rightward into a full field (animated) when clicked — the pattern
    used by many desktop apps. In normal mode it is just a full-width box.

    `expandedChanged(bool)` fires when it opens/closes while compact, so the host
    can free room (e.g. hide the window title) during search.
    """

    COLLAPSED_W = 36
    OPEN_MIN = 150  # keep the open field wide enough to read what you type
    expandedChanged = Signal(bool)

    def __init__(self, colors, placeholder="", parent=None):
        super().__init__(parent)
        self._colors = colors
        self._compact = False
        self._expanded = True
        self._max_expanded = 560

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.line_edit = QLineEdit(objectName="SearchBox")
        self.line_edit.setPlaceholderText(placeholder)
        self.line_edit.setClearButtonEnabled(True)
        self._icon_action = self.line_edit.addAction(
            icons.icon("search", colors["text_dim"], 16), QLineEdit.LeadingPosition)
        self.line_edit.installEventFilter(self)
        row.addWidget(self.line_edit)

        self.icon_btn = QPushButton(objectName="iconButton")
        self.icon_btn.setIcon(icons.icon("search", colors["text_dim"], 18))
        self.icon_btn.setIconSize(QSize(18, 18))
        self.icon_btn.setCursor(Qt.PointingHandCursor)
        self.icon_btn.setToolTip(placeholder)
        self.icon_btn.clicked.connect(self._open)
        self.icon_btn.hide()
        row.addWidget(self.icon_btn)

        self._anim = QPropertyAnimation(self, b"maximumWidth", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    # --- public API -------------------------------------------------------
    def set_max_expanded(self, width):
        self._max_expanded = width
        if self._expanded and not self._compact:
            self.setMaximumWidth(width)

    def set_compact(self, compact):
        if compact == self._compact:
            return
        self._compact = compact
        self._sync(animate=False)

    def is_open(self):
        """True when the full field (not the icon) is showing while compact."""
        return self._compact and self._expanded

    # --- state ------------------------------------------------------------
    def _want_expanded(self):
        if not self._compact:
            return True
        return self.line_edit.hasFocus() or bool(self.line_edit.text())

    def _open(self):
        self._set_expanded(True, animate=True)
        self.line_edit.setFocus()

    def _sync(self, animate):
        self._set_expanded(self._want_expanded(), animate=animate)

    def _set_expanded(self, expanded, animate):
        was = self._expanded
        self._expanded = expanded
        if expanded:
            self.icon_btn.hide()
            self.line_edit.show()
            target = self._max_expanded
        else:
            self.line_edit.hide()
            self.icon_btn.show()
            target = self.COLLAPSED_W
        self._animate(target, animate)
        self.updateGeometry()  # minimum changes between collapsed and open
        if self._compact and expanded != was:
            self.expandedChanged.emit(expanded)

    def _animate(self, target, animate):
        self._anim.stop()
        if animate and self.isVisible():
            self._anim.setStartValue(self.maximumWidth())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self.setMaximumWidth(target)

    def minimumSizeHint(self):
        if self._compact:  # icon-sized when idle, readable once opened
            w = self.OPEN_MIN if self._expanded else self.COLLAPSED_W
            return QSize(w, super().minimumSizeHint().height())
        return super().minimumSizeHint()

    def eventFilter(self, obj, ev):
        if obj is self.line_edit and ev.type() == QEvent.FocusOut:
            QTimer.singleShot(0, lambda: self._sync(animate=True))
        return False

    def refresh_theme(self, colors):
        self._colors = colors
        self._icon_action.setIcon(icons.icon("search", colors["text_dim"], 16))
        self.icon_btn.setIcon(icons.icon("search", colors["text_dim"], 18))


class ContentComboBox(QComboBox):
    """Combo that sizes to its *current* text — not the widest item in the list,
    which is what `AdjustToContents` does and what makes a short selection (e.g.
    one language) hog space because some other option is long. The drop-down
    popup still widens to show every option in full. A small minimum lets a
    FlowLayout truncate or wrap it when space is tight.
    """

    def __init__(self, parent=None, min_chars=4):
        super().__init__(parent)
        self._min_chars = min_chars  # how small it may shrink before truncating
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.currentTextChanged.connect(self.updateGeometry)

    def _size_for(self, text):
        self.ensurePolished()
        fm = self.fontMetrics()
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        content = QSize(fm.horizontalAdvance(text) + 4, fm.height())
        return self.style().sizeFromContents(QStyle.CT_ComboBox, opt, content, self)

    def sizeHint(self):
        return self._size_for(self.currentText())

    def minimumSizeHint(self):
        return self._size_for("o" * self._min_chars)  # small, truncatable floor

    def showPopup(self):
        # Widen the list to its longest entry so full names stay readable.
        fm = self.view().fontMetrics()
        widest = max((fm.horizontalAdvance(self.itemText(i))
                      for i in range(self.count())), default=0)
        self.view().setMinimumWidth(widest + 48)
        super().showPopup()


class FlowLayout(QLayout):
    """Left-to-right layout that lets each item truncate into the remaining row
    space (down to its minimum width) before wrapping onto a new row — so a row
    of controls compresses a little, then reflows, instead of clipping. Its
    minimum width is the widest single item's minimum, keeping the host
    narrow-friendly.
    """

    def __init__(self, parent=None, hspacing=8, vspacing=6):
        super().__init__(parent)
        self._items = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(0, 0, 0, 0)

    # --- QLayout plumbing --------------------------------------------------
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # --- wrapping ----------------------------------------------------------
    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        left = rect.x() + m.left()
        right = rect.right() - m.right()
        x = left
        y = rect.y() + m.top()
        row = []            # (item, x, width, height) buffered to center vertically
        row_height = 0

        def place_row():
            nonlocal y
            for item, ix, iw, ih in row:
                if not test_only:
                    off = max(0, (row_height - ih) // 2)  # center within the row
                    item.setGeometry(QRect(QPoint(ix, y + off), QSize(iw, ih)))
            y += row_height + self._vspace

        for item in self._items:
            hint = item.sizeHint()
            mn = item.minimumSize().width()
            avail = right - x
            if row and avail < mn:               # even truncated it won't fit: wrap
                place_row()
                row, x, row_height = [], left, 0
                avail = right - x
            w = max(mn, min(hint.width(), avail))  # truncate into the room we have
            row.append((item, x, w, hint.height()))
            x += w + self._hspace
            row_height = max(row_height, hint.height())
        if row:
            place_row()
        return y - self._vspace + m.bottom() - rect.y()


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


class OverflowToolBar(QWidget):
    """A horizontal row of icon buttons that collapses low-priority buttons
    into a trailing "⋯" menu when there isn't room to show them all.

    Callers create the buttons as usual and keep their references (signals stay
    connected); only visibility changes with width. The widget reports a minimum
    width of just one button plus the overflow control, so — like `ElidedLabel`
    — it never pins its container wide.
    """

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._colors = colors
        self._items = []  # [{"btn": QAbstractButton, "prio": int}], in display order
        self._relaying = False

        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(4)

        self._menu = QMenu(self)
        self._overflow = QPushButton(objectName="iconButton")
        self._overflow.setIcon(icons.icon("more", colors["text"], 18))
        self._overflow.setIconSize(QSize(18, 18))
        self._overflow.setToolTip(tr("More actions"))
        self._overflow.setCursor(Qt.PointingHandCursor)
        self._overflow.setMenu(self._menu)
        self._overflow.hide()
        self._row.addWidget(self._overflow)

    def add_button(self, button, priority=0):
        """Add an action button. Lower `priority` buttons collapse into the
        menu first when space runs out; display order follows insertion order."""
        self._row.insertWidget(self._row.count() - 1, button)  # before overflow
        self._items.append({"btn": button, "prio": priority})

    # --- sizing -----------------------------------------------------------
    def sizeHint(self):
        # Preferred: every button shown, overflow hidden.
        sp = self._row.spacing()
        w = sum(it["btn"].sizeHint().width() for it in self._items)
        w += sp * max(0, len(self._items) - 1)
        return QSize(w, self._row.sizeHint().height())

    def minimumSizeHint(self):
        # Collapse all the way down to just the "⋯" control, so a neighbouring
        # widget (e.g. the reader title) can claim the rest when space is tight.
        if not self._items:
            return QSize(0, self._row.sizeHint().height())
        ow = self._overflow.sizeHint().width()
        return QSize(ow, self._row.sizeHint().height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        if self._relaying or not self._items:
            return
        self._relaying = True
        avail = self.width()
        sp = self._row.spacing()
        widths = {id(it["btn"]): it["btn"].sizeHint().width() for it in self._items}
        full = sum(widths.values()) + sp * (len(self._items) - 1)
        if full <= avail:
            hidden = []
        else:
            reserve = self._overflow.sizeHint().width() + sp
            used, keep = 0, set()
            for it in sorted(self._items, key=lambda it: -it["prio"]):
                w = widths[id(it["btn"])] + sp
                if used + w + reserve <= avail:
                    used += w
                    keep.add(id(it["btn"]))
                else:
                    break
            hidden = [it for it in self._items if id(it["btn"]) not in keep]
        self._apply(hidden)
        self._relaying = False

    def _apply(self, hidden):
        hidden_ids = {id(it["btn"]) for it in hidden}
        for it in self._items:
            it["btn"].setVisible(id(it["btn"]) not in hidden_ids)
        self._menu.clear()
        for it in hidden:  # keep display order in the menu
            btn = it["btn"]
            act = self._menu.addAction(btn.icon(), btn.toolTip())
            if btn.isCheckable():
                act.setCheckable(True)
                act.setChecked(btn.isChecked())
            act.triggered.connect(lambda _=False, b=btn: b.click())
        self._overflow.setVisible(bool(hidden))

    def refresh_theme(self, colors):
        """Re-tint the overflow control and rebuild the menu (icons may have
        changed). Call after the member buttons have themselves been re-tinted."""
        self._colors = colors
        self._overflow.setIcon(icons.icon("more", colors["text"], 18))
        self._relayout()


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
            clear.setToolTip(tr("No color"))
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
            self._button.setText(tr("None"))
            self._button.setStyleSheet("")

    def _pick(self):
        current = self._color if self._color.isValid() else QColor("#ffffff")
        picked = QColorDialog.getColor(current, self, tr("Choose Color"))
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
                grid.addWidget(QLabel(tr("in")), i, 2)
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
