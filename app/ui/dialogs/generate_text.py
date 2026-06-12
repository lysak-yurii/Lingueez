"""Generate a study text from selected words via the configured AI provider."""
import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTextEdit,
)

from app.core import ai
from app.ui.dialogs.base import FramelessDialog
from app.ui.workers import run_in_thread


class GenerateTextDialog(FramelessDialog):
    text_saved = Signal()

    def __init__(self, parent, words, language):
        super().__init__(parent, title="Generate Text")
        self.words = words
        self.language = language
        self.generated_title = None
        self.generated_text = None
        self.ai_label = ai.provider_label()

        self.setMinimumSize(640, 520)
        self.setAttribute(Qt.WA_DeleteOnClose)

        layout = self.content_layout
        layout.setContentsMargins(18, 18, 18, 14)
        layout.setSpacing(10)

        info = QLabel(f"Generating a {language} text from {len(words)} word(s) "
                      f"with {self.ai_label}:")
        layout.addWidget(info)
        words_label = QLabel(", ".join(words))
        words_label.setObjectName("dimLabel")
        words_label.setWordWrap(True)
        layout.addWidget(words_label)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Title…")
        layout.addWidget(self.title_edit)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Generated text appears here…")
        layout.addWidget(self.text_edit, 1)

        buttons = QHBoxLayout()
        self.generate_btn = QPushButton("Generate", objectName="primaryButton")
        self.generate_btn.clicked.connect(self.generate)
        buttons.addWidget(self.generate_btn)
        buttons.addStretch(1)
        self.save_btn = QPushButton("Save to Texts")
        self.save_btn.clicked.connect(self.save)
        self.save_btn.setEnabled(False)
        buttons.addWidget(self.save_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        if not ai.has_api_key():
            self.text_edit.setPlaceholderText(
                f"{self.ai_label} API key is not set. "
                f"Configure it in Settings → APIs → AI.")
            self.generate_btn.setEnabled(False)
        else:
            self.generate()

    def generate(self):
        self.generate_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.text_edit.setPlainText("Generating…")

        def work():
            return ai.generate_combined_text(", ".join(self.words), self.language)

        def done(result):
            title, text = result
            self.generated_title, self.generated_text = title, text
            self.title_edit.setText(title or "")
            self.text_edit.setPlainText(text or "")
            self.save_btn.setEnabled(bool(text))

        def fail(message):
            self.text_edit.setPlainText("")
            QMessageBox.critical(self, self.ai_label, message)

        run_in_thread(work, on_result=done, on_error=fail,
                      on_finished=lambda: self.generate_btn.setEnabled(True))

    def save(self):
        title = self.title_edit.text().strip()
        text = self.text_edit.toPlainText().strip()
        if not text:
            return
        ok, message = ai.save_generated_text_to_db(
            None, title, text, ", ".join(self.words), self.language)
        if ok:
            self.text_saved.emit()
            self.accept()
        else:
            logging.error(message)
            QMessageBox.critical(self, "Save failed", message)
