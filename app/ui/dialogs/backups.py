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

"""Restore points: list, restore and remove the app's automatic daily backups."""
import logging
import os
import shutil
import sqlite3
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from app.i18n import full_date, month_abbr, ntr, tr, weekday_name
from app.ui.dialogs.base import FramelessDialog

BACKUP_DIR = 'backups'
DB_PATH = 'dictionary.db'
DAILY_PREFIX = 'dictionary_backup_'
SNAPSHOT_PREFIX = 'dictionary_pre_restore_'


def _backup_counts(path):
    """Return (words, texts, tags) counts inside a backup file."""
    counts = []
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        for table in ("words", "texts", "tags"):
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts.append(cursor.fetchone()[0])
            except sqlite3.Error:
                counts.append(0)
        conn.close()
    except Exception as exc:
        logging.error(f"Error reading backup {path}: {exc}")
        counts = [0, 0, 0]
    return counts


def _friendly_date(date):
    """A backup date -> a plain-language label ('Today', 'Monday · June 9, 2026')."""
    today = datetime.now().date()
    days = (today - date.date()).days
    full = full_date(date)
    if days <= 0:
        return tr("Today")
    if days == 1:
        return tr("Yesterday")
    if days < 7:
        return f"{weekday_name(date)} · {full}"
    return full


def _date_phrase(date):
    """A backup date -> a phrase that reads naturally inside a sentence."""
    today = datetime.now().date()
    days = (today - date.date()).days
    full = full_date(date)
    if days <= 0:
        return tr("today")
    if days == 1:
        return tr("yesterday")
    if days < 7:
        return f"{weekday_name(date)}, {full}"
    return full


def _short_when(moment):
    """A snapshot timestamp -> 'today 18:20' / 'yesterday 09:05' / 'Jun 9 18:20'."""
    today = datetime.now().date()
    days = (today - moment.date()).days
    time = moment.strftime('%H:%M')
    if days <= 0:
        return tr("today {time}").format(time=time)
    if days == 1:
        return tr("yesterday {time}").format(time=time)
    return f"{month_abbr(moment)} {moment.day} {time}"


def _content_summary(words, texts, tags):
    """Counts -> 'X words · Y texts · Z tags' with grammar and grouping."""
    w_noun = ntr(words, tr("word"), tr("words"), tr("words"))
    t_noun = ntr(texts, tr("text"), tr("texts"), tr("texts"))
    g_noun = ntr(tags, tr("tag"), tr("tags"), tr("tags"))
    return " · ".join((f"{words:,} {w_noun}", f"{texts:,} {t_noun}", f"{tags:,} {g_noun}"))


