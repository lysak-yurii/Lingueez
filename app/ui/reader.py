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

"""Text reading: sentence tokenizer, QtMultimedia playback engine, toolbar.

ReaderPlayer replaces the old fire-and-forget read_words_list() flow for
the texts page. Text is tokenized into sentence chunks with character
offsets; chunks are synthesized in the background while playback starts
on the first ready one. Playback runs on QMediaPlayer, so the session
supports pause/resume, sentence skips, click-to-seek and live playback
rate changes. Word-level highlight timing is estimated by distributing
each chunk's real audio duration across its words by length.
"""
import logging
import os
import re
import threading
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from app.core import audio
from app.i18n import lang_label, tr
from app.ui import icons
from app.ui.widgets import ElidedLabel

# Sentence ends at .!?… (plus closing quotes/brackets) followed by
# whitespace, or at any newline run. Offsets are kept so chunk ranges
# always map back into the original plain text.
_SENTENCE_END = re.compile(r"[.!?…]+[)\"'»”’]*\s+|\n+")
# A word, allowing inner apostrophes/hyphens (don't, well-known, l'eau)
_WORD = re.compile(r"[^\W\d_][\w]*(?:[’'\-][\w]+)*", re.UNICODE)

MAX_CHUNK_CHARS = 250

_FAILED = "<failed>"


class Chunk:
    """One synthesis unit: a sentence group with absolute char offsets."""

    __slots__ = ("start", "end", "text", "words", "weights")

    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text
        # absolute (start, end) per word
        self.words = [(start + m.start(), start + m.end())
                      for m in _WORD.finditer(text)]
        # Duration weights: word length (+2 for the inter-word gap), plus a
        # bonus for the pause TTS engines insert at punctuation after the
        # word — without it the highlight runs ahead through commas and
        # sentence ends and seems to lag on the words that follow.
        self.weights = []
        for i, (w_start, w_end) in enumerate(self.words):
            weight = (w_end - w_start) + 2
            gap_end = (self.words[i + 1][0] if i + 1 < len(self.words)
                       else start + len(text))
            gap = text[w_end - start:gap_end - start]
            if any(ch in gap for ch in ".!?…\n"):
                weight += 10
            elif any(ch in gap for ch in ",;:"):
                weight += 4
            self.weights.append(weight)


