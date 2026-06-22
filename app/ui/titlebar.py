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

"""Client-side window decorations: integrated min/max/close controls,
drag-to-move header and frameless edge resizing."""
from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from app.i18n import tr
from app.ui import icons

RESIZE_MARGIN = 7


class WindowControls(QWidget):
    """Minimize / maximize-restore / close buttons for a frameless window."""

    def __init__(self, window, colors):
        super().__init__(window)
        self._window = window
        self._colors = colors

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.min_btn = self._button("win-min", tr("Minimize"), window.showMinimized)
        self.max_btn = self._button("win-max", tr("Maximize"), self._toggle_maximize)
        self.close_btn = self._button("x", tr("Close"), window.close, close=True)

        window.windowHandle() and None  # noqa: B018 - handle created lazily
        window.installEventFilter(self)

    def _button(self, icon_name, tip, slot, close=False):
        btn = QPushButton(self)
        btn.setObjectName("winBtnClose" if close else "winBtn")
        btn.setIcon(icons.icon(icon_name, self._colors["text_dim"], 16))
        btn.setIconSize(QSize(15, 15))
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        self.layout().addWidget(btn)
        return btn

    def set_colors(self, colors):
        """Re-tint the control icons after a theme change."""
        self._colors = colors
        self.min_btn.setIcon(icons.icon("win-min", colors["text_dim"], 16))
        maximized = self._window.isMaximized()
        self.max_btn.setIcon(icons.icon(
            "win-restore" if maximized else "win-max", colors["text_dim"], 16))
        self.close_btn.setIcon(icons.icon("x", colors["text_dim"], 16))

    def _toggle_maximize(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()

    def eventFilter(self, obj, event):
        if obj is self._window and event.type() == QEvent.WindowStateChange:
            maximized = self._window.isMaximized()
            self.max_btn.setIcon(icons.icon(
                "win-restore" if maximized else "win-max",
                self._colors["text_dim"], 16))
            self.max_btn.setToolTip(tr("Restore") if maximized else tr("Maximize"))
        return False


class DragArea(QWidget):
    """A widget that moves its frameless top-level window when dragged."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            win = self.window().windowHandle()
            if win is not None:
                win.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            top = self.window()
            if top.isMaximized():
                top.showNormal()
            else:
                top.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class FramelessResizer(QObject):
    """Application-level event filter providing 8-direction edge resizing
    for a frameless window (with cursor feedback)."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._cursor_overridden = False

    def _edges_at(self, global_pos):
        if self._window.isMaximized() or self._window.isFullScreen():
            return Qt.Edges()
        # Work in the window's own coordinates. A frameless window's
        # frameGeometry() is unreliable on some platforms (fractional scaling,
        # Wayland CSD shadow margins): it reports a position/size that doesn't
        # match the rendered surface, which pushed the resize bands inside the
        # window. mapFromGlobal() + the live width()/height() always describe
        # what's actually drawn, so the edges line up with the visible border.
        local = self._window.mapFromGlobal(global_pos)
        x, y = local.x(), local.y()
        w, h = self._window.width(), self._window.height()
        if not (0 <= x <= w and 0 <= y <= h):
            return Qt.Edges()
        edges = Qt.Edges()
        if x <= RESIZE_MARGIN:
            edges |= Qt.LeftEdge
        if x >= w - RESIZE_MARGIN:
            edges |= Qt.RightEdge
        if y <= RESIZE_MARGIN:
            edges |= Qt.TopEdge
        if y >= h - RESIZE_MARGIN:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges):
        if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            return Qt.SizeFDiagCursor
        if edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            return Qt.SizeBDiagCursor
        if edges & (Qt.LeftEdge | Qt.RightEdge):
            return Qt.SizeHorCursor
        if edges & (Qt.TopEdge | Qt.BottomEdge):
            return Qt.SizeVerCursor
        return None

    def eventFilter(self, obj, event):
        # Defensive: this filter sees every event in the app; never let an
        # unexpected event type take the process down.
        try:
            etype = event.type()
            if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                # Only react to presses on the main window itself
                widget = obj if hasattr(obj, 'window') and callable(obj.window) else None
                if widget is None or widget.window() is not self._window:
                    return False
                edges = self._edges_at(event.globalPosition().toPoint())
                if edges:
                    handle = self._window.windowHandle()
                    if handle is not None:
                        handle.startSystemResize(edges)
                        return True
            elif etype == QEvent.MouseMove and not event.buttons():
                widget = obj if hasattr(obj, 'window') and callable(obj.window) else None
                if widget is not None and widget.window() is self._window:
                    edges = self._edges_at(event.globalPosition().toPoint())
                    cursor = self._cursor_for(edges)
                    from PySide6.QtWidgets import QApplication
                    if cursor is not None:
                        if not self._cursor_overridden:
                            QApplication.setOverrideCursor(cursor)
                            self._cursor_overridden = True
                    elif self._cursor_overridden:
                        QApplication.restoreOverrideCursor()
                        self._cursor_overridden = False
        except Exception:
            return False
        return False
