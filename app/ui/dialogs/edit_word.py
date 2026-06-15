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

"""Edit Word dialog."""
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
)

from app.i18n import fill_lang_combo, get_lang, set_lang, tr
from app.ui.dialogs.base import FramelessDialog


class EditWordDialog(FramelessDialog):
    def __init__(self, parent, record, languages, statuses):
        super().__init__(parent, title=tr("Edit — {word}").format(word=record.get('Word1', '')))
        self.setMinimumWidth(520)

        layout = self.content_layout
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel(tr("Language")), 0, 0)
        self.lang1_combo = QComboBox()
        self.lang1_combo.setEditable(True)
        fill_lang_combo(self.lang1_combo, languages)
        set_lang(self.lang1_combo, str(record.get('Language1') or ""))
        grid.addWidget(self.lang1_combo, 1, 0)

        grid.addWidget(QLabel(tr("Word")), 0, 1)
        self.word1_edit = QLineEdit(str(record.get('Word1') or ""))
        grid.addWidget(self.word1_edit, 1, 1)

        grid.addWidget(QLabel(tr("Translation language")), 2, 0)
        self.lang2_combo = QComboBox()
        self.lang2_combo.setEditable(True)
        fill_lang_combo(self.lang2_combo, languages)
        set_lang(self.lang2_combo, str(record.get('Language2') or ""))
        grid.addWidget(self.lang2_combo, 3, 0)

        grid.addWidget(QLabel(tr("Translation")), 2, 1)
        self.word2_edit = QLineEdit(str(record.get('Word2') or ""))
        grid.addWidget(self.word2_edit, 3, 1)

        grid.addWidget(QLabel(tr("Status")), 4, 0)
        self.status_combo = QComboBox()
        # Stored value stays English (item userData); only the label is localized.
        for s in statuses:
            self.status_combo.addItem(tr(s), s)
        idx = self.status_combo.findData(str(record.get('Status') or "New"))
        self.status_combo.setCurrentIndex(idx if idx >= 0 else 0)
        grid.addWidget(self.status_combo, 5, 0)

        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        self.info_label = QLabel(objectName="dimLabel")
        self.info_label.setWordWrap(True)
        self.info_label.setVisible(False)
        layout.addWidget(self.info_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton(tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton(tr("Save"), objectName="primaryButton")
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _info(self, message):
        self.info_label.setText(message)
        self.info_label.setVisible(bool(message))

    def accept(self):
        from app.core.audio import is_language_supported
        for combo, word_edit in (
            (self.lang1_combo, self.word1_edit),
            (self.lang2_combo, self.word2_edit),
        ):
            lang = get_lang(combo).strip()
            # Only enforce on the side that actually has a word to speak.
            if word_edit.text().strip() and not is_language_supported(lang):
                self._info(tr("Unsupported language: {lang}. Pick one from the list.")
                           .format(lang=lang or tr("(empty)")))
                combo.setFocus()
                return
        super().accept()

    def result_data(self):
        return {
            'Language1': get_lang(self.lang1_combo).strip(),
            'Word1': self.word1_edit.text().strip(),
            'Language2': get_lang(self.lang2_combo).strip(),
            'Word2': self.word2_edit.text().strip(),
            'Status': self.status_combo.currentData(),
        }
