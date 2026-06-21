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

"""Dialog offering to add local-only words/texts to the signed-in account.

Non-destructive: the local store is never modified. The user picks exactly which
items to copy up (all checked by default); the chosen subset is returned via
:meth:`selection`.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import ntr, tr
from app.ui import icons
from app.ui.dialogs.base import FramelessDialog


class ContributeDialog(FramelessDialog):
    """Counts + selectable checklist of local items missing from the account."""

    def __init__(self, parent, email, words, texts, suppressed=False):
        super().__init__(parent, title=tr("Sync this device's data to your account"))
        self._words = words
        self._texts = texts
        self.setMinimumWidth(440)
        self.setMinimumHeight(420)
        self.content_layout.setSpacing(12)
        c = self.colors

        nw, nt = len(words), len(texts)
        word_phrase = ntr(nw, tr("{n} word"), tr("{n} words"),
                          tr("{n} words (genitive)")).format(n=nw)
        text_phrase = ntr(nt, tr("{n} text"), tr("{n} texts"),
                          tr("{n} texts (genitive)")).format(n=nt)
        account = email or tr("your account")
        # Phrased with "This device has …" so it reads correctly for both singular
        # and plural counts (no verb-agreement pitfalls).
        if nw and nt:
            summary = tr("This device has {words} and {texts} not yet in {account}.")
        elif nw:
            summary = tr("This device has {words} not yet in {account}.")
        else:
            summary = tr("This device has {texts} not yet in {account}.")
        intro = QLabel(summary.format(words=word_phrase, texts=text_phrase, account=account))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"font-size:14px; font-weight:600; color:{c['text']};")
        self.content_layout.addWidget(intro)

        hint = QLabel(tr("Select the items to add. They are copied to your account and "
                         "uploaded to the cloud, so they appear on your other devices. "
                         "The copy on this device is kept."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {c['text_dim']}; font-size:12.5px;")
        self.content_layout.addWidget(hint)

        self._word_list = self._build_section(
            tr("Words"), "book", words, lambda w: self._word_label(w)) if nw else None
        self._text_list = self._build_section(
            tr("Texts"), "file-text", texts,
            lambda t: (t.get('Title') or tr("(untitled)"))) if nt else None

        self.suppress_check = QCheckBox(tr("Don't ask again for this account"))
        self.suppress_check.setCursor(Qt.PointingHandCursor)
        self.suppress_check.setStyleSheet(f"color: {c['text_dim']};")
        # Reflect the account's current setting so it can be seen and toggled back
        # off here (this dialog, via the Settings button, is how the opt-out is undone).
        self.suppress_check.setChecked(suppressed)
        self.content_layout.addWidget(self.suppress_check)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(tr("Cancel"))
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self._add_btn = QPushButton(objectName="primaryButton")
        self._add_btn.setCursor(Qt.PointingHandCursor)
        self._add_btn.setDefault(True)
        self._add_btn.clicked.connect(self.accept)
        row.addWidget(self._add_btn)
        self.content_layout.addLayout(row)
        self._update_add_button()

    # ---- section construction ----------------------------------------
    def _build_section(self, title, icon_name, items, label_fn):
        c = self.colors
        header = QHBoxLayout()
        header.setContentsMargins(2, 4, 2, 0)
        header.setSpacing(7)
        select_all = QCheckBox(title)
        select_all.setChecked(True)
        select_all.setCursor(Qt.PointingHandCursor)
        select_all.setStyleSheet(f"font-weight: 600; color: {c['text']};")
        header.addWidget(select_all)
        glyph = QLabel()
        glyph.setPixmap(icons.icon(icon_name, c["text_dim"], 15).pixmap(15, 15))
        header.addWidget(glyph, 0, Qt.AlignVCenter)
        header.addStretch(1)
        count = QLabel(str(len(items)))
        count.setStyleSheet(f"color: {c['text_dim']}; font-size: 11.5px;")
        header.addWidget(count)
        self.content_layout.addLayout(header)

        lst = QListWidget(objectName="ContributeList")
        lst.setFrameShape(QListWidget.NoFrame)
        lst.setStyleSheet(
            f"#ContributeList{{background:{c['surface_alt']};"
            f" border:1px solid {c['border']}; border-radius:10px;"
            f" padding:5px; outline:none;}}"
            f"#ContributeList::item{{padding:7px 9px; border-radius:6px;"
            f" margin:1px 2px; color:{c['text']};}}"
            f"#ContributeList::item:hover{{background:{c['surface']};}}"
            f"#ContributeList::item:selected{{background:{c['surface']};"
            f" color:{c['text']};}}")
        for it in items:
            row = QListWidgetItem(label_fn(it))
            row.setFlags(row.flags() | Qt.ItemIsUserCheckable)
            row.setCheckState(Qt.Checked)
            row.setData(Qt.UserRole, it)
            lst.addItem(row)
        # Fit the container to its content (up to ~5 rows), then scroll — no big
        # empty box for short lists.
        row_h = lst.sizeHintForRow(0) if items else 0
        lst.setMaximumHeight(min(len(items), 5) * row_h + 14)
        self.content_layout.addWidget(lst)

        # Header checkbox toggles all; item changes refresh header + Add button.
        select_all.toggled.connect(lambda on, l=lst: self._set_all(l, on))
        lst.itemChanged.connect(lambda _it, s=select_all, l=lst: self._sync_header(s, l))
        lst.itemChanged.connect(lambda _it: self._update_add_button())
        return lst

    @staticmethod
    def _word_label(w):
        a = (w.get('Word1') or '').strip()
        b = (w.get('Word2') or '').strip()
        return f"{a} → {b}" if b else (a or '—')

    def _set_all(self, lst, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        lst.blockSignals(True)
        for i in range(lst.count()):
            lst.item(i).setCheckState(state)
        lst.blockSignals(False)
        self._update_add_button()

    def _sync_header(self, select_all, lst):
        checked = sum(1 for i in range(lst.count())
                      if lst.item(i).checkState() == Qt.Checked)
        select_all.blockSignals(True)
        select_all.setChecked(checked == lst.count())
        select_all.blockSignals(False)

    def _checked(self, lst):
        if lst is None:
            return []
        return [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                if lst.item(i).checkState() == Qt.Checked]

    def _update_add_button(self):
        n = len(self._checked(self._word_list)) + len(self._checked(self._text_list))
        self._add_btn.setText(
            ntr(n, tr("Add {n} item"), tr("Add {n} items"),
                tr("Add {n} items (genitive)")).format(n=n))
        self._add_btn.setEnabled(n > 0)

    # ---- result ------------------------------------------------------
    def selection(self):
        """Return ``(selected_words, selected_texts, dont_ask_again)``."""
        return (self._checked(self._word_list),
                self._checked(self._text_list),
                self.suppress_check.isChecked())
