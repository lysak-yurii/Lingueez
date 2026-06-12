"""Save selected words as a single MP3 with progress and cancellation."""
import logging
import threading

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
)

from app.config import get_float, get_int, load_settings
from app.core.audio import save_audio_file
from app.ui.dialogs.base import FramelessDialog
from app.ui.workers import run_in_thread


class AudioSaverDialog(FramelessDialog):
    def __init__(self, parent, words, languages, initial_name):
        super().__init__(parent, title="Save to Audio")
        self.words = words
        self.languages = languages
        self.initial_name = initial_name
        self.cancel_event = threading.Event()
        self.temp_files = set()
        self._running = False

        self.setMinimumWidth(460)

        layout = self.content_layout
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        self.info_label = QLabel(
            f"Generate one MP3 file from {len(words)} word/translation pair(s).")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, len(words) * 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setObjectName("dimLabel")
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_btn)
        self.start_btn = QPushButton("Choose File && Start", objectName="primaryButton")
        self.start_btn.clicked.connect(self.start)
        buttons.addWidget(self.start_btn)
        layout.addLayout(buttons)

    def start(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Audio As",
                                              self.initial_name, "MP3 files (*.mp3)")
        if not path:
            return
        settings = load_settings()
        pause_ms = int(get_float(settings, "pause_duration", 0.5) * 1000)
        repeats = get_int(settings, "number_of_repeats", 1)

        self.start_btn.setEnabled(False)
        self._running = True
        self.status_label.setText("Generating audio…")
        self.progress.setRange(0, len(self.words))

        def progress_callback(current, word):
            if current == 'compiling_audio':
                self.status_label.setText("Compiling final audio file…")
                self.progress.setRange(0, 0)  # indeterminate
            else:
                self.progress.setValue(int(current))
                self.status_label.setText(f"Processed: {word}")

        def work(progress_callback=None):
            save_audio_file(
                self.words, path, self.languages,
                progress_callback=progress_callback,
                is_cancelled=self.cancel_event,
                all_temp_files=self.temp_files,
                logger=lambda message, level='info': logging.log(logging.INFO, message),
                pause_duration=pause_ms,
                number_of_repeats=repeats,
            )
            return path

        def done(result):
            self._running = False
            if self.cancel_event.is_set():
                self.status_label.setText("Cancelled.")
                self.reject()
            else:
                self.progress.setRange(0, 1)
                self.progress.setValue(1)
                QMessageBox.information(self, "Audio saved", f"Audio file saved to:\n{result}")
                self.accept()

        def fail(message):
            self._running = False
            QMessageBox.critical(self, "Audio Error", f"Failed to save audio:\n{message}")
            self.reject()

        run_in_thread(work, wants_progress=True,
                      on_progress=lambda a, b: progress_callback(a, b),
                      on_result=done, on_error=fail)

    def cancel(self):
        if self._running:
            self.cancel_event.set()
            self.status_label.setText("Cancelling…")
        else:
            self.reject()
