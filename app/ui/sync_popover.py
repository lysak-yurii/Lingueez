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
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import month_abbr, ntr, tr
from app.ui import icons
from app.ui.workers import run_in_thread


def humanize_time(iso_str):
    """ISO timestamp -> short human phrase in local time ('12 min ago')."""
    if not iso_str:
        return tr("never")
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
        return tr("just now")
    if seconds < 3600:
        return tr("{n} min ago").format(n=int(seconds // 60))
    if moment.date() == now.date():
        return tr("today {time}").format(time=moment.strftime('%H:%M'))
    if (now.date() - moment.date()).days == 1:
        return tr("yesterday {time}").format(time=moment.strftime('%H:%M'))
    return f"{moment.day:02d} {month_abbr(moment)} {moment.year}, {moment.strftime('%H:%M')}"


class SyncPopover(QFrame):
    """Anchored cloud-sync status bubble with a Sync Now action.

    Two states share the same shell: the live status grid (signed in / personal
    server) and a local-only *promo* that pitches sync and offers a Sign in CTA."""

    sync_requested = Signal()
    sign_in_requested = Signal()

    def __init__(self, colors, parent=None):
        super().__init__(parent, objectName="SyncPopover")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)  # rounded corners
        self._colors = colors
        self._request = 0  # bumps on every show/hide; stale-result guard
        self._anchor = None  # button to re-anchor against when content resizes
        self._syncing = False  # a sync is in progress while the bubble is open
        self._fetch_status = None  # callable to re-pull the snapshot on refresh
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
        title = QLabel(tr("Cloud Sync"))
        title.setStyleSheet("font-weight: 600;")
        head.addWidget(title)
        head.addStretch(1)
        lay.addLayout(head)

        # ---- live status state (signed in / personal server) ----------------
        # Grouped in one container so it can be swapped wholesale for the promo.
        self.status_widget = QWidget()
        status_lay = QVBoxLayout(self.status_widget)
        status_lay.setContentsMargins(0, 0, 0, 0)
        status_lay.setSpacing(10)
        lay.addWidget(self.status_widget)

        # Who/where the app is syncing, with a mode icon: a person for a built-in
        # account (shows the email), a server for the personal own-Supabase mode
        # (shows just "Personal"). Hidden when local-only.
        ident_row = QHBoxLayout()
        ident_row.setContentsMargins(0, 0, 0, 0)  # align flush with the header, no extra inset
        ident_row.setSpacing(6)
        self.identity_icon = QLabel()
        ident_row.addWidget(self.identity_icon)
        self.identity_label = QLabel("")
        self.identity_label.setObjectName("dimLabel")
        self.identity_label.setWordWrap(True)
        self.identity_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        ident_row.addWidget(self.identity_label, 1)
        self.identity_widget = QWidget()
        self.identity_widget.setLayout(ident_row)
        self.identity_widget.setVisible(False)
        status_lay.addWidget(self.identity_widget)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self._values = {}
        for row, (key, caption) in enumerate(
                (("status", tr("Status")), ("last", tr("Last sync")),
                 ("pending", tr("Pending")))):
            label = QLabel(caption, objectName="dimLabel")
            value = QLabel("…")
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1, Qt.AlignRight)
            self._values[key] = value
        grid.setColumnStretch(1, 1)
        status_lay.addLayout(grid)

        self.note_label = QLabel("")
        self.note_label.setObjectName("dimLabel")
        self.note_label.setWordWrap(True)
        self.note_label.setVisible(False)
        status_lay.addWidget(self.note_label)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.sync_btn = QPushButton(tr("Sync Now"), objectName="primaryButton")
        self.sync_btn.setCursor(Qt.PointingHandCursor)
        self.sync_btn.clicked.connect(self._on_sync_clicked)
        footer.addWidget(self.sync_btn)
        status_lay.addLayout(footer)

        # ---- local-only promo state -----------------------------------------
        # Shown instead of the status grid when signed out: a short pitch + a
        # Sign in CTA that funnels into the app's shared sign-in flow.
        self.promo_widget = QWidget()
        promo_lay = QVBoxLayout(self.promo_widget)
        promo_lay.setContentsMargins(0, 0, 0, 0)
        promo_lay.setSpacing(8)
        promo_headline = QLabel(tr("Back up your vocabulary"))
        promo_headline.setStyleSheet("font-weight: 600;")
        promo_headline.setWordWrap(True)
        promo_lay.addWidget(promo_headline)
        self.promo_body = QLabel("")
        self.promo_body.setObjectName("dimLabel")
        self.promo_body.setWordWrap(True)
        promo_lay.addWidget(self.promo_body)
        promo_footer = QHBoxLayout()
        promo_footer.addStretch(1)
        self.promo_signin_btn = QPushButton(tr("Sign in"), objectName="primaryButton")
        self.promo_signin_btn.setCursor(Qt.PointingHandCursor)
        self.promo_signin_btn.clicked.connect(self._on_sign_in_clicked)
        promo_footer.addWidget(self.promo_signin_btn)
        promo_lay.addLayout(promo_footer)
        self.promo_widget.setVisible(False)
        lay.addWidget(self.promo_widget)

    # ------------------------------------------------------------- public

    def show_below(self, button, fetch_status, syncing=False):
        """Open under *button* and load the status via *fetch_status*()."""
        self._request += 1
        self._syncing = syncing
        self._fetch_status = fetch_status
        self.promo_widget.setVisible(False)
        self.status_widget.setVisible(True)
        self._set_value("status", "…", dim=True)
        self._set_value("last", "…", dim=True)
        self._set_value("pending", "…", dim=True)
        self.identity_widget.setVisible(False)
        self.note_label.setVisible(False)
        self._apply_sync_button()

        self._anchor = button
        self.adjustSize()
        self._reposition()
        self.show()

        self._load(fetch_status)

    def set_syncing(self, syncing):
        """Reflect a sync starting or finishing while the bubble is already open.

        While syncing, the Status row reads 'Syncing…' (so it never misleadingly
        shows 'everything synced'); when the sync ends we re-pull a fresh snapshot
        so Last sync / Pending update without the user having to reopen the bubble."""
        if syncing == self._syncing:
            return
        self._syncing = syncing
        self._apply_sync_button()
        if syncing:
            self._set_value("status", tr("Syncing…"), color=self._colors["accent"])
            self._set_value("pending", "—", dim=True)
            self._resize_to_content()
        else:
            self._load(self._fetch_status)

    def _apply_sync_button(self):
        self.sync_btn.setEnabled(not self._syncing)
        self.sync_btn.setText(tr("Syncing…") if self._syncing else tr("Sync Now"))

    def _load(self, fetch_status):
        """(Re)fetch the status on a worker; a request counter orphans stale results."""
        if not fetch_status:
            return
        self._request += 1
        request = self._request

        def done(info):
            if request == self._request:
                self._fill(info)

        def fail(message):
            if request == self._request:
                self._show_error(message)

        run_in_thread(fetch_status, on_result=done, on_error=fail)

    def _reposition(self):
        """Anchor under the cloud button, right edges aligned, kept on-screen."""
        if not self._anchor:
            return
        button = self._anchor
        corner = button.mapToGlobal(QPoint(button.width(), button.height()))
        x = corner.x() - self.width()          # right edges aligned
        y = corner.y() + 6
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
            if y + self.height() > screen.bottom() - 4:  # no room: flip above
                y = button.mapToGlobal(QPoint(0, 0)).y() - self.height() - 6
        self.move(QPoint(x, y))

    def _resize_to_content(self):
        """Re-fit to the current text. The status arrives asynchronously, so
        the layout must be recomputed *before* adjustSize — otherwise the
        bubble keeps its placeholder-sized width and clips longer strings
        (e.g. the Ukrainian 'все синхронізовано'). Re-anchor afterwards, since
        the right edge must stay aligned to the button as the width grows.

        The status/promo content lives in nested container widgets, so their
        layouts must be invalidated too — otherwise the outer layout re-fits to a
        stale child size hint and the bubble keeps clipping (e.g. the Ukrainian
        'все синхронізовано')."""
        for inner in (self.status_widget, self.promo_widget):
            if inner.layout() is not None:
                inner.layout().invalidate()
                inner.layout().activate()
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()
        self._reposition()

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
        # Mode icon on the identity line only: a person for a built-in account, a server
        # for the own-Supabase mode. The header keeps the cloud icon in every mode.
        mode = info.get("mode")
        identity = info.get("identity")
        if identity:
            mode_icon = {"personal": "server", "account": "user"}.get(mode, "cloud")
            self.identity_icon.setPixmap(
                icons.icon(mode_icon, self._colors["text_dim"], 14).pixmap(14, 14))
            self.identity_label.setText(identity)
            self.identity_widget.setVisible(True)
        else:
            self.identity_widget.setVisible(False)

        if self._syncing:
            # A sync is running right now: say so, instead of a stale snapshot that
            # would read 'Connected' next to 'everything synced' mid-sync.
            self._set_value("status", tr("Syncing…"), color=self._colors["accent"])
        else:
            connected = bool(info.get("enabled"))
            self._set_value(
                "status",
                tr("Connected") if connected else tr("Not connected"),
                color=self._colors["success" if connected else "danger"])

        last = info.get("last_sync_time")
        self._set_value("last", humanize_time(last),
                        dim=not last, tooltip=str(last or ""))

        self._fill_pending(info)

        if not info.get("first_sync_completed"):
            self.note_label.setText(tr("Initial sync has not completed yet."))
            self.note_label.setVisible(True)
        else:
            # The popover is a cached singleton; without this the note would
            # stick forever once shown, even after a successful sync.
            self.note_label.setVisible(False)
        self._resize_to_content()

    def _fill_pending(self, info):
        """The 'Pending' value: queued local changes, the all-clear, or — mid-sync —
        a neutral dash, since asserting 'everything synced' while a sync is still
        running would contradict the 'Syncing…' status. The accurate verdict lands
        the moment the sync ends and the snapshot is re-pulled."""
        if self._syncing:
            self._set_value("pending", "—", dim=True)
            return
        operations = int(info.get("pending_operations") or 0)
        deletions = int(info.get("pending_deletions") or 0)
        if operations or deletions:
            parts = []
            if operations:
                noun = ntr(operations, tr("change"), tr("changes"), tr("changes (genitive)"))
                parts.append(f"{operations} {noun}")
            if deletions:
                noun = ntr(deletions, tr("deletion"), tr("deletions"), tr("deletions (genitive)"))
                parts.append(f"{deletions} {noun}")
            self._set_value("pending", " · ".join(parts),
                            color=self._colors["warning"])
        else:
            self._set_value("pending", tr("everything synced"), dim=True)

    def _show_error(self, message):
        self._set_value("status", "Error", color=self._colors["danger"])
        self._set_value("last", "—", dim=True)
        self._set_value("pending", "—", dim=True)
        self.note_label.setText(message)
        self.note_label.setVisible(True)
        self._resize_to_content()

    def show_promo(self, button, word_count, text_count=0):
        """Open under *button* in the local-only promo state: pitch sync + offer a
        Sign in CTA. No worker fetch — the content is static save for the counts.
        The body names words, and texts too once at least one is saved."""
        self._request += 1  # orphan any in-flight status fetch from a prior open
        self.status_widget.setVisible(False)
        n, m = int(word_count or 0), int(text_count or 0)
        words = "{n} {noun}".format(
            n=n, noun=ntr(n, tr("word"), tr("words"), tr("words (genitive)")))
        texts = "{m} {noun}".format(
            m=m, noun=ntr(m, tr("text"), tr("texts"), tr("texts (genitive)")))
        if n and m:
            items = tr("{words} and {texts}").format(words=words, texts=texts)
        else:
            # Drop the empty side so the icon-by-texts-only case doesn't read
            # "0 words and N texts".
            items = texts if not n else words
        self.promo_body.setText(
            tr("You've saved {items} here. Sign in to keep them safe and "
               "study on all your devices.").format(items=items))
        self.promo_widget.setVisible(True)
        self._anchor = button
        self._resize_to_content()
        self.show()

    def _on_sign_in_clicked(self):
        self.hide()
        self.sign_in_requested.emit()

    def _on_sync_clicked(self):
        self.hide()
        self.sync_requested.emit()
