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

"""Definition viewer/editor with AI generation.

Renders the stored '***' / '**' / '*' markup as rich text, supports
editing the raw definition, switching between Definition (Word1) and
Definition2 (Word2), and generating missing definitions via the
configured AI provider (ChatGPT or Gemini).
"""
import html
import logging
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit,
)

from app.core import ai
from app.i18n import tr
from app.ui.dialogs.base import FramelessDialog
from app.ui.workers import run_in_thread


def markup_to_html(text):
    """Convert the legacy ***heading** / **bold** / *italic* markup to HTML."""
    if not text:
        return ""
    out = html.escape(text)
    out = re.sub(r'\*\*\*(.+?)\*\*\*', r'<h3>\1</h3>', out, flags=re.S)
    out = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', out, flags=re.S)
    out = re.sub(r'\*(.+?)\*', r'<i>\1</i>', out, flags=re.S)
    out = out.replace("\n", "<br>")
    return out


class DefinitionDialog(FramelessDialog):
    definition_changed = Signal()

    def __init__(self, parent, record, db_adapter):
        super().__init__(parent, title=tr("Definition — {word}").format(word=record.get('Word1', '')))
        self.record = record
        self.db_adapter = db_adapter
        self.word_id = record["ID"]
        self.current_field = 'Word1'   # which word's definition is shown
        self._pick_initial_field = True  # on first load, open the side that has a definition
        self.editing = False
        self.ai_label = ai.provider_label()

        self.setMinimumSize(620, 480)
        self.setAttribute(Qt.WA_DeleteOnClose)

        layout = self.content_layout
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(10)

        self.header_label = QLabel()
        self.header_label.setObjectName("AppTitle")
        layout.addWidget(self.header_label)

        self.sub_label = QLabel()
        self.sub_label.setObjectName("dimLabel")
        layout.addWidget(self.sub_label)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text, 1)

        buttons = QHBoxLayout()
        self.switch_btn = QPushButton(tr("Show translation's definition"))
        self.switch_btn.clicked.connect(self.switch_definition)
        buttons.addWidget(self.switch_btn)

        self.generate_btn = QPushButton(tr("Generate with AI"))
        self.generate_btn.clicked.connect(self.generate_definition)
        buttons.addWidget(self.generate_btn)

        buttons.addStretch(1)

        self.edit_btn = QPushButton(tr("Edit"))
        self.edit_btn.clicked.connect(self.toggle_edit)
        buttons.addWidget(self.edit_btn)

        self.save_btn = QPushButton(tr("Save"), objectName="primaryButton")
        self.save_btn.clicked.connect(self.save_definition)
        self.save_btn.hide()
        buttons.addWidget(self.save_btn)

        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.clicked.connect(self.cancel_edit)
        self.cancel_btn.hide()
        buttons.addWidget(self.cancel_btn)

        close_btn = QPushButton(tr("Close"))
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.reload_word()

    # ------------------------------------------------------------------

    def reload_word(self):
        word = self.db_adapter.get_word(self.word_id) or self.record
        self.word = word
        if self._pick_initial_field:
            self._pick_initial_field = False
            has_def1 = bool(str(word.get('Definition') or "").strip())
            has_def2 = bool(str(word.get('Definition2') or "").strip())
            if not has_def1 and has_def2:
                self.current_field = 'Word2'
        self.refresh_view()

    def _definition_column(self):
        return 'Definition' if self.current_field == 'Word1' else 'Definition2'

    def _displayed_word(self):
        return self.word.get(self.current_field) or ""

    def refresh_view(self):
        definition = self.word.get(self._definition_column()) or ""
        word_label = self._displayed_word()
        lang = self.word.get('Language1' if self.current_field == 'Word1' else 'Language2') or ""
        self.header_label.setText(str(word_label))
        def_label = tr("Definition") if self.current_field == 'Word1' else tr("Definition 2")
        self.sub_label.setText(f"{lang} · {def_label}")
        if definition:
            self.text.setHtml(markup_to_html(str(definition)))
            self.generate_btn.setText(tr("Regenerate with AI"))
        else:
            self.text.setHtml(
                f"<i>{tr('No definition stored yet. Use \"Generate with AI\" or \"Edit\" to add one.')}</i>")
            self.generate_btn.setText(tr("Generate with AI"))
        if self.current_field == 'Word2':
            self.switch_btn.setText(tr("Show word's definition"))
        else:
            self.switch_btn.setText(tr("Show translation's definition"))

    def switch_definition(self):
        if self.editing:
            return
        self.current_field = 'Word2' if self.current_field == 'Word1' else 'Word1'
        self.refresh_view()

    # ------------------------------------------------------------ editing

    def toggle_edit(self):
        self.editing = True
        raw = self.word.get(self._definition_column()) or ""
        self.text.setReadOnly(False)
        self.text.setPlainText(str(raw))
        self.edit_btn.hide()
        self.save_btn.show()
        self.cancel_btn.show()
        self.switch_btn.setEnabled(False)
        self.generate_btn.setEnabled(False)

    def cancel_edit(self):
        self.editing = False
        self.text.setReadOnly(True)
        self.edit_btn.show()
        self.save_btn.hide()
        self.cancel_btn.hide()
        self.switch_btn.setEnabled(True)
        self.generate_btn.setEnabled(True)
        self.refresh_view()

    def save_definition(self):
        new_text = self.text.toPlainText().strip()
        try:
            self.db_adapter.update_word(self.word_id, {self._definition_column(): new_text})
            self.definition_changed.emit()
        except Exception as exc:
            logging.error(f"Error saving definition: {exc}")
            QMessageBox.critical(self, tr("Error"), tr("Failed to save definition:\n{error}").format(error=exc))
            return
        self.cancel_edit()
        self.reload_word()

    # --------------------------------------------------------------- gpt

    def generate_definition(self):
        word = self._displayed_word()
        if not str(word).strip():
            QMessageBox.warning(self, tr("No word"), tr("There is no word to define."))
            return
        if not ai.has_api_key():
            QMessageBox.warning(self, tr("API key missing"),
                                tr("Set your {ai} API key in Settings → APIs → AI first.").format(ai=self.ai_label))
            return
        lang1 = self.word.get('Language1') or "English"
        lang2 = self.word.get('Language2') or "English"
        if self.current_field == 'Word2':
            lang1, lang2 = lang2, lang1

        self.generate_btn.setEnabled(False)
        self.text.setHtml(f"<i>{tr('Generating definition…')}</i>")

        field = self.current_field

        def work():
            return ai.update_definition_in_db(str(word), lang1, lang2, field, self.word_id)

        def done(result):
            ok, message = result
            if ok:
                self.definition_changed.emit()
                self.reload_word()
            else:
                self.refresh_view()
                QMessageBox.warning(self, self.ai_label, message)

        run_in_thread(work, on_result=done,
                      on_error=lambda e: (self.refresh_view(),
                                          QMessageBox.critical(self, self.ai_label, e)),
                      on_finished=lambda: self.generate_btn.setEnabled(True))