def _sentence_spans(text):
    """Split *text* into (start, end) sentence spans covering it fully."""
    spans, start = [], 0
    for match in _SENTENCE_END.finditer(text):
        if match.end() > start:
            spans.append((start, match.end()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def tokenize(text):
    """Group sentences into chunks of <= MAX_CHUNK_CHARS for synthesis.

    Chunks without any word characters are dropped, so the TTS provider
    is never asked to speak empty or punctuation-only fragments.
    """
    chunks, group_start = [], None
    for start, end in _sentence_spans(text):
        if group_start is None:
            group_start = start
        if end - group_start > MAX_CHUNK_CHARS and start > group_start:
            chunks.append(Chunk(group_start, start, text[group_start:start]))
            group_start = start
    if group_start is not None and group_start < len(text):
        chunks.append(Chunk(group_start, len(text), text[group_start:]))
    return [c for c in chunks if c.words]


class ReaderPlayer(QObject):
    """Controllable text-reading engine on top of QMediaPlayer.

    Synthesis happens on a small thread pool; results are marshalled to
    the GUI thread through the queued _chunk_ready signal, so all state
    lives on the GUI thread. A generation counter invalidates everything
    from earlier sessions.
    """

    state_changed = Signal(str)        # "playing" | "paused" | "buffering"
    word_changed = Signal(int, int)    # absolute char range; (-1, -1) clears
    sentence_changed = Signal(int, int)  # absolute char range of the chunk
    progress_changed = Signal(int, int)  # current chunk index, total chunks
    finished = Signal()                # session over (natural end or stop)
    error = Signal(str)                # user-facing failure message
    read_warning = Signal(str)         # non-fatal: some chunk couldn't be read

    _chunk_ready = Signal(int, int, str)  # generation, index, path ("" = failed)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._audio_out = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_player_error)
        self._chunk_ready.connect(self._on_chunk_ready)

        # positionChanged can be sparse (backend-dependent, ~100ms or worse);
        # polling position() keeps the word highlight tight.
        self._tick = QTimer(self)
        self._tick.setInterval(50)
        self._tick.timeout.connect(self._on_tick)

        self._generation = 0
        self._active = False
        self._chunks = []
        self._files = []         # None = pending, _FAILED, or mp3 path
        self._executor = None
        self._cancel = None
        self._index = -1
        self._words = []         # absolute word ranges of the current chunk
        self._timeline = []      # cumulative ms end-time per word
        self._word_idx = -1
        self._pending_word = None  # word to seek to once duration is known
        self._waiting = False    # current chunk's audio not synthesized yet
        self._paused = False
        self._rate = 1.0
        self._leftovers = set()  # temp files that could not be deleted yet
        self._warned_failed = False  # warned about a failed chunk this session

    # ------------------------------------------------------------- public

    @property
    def active(self):
        return self._active

    def start(self, text, language, start_char=0):
        """Begin a session reading *text* from *start_char*; True on success."""
        self.stop()
        lang_code = audio.lang_codes.get(language)
        if not lang_code:
            self.error.emit(
                tr("Unsupported language: {language}").format(language=lang_label(language)))
            return False
        chunks = tokenize(text)
        if not chunks:
            self.error.emit(tr("Nothing to read."))
            return False
        audio.stop_playback()  # halt any pygame playback (words / pronounce)

        self._generation += 1
        generation = self._generation
        self._active = True
        self._warned_failed = False
        self._chunks = chunks
        self._files = [None] * len(chunks)
        self._cancel = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=3)
        for i, chunk in enumerate(chunks):
            self._executor.submit(self._synthesize, generation, i,
                                  chunk.text, lang_code, self._cancel)

        index = next((i for i, c in enumerate(chunks) if c.end > start_char), 0)
        self._goto(index, word=self._word_at(index, start_char))
        self._tick.start()
        return True

    def toggle_pause(self):
        if not self._active:
            return
        self._paused = not self._paused
        if self._waiting:
            self.state_changed.emit("paused" if self._paused else "buffering")
            return
        if self._paused:
            self._player.pause()
            self.state_changed.emit("paused")
        else:
            self._player.play()
            self.state_changed.emit("playing")

    def pause(self):
        if self._active and not self._paused:
            self.toggle_pause()

    def next_sentence(self):
        if self._active:
            self._paused = False
            self._goto(self._index + 1)

    def prev_sentence(self):
        if self._active:
            self._paused = False
            self._goto(self._index - 1, direction=-1)

    def seek_to_char(self, pos):
        """Jump playback to the word at plain-text offset *pos*."""
        if not self._active:
            return
        index = next((i for i, c in enumerate(self._chunks) if c.end > pos), None)
        if index is None or self._files[index] == _FAILED:
            return
        word = self._word_at(index, pos)
        self._paused = False
        if index == self._index and self._timeline:
            self._seek_word(word)
            self._player.play()
            self.state_changed.emit("playing")
        else:
            self._goto(index, word=word)

    def set_rate(self, rate):
        self._rate = float(rate)
        if self._active:
            self._player.setPlaybackRate(self._rate)

    def stop(self):
        if self._active:
            self._teardown()
            self.finished.emit()
        else:
            self._cleanup_leftovers()

    # ------------------------------------------------------ worker thread

    def _synthesize(self, generation, index, text, lang_code, cancel):
        if cancel.is_set():
            return
        try:
            path = audio.synthesize_speech(text, lang_code,
                                           cancellation_event=cancel)
        except Exception as exc:
            logging.warning(f"Reader: synthesis failed for chunk {index}: {exc}")
            path = None
        if cancel.is_set():
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
            return
        self._chunk_ready.emit(generation, index, path or "")

    # --------------------------------------------------------- GUI thread

    def _on_chunk_ready(self, generation, index, path):
        if generation != self._generation:
            if path:
                self._try_remove(path)
            return
        self._files[index] = path or _FAILED
        if not path:
            logging.warning(f"Reader: skipping chunk {index} (synthesis failed)")
            if not self._warned_failed:
                self._warned_failed = True
                self.read_warning.emit(tr(
                    "Some text couldn't be read aloud — unsupported language "
                    "or unreadable characters."))
        if self._waiting and index == self._index:
            if path:
                self._load_current()
            else:
                self._goto(self._index + 1)

    def _word_at(self, index, char_pos):
        for w, (start, end) in enumerate(self._chunks[index].words):
            if end > char_pos:
                return w
        return 0

    def _goto(self, index, word=0, direction=1):
        """Move to chunk *index*, skipping failed chunks in *direction*."""
        total = len(self._chunks)
        while 0 <= index < total and self._files[index] == _FAILED:
            index += direction
        if index < 0:
            return  # nothing playable before the first chunk: stay put
        if index >= total:
            self._finish()
            return
        self._index = index
        self._pending_word = word
        self._words = self._chunks[index].words
        self._timeline = []
        self._word_idx = -1
        chunk = self._chunks[index]
        self.progress_changed.emit(index, total)
        self.sentence_changed.emit(chunk.start, chunk.end)
        self._load_current()

    def _load_current(self):
        path = self._files[self._index]
        if path is None:
            self._waiting = True
            self._player.stop()
            self.state_changed.emit("paused" if self._paused else "buffering")
            return
        self._waiting = False
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.setPlaybackRate(self._rate)
        if self._paused:
            self._player.pause()
            self.state_changed.emit("paused")
        else:
            self._player.play()
            self.state_changed.emit("playing")

    def _seek_word(self, word):
        start_ms = 0 if word <= 0 else self._timeline[word - 1]
        self._player.setPosition(int(start_ms))

    def _on_tick(self):
        if self._active and not self._paused and not self._waiting:
            self._on_position(self._player.position())

    def _on_duration(self, duration_ms):
        if not self._active or duration_ms <= 0 or not self._words:
            return
        # Estimate: spread the real duration over the punctuation-aware
        # word weights precomputed by the tokenizer.
        weights = self._chunks[self._index].weights
        total = sum(weights)
        acc, bounds = 0, []
        for weight in weights:
            acc += weight
            bounds.append(duration_ms * acc / total)
        self._timeline = bounds
        if self._pending_word is not None:
            if self._pending_word > 0:
                self._seek_word(self._pending_word)
            self._pending_word = None

    def _on_position(self, pos):
        if not self._active or not self._timeline:
            return
        idx = min(bisect_right(self._timeline, pos), len(self._words) - 1)
        if idx != self._word_idx:
            self._word_idx = idx
            start, end = self._words[idx]
            self.word_changed.emit(start, end)

    def _on_media_status(self, status):
        if self._active and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._goto(self._index + 1)

    def _on_player_error(self, _error, error_string):
        if not self._active:
            return
        logging.warning(f"Reader: playback error: {error_string}")
        if 0 <= self._index < len(self._files):
            self._files[self._index] = _FAILED
        self._goto(self._index + 1)

    # ------------------------------------------------------------ cleanup

    def _finish(self):
        self._teardown()
        self.finished.emit()

    def _teardown(self):
        self._active = False
        self._tick.stop()
        self._generation += 1  # invalidates in-flight synthesis results
        if self._cancel is not None:
            self._cancel.set()
            self._cancel = None
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        self._player.stop()
        self._player.setSource(QUrl())  # release the file handle
        for path in self._files:
            if path and path != _FAILED:
                self._try_remove(path)
        self._chunks = []
        self._files = []
        self._words = []
        self._timeline = []
        self._index = -1
        self._word_idx = -1
        self._pending_word = None
        self._waiting = False
        self._paused = False
        self.word_changed.emit(-1, -1)
        self._cleanup_leftovers()

    def _try_remove(self, path):
        try:
            os.remove(path)
        except OSError:
            if os.path.exists(path):
                self._leftovers.add(path)

    def _cleanup_leftovers(self):
        for path in list(self._leftovers):
            try:
                os.remove(path)
                self._leftovers.discard(path)
            except OSError:
                if not os.path.exists(path):
                    self._leftovers.discard(path)


class ReaderToolbar(QWidget):
    """Reading controls shown in the reader card during a session."""

    prev_clicked = Signal()
    toggle_clicked = Signal()
    next_clicked = Signal()
    stop_clicked = Signal()
    rate_changed = Signal(float)

    RATES = [("0.75×", 0.75), ("1×", 1.0), ("1.25×", 1.25),
             ("1.5×", 1.5), ("2×", 2.0)]

    def __init__(self, colors, parent=None):
        super().__init__(parent, objectName="ReaderToolbar")
        self._colors = colors
        self._state = "playing"
        self._progress = ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(2)

        self.prev_btn = self._button("skip-back", tr("Previous sentence"), self.prev_clicked)
        lay.addWidget(self.prev_btn)
        self.play_btn = self._button("pause", tr("Pause"), self.toggle_clicked)
        lay.addWidget(self.play_btn)
        self.next_btn = self._button("skip-forward", tr("Next sentence"), self.next_clicked)
        lay.addWidget(self.next_btn)
        self.stop_btn = self._button("x", tr("Stop reading"), self.stop_clicked,
                                     color_key="danger", size=15)
        lay.addWidget(self.stop_btn)

        lay.addSpacing(8)
        self.rate_combo = QComboBox()
        for label, _rate in self.RATES:
            self.rate_combo.addItem(label)
        self.rate_combo.setCurrentIndex(1)
        self.rate_combo.setToolTip(tr("Reading speed"))
        self.rate_combo.setCursor(Qt.PointingHandCursor)
        self.rate_combo.currentIndexChanged.connect(
            lambda i: self.rate_changed.emit(self.RATES[i][1]))
        lay.addWidget(self.rate_combo)

        lay.addSpacing(8)
        self.status_label = ElidedLabel(min_width=60)
        self.status_label.setObjectName("dimLabel")
        lay.addWidget(self.status_label, 1)

    def _button(self, name, tooltip, signal, color_key="text", size=16):
        btn = QPushButton(objectName="iconButton")
        btn.setIcon(icons.icon(name, self._colors[color_key], size))
        btn.setIconSize(QSize(size, size))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(signal.emit)
        return btn

    def set_state(self, state):
        self._state = state
        playing = state == "playing"
        self.play_btn.setIcon(icons.icon(
            "pause" if playing else "play", self._colors["text"], 16))
        self.play_btn.setToolTip(tr("Pause") if playing else tr("Resume"))
        self._update_status()

    def set_progress(self, index, total):
        self._progress = tr("Sentence {n} / {total}").format(n=index + 1, total=total)
        self._update_status()

    def reset(self):
        self._progress = ""
        self.set_state("playing")

    def _update_status(self):
        suffix = "  ·  " + tr("buffering…") if self._state == "buffering" else ""
        self.status_label.set_full_text(self._progress + suffix)

    def refresh_theme(self, colors):
        self._colors = colors
        self.prev_btn.setIcon(icons.icon("skip-back", colors["text"], 16))
        self.next_btn.setIcon(icons.icon("skip-forward", colors["text"], 16))
        self.stop_btn.setIcon(icons.icon("x", colors["danger"], 15))
        self.set_state(self._state)
