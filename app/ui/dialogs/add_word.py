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

"""Add Word dialog — compact two-row capture with DeepL translation,
language detect and inline TTS preview. New words are saved as 'New'."""
import logging

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton,
)

from app.config import get_bool, load_settings
from app.core.audio import speak_word
from app.core.backup_management import backup_database
from app.core.database_adapter import DatabaseAdapter
from app.core.translator import DEEPL_LANGUAGE_CODES, translate
from app.i18n import fill_lang_combo, get_lang, lang_label, set_lang, tr
from app.ui import icons
from app.ui.dialogs.base import FramelessDialog
from app.ui.workers import run_in_thread


class AddWordDialog(FramelessDialog):
    word_saved = Signal()

    def __init__(self, parent, prefill=None, auto_translate=False, language1=None):
        super().__init__(parent, title=tr("Add Word"))
        self.setMinimumWidth(540)
        self.setAttribute(Qt.WA_DeleteOnClose)

        settings = load_settings()
        # Cloud sync follows the login state (signed in ⇒ sync on).
        from app.core.auth_manager import get_auth_manager
        self.db_adapter = DatabaseAdapter(use_cloud=get_auth_manager().is_logged_in())
        colors = self.colors

        languages = sorted(DEEPL_LANGUAGE_CODES.keys())
        layout = self.content_layout
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self.lang1_combo = QComboBox()
        fill_lang_combo(self.lang1_combo, languages, head=["Detect language"])
        set_lang(self.lang1_combo, "English")
        self.lang1_combo.setFixedWidth(150)
        self.lang1_combo.setCursor(Qt.PointingHandCursor)
        grid.addWidget(self.lang1_combo, 0, 0)

        self.word1_edit = QLineEdit()
        self.word1_edit.setPlaceholderText(tr("Type a word or phrase…"))
        self.word1_edit.setClearButtonEnabled(True)
        speak1 = self.word1_edit.addAction(
            icons.icon("volume", colors["text_dim"], 16), QLineEdit.TrailingPosition)
        speak1.setToolTip(tr("Pronounce"))
        speak1.triggered.connect(lambda: self._speak(self.word1_edit.text(),
                                                     get_lang(self.lang1_combo)))
        grid.addWidget(self.word1_edit, 0, 1)

        self.swap_btn = QPushButton(objectName="iconButton")
        self.swap_btn.setIcon(icons.icon("swap", colors["text_dim"], 17))
        self.swap_btn.setIconSize(QSize(17, 17))
        self.swap_btn.setToolTip(tr("Swap word and translation"))
        self.swap_btn.setCursor(Qt.PointingHandCursor)
        self.swap_btn.clicked.connect(self.swap_entries)
        grid.addWidget(self.swap_btn, 0, 2, 2, 1, Qt.AlignVCenter)

        self.lang2_combo = QComboBox()
        fill_lang_combo(self.lang2_combo, languages)
        set_lang(self.lang2_combo, "German")
        self.lang2_combo.setFixedWidth(150)
        self.lang2_combo.setCursor(Qt.PointingHandCursor)
        grid.addWidget(self.lang2_combo, 1, 0)

        self.word2_edit = QLineEdit()
        self.word2_edit.setPlaceholderText(tr("Translation…"))
        self.word2_edit.setClearButtonEnabled(True)
        speak2 = self.word2_edit.addAction(
            icons.icon("volume", colors["text_dim"], 16), QLineEdit.TrailingPosition)
        speak2.setToolTip(tr("Pronounce"))
        speak2.triggered.connect(lambda: self._speak(self.word2_edit.text(),
                                                     get_lang(self.lang2_combo)))
        grid.addWidget(self.word2_edit, 1, 1)

        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.info_label = QLabel("")
        self.info_label.setObjectName("dimLabel")
        self.info_label.setWordWrap(True)
        self.info_label.hide()
        layout.addWidget(self.info_label)

        buttons = QHBoxLayout()
        self.translate_btn = QPushButton(f"  {tr('Translate')}")
        self.translate_btn.setIcon(icons.icon("globe", colors["text"], 15))
        self.translate_btn.setToolTip(tr("Translate with DeepL (Enter)"))
        self.translate_btn.setCursor(Qt.PointingHandCursor)
        self.translate_btn.clicked.connect(self.do_translate)
        buttons.addWidget(self.translate_btn)
        buttons.addStretch(1)
        cancel = QPushButton(tr("Cancel"))
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton(tr("Save Word"), objectName="primaryButton")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.save_word)
        save.setDefault(True)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        self.word1_edit.returnPressed.connect(self.do_translate)
        self.word2_edit.returnPressed.connect(self.save_word)
        self.word1_edit.setFocus()

        if prefill:
            self.word1_edit.setText(prefill)
            if language1 and self.lang1_combo.findData(language1) >= 0:
                set_lang(self.lang1_combo, language1)
            else:
                set_lang(self.lang1_combo, "Detect language")
            if len(prefill.split()) >= 100:
                self._info(tr("The text was truncated to the first 100 words."))
        if auto_translate and prefill:
            self.do_translate()

    # ------------------------------------------------------------------

    def _info(self, message):
        self.info_label.setText(message)
        self.info_label.setVisible(bool(message))

    def _speak(self, word, language):
        if not word.strip():
            return
        if language == "Detect language":
            language = "English"
        run_in_thread(speak_word, word, language, on_error=self._info)

    def swap_entries(self):
        w1, w2 = self.word1_edit.text(), self.word2_edit.text()
        l1 = get_lang(self.lang1_combo)
        l2 = get_lang(self.lang2_combo)
        self.word1_edit.setText(w2)
        self.word2_edit.setText(w1)
        if l1 != "Detect language":
            set_lang(self.lang1_combo, l2)
            set_lang(self.lang2_combo, l1)

    def do_translate(self):
        word = self.word1_edit.text().strip()
        if not word:
            self._info(tr("Enter a word to translate."))
            return
        source = get_lang(self.lang1_combo)
        target = get_lang(self.lang2_combo)
        self.translate_btn.setEnabled(False)
        self._info(tr("Translating…"))

        def work():
            translation, detected = translate(word, target, source)
            # Same-language guard: switch target like the original app
            effective_source = detected or (None if source == "Detect language" else source)
            if effective_source == target:
                new_target = 'German' if effective_source == 'English' else 'English'
                translation, _ = translate(word, new_target, effective_source)
                return translation, effective_source, new_target
            return translation, effective_source, target

        def done(result):
            translation, detected_source, target_used = result
            self.word2_edit.setText(translation)
            if detected_source and get_lang(self.lang1_combo) == "Detect language":
                set_lang(self.lang1_combo, detected_source)
            if target_used != get_lang(self.lang2_combo):
                set_lang(self.lang2_combo, target_used)
                self._info(tr("Source equals target — translated to {lang} instead.").format(lang=lang_label(target_used)))
            else:
                self._info("")

        run_in_thread(work, on_result=done, on_error=self._info,
                      on_finished=lambda: self.translate_btn.setEnabled(True))

    def save_word(self):
        word1 = self.word1_edit.text().strip()
        word2 = self.word2_edit.text().strip()
        lang1 = get_lang(self.lang1_combo)
        lang2 = get_lang(self.lang2_combo)

        if not word1 or not word2:
            self._info(tr("Both word and translation are required."))
            return
        if lang1 == "Detect language":
            self._info(tr("Please select the source language before saving."))
            return

        try:
            result = self.db_adapter.insert_word({
                'Language1': lang1, 'Word1': word1,
                'Language2': lang2, 'Word2': word2,
                'Status': 'New', 'Source': 'manual',
            })
            if not result:
                self._info(tr("'{word}' already exists in your dictionary.").format(word=f"{word1} – {word2}"))
                return
            backup_database()
            self.word_saved.emit()
            self.accept()
        except Exception as exc:
            logging.error(f"Error saving new word: {exc}")
            QMessageBox.critical(self, tr("Error"), tr("Failed to save word:\n{error}").format(error=exc))