class BackupsDialog(FramelessDialog):
    def __init__(self, parent, on_restored=None):
        super().__init__(parent, title=tr("Restore an earlier version"))
        self.on_restored = on_restored
        self.setMinimumSize(560, 460)

        layout = self.content_layout

        hint = QLabel(tr("Your database is backed up automatically after every change. Pick an earlier version below to restore it."))
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.listing = QListWidget(objectName="BackupsList")
        self.listing.setSelectionMode(QAbstractItemView.SingleSelection)
        self.listing.itemSelectionChanged.connect(self._update_buttons)
        self.listing.itemDoubleClicked.connect(lambda _i: self.restore_selected())
        layout.addWidget(self.listing, 1)

        self.empty_label = QLabel(tr("No saved versions yet. A backup is made automatically after every change."))
        self.empty_label.setObjectName("dimLabel")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()
        layout.addWidget(self.empty_label, 1)

        buttons = QHBoxLayout()
        self.restore_btn = QPushButton(tr("Restore this version"), objectName="primaryButton")
        self.restore_btn.clicked.connect(self.restore_selected)
        buttons.addWidget(self.restore_btn)
        self.delete_btn = QPushButton(tr("Delete"), objectName="dangerButton")
        self.delete_btn.clicked.connect(self.delete_selected)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        close_btn = QPushButton(tr("Close"))
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.load_backups()

    def _collect_entries(self):
        """Restore points sorted newest-first: daily backups + the latest undo point."""
        entries = []
        if not os.path.isdir(BACKUP_DIR):
            return entries

        daily, snapshots = [], []
        for filename in os.listdir(BACKUP_DIR):
            if not filename.endswith('.db'):
                continue
            try:
                if filename.startswith(DAILY_PREFIX):
                    when = datetime.strptime(filename[len(DAILY_PREFIX):-3], '%Y-%m-%d')
                    daily.append((when, filename))
                elif filename.startswith(SNAPSHOT_PREFIX):
                    when = datetime.strptime(filename[len(SNAPSHOT_PREFIX):-3], '%Y%m%d_%H%M%S')
                    snapshots.append((when, filename))
            except ValueError:
                logging.error(f"Unrecognised backup name: {filename}")

        daily.sort(reverse=True)
        for index, (when, filename) in enumerate(daily):
            title = _friendly_date(when) + (f" · {tr('Most recent')}" if index == 0 else "")
            entries.append({
                "when": when, "filename": filename, "title": title,
                "summary": self._summary_for(filename),
                "phrase": tr("the version from {date}").format(date=_date_phrase(when)),
            })

        # Only the most recent undo point is offered; older ones are pruned on restore.
        if snapshots:
            when, filename = max(snapshots)
            entries.append({
                "when": when, "filename": filename,
                "title": tr("Before your last restore"),
                "summary": tr("Saved {when} · {summary}").format(
                    when=_short_when(when), summary=self._summary_for(filename)),
                "phrase": tr("the version from just before your last restore"),
            })

        entries.sort(key=lambda e: e["when"], reverse=True)
        return entries

    @staticmethod
    def _summary_for(filename):
        words, texts, tags = _backup_counts(os.path.join(BACKUP_DIR, filename))
        return _content_summary(words, texts, tags)

    def load_backups(self):
        self.listing.clear()
        for entry in self._collect_entries():
            item = QListWidgetItem(self.listing)
            item.setData(Qt.UserRole, entry["filename"])
            item.setData(Qt.UserRole + 1, entry["phrase"])
            row = self._make_row(entry["title"], entry["summary"])
            item.setSizeHint(row.sizeHint())
            self.listing.setItemWidget(item, row)

        has_items = self.listing.count() > 0
        self.listing.setVisible(has_items)
        self.empty_label.setVisible(not has_items)
        self._update_buttons()

    def _make_row(self, title, summary):
        row = QWidget()
        box = QVBoxLayout(row)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(2)
        primary = QLabel(title)
        primary.setObjectName("backupTitle")
        secondary = QLabel(summary)
        secondary.setObjectName("dimLabel")
        box.addWidget(primary)
        box.addWidget(secondary)
        return row

    def _selected(self):
        """Return (filename, sentence_phrase) for the selection, or (None, None)."""
        items = self.listing.selectedItems()
        if not items:
            return None, None
        item = items[0]
        return item.data(Qt.UserRole), item.data(Qt.UserRole + 1)

    def _update_buttons(self):
        enabled = bool(self.listing.selectedItems())
        self.restore_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled)

    def restore_selected(self):
        filename, phrase = self._selected()
        if not filename:
            return
        if QMessageBox.question(
                self, tr("Restore Version"),
                tr("Restore {phrase}?\n\nYour current data is saved first, so you can undo this.").format(phrase=phrase),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            safety = f"{SNAPSHOT_PREFIX}{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            shutil.copy2(DB_PATH, os.path.join(BACKUP_DIR, safety))
            shutil.copy2(os.path.join(BACKUP_DIR, filename), DB_PATH)
            self._prune_snapshots(keep=safety)
            QMessageBox.information(
                self, tr("Restore"),
                tr('Your database has been restored to {phrase}.\n\nChanged your mind? Restore "{before}" to undo.').format(
                    phrase=phrase, before=tr("Before your last restore")))
            if self.on_restored:
                self.on_restored()
            self.accept()
        except Exception as exc:
            logging.error(f"Restore failed: {exc}")
            QMessageBox.critical(self, tr("Restore Error"),
                                 tr("Sorry, that version could not be restored:\n{error}").format(error=exc))

    def _prune_snapshots(self, keep):
        """Keep only one undo point so old pre-restore snapshots don't pile up."""
        for filename in os.listdir(BACKUP_DIR):
            if filename.startswith(SNAPSHOT_PREFIX) and filename != keep:
                try:
                    os.remove(os.path.join(BACKUP_DIR, filename))
                except OSError as exc:
                    logging.error(f"Could not remove old snapshot {filename}: {exc}")

    def delete_selected(self):
        filename, phrase = self._selected()
        if not filename:
            return
        if QMessageBox.question(
                self, tr("Remove Version"),
                tr("Remove {phrase}?").format(phrase=phrase),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            os.remove(os.path.join(BACKUP_DIR, filename))
            self.load_backups()
        except Exception as exc:
            QMessageBox.critical(self, tr("Remove Error"),
                                 tr("Sorry, that version could not be removed:\n{error}").format(error=exc))
