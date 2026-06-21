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

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from app.i18n import lang_label, tr
from app.ui import icons
from app.ui.dialogs.base import FramelessDialog


class SyncReviewDialog(FramelessDialog):
    """Lets the user Keep (re-upload) or Remove rows that were deleted elsewhere
    while this device was offline past the retention window."""

    def __init__(self, parent, words: List[dict], texts: List[dict]):
        super().__init__(parent, title=tr("Items deleted on another device"))
        self.setMinimumSize(600, 480)
        self._choice: Optional[bool] = None  # True=keep, False=remove, None=cancel
        c = self.colors
        total = len(words) + len(texts)

        self.content_layout.setSpacing(16)

        # ── Header: icon badge + headline + explanation ──────────────────────
        header = QHBoxLayout()
        header.setSpacing(14)
        badge = QLabel()
        badge.setFixedSize(46, 46)
        badge.setAlignment(Qt.AlignCenter)
        badge.setPixmap(icons.icon("trash", c["danger"], 22).pixmap(QSize(22, 22)))
        badge.setStyleSheet(
            f"background:{c['surface_alt']}; border:1px solid {c['border']};"
            f" border-radius:23px;")
        header.addWidget(badge, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(4)
        title = QLabel(tr("Items deleted on another device"))
        title.setStyleSheet(f"font-size:16px; font-weight:600; color:{c['text']};")
        col.addWidget(title)
        sub = QLabel(tr(
            "This device was offline long enough that {n} item(s) below were deleted "
            "on your other devices in the meantime, yet still exist here.\n\n"
            "Keep them (re-upload to the cloud for all your devices), or remove them "
            "from this device?").format(n=total))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color:{c['text_dim']}; font-size:12.5px;")
        col.addWidget(sub)
        header.addLayout(col, 1)
        self.content_layout.addLayout(header)

        # ── Scrollable list of cards ─────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        vb = QVBoxLayout(holder)
        vb.setContentsMargins(0, 0, 4, 0)
        vb.setSpacing(8)
        for w in words:
            w1, w2 = (w.get('Word1') or '').strip(), (w.get('Word2') or '').strip()
            vb.addWidget(self._card("book", f"{w1}  →  {w2}" if w2 else w1,
                                    lang_label(w.get('Language1', '') or '')))
        for t in texts:
            vb.addWidget(self._card("file-text", t.get('Title') or tr("(untitled)"),
                                    t.get('Category') or lang_label(t.get('Language', '') or '')))
        vb.addStretch(1)
        scroll.setWidget(holder)
        self.content_layout.addWidget(scroll, 1)

        # ── Footer ───────────────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(8)
        remove = QPushButton(tr("Remove from this device"), objectName="dangerButton")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setIcon(icons.icon("trash", c["danger"], 15))
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        row.addStretch(1)
        later = QPushButton(tr("Decide later"))
        later.setCursor(Qt.PointingHandCursor)
        later.clicked.connect(self.reject)
        row.addWidget(later)
        keep = QPushButton(tr("Keep & upload"), objectName="primaryButton")
        keep.setCursor(Qt.PointingHandCursor)
        keep.setIcon(icons.icon("cloud", "white", 15))
        keep.setDefault(True)
        keep.clicked.connect(self._keep)
        row.addWidget(keep)
        self.content_layout.addLayout(row)

    def _card(self, icon_name: str, primary: str, chip: str) -> QFrame:
        """A single rounded row: leading glyph, the item text, and a soft language/
        category chip on the right."""
        c = self.colors
        frame = QFrame(objectName="ReviewCard")
        frame.setStyleSheet(
            f"#ReviewCard{{background:{c['surface']}; border:1px solid {c['border']};"
            f" border-radius:10px;}}"
            f"#ReviewCard:hover{{border-color:{c['accent']};}}")
        h = QHBoxLayout(frame)
        h.setContentsMargins(13, 11, 13, 11)
        h.setSpacing(12)
        glyph = QLabel()
        glyph.setPixmap(icons.icon(icon_name, c["text_dim"], 17).pixmap(QSize(17, 17)))
        h.addWidget(glyph, 0, Qt.AlignVCenter)
        label = QLabel(primary)
        label.setStyleSheet(f"color:{c['text']}; font-size:13.5px; font-weight:500;")
        h.addWidget(label, 1)
        if chip:
            ch = QLabel(chip)
            ch.setStyleSheet(
                f"color:{c['accent_text']}; background:{c['accent_soft']};"
                f" border-radius:9px; padding:2px 9px; font-size:11px;")
            h.addWidget(ch, 0, Qt.AlignVCenter)
        return frame

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
