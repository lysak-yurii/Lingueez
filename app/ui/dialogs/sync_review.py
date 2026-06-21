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

"""Stale-reconnect deletion review.

When a device reconnects after being offline longer than the cloud's tombstone
retention, rows that were deleted on other devices in the meantime have lost their
tombstone, so the sync engine can't tell "deleted while away" from "new local data".
Rather than guess (and risk resurrecting deletions or losing local work), we ask the
user. See ``SyncManager.detect_stale_orphans``.
"""
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
)

from app.i18n import lang_label, tr
from app.ui.dialogs.base import FramelessDialog, quiet_frame, quiet_row


class SyncReviewDialog(FramelessDialog):
    """Lets the user Keep (re-upload) or Remove rows that were deleted elsewhere
    while this device was offline past the retention window."""

    def __init__(self, parent, words: List[dict], texts: List[dict]):
        super().__init__(parent, title=tr("Items deleted on another device"))
        self.setMinimumWidth(460)
        self._choice: Optional[bool] = None  # True=keep, False=remove, None=cancel
        c = self.colors
        total = len(words) + len(texts)

        self.content_layout.setSpacing(14)

        # ── Header: one quiet line of explanation (the title bar carries the
        #    title, matching the app's confirm() dialogs) ─────────────────────
        sub = QLabel(tr(
            "While this device was offline, {n} item(s) here were deleted on your "
            "other devices. Keep them in the cloud, or remove them from this device?"
        ).format(n=total))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{c['text_dim']}; font-size:12.5px;")
        self.content_layout.addWidget(sub)

        # ── The quiet frame: one container, a hairline between rows. A leading
        #    glyph distinguishes words ("book") from texts ("file-text"). ──────
        rows = []
        for w in words:
            w1, w2 = (w.get('Word1') or '').strip(), (w.get('Word2') or '').strip()
            rows.append(("book", f"{w1}  →  {w2}" if w2 else w1,
                         lang_label(w.get('Language1', '') or '')))
        for t in texts:
            rows.append(("file-text", t.get('Title') or tr("(untitled)"),
                         t.get('Category') or tr("Text")))

        frame, vb = quiet_frame(c)
        last = len(rows) - 1
        for i, (icon, primary, trailing) in enumerate(rows):
            vb.addWidget(quiet_row(c, primary, trailing, icon=icon,
                                   divider=(i < last)))

        if len(rows) > 5:  # only scroll when the list would overflow
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setMaximumHeight(5 * 44)
            scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
            scroll.setWidget(frame)
            self.content_layout.addWidget(scroll, 1)
        else:
            self.content_layout.addWidget(frame)

        # ── Footer ───────────────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(8)
        remove = QPushButton(tr("Remove from this device"), objectName="dangerButton")
        remove.setCursor(Qt.PointingHandCursor)
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        row.addStretch(1)
        later = QPushButton(tr("Decide later"))
        later.setCursor(Qt.PointingHandCursor)
        later.clicked.connect(self.reject)
        row.addWidget(later)
        keep = QPushButton(tr("Keep & upload").replace("&", "&&"),
                           objectName="primaryButton")
        keep.setCursor(Qt.PointingHandCursor)
        keep.setDefault(True)
        keep.clicked.connect(self._keep)
        row.addWidget(keep)
        self._footer_btns = (remove, later, keep)
        self.content_layout.addLayout(row)

    def showEvent(self, event):
        # Pin each footer button to its natural width so longer (e.g. localized)
        # labels widen the dialog instead of being clipped. Done here, not in
        # __init__, so the stylesheet padding is already in the size hint.
        super().showEvent(event)
        for btn in self._footer_btns:
            btn.ensurePolished()
            btn.setMinimumWidth(btn.sizeHint().width())
        self.adjustSize()

    def _keep(self):
        self._choice = True
        self.accept()

    def _remove(self):
        self._choice = False
        self.accept()

    @classmethod
    def ask(cls, parent, words: List[dict], texts: List[dict]) -> Optional[bool]:
        """Show the review. Returns True (keep & upload), False (remove), or None
        (the user deferred — leave everything untouched for now)."""
        dlg = cls(parent, words, texts)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg._choice
