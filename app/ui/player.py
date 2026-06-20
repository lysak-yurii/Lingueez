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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pygame
from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QWidget,
)

from app.core import audio
from app.i18n import ntr, tr
from app.ui import icons
from app.ui.widgets import ElidedLabel


class _Session:
    """State of one playback run; a new run gets a fresh session object."""

    def __init__(self, pairs, languages, pause=0.5, repeats=1):
        self.pairs = pairs          # [(word, translation), ...]
        self.languages = languages  # [(lang1, lang2), ...]
        self.stop = threading.Event()
        self.cmds = queue.Queue()
        self.paused = False
        # Reasons words were skipped during synthesis (filled from worker
        # threads, so guarded by a lock); summarized once when the run ends.
        self.skipped = Counter()
        self.skipped_lock = threading.Lock()
        # Pacing — read live each loop so the popup can tune mid-session.
        self.pause = max(0.0, pause)     # seconds of silence between words/repeats
        self.repeats = max(1, repeats)   # times each pair plays per pass


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
    synthesis_warning = Signal(str)  # some words couldn't be synthesized

    _mixer_lock = threading.Lock()  # pygame.mixer.music is not thread-safe

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None

    @property
    def active(self):
        session = self._session
        return session is not None and not session.stop.is_set()

    def play(self, pairs, languages, pause=0.5, repeats=1):
        """Start a new session (stops any previous playback first)."""
        self.stop()
        audio.stop_playback()  # halt queue-based playback (texts TTS)
        session = _Session(pairs, languages, pause=pause, repeats=repeats)
        self._session = session
        threading.Thread(target=self._run, args=(session,), daemon=True).start()

    def set_pause(self, seconds):
        """Tune the inter-word pause of the running session (live)."""
        session = self._session
        if session is not None:
            session.pause = max(0.0, seconds)

    def set_repeats(self, count):
        """Tune the per-pair repeat count of the running session (live)."""
        session = self._session
        if session is not None:
            session.repeats = max(1, count)

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
                # Play the pair up to `repeats` times. Each repeat that runs to
                # the end (jump is None, not stopped, has audio) counts as one
                # listen. A next/prev jump breaks out immediately and is honored.
                jump = None
                repeats = max(1, session.repeats) if files else 1
                for rep in range(repeats):
                    jump = self._play_word(session, files, i, total)
                    if jump is not None:
                        break
                    if not session.stop.is_set() and files:
                        self.word_completed.emit(i)
                    if rep < repeats - 1 and session.pause:  # gap between repeats
                        if session.stop.wait(session.pause):
                            break
                if jump is None:
                    if i + 1 < total and session.pause:  # gap before next word
                        session.stop.wait(session.pause)
                    i += 1
                else:
                    i = jump
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
                with session.skipped_lock:
                    skipped = dict(session.skipped)
                if skipped:
                    total = sum(skipped.values())
                    noun = ntr(total, tr("word"), tr("words"), tr("words (genitive)"))
                    reasons = ", ".join(skipped)
                    self.synthesis_warning.emit(
                        tr("Skipped {n} {noun} ({reasons}).").format(
                            n=total, noun=noun, reasons=reasons))
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
            if not text:
                continue
            if lang not in audio.lang_codes:
                self._note_skip(session, tr("unsupported language"))
                continue
            filename = audio.synthesize_speech(
                text, audio.lang_codes[lang], cancellation_event=session.stop)
            if filename:
                files.append((slot, filename))
                with audio.temp_files_lock:
                    audio.all_temp_files.add(filename)
            elif not session.stop.is_set():
                self._note_skip(session, tr("unreadable text"))
        return files

    @staticmethod
    def _note_skip(session, reason):
        with session.skipped_lock:
            session.skipped[reason] += 1

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
    config_clicked = Signal()
    stop_clicked = Signal()

    WORD_WIDTH = 360

    def __init__(self, colors, parent=None):
        super().__init__(parent, objectName="PlayerBar")
        self._colors = colors
        self._paused = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(2)

        # The bar is right-anchored, so the variable-width metadata lives on the
        # LEFT (its edge moves with the word length) together with the rarely-used
        # settings button; the frequently-used transport controls + stop sit on
        # the fixed RIGHT edge so prev/play/next/stop never shift around.
        self.config_btn = self._button(
            "sliders", "text", tr("Playback settings"), self.config_clicked, 15)
        lay.addWidget(self.config_btn)
        self.pos_label = QLabel("", objectName="dimLabel")
        lay.addWidget(self.pos_label)
        # elidable + collapsible: under width pressure the metadata yields
        # entirely so the transport controls never force the window to grow
        # (tooltip / highlighted table row / mini player still show the word)
        self.word_label = ElidedLabel(min_width=0)
        self.word_label.setObjectName("PlayerWord")
        self.word_label.setMaximumWidth(self.WORD_WIDTH)
        lay.addWidget(self.word_label, 1)
        lay.addSpacing(6)

        self.prev_btn = self._button("skip-back", "text", tr("Previous word"), self.prev_clicked)
        lay.addWidget(self.prev_btn)
        self.play_btn = self._button("pause", "text", tr("Pause"), self.toggle_clicked)
        lay.addWidget(self.play_btn)
        self.next_btn = self._button("skip-forward", "text", tr("Next word"), self.next_clicked)
        lay.addWidget(self.next_btn)

        self.stop_btn = self._button("x", "danger", tr("Stop playback"), self.stop_clicked, 15)
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
        self.play_btn.setToolTip(tr("Resume") if paused else tr("Pause"))

    def refresh_theme(self, colors):
        self._colors = colors
        self.prev_btn.setIcon(icons.icon("skip-back", colors["text"], 16))
        self.next_btn.setIcon(icons.icon("skip-forward", colors["text"], 16))
        self.config_btn.setIcon(icons.icon("sliders", colors["text"], 15))
        self.stop_btn.setIcon(icons.icon("x", colors["danger"], 15))
        self.set_paused(self._paused)


