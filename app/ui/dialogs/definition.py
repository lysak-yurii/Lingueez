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
        super().__init__(parent, title=f"Definition — {record.get('Word1', '')}")
        self.record = record
        self.db_adapter = db_adapter
        self.word_id = int(record["ID"])
        self.current_field = 'Word1'   # which word's definition is shown
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
        self.switch_btn = QPushButton("Show translation's definition")
        self.switch_btn.clicked.connect(self.switch_definition)
        buttons.addWidget(self.switch_btn)

        self.generate_btn = QPushButton(f"Generate with {self.ai_label}")
        self.generate_btn.clicked.connect(self.generate_definition)
        buttons.addWidget(self.generate_btn)

        buttons.addStretch(1)

        self.edit_btn = QPushButton("Edit")
        self.edit_btn.clicked.connect(self.toggle_edit)
        buttons.addWidget(self.edit_btn)

        self.save_btn = QPushButton("Save", objectName="primaryButton")
        self.save_btn.clicked.connect(self.save_definition)
        self.save_btn.hide()
        buttons.addWidget(self.save_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_edit)
        self.cancel_btn.hide()
        buttons.addWidget(self.cancel_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.reload_word()

    # ------------------------------------------------------------------

    def reload_word(self):
        word = self.db_adapter.get_word(self.word_id) or self.record
        self.word = word
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
        self.sub_label.setText(f"{lang} · {'Definition' if self.current_field == 'Word1' else 'Definition 2'}")
        if definition:
            self.text.setHtml(markup_to_html(str(definition)))
            self.generate_btn.setText(f"Regenerate with {self.ai_label}")
        else:
            self.text.setHtml(f"<i>No definition stored yet. "
                              f"Use “Generate with {self.ai_label}” or “Edit” to add one.</i>")
            self.generate_btn.setText(f"Generate with {self.ai_label}")
        other = "word's" if self.current_field == 'Word2' else "translation's"
        self.switch_btn.setText(f"Show {other} definition")

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
            QMessageBox.critical(self, "Error", f"Failed to save definition:\n{exc}")
            return
        self.cancel_edit()
        self.reload_word()

    # --------------------------------------------------------------- gpt

    def generate_definition(self):
        word = self._displayed_word()
        if not str(word).strip():
            QMessageBox.warning(self, "No word", "There is no word to define.")
            return
        if not ai.has_api_key():
            QMessageBox.warning(self, "API key missing",
                                f"Set your {self.ai_label} API key in "
                                f"Settings → APIs → AI first.")
            return
        lang1 = self.word.get('Language1') or "English"
        lang2 = self.word.get('Language2') or "English"
        if self.current_field == 'Word2':
            lang1, lang2 = lang2, lang1

        self.generate_btn.setEnabled(False)
        self.text.setHtml("<i>Generating definition…</i>")

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
