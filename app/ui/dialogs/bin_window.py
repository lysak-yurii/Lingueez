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

"""Bin: soft-deleted words/texts stored in the Supabase cloud."""
import logging
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QAbstractItemView,
)

from app.config import get_int, load_settings
from app.core.supabase_client import SupabaseClient
from app.i18n import lang_label, tr
from app.ui.dialogs.base import FramelessDialog


def _fmt_date(value):
    if not value:
        return ""
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(value)


class BinWindow(FramelessDialog):
    def __init__(self, parent, db_adapter, on_restored=None):
        super().__init__(parent, title=tr("Bin — Deleted Items"))
        self.db_adapter = db_adapter
        self.on_restored = on_restored
        self.supabase = SupabaseClient()

        self.setMinimumSize(760, 480)
        self.setAttribute(Qt.WA_DeleteOnClose)

        layout = self.content_layout
        layout.setContentsMargins(16, 16, 16, 12)

        if not self.supabase.is_connected():
            note = QLabel(tr("The Bin requires cloud sync (Supabase). Enable and configure it in Settings → APIs → Sync."))
            note.setWordWrap(True)
            layout.addWidget(note)
            close_btn = QPushButton(tr("Close"))
            close_btn.clicked.connect(self.reject)
            layout.addWidget(close_btn, alignment=Qt.AlignRight)
            return

        self.tabs = QTabWidget()
        self.words_table = self._make_table([
            "ID", tr("Word"), tr("Translation"), tr("Language"), tr("Translation language"), tr("Deleted at")])
        self.texts_table = self._make_table([
            "ID", tr("Title"), tr("Language"), tr("Category"), tr("Deleted at")])
        self.tabs.addTab(self.words_table, tr("Words"))
        self.tabs.addTab(self.texts_table, tr("Texts"))
        layout.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        restore_btn = QPushButton(tr("Restore Selected"), objectName="primaryButton")
        restore_btn.clicked.connect(self.restore_selected)
        buttons.addWidget(restore_btn)
        delete_btn = QPushButton(tr("Delete Permanently"), objectName="dangerButton")
        delete_btn.clicked.connect(self.delete_selected)
        buttons.addWidget(delete_btn)
        cleanup_btn = QPushButton(tr("Cleanup Old Items…"))
        cleanup_btn.clicked.connect(self.manual_cleanup)
        buttons.addWidget(cleanup_btn)
        buttons.addStretch(1)
        refresh_btn = QPushButton(tr("Refresh"))
        refresh_btn.clicked.connect(self.load_data)
        buttons.addWidget(refresh_btn)
        close_btn = QPushButton(tr("Close"))
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.load_data()

    def _make_table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        # Size each column to its header/content so labels aren't clipped
        # (e.g. "Translation language"); the last column fills the remainder.
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)
        return table

    def load_data(self):
        try:
            words = self.supabase.get_all_soft_deleted_items('words')
            self.words_table.setRowCount(0)
            for word in words:
                row = self.words_table.rowCount()
                self.words_table.insertRow(row)
                for col, value in enumerate([
                        word.get('ID') or word.get('id'), word.get('Word1', ''),
                        word.get('Word2', ''), lang_label(word.get('Language1', '')),
                        lang_label(word.get('Language2', '')), _fmt_date(word.get('deleted_at'))]):
                    self.words_table.setItem(row, col, QTableWidgetItem(str(value)))

            texts = self.supabase.get_all_soft_deleted_items('texts')
            self.texts_table.setRowCount(0)
            for text in texts:
                row = self.texts_table.rowCount()
                self.texts_table.insertRow(row)
                for col, value in enumerate([
                        text.get('ID') or text.get('id'), text.get('Title', ''),
                        lang_label(text.get('Language', '')), text.get('Category', ''),
                        _fmt_date(text.get('deleted_at'))]):
                    self.texts_table.setItem(row, col, QTableWidgetItem(str(value)))
        except Exception as exc:
            logging.error(f"Error loading soft-deleted items: {exc}")
            QMessageBox.critical(self, tr("Error"), tr("Failed to load deleted items:\n{error}").format(error=exc))

    def _selected(self):
        if self.tabs.currentIndex() == 0:
            table, item_type = self.words_table, "words"
        else:
            table, item_type = self.texts_table, "texts"
        rows = sorted({ix.row() for ix in table.selectionModel().selectedRows()}, reverse=True)
        return [(item_type, int(table.item(r, 0).text()), r, table) for r in rows]

    def restore_selected(self):
        items = self._selected()
        if not items:
            QMessageBox.information(self, tr("Bin"), tr("Select item(s) to restore."))
            return
        if QMessageBox.question(self, tr("Restore"),
                                tr("Restore {count} item(s)?").format(count=len(items)),
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        restored = failed = 0
        for item_type, record_id, row, table in items:
            try:
                ok = (self.db_adapter.restore_word(record_id) if item_type == "words"
                      else self.db_adapter.restore_text(record_id))
                if ok:
                    table.removeRow(row)
                    restored += 1
                else:
                    failed += 1
            except Exception as exc:
                logging.error(f"Error restoring {item_type} {record_id}: {exc}")
                failed += 1
        if restored and self.on_restored:
            self.on_restored()
        msg = tr("Restored {count} item(s).").format(count=restored)
        if failed:
            msg += " " + tr("{n} failed.").format(n=failed)
        QMessageBox.information(self, tr("Restore"), msg)

    def delete_selected(self):
        items = self._selected()
        if not items:
            QMessageBox.information(self, tr("Bin"), tr("Select item(s) to delete permanently."))
            return
        if QMessageBox.question(
                self, tr("Permanent Delete"),
                tr("Permanently delete {count} item(s)?\n\nThis cannot be undone!").format(count=len(items)),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        deleted = failed = 0
        for item_type, record_id, row, table in items:
            try:
                ok = (self.supabase.hard_delete_word(record_id) if item_type == "words"
                      else self.supabase.hard_delete_text(record_id))
                if ok:
                    table.removeRow(row)
                    deleted += 1
                else:
                    failed += 1
            except Exception as exc:
                logging.error(f"Error permanently deleting {item_type} {record_id}: {exc}")
                failed += 1
        msg = tr("Permanently deleted {count} item(s).").format(count=deleted)
        if failed:
            msg += " " + tr("{n} failed.").format(n=failed)
        QMessageBox.information(self, tr("Delete"), msg)

    def manual_cleanup(self):
        settings = load_settings()
        grace_days = get_int(settings, 'cleanup_grace_period_days', 30)
        try:
            words_count = self.supabase.get_old_soft_deletes_count('words', grace_days)
            texts_count = self.supabase.get_old_soft_deletes_count('texts', grace_days)
        except Exception as exc:
            QMessageBox.critical(self, tr("Cleanup"), tr("Failed to count old items:\n{error}").format(error=exc))
            return
        total = words_count + texts_count
        if total == 0:
            QMessageBox.information(self, tr("Cleanup"),
                                    tr("No items older than {n} days found.").format(n=grace_days))
            return
        if QMessageBox.question(
                self, tr("Cleanup"),
                tr("Permanently delete {total} item(s) deleted more than {days} days ago?\n"
                   "({words} words, {texts} texts)\n\nThis cannot be undone!").format(
                       total=total, days=grace_days, words=words_count, texts=texts_count),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            words_deleted = self.supabase.cleanup_old_soft_deletes('words', grace_days)
            texts_deleted = self.supabase.cleanup_old_soft_deletes('texts', grace_days)
            QMessageBox.information(
                self, tr("Cleanup"),
                tr("Permanently deleted {count} old item(s).").format(count=words_deleted + texts_deleted))
            self.load_data()
        except Exception as exc:
            logging.error(f"Cleanup failed: {exc}")
            QMessageBox.critical(self, tr("Cleanup"), tr("Failed to cleanup:\n{error}").format(error=exc))
