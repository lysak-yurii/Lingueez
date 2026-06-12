"""Color-coded log viewer (used for imports and general app logging)."""
import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QPushButton, QTextEdit,
)

from app.ui.dialogs.base import FramelessDialog

LEVEL_COLORS = {
    'info': None,
    'warning': "#d29922",
    'error': "#e5534b",
    'success': "#3fb950",
    'new': "#4f8cff",
    'rejected': "#e5534b",
}

LOG_FILE = "app.log"
TAIL_LINES = 500
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(message)s'  # keep in sync with main.py


def _level_key(levelno):
    if levelno >= logging.ERROR:
        return 'error'
    if levelno >= logging.WARNING:
        return 'warning'
    return 'info'


class _LogBridge(QObject):
    message = Signal(str, str)


class _LiveLogHandler(logging.Handler):
    """Forwards log records to the window across threads via a Qt signal."""

    def __init__(self):
        super().__init__()
        self.bridge = _LogBridge()
        self.setFormatter(logging.Formatter(LOG_FORMAT))

    def emit(self, record):
        try:
            self.bridge.message.emit(self.format(record), _level_key(record.levelno))
        except Exception:
            pass


class LogWindow(FramelessDialog):
    def __init__(self, parent, title="Log", follow_app_log=False):
        super().__init__(parent, title=title)
        self.setMinimumSize(700, 460)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setModal(False)
        self._live_handler = None

        layout = self.content_layout
        layout.setContentsMargins(14, 14, 14, 12)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.text, 1)

        buttons = QHBoxLayout()
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.text.clear)
        buttons.addWidget(clear_btn)
        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self.export_log)
        buttons.addWidget(export_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        if follow_app_log:
            self._load_app_log_tail()
            self._live_handler = _LiveLogHandler()
            self._live_handler.bridge.message.connect(self.log_message)
            logging.getLogger().addHandler(self._live_handler)

    def _load_app_log_tail(self):
        try:
            with open(LOG_FILE, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-TAIL_LINES:]
        except OSError as exc:
            self.log_message(f"Could not read {LOG_FILE}: {exc}", 'warning')
            return
        self.text.setUpdatesEnabled(False)
        level = 'info'
        for line in lines:
            # continuation lines (tracebacks) keep the previous level
            if " - ERROR - " in line or " - CRITICAL - " in line:
                level = 'error'
            elif " - WARNING - " in line:
                level = 'warning'
            elif " - INFO - " in line or " - DEBUG - " in line:
                level = 'info'
            self.log_message(line.rstrip("\n"), level)
        self.text.setUpdatesEnabled(True)

    def closeEvent(self, event):
        if self._live_handler is not None:
            logging.getLogger().removeHandler(self._live_handler)
            self._live_handler = None
        super().closeEvent(event)

    def log_message(self, message, level='info'):
        cursor = self.text.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        color = LEVEL_COLORS.get(level)
        if color:
            fmt.setForeground(QColor(color))
        cursor.insertText(message + "\n", fmt)
        self.text.setTextCursor(cursor)
        self.text.ensureCursorVisible()

    def bulk_insert(self, messages):
        for message, level in messages:
            self.log_message(message, level)

    def export_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Log", "log.txt",
                                              "Text files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.text.toPlainText())
