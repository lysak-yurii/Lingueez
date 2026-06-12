"""Cloud-sync status bubble, anchored below the top-bar cloud icon.

Same shell as the word-translation popover: a frameless Qt.Popup QFrame
that paints its own rounded body and closes on any click elsewhere. The
status is fetched on a worker thread after the bubble is shown; a request
counter orphans results that arrive after the bubble was reopened/closed.
"""
from datetime import datetime, timezone

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from app.ui import icons
from app.ui.workers import run_in_thread


def humanize_time(iso_str):
    """ISO timestamp -> short human phrase in local time ('12 min ago')."""
    if not iso_str:
        return "never"
    try:
        moment = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except ValueError:
        return str(iso_str)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone()
    now = datetime.now().astimezone()
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if moment.date() == now.date():
        return f"today {moment.strftime('%H:%M')}"
    if (now.date() - moment.date()).days == 1:
        return f"yesterday {moment.strftime('%H:%M')}"
    return moment.strftime("%d %b %Y, %H:%M")


class SyncPopover(QFrame):
    """Anchored cloud-sync status bubble with a Sync Now action."""

    sync_requested = Signal()

    def __init__(self, colors, parent=None):
        super().__init__(parent, objectName="SyncPopover")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)  # rounded corners
        self._colors = colors
        self._request = 0  # bumps on every show/hide; stale-result guard
        self.setMinimumWidth(280)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.icon_label = QLabel()
        self.icon_label.setPixmap(
            icons.icon("cloud", colors["text"], 16).pixmap(16, 16))
        head.addWidget(self.icon_label)
        title = QLabel("Cloud Sync")
        title.setStyleSheet("font-weight: 600;")
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self._values = {}
        for row, (key, caption) in enumerate(
                (("status", "Status"), ("last", "Last sync"),
                 ("pending", "Pending"))):
            label = QLabel(caption, objectName="dimLabel")
            value = QLabel("…")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1, Qt.AlignRight)
            self._values[key] = value
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        self.note_label = QLabel("")
        self.note_label.setObjectName("dimLabel")
        self.note_label.setWordWrap(True)
        self.note_label.setVisible(False)
        lay.addWidget(self.note_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.sync_btn = QPushButton("Sync Now", objectName="primaryButton")
        self.sync_btn.setCursor(Qt.PointingHandCursor)
        self.sync_btn.clicked.connect(self._on_sync_clicked)
        footer.addWidget(self.sync_btn)
        lay.addLayout(footer)

    # ------------------------------------------------------------- public

    def show_below(self, button, fetch_status, syncing=False):
        """Open under *button* and load the status via *fetch_status*()."""
        self._request += 1
        self._set_value("status", "…", dim=True)
        self._set_value("last", "…", dim=True)
        self._set_value("pending", "…", dim=True)
        self.note_label.setVisible(False)
        self.sync_btn.setEnabled(not syncing)
        self.sync_btn.setText("Syncing…" if syncing else "Sync Now")

        self.adjustSize()
        corner = button.mapToGlobal(QPoint(button.width(), button.height()))
        x = corner.x() - self.width()          # right edges aligned
        y = corner.y() + 6
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
            if y + self.height() > screen.bottom() - 4:  # no room: flip above
                y = button.mapToGlobal(QPoint(0, 0)).y() - self.height() - 6
        self.move(QPoint(x, y))
        self.show()

        request = self._request

        def done(info):
            if request == self._request:
                self._fill(info)

        def fail(message):
            if request == self._request:
                self._show_error(message)

        run_in_thread(fetch_status, on_result=done, on_error=fail)

    def refresh_theme(self, colors):
        self._colors = colors
        self.icon_label.setPixmap(
            icons.icon("cloud", colors["text"], 16).pixmap(16, 16))
        self.update()

    # -------------------------------------------------------------- intern

    def hideEvent(self, event):
        self._request += 1  # orphan any in-flight worker result
        super().hideEvent(event)

    def paintEvent(self, event):
        # Painted by hand: translucent top-level windows do not reliably
        # get their QSS background (same approach as WordPopup).
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor(self._colors["surface_alt"]))
        painter.setPen(QPen(QColor(self._colors["border"]), 1))
        painter.drawRoundedRect(rect, 10, 10)

    def _set_value(self, key, text, color=None, dim=False, tooltip=""):
        label = self._values[key]
        if dim:
            color = self._colors["text_dim"]
        label.setStyleSheet(f"color: {color};" if color else "")
        label.setText(text)
        label.setToolTip(tooltip)

    def _fill(self, info):
        connected = bool(info.get("enabled"))
        self._set_value(
            "status",
            "Connected" if connected else "Not connected",
            color=self._colors["success" if connected else "danger"])

        last = info.get("last_sync_time")
        self._set_value("last", humanize_time(last),
                        dim=not last, tooltip=str(last or ""))

        operations = int(info.get("pending_operations") or 0)
        deletions = int(info.get("pending_deletions") or 0)
        if operations or deletions:
            parts = []
            if operations:
                parts.append(f"{operations} change{'s' if operations != 1 else ''}")
            if deletions:
                parts.append(f"{deletions} deletion{'s' if deletions != 1 else ''}")
            self._set_value("pending", " · ".join(parts),
                            color=self._colors["warning"])
        else:
            self._set_value("pending", "everything synced", dim=True)

        if not info.get("first_sync_completed"):
            self.note_label.setText("Initial sync has not completed yet.")
            self.note_label.setVisible(True)
        self.adjustSize()

    def _show_error(self, message):
        self._set_value("status", "Error", color=self._colors["danger"])
        self._set_value("last", "—", dim=True)
        self._set_value("pending", "—", dim=True)
        self.note_label.setText(message)
        self.note_label.setVisible(True)
        self.adjustSize()

    def _on_sync_clicked(self):
        self.hide()
        self.sync_requested.emit()
