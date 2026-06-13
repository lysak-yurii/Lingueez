"""Word playback: a controllable player engine + the compact player bar.

WordPlayer is the playback engine for the words table: synthesis is
prefetched in the background while playback starts on the first ready
word, and the session responds to pause/resume,
previous/next and stop commands at word granularity. Synthesized files
are kept for the whole session so jumping backwards never re-synthesizes.
"""
import logging
import os
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import pygame
from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from app.core import audio
from app.ui import icons
from app.ui.widgets import ElidedLabel


class _Session:
    """State of one playback run; a new run gets a fresh session object."""

    def __init__(self, pairs, languages):
        self.pairs = pairs          # [(word, translation), ...]
        self.languages = languages  # [(lang1, lang2), ...]
        self.stop = threading.Event()
        self.cmds = queue.Queue()
        self.paused = False


class WordPlayer(QObject):
    """Threaded playback engine with pause/seek commands.

    Signals are emitted from the worker thread; Qt delivers them queued
    on the GUI thread.
    """

    index_changed = Signal(int)   # word index now playing
    part_changed = Signal(int)    # 0 = word side, 1 = translation side
    state_changed = Signal(bool)  # True = paused
    word_completed = Signal(int)  # a word finished playing in full (not skipped)
    finished = Signal()           # session ended (natural end or stop)

    _mixer_lock = threading.Lock()  # pygame.mixer.music is not thread-safe

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None

    @property
    def active(self):
        session = self._session
        return session is not None and not session.stop.is_set()

    def play(self, pairs, languages):
        """Start a new session (stops any previous playback first)."""
        self.stop()
        audio.stop_playback()  # halt queue-based playback (texts TTS)
        session = _Session(pairs, languages)
        self._session = session
        threading.Thread(target=self._run, args=(session,), daemon=True).start()

    def toggle_pause(self):
        self._cmd("toggle")

    def next(self):
        self._cmd("next")

    def prev(self):
        self._cmd("prev")

    def stop(self):
        session = self._session
        if session is not None and not session.stop.is_set():
            session.stop.set()
            session.cmds.put("stop")

    def _cmd(self, name):
        session = self._session
        if session is not None and not session.stop.is_set():
            session.cmds.put(name)

    # ------------------------------------------------------------ worker

    def _run(self, session):
        total = len(session.pairs)
        executor = ThreadPoolExecutor(max_workers=4)
        futures = [executor.submit(self._synthesize, session, i) for i in range(total)]
        try:
            i = 0
            while not session.stop.is_set() and 0 <= i < total:
                self.index_changed.emit(i)
                try:
                    files = futures[i].result()
                except Exception as exc:
                    logging.error(f"Word synthesis failed: {exc}")
                    files = []
                jump = self._play_word(session, files, i, total)
                # A word counts as "listened" only when it played to the end:
                # jump is None (not skipped via next/prev), the session wasn't
                # stopped, and there was actual audio.
                if jump is None and not session.stop.is_set() and files:
                    self.word_completed.emit(i)
                i = i + 1 if jump is None else jump
        finally:
            session.stop.set()
            executor.shutdown(wait=False, cancel_futures=True)
            for future in futures:
                if future.done() and not future.cancelled():
                    try:
                        for _slot, filename in future.result() or []:
                            audio._remove_temp_file(filename)
                    except Exception:
                        pass
            if self._session is session:
                self.finished.emit()

    def _synthesize(self, session, i):
        """Return [(slot, filename), ...] — slot 0 = word, 1 = translation."""
        word, translation = session.pairs[i]
        lang1, lang2 = session.languages[i]
        files = []
        for slot, (text, lang) in enumerate(((word, lang1), (translation, lang2))):
            if session.stop.is_set():
                break
            text = str(text or "").strip()
            if not text or lang not in audio.lang_codes:
                continue
            filename = audio.synthesize_speech(
                text, audio.lang_codes[lang], cancellation_event=session.stop)
            if filename:
                files.append((slot, filename))
                with audio.temp_files_lock:
                    audio.all_temp_files.add(filename)
        return files

    def _play_word(self, session, files, i, total):
        """Play one word's files; returns a jump index or None to advance."""
        for slot, filename in files:
            if session.stop.is_set():
                return None
            if not filename or not os.path.exists(filename):
                continue
            self.part_changed.emit(slot)
            with self._mixer_lock:
                try:
                    pygame.mixer.music.load(filename)
                    pygame.mixer.music.play()
                    if session.paused:
                        pygame.mixer.music.pause()
                except pygame.error as exc:
                    logging.error(f"Playback error: {exc}")
                    continue
            jump = self._wait(session, i, total)
            with self._mixer_lock:
                try:
                    pygame.mixer.music.stop()
                    pygame.mixer.music.unload()
                except pygame.error:
                    pass
            if jump is not None:
                return jump
            if session.stop.wait(0.18):  # short gap between word/translation
                return None
        return None

    def _wait(self, session, i, total):
        """Poll for commands until the track ends; returns a jump index."""
        while not session.stop.is_set():
            try:
                cmd = session.cmds.get(timeout=0.05)
            except queue.Empty:
                cmd = None
            if cmd == "stop":
                return None
            if cmd == "toggle":
                with self._mixer_lock:
                    try:
                        if session.paused:
                            pygame.mixer.music.unpause()
                        else:
                            pygame.mixer.music.pause()
                    except pygame.error:
                        pass
                session.paused = not session.paused
                self.state_changed.emit(session.paused)
            elif cmd in ("next", "prev"):
                if session.paused:
                    session.paused = False
                    self.state_changed.emit(False)
                # next past the last word ends the session via the run loop
                return min(i + 1, total) if cmd == "next" else max(i - 1, 0)
            if not session.paused:
                with self._mixer_lock:
                    try:
                        busy = pygame.mixer.music.get_busy()
                    except pygame.error:
                        busy = False
                if not busy:
                    return None
        return None


