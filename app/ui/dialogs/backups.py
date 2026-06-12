"""Backup management: list, preview, restore and delete daily DB backups."""
import logging
import os
import shutil
import sqlite3
from datetime import datetime

from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem,
)

from app.ui.dialogs.base import FramelessDialog

BACKUP_DIR = 'backups'
DB_PATH = 'dictionary.db'


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


class BackupsDialog(FramelessDialog):
    def __init__(self, parent, on_restored=None):
        super().__init__(parent, title="Backups")
        self.on_restored = on_restored
        self.setMinimumSize(640, 440)

        layout = self.content_layout
        layout.setContentsMargins(16, 16, 16, 12)

        hint = QLabel("A backup is created automatically after every change. "
                      "One backup per day is kept for the current month, "
                      "one per month for older months.")
        hint.setObjectName("dimLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["File", "Date", "Words", "Texts", "Tags"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 250)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        restore_btn = QPushButton("Restore Selected", objectName="primaryButton")
        restore_btn.clicked.connect(self.restore_selected)
        buttons.addWidget(restore_btn)
        delete_btn = QPushButton("Delete Selected", objectName="dangerButton")
        delete_btn.clicked.connect(self.delete_selected)
        buttons.addWidget(delete_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self.load_backups()

    def load_backups(self):
        self.table.setRowCount(0)
        if not os.path.isdir(BACKUP_DIR):
            return
        files = sorted(
            (f for f in os.listdir(BACKUP_DIR)
             if f.startswith('dictionary_backup_') and f.endswith('.db')),
            reverse=True)
        for filename in files:
            path = os.path.join(BACKUP_DIR, filename)
            date_str = filename[18:-3]
            words, texts, tags = _backup_counts(path)
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate([filename, date_str, words, texts, tags]):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))

    def _selected_file(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "Backups", "Select a backup first.")
            return None
        return self.table.item(rows[0].row(), 0).text()

    def restore_selected(self):
        filename = self._selected_file()
        if not filename:
            return
        if QMessageBox.question(
                self, "Restore Backup",
                f"Replace the current database with '{filename}'?\n\n"
                "A safety copy of the current database will be made first.",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            safety = f"dictionary_pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            shutil.copy2(DB_PATH, os.path.join(BACKUP_DIR, safety))
            shutil.copy2(os.path.join(BACKUP_DIR, filename), DB_PATH)
            QMessageBox.information(self, "Restore", "Backup restored successfully.")
            if self.on_restored:
                self.on_restored()
            self.accept()
        except Exception as exc:
            logging.error(f"Restore failed: {exc}")
            QMessageBox.critical(self, "Restore Error", f"Failed to restore backup:\n{exc}")

    def delete_selected(self):
        filename = self._selected_file()
        if not filename:
            return
        if QMessageBox.question(self, "Delete Backup", f"Delete '{filename}'?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            os.remove(os.path.join(BACKUP_DIR, filename))
            self.load_backups()
        except Exception as exc:
            QMessageBox.critical(self, "Delete Error", f"Failed to delete backup:\n{exc}")
