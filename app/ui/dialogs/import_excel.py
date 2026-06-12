"""Excel-import flow: analyze in background, review per-row, apply, report.

One dialog hosts the whole flow — classification table with per-row
checkboxes, filter chips, an embedded collapsible activity log and a
progress bar — so nothing is layered in extra popups.
"""
import logging
import os
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QToolButton,
)

from app.config import load_settings
from app.core.backup_management import backup_database
from app.core.importer import (
    ACTION_ADD, ACTION_SKIP, ACTION_UPDATE, analyze_excel_import,
    apply_additions, apply_updates,
)
from app.ui.dialogs.base import FramelessDialog
from app.ui.dialogs.log_window import LEVEL_COLORS
from app.ui.workers import run_in_thread

logger = logging.getLogger(__name__)

_PY_LEVELS = {'error': logging.ERROR, 'warning': logging.WARNING}

COL_CHECK, COL_ROW, COL_WORD1, COL_LANG1, COL_WORD2, COL_LANG2, COL_ACTION, COL_DETAILS = range(8)
HEADERS = ["", "Row", "Word 1", "Language 1", "Word 2", "Language 2", "Action", "Details"]

ACTION_LABELS = {ACTION_ADD: "Add", ACTION_UPDATE: "Update", ACTION_SKIP: "Skip"}
ACTION_LEVEL = {ACTION_ADD: 'new', ACTION_UPDATE: 'warning', ACTION_SKIP: None}

FILTERS = [("all", "All"), (ACTION_ADD, "To add"),
           (ACTION_UPDATE, "To update"), (ACTION_SKIP, "Skipped")]


def _cell_text(value):
    if value is None:
        return ""
    text = str(value)
    return "" if text.lower() == 'nan' else text