class PlaybackSettingsPopup(QWidget):
    """Compact flyout to tune playback pacing: pause between words and repeats.

    Uses Qt.Popup so a click outside dismisses it. Values are emitted on every
    change so the caller can persist and apply them live.
    """

    pause_changed = Signal(float)
    repeats_changed = Signal(int)

    def __init__(self, pause, repeats, parent=None):
        super().__init__(parent, objectName="PlayerBar")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)

        form = QFormLayout(self)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)

        self.pause_spin = QDoubleSpinBox()
        self.pause_spin.setRange(0, 10)
        self.pause_spin.setSingleStep(0.1)
        self.pause_spin.setSuffix(tr(" s"))
        self.pause_spin.setValue(pause)
        self.pause_spin.valueChanged.connect(self.pause_changed.emit)
        form.addRow(QLabel(tr("Pause between words")), self.pause_spin)

        self.repeats_spin = QSpinBox()
        self.repeats_spin.setRange(1, 10)
        self.repeats_spin.setSuffix("×")
        self.repeats_spin.setValue(repeats)
        self.repeats_spin.valueChanged.connect(self.repeats_changed.emit)
        form.addRow(QLabel(tr("Repeats per word")), self.repeats_spin)

    def popup_at(self, anchor):
        """Show anchored below ``anchor`` (a widget), right-aligned to it —
        flipping above when there's no room below (e.g. the player is on the
        bottom shelf, near the screen's bottom edge)."""
        self.adjustSize()
        bottom_right = anchor.mapToGlobal(anchor.rect().bottomRight())
        x = bottom_right.x() - self.width()
        y = bottom_right.y() + 6
        screen = anchor.screen().availableGeometry() if anchor.screen() else None
        if screen:
            x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
            if y + self.height() > screen.bottom() - 4:  # no room below: flip above
                y = anchor.mapToGlobal(anchor.rect().topRight()).y() - self.height() - 6
        else:
            x = max(0, x)
        self.move(x, y)
        self.show()