class PlayerBar(QWidget):
    """Compact playback pill: prev / pause / next, position and word."""

    prev_clicked = Signal()
    toggle_clicked = Signal()
    next_clicked = Signal()
    stop_clicked = Signal()

    WORD_WIDTH = 150

    def __init__(self, colors, parent=None):
        super().__init__(parent, objectName="PlayerBar")
        self._colors = colors
        self._paused = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(2)

        self.prev_btn = self._button("skip-back", "text", "Previous word", self.prev_clicked)
        lay.addWidget(self.prev_btn)
        self.play_btn = self._button("pause", "text", "Pause", self.toggle_clicked)
        lay.addWidget(self.play_btn)
        self.next_btn = self._button("skip-forward", "text", "Next word", self.next_clicked)
        lay.addWidget(self.next_btn)

        lay.addSpacing(6)
        self.pos_label = QLabel("", objectName="dimLabel")
        lay.addWidget(self.pos_label)
        # elidable: the pill compresses instead of widening the window
        self.word_label = ElidedLabel(min_width=36)
        self.word_label.setObjectName("PlayerWord")
        self.word_label.setMaximumWidth(self.WORD_WIDTH)
        lay.addWidget(self.word_label, 1)

        self.stop_btn = self._button("x", "danger", "Stop playback", self.stop_clicked, 15)
        lay.addWidget(self.stop_btn)

    def _button(self, name, color_key, tooltip, signal, size=16):
        btn = QPushButton()
        btn.setIcon(icons.icon(name, self._colors[color_key], size))
        btn.setIconSize(QSize(size, size))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(signal.emit)
        return btn

    def set_position(self, index, total, word):
        self.pos_label.setText(f"{index + 1}/{total}")
        self.word_label.set_full_text(str(word or ""))

    def set_paused(self, paused):
        self._paused = paused
        self.play_btn.setIcon(icons.icon(
            "play" if paused else "pause", self._colors["text"], 16))
        self.play_btn.setToolTip("Resume" if paused else "Pause")

    def refresh_theme(self, colors):
        self._colors = colors
        self.prev_btn.setIcon(icons.icon("skip-back", colors["text"], 16))
        self.next_btn.setIcon(icons.icon("skip-forward", colors["text"], 16))
        self.stop_btn.setIcon(icons.icon("x", colors["danger"], 15))
        self.set_paused(self._paused)