class ImportReviewDialog(FramelessDialog):
    """Single-window import experience: analyze → review/select → apply."""

    _log_line = Signal(str, str)  # message, level — safe from worker threads

    def __init__(self, parent, db_adapter, path):
        super().__init__(parent, title="Import from Excel")
        self.main = parent
        self.db_adapter = db_adapter
        self.path = path
        self._applying = False
        self._populating = False
        self._row_payloads = []

        self.setMinimumSize(860, 520)
        self.resize(1040, 660)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.WindowModal)

        self._log_line.connect(self._append_log)
        self._build_ui()
        self._start_analysis()

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        layout = self.content_layout
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        file_label = QLabel(f"<b>{os.path.basename(self.path)}</b>")
        header.addWidget(file_label)
        header.addStretch(1)
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(f"color: {self.colors['text_dim']};")
        header.addWidget(self.summary_label)
        layout.addLayout(header)

        hint = QLabel("Expected columns: Language1, Language2, Word1, Word2 — named in a "
                      "header row, or headerless with the first four columns in that order. "
                      "A ready-made template is available in the app menu → Save Import Template.")
        hint.setStyleSheet(f"color: {self.colors['text_dim']};")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.filter_buttons = {}
        for key, label in FILTERS:
            btn = QPushButton(label, objectName="chipButton")
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(Qt.PointingHandCursor)
            toolbar.addWidget(btn)
            self.filter_buttons[key] = btn
        self.filter_buttons["all"].setChecked(True)
        toolbar.addStretch(1)
        self.select_all = QCheckBox("Select all")
        self.select_all.setTristate(False)
        self.select_all.clicked.connect(self._on_select_all)
        toolbar.addWidget(self.select_all)
        self.selection_label = QLabel("")
        self.selection_label.setStyleSheet(f"color: {self.colors['text_dim']};")
        toolbar.addWidget(self.selection_label)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header_view = self.table.horizontalHeader()
        header_view.setStretchLastSection(True)
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(COL_CHECK, 34)
        self.table.setColumnWidth(COL_ROW, 52)
        for col in (COL_WORD1, COL_WORD2):
            self.table.setColumnWidth(col, 190)
        for col in (COL_LANG1, COL_LANG2):
            self.table.setColumnWidth(col, 100)
        self.table.setColumnWidth(COL_ACTION, 80)
        self.table.itemChanged.connect(self._on_item_changed)
        header_view.sortIndicatorChanged.connect(lambda *_: self._apply_filter())
        # connect chips only now that the table they filter exists
        for btn in self.filter_buttons.values():
            btn.toggled.connect(self._apply_filter)
        layout.addWidget(self.table, 1)

        log_bar = QHBoxLayout()
        self.log_toggle = QToolButton()
        self.log_toggle.setText("Activity log")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setArrowType(Qt.RightArrow)
        self.log_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.log_toggle.setAutoRaise(True)
        self.log_toggle.setCursor(Qt.PointingHandCursor)
        self.log_toggle.toggled.connect(self._toggle_log)
        log_bar.addWidget(self.log_toggle)
        log_bar.addStretch(1)
        self.export_log_btn = QPushButton("Export log…")
        self.export_log_btn.clicked.connect(self._export_log)
        self.export_log_btn.hide()
        log_bar.addWidget(self.export_log_btn)
        layout.addLayout(log_bar)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.NoWrap)
        self.log_view.setMaximumHeight(180)
        self.log_view.hide()
        layout.addWidget(self.log_view)

        status_bar = QHBoxLayout()
        self.status_label = QLabel("")
        status_bar.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(220)
        self.progress.setTextVisible(False)
        self.progress.hide()
        status_bar.addWidget(self.progress)
        layout.addLayout(status_bar)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.import_btn = QPushButton("Import", objectName="primaryButton")
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._start_import)
        buttons.addWidget(self.import_btn)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.select_all.setEnabled(False)
        for btn in self.filter_buttons.values():
            btn.setEnabled(False)

    # ------------------------------------------------------------- logging

    def _log(self, message, level='info'):
        # safe from any thread: the signal queues onto the GUI thread
        self._log_line.emit(message, level)
        logger.log(_PY_LEVELS.get(level, logging.INFO), message)

    def _append_log(self, message, level):
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        time_fmt = QTextCharFormat()
        time_fmt.setForeground(QColor(self.colors['text_dim']))
        cursor.insertText(time.strftime("%H:%M:%S  "), time_fmt)
        fmt = QTextCharFormat()
        color = LEVEL_COLORS.get(level)
        if color:
            fmt.setForeground(QColor(color))
        cursor.insertText(message + "\n", fmt)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()
        if level == 'error' and not self.log_toggle.isChecked():
            self.log_toggle.setChecked(True)

    def _toggle_log(self, expanded):
        self.log_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.log_view.setVisible(expanded)
        self.export_log_btn.setVisible(expanded)

    def _export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Import Log",
                                              "import-log.txt", "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.log_view.toPlainText())

    # ------------------------------------------------------------- analyze

    def _start_analysis(self):
        self.status_label.setText("Analyzing file…")
        self.progress.setRange(0, 0)
        self.progress.show()
        self._log(f"Selected file: {self.path}")
        settings = load_settings()

        def work():
            return analyze_excel_import(self.path, settings, log=self._log)

        run_in_thread(work, on_result=self._on_analyzed, on_error=self._on_analyze_error)

    def _on_analyze_error(self, error):
        self.progress.hide()
        self.status_label.setText("Analysis failed — see the activity log.")
        self._log(f"Analysis failed: {error}", 'error')

    def _on_analyzed(self, result):
        self.progress.hide()
        if result is None:
            self.status_label.setText("Could not read the Excel file — see the activity log.")
            self._log("Could not read the Excel file.", 'error')
            return

        rows, counts = result['rows'], result['counts']
        self._populate_table(rows)

        self.filter_buttons["all"].setText(f"All ({counts['total']})")
        self.filter_buttons[ACTION_ADD].setText(f"To add ({counts['add']})")
        self.filter_buttons[ACTION_UPDATE].setText(f"To update ({counts['update']})")
        self.filter_buttons[ACTION_SKIP].setText(f"Skipped ({counts['skip']})")
        for btn in self.filter_buttons.values():
            btn.setEnabled(True)
        self.summary_label.setText(
            f"{counts['total']} rows: {counts['add']} new · "
            f"{counts['update']} updates · {counts['skip']} skipped")

        if counts['add'] or counts['update']:
            self.select_all.setEnabled(True)
            self.status_label.setText("Review the proposed changes, then import the selected rows.")
        else:
            self.status_label.setText("Nothing to import — no new or changed entries found.")
        self._refresh_selection_ui()

    def _populate_table(self, rows):
        self._populating = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        dim = QColor(self.colors['text_dim'])
        for row_idx, payload in enumerate(rows):
            actionable = payload['action'] != ACTION_SKIP

            check = QTableWidgetItem()
            check.setData(Qt.UserRole, payload)
            if actionable:
                check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                check.setCheckState(Qt.Checked)
            else:
                check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(row_idx, COL_CHECK, check)

            row_item = QTableWidgetItem()
            row_item.setData(Qt.DisplayRole, payload['row'])
            row_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row_idx, COL_ROW, row_item)

            for col, key in ((COL_WORD1, 'Word1'), (COL_LANG1, 'Language1'),
                             (COL_WORD2, 'Word2'), (COL_LANG2, 'Language2')):
                item = QTableWidgetItem(_cell_text(payload[key]))
                if not actionable:
                    item.setForeground(dim)
                self.table.setItem(row_idx, col, item)

            action_item = QTableWidgetItem(ACTION_LABELS[payload['action']])
            level = ACTION_LEVEL[payload['action']]
            action_item.setForeground(QColor(LEVEL_COLORS[level]) if level else dim)
            if actionable:
                font = action_item.font()
                font.setBold(True)
                action_item.setFont(font)
            self.table.setItem(row_idx, COL_ACTION, action_item)

            detail_item = QTableWidgetItem(payload['detail'])
            detail_item.setToolTip(payload['detail'])
            if not actionable:
                detail_item.setForeground(dim)
            self.table.setItem(row_idx, COL_DETAILS, detail_item)

        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_ROW, Qt.AscendingOrder)
        self._populating = False
        self._apply_filter()

    # ---------------------------------------------------- selection/filter

    def _current_filter(self):
        for key, btn in self.filter_buttons.items():
            if btn.isChecked():
                return key
        return "all"

    def _apply_filter(self, *_):
        wanted = self._current_filter()
        for row_idx in range(self.table.rowCount()):
            payload = self.table.item(row_idx, COL_CHECK).data(Qt.UserRole)
            self.table.setRowHidden(row_idx, wanted != "all" and payload['action'] != wanted)

    def _checkable_items(self, visible_only=False):
        items = []
        for row_idx in range(self.table.rowCount()):
            if visible_only and self.table.isRowHidden(row_idx):
                continue
            item = self.table.item(row_idx, COL_CHECK)
            if item.flags() & Qt.ItemIsUserCheckable:
                items.append(item)
        return items

    def _on_item_changed(self, item):
        if not self._populating and item.column() == COL_CHECK:
            self._refresh_selection_ui()

    def _on_select_all(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        self._populating = True
        for item in self._checkable_items(visible_only=True):
            item.setCheckState(state)
        self._populating = False
        self._refresh_selection_ui()

    def _selected_payloads(self):
        return [item.data(Qt.UserRole) for item in self._checkable_items()
                if item.checkState() == Qt.Checked]

    def _refresh_selection_ui(self):
        if self._applying:
            return
        items = self._checkable_items()
        selected = sum(1 for i in items if i.checkState() == Qt.Checked)
        self.selection_label.setText(f"{selected} of {len(items)} selected" if items else "")

        self.select_all.blockSignals(True)
        if not items or selected == 0:
            # tristate off while fully (un)checked so a click toggles
            # directly instead of cycling through the partial state
            self.select_all.setTristate(False)
            self.select_all.setCheckState(Qt.Unchecked)
        elif selected == len(items):
            self.select_all.setTristate(False)
            self.select_all.setCheckState(Qt.Checked)
        else:
            self.select_all.setTristate(True)
            self.select_all.setCheckState(Qt.PartiallyChecked)
        self.select_all.blockSignals(False)

        self.import_btn.setEnabled(selected > 0)
        self.import_btn.setText(f"Import {selected} Item(s)" if selected else "Import")

    # --------------------------------------------------------------- apply

    def _start_import(self):
        payloads = self._selected_payloads()
        adds = [p for p in payloads if p['action'] == ACTION_ADD]
        updates = [p for p in payloads if p['action'] == ACTION_UPDATE]
        total = len(adds) + len(updates)
        if not total:
            return

        self._applying = True
        self._set_review_locked(True)
        self.close_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.import_btn.setText("Importing…")
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.progress.show()
        self.status_label.setText(f"Importing {total} item(s)…")
        self._log(f"Starting import: {len(adds)} addition(s), {len(updates)} update(s).")

        if hasattr(self.main, '_sync_before_db_operation'):
            self.main._sync_before_db_operation()

        def work(progress_callback=None):
            def prog(offset):
                return (lambda done, _n: progress_callback(offset + done, total)) \
                    if progress_callback else None

            added, add_failed = apply_additions(
                self.db_adapter, adds, log=self._log, progress=prog(0))
            updated, update_failed = apply_updates(
                self.db_adapter, updates, log=self._log, progress=prog(len(adds)))

            backup_error = None
            try:
                backup_database()
                self._log("Database backed up.")
            except Exception as exc:
                backup_error = str(exc)
                self._log(f"Error during backup: {exc}", 'error')

            return {'added': added, 'updated': updated,
                    'failed': add_failed + update_failed,
                    'backup_error': backup_error}

        run_in_thread(work, wants_progress=True,
                      on_progress=lambda done, _t: self.progress.setValue(done or 0),
                      on_result=self._on_applied, on_error=self._on_apply_error)

    def _set_review_locked(self, locked):
        """Freeze checkboxes and bulk-selection controls (filters stay usable)."""
        self.select_all.setEnabled(not locked)
        self._populating = True
        for item in self._checkable_items():
            flags = item.flags()
            item.setFlags(flags & ~Qt.ItemIsUserCheckable if locked
                          else flags | Qt.ItemIsUserCheckable)
        self._populating = False

    def _on_apply_error(self, error):
        self._applying = False
        self.progress.hide()
        self.close_btn.setEnabled(True)
        self.import_btn.setText("Import failed")
        self.status_label.setText("Import failed — see the activity log.")
        self._log(f"Import failed: {error}", 'error')

    def _on_applied(self, summary):
        self._applying = False
        self.progress.hide()
        self.close_btn.setEnabled(True)
        self.import_btn.hide()

        # match by file row number — payload dicts lose Python identity on
        # the round-trip through QTableWidgetItem.data()
        failed_rows = {p['row'] for p in summary['failed']}
        ok_color = QColor(LEVEL_COLORS['success'])
        fail_color = QColor(LEVEL_COLORS['error'])
        # suspend sorting so editing the Action column can't reorder rows
        # mid-loop when the user sorted by that column
        self.table.setSortingEnabled(False)
        for row_idx in range(self.table.rowCount()):
            check = self.table.item(row_idx, COL_CHECK)
            payload = check.data(Qt.UserRole)
            if payload['action'] == ACTION_SKIP or check.checkState() != Qt.Checked:
                continue
            action_item = self.table.item(row_idx, COL_ACTION)
            if payload['row'] in failed_rows:
                action_item.setText("Failed")
                action_item.setForeground(fail_color)
            else:
                action_item.setText("Added" if payload['action'] == ACTION_ADD else "Updated")
                action_item.setForeground(ok_color)
        self.table.setSortingEnabled(True)

        parts = [f"{summary['added']} added", f"{summary['updated']} updated"]
        if summary['failed']:
            parts.append(f"{len(summary['failed'])} failed")
        message = "Import finished: " + ", ".join(parts) + "."
        if summary['backup_error']:
            message += " Backup failed — see the activity log."
        self.status_label.setText(message)
        self._log(message, 'error' if summary['failed'] else 'success')

        if hasattr(self.main, 'load_data'):
            self.main.load_data()

    # --------------------------------------------------------------- close

    def reject(self):
        if not self._applying:
            super().reject()

    def closeEvent(self, event):
        if self._applying:
            event.ignore()
        else:
            super().closeEvent(event)


class ImportExcelFlow:
    """Entry point kept for the main window: pick a file, open the review."""

    def __init__(self, main_window, db_adapter):
        self.main = main_window
        self.db_adapter = db_adapter

    def run(self):
        path, _ = QFileDialog.getOpenFileName(self.main, "Import Excel", "",
                                              "Excel files (*.xlsx *.xls)")
        if not path:
            return
        dialog = ImportReviewDialog(self.main, self.db_adapter, path)
        dialog.show()
