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

"""Compact word-translation popover for the texts reader.

Reverso-caption style: anchored directly above the clicked word, no window
chrome, closes on any click elsewhere (Qt.Popup). Shows the DeepL
translation plus three small controls: a tiny target-language dropdown,
"add with AI" (lemma form + context-aware translation) and "add as is".

One instance is reused for all clicks: worker-thread results are matched
against a request counter, so a popup re-opened for another word simply
ignores late replies meant for the previous one.
"""
import logging

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton

from app.config import load_settings, save_settings
from app.core import ai
from app.i18n import lang_label, tr
from app.core.backup_management import backup_database
from app.core.errors import DuplicateWordError
from app.core.translator import DEEPL_LANGUAGE_CODES, translate
from app.ui import icons
from app.ui.toast import show_toast
from app.ui.workers import run_in_thread


class WordPopup(QFrame):
    """Anchored translation popover with add-to-vocabulary actions."""

    word_saved = Signal()  # a word was added — let the main window refresh
    closed = Signal()      # hidden — the host clears the click highlight

    MAX_LABEL_CHARS = 60

    def __init__(self, colors, db_adapter, parent=None):
        super().__init__(parent, objectName="WordPopup")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)  # rounded QSS corners
        self._colors = colors
        self.db_adapter = db_adapter
        self._request = 0       # bumps on every show/hide; stale guard
        self._word = ""
        self._language = ""     # source language (the text's language)
        self._sentence = ""
        self._translation = None
        self._busy = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 8, 7)
        lay.setSpacing(4)

        self.text_label = QLabel("…")
        self.text_label.setObjectName("WordPopupText")
        lay.addWidget(self.text_label, 1)

        lay.addSpacing(4)
        self.lang_btn = self._button("chevron-down", tr("Translation language"),
                                     self._pick_language, size=12)
        lay.addWidget(self.lang_btn)
        self.ai_btn = self._button("sparkles",
                                   tr("Add with AI (lemma + best translation)"),
                                   self._add_with_ai)
        lay.addWidget(self.ai_btn)
        self.add_btn = self._button("plus", tr("Add to vocabulary as is"), self._add_plain)
        lay.addWidget(self.add_btn)

    def _button(self, name, tooltip, slot, size=15):
        btn = QPushButton(objectName="iconButton")
        btn.setIcon(icons.icon(name, self._colors["text_dim"], size))
        btn.setIconSize(QSize(size, size))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def refresh_theme(self, colors):
        self._colors = colors
        self.lang_btn.setIcon(icons.icon("chevron-down", colors["text_dim"], 12))
        self.ai_btn.setIcon(icons.icon("sparkles", colors["text_dim"], 15))
        self.add_btn.setIcon(icons.icon("plus", colors["text_dim"], 15))
        self.update()

    def paintEvent(self, event):
        # Painted by hand: a translucent top-level window does not reliably
        # get its QSS background, which left the pill see-through.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor(self._colors["surface_alt"]))
        painter.setPen(QPen(QColor(self._colors["border"]), 1))
        painter.drawRoundedRect(rect, 8, 8)

    # ------------------------------------------------------------- public

    def show_for(self, word, language, sentence, anchor_rect):
        """Open above *anchor_rect* (global coords) and translate *word*."""
        self._request += 1
        self._word = word
        self._language = language
        self._sentence = sentence
        self._translation = None
        self._set_busy(False)
        self._set_text("…", dim=True)

        self.adjustSize()
        self._place(anchor_rect)
        self.show()
        self._translate()

    # -------------------------------------------------------------- intern

    def hideEvent(self, event):
        self._request += 1  # orphan any in-flight worker results
        super().hideEvent(event)
        self.closed.emit()

    def _target(self):
        target = str(load_settings().get("reader_translate_target", "English"))
        return target if target in DEEPL_LANGUAGE_CODES else "English"

    def _place(self, anchor_rect):
        self.layout().activate()  # child geometries needed for alignment
        size = self.size()
        # the translation text starts where the word starts (first letter)
        x = anchor_rect.left() - self.text_label.geometry().left()
        y = anchor_rect.top() - size.height() - 2
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen:
            x = max(screen.left() + 4, min(x, screen.right() - size.width() - 4))
            if y < screen.top() + 4:  # no room above: flip below the word
                y = anchor_rect.bottom() + 2
        self.move(QPoint(x, y))

    def _set_text(self, text, dim=False, danger=False):
        if len(text) > self.MAX_LABEL_CHARS:
            text = text[:self.MAX_LABEL_CHARS - 1] + "…"
        color = self._colors["danger"] if danger else (
            self._colors["text_dim"] if dim else self._colors["text"])
        self.text_label.setStyleSheet(f"color: {color};")
        self.text_label.setText(text)
        self.adjustSize()

    def _set_busy(self, busy):
        self._busy = busy
        for btn in (self.lang_btn, self.ai_btn, self.add_btn):
            btn.setEnabled(not busy)

    def _toast(self, title, message, kind):
        show_toast(self.parent().window(), title, message, kind)

    # ---------------------------------------------------------- translate

    def _translate(self):
        request = self._request
        word, target = self._word, self._target()
        source = self._language if self._language in DEEPL_LANGUAGE_CODES else None
        if source == target:
            source = None  # let DeepL detect; avoids same-language no-ops

        def work():
            translation, _detected = translate(word, target, source)
            return translation

        def done(translation):
            if request != self._request:
                return
            self._translation = translation
            self._set_text(translation)

        def fail(message):
            if request != self._request:
                return
            logging.warning(f"Word popup translation failed: {message}")
            self._set_text(message, danger=True)

        run_in_thread(work, on_result=done, on_error=fail)

    def _pick_language(self):
        menu = QMenu(self)
        current = self._target()
        for name in sorted(DEEPL_LANGUAGE_CODES):
            action = menu.addAction(lang_label(name))
            action.setData(name)  # store the canonical English name
            action.setCheckable(True)
            action.setChecked(name == current)
        chosen = menu.exec(self.lang_btn.mapToGlobal(
            QPoint(0, self.lang_btn.height())))
        if chosen and chosen.data() != current:
            settings = load_settings()
            settings["reader_translate_target"] = chosen.data()
            save_settings(settings)
            self._set_text("…", dim=True)
            self._translate()

    # --------------------------------------------------------------- save

    def _insert(self, word1, word2):
        """Insert on the worker thread; returns (inserted, word1, word2).

        A duplicate is a normal outcome here (the user re-clicked a known word),
        so swallow DuplicateWordError and report it as not-inserted rather than
        letting it surface as an error toast."""
        try:
            result = self.db_adapter.insert_word({
                'Language1': self._language, 'Word1': word1,
                'Language2': self._target(), 'Word2': word2,
                'Status': 'New', 'Source': 'reader',
            })
        except DuplicateWordError:
            return False, word1, word2
        if result:
            backup_database()
        return bool(result), word1, word2

    def _after_insert(self, result, original=None):
        inserted, word1, word2 = result
        if not inserted:
            self._set_busy(False)
            self._toast(tr("Vocabulary"),
                        tr("'{pair}' is already in your dictionary.").format(
                            pair=f"{word1} – {word2}"), "info")
            return
        label = f"{original} → {word1}" if original and original != word1 else word1
        self._toast(tr("Vocabulary"),
                    tr("{label} — {translation} · added").format(
                        label=label, translation=word2), "success")
        self.word_saved.emit()
        self.hide()

    def _save_failed(self, message):
        self._set_busy(False)
        self._toast(tr("Vocabulary"), message, "error")

    def _add_plain(self):
        if self._busy or not self._translation:
            return
        self._set_busy(True)
        run_in_thread(self._insert, self._word, self._translation,
                      on_result=self._after_insert, on_error=self._save_failed)

    def _add_with_ai(self):
        if self._busy:
            return
        request = self._request
        word, sentence = self._word, self._sentence
        source, target = self._language, self._target()
        self._set_busy(True)
        self._set_text(tr("Thinking…"), dim=True)

        def work():
            lemma, translation = ai.lemma_translate(word, sentence, source, target)
            return self._insert(lemma, translation)

        def done(result):
            if request != self._request:
                return
            self._after_insert(result, original=word)

        def fail(message):
            if request != self._request:
                return
            self._set_busy(False)
            self._set_text(self._translation or "…", dim=not self._translation)
            self._toast(tr("AI"), message, "error")

        run_in_thread(work, on_result=done, on_error=fail)
