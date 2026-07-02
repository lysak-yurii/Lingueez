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

"""Flashcards page: manual SM-2 review and audio-synced autoplay in one view.

Manual mode is the classic loop — flip the card, grade it Hard/Good/Easy —
with the SM-2 scheduling from :mod:`app.core.srs` persisted locally. Autoplay
mode reuses the read-aloud engine: the main window routes ``WordPlayer``'s
``index_changed``/``part_changed`` signals here, so the visible card advances
and flips exactly when the audio does, and the page's transport row drives the
player back (pause/resume/skip/stop). Pausing playback hands control to the
user (grading enabled) without leaving the page.

The page owns no word data: decks come from a ``deck_provider(kind, n)``
callable injected by the main window ("due" / "filtered" / "newest" /
"selected"), and status promotions are emitted back for the main window to
apply through the normal synced update path.
"""
from __future__ import annotations

import logging
import random
import threading
from datetime import datetime

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QSpinBox, QStackedLayout, QVBoxLayout, QWidget,
)

from app.core import audio
from app.core import db as dbq
from app.core import srs
from app.i18n import tr
from app.ui import icons
from app.ui.animations import fade_swap, flip_swap
from app.ui.workers import run_in_thread

DECK_KINDS = ("due", "filtered", "newest", "selected")
FLIP_MS = 220
FLIP_MS_AUTOPLAY = 160


def _soft(color_hex, alpha=36):
    c = QColor(color_hex)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


class _Panel(QWidget):
    """Rounded surface container for the picker / completion states."""

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._colors = colors
        self.setMaximumWidth(560)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def refresh_theme(self, colors):
        self._colors = colors
        self.update()

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(self._colors["surface"]))
        p.setPen(QPen(QColor(self._colors["border"]), 1))
        p.drawRoundedRect(rect, 16, 16)
        p.end()


class _DeckLogo(QWidget):
    """Three fanned cards with 'A'/'Я' glyphs — the page's animated emblem.

    On every visit to the picker the fan spreads open with a springy ease,
    then keeps a barely-there sway so the panel feels alive without being
    distracting.
    """

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._colors = colors
        self._spread = 1.0   # 0 = stacked, 1 = fanned open
        self._sway = 0.0     # ±1 slow idle drift
        self.setFixedSize(150, 104)

        self._intro = QVariantAnimation(self)
        self._intro.setDuration(700)
        self._intro.setStartValue(0.0)
        self._intro.setEndValue(1.0)
        self._intro.setEasingCurve(QEasingCurve.OutBack)
        self._intro.valueChanged.connect(self._set_spread)

        self._idle = QVariantAnimation(self)
        self._idle.setDuration(4200)
        self._idle.setStartValue(-1.0)
        self._idle.setKeyValueAt(0.5, 1.0)  # out and back — seamless loop
        self._idle.setEndValue(-1.0)
        self._idle.setEasingCurve(QEasingCurve.InOutSine)
        self._idle.setLoopCount(-1)
        self._idle.valueChanged.connect(self._set_sway)

    def _set_spread(self, v):
        self._spread = float(v)
        self.update()

    def _set_sway(self, v):
        self._sway = float(v)
        self.update()

    def replay(self):
        self._idle.stop()
        self._intro.stop()
        self._intro.start()
        self._idle.start()

    def stop(self):
        self._intro.stop()
        self._idle.stop()

    def refresh_theme(self, colors):
        self._colors = colors
        self.update()

    def hideEvent(self, event):  # noqa: N802 — don't animate while unseen
        self.stop()
        super().hideEvent(event)

    def paintEvent(self, _event):  # noqa: N802
        c = self._colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pivot = QPointF(self.width() / 2, self.height() - 4)
        card_w, card_h = 54, 74
        sway = self._sway * 1.6  # degrees
        accent = QColor(c["accent"])
        # back → front so the front card overlaps
        for angle, fill, border, glyph, glyph_color in (
            (-26, QColor(c["surface_alt"] if "surface_alt" in c
                         else c["surface"]), QColor(c["border"]), "Я",
             QColor(c["text_dim"])),
            (-6, QColor(c["surface"]), QColor(c["border"]), "", None),
            (16, accent, accent, "A", QColor("white")),  # matches primaryButton
        ):
            p.save()
            p.translate(pivot)
            p.rotate(angle * self._spread + sway)
            rect = QRectF(-card_w / 2, -card_h - 8, card_w, card_h)
            p.setPen(QPen(border, 1.2))
            if fill is accent:
                soft = QColor(accent)
                soft.setAlpha(230)
                p.setBrush(soft)
            else:
                p.setBrush(fill)
            p.drawRoundedRect(rect, 8, 8)
            if glyph:
                f = QFont()
                f.setPointSizeF(17)
                f.setWeight(QFont.Bold)
                p.setFont(f)
                p.setPen(glyph_color)
                p.drawText(rect, Qt.AlignCenter, glyph)
            p.restore()
        p.end()


class _SlimBar(QWidget):
    """Thin rounded progress bar under the session header."""

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._colors = colors
        self._current = 0
        self._total = 0
        self.setFixedHeight(5)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_progress(self, current, total):
        self._current = int(current)
        self._total = int(total)
        self.update()

    def refresh_theme(self, colors):
        self._colors = colors
        self.update()

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        radius = r.height() / 2
        p.setPen(Qt.NoPen)
        track = QColor(self._colors["text_dim"])
        track.setAlpha(38)
        p.setBrush(track)
        p.drawRoundedRect(r, radius, radius)
        if self._total > 0:
            w = max(r.height(), r.width() * (self._current / self._total))
            p.setBrush(QColor(self._colors["accent"]))
            p.drawRoundedRect(QRectF(r.left(), r.top(), w, r.height()),
                              radius, radius)
        p.end()


class FlashcardWidget(QWidget):
    """The card itself: word on the front, translation + definition on the back."""

    clicked = Signal()
    speak_clicked = Signal()

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._colors = colors
        self._record = {}
        self._definition = ""
        self._side = 0  # 0 = front (word), 1 = back (translation)
        self._flipping = False
        self._hint_text = ""
        self.setMinimumSize(380, 320)
        self.setMaximumWidth(640)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 24, 32, 30)
        lay.setSpacing(12)

        head = QHBoxLayout()
        self.caption = QLabel()
        self.speak_btn = QPushButton(objectName="iconButton")
        self.speak_btn.setToolTip(tr("Pronounce"))
        self.speak_btn.setCursor(Qt.PointingHandCursor)
        self.speak_btn.setIconSize(QSize(16, 16))
        self.speak_btn.setFocusPolicy(Qt.NoFocus)  # keep Space for flipping
        self.speak_btn.clicked.connect(self.speak_clicked.emit)
        self.status_chip = QLabel()
        self.status_chip.setAlignment(Qt.AlignCenter)
        head.addWidget(self.caption)
        head.addStretch(1)
        head.addWidget(self.speak_btn)
        head.addSpacing(4)
        head.addWidget(self.status_chip)
        lay.addLayout(head)

        lay.addStretch(1)
        self.word = QLabel(alignment=Qt.AlignCenter)
        self.word.setWordWrap(True)
        self.divider = QFrame()
        self.divider.setFixedSize(120, 1)
        self.body = QLabel(alignment=Qt.AlignCenter)
        self.body.setWordWrap(True)
        lay.addWidget(self.word)
        div_row = QHBoxLayout()
        div_row.addStretch(1)
        div_row.addWidget(self.divider)
        div_row.addStretch(1)
        lay.addLayout(div_row)
        lay.addWidget(self.body)
        lay.addStretch(1)

        self.hint = QLabel(alignment=Qt.AlignCenter)
        lay.addWidget(self.hint)
        self._apply_styles()

    # -------------------------------------------------------------- public

    @property
    def side(self):
        return self._side

    def set_card(self, record, hint_text=""):
        self._record = record or {}
        self._definition = ""
        self._side = 0
        self._hint_text = hint_text
        self._refresh_faces()

    def set_definition(self, text):
        self._definition = str(text or "").strip()
        if self._side == 1:
            self._refresh_faces()

    def show_side(self, side, animate=True, duration=FLIP_MS):
        side = 1 if side else 0
        if side == self._side:
            return
        self.flip(animate=animate, duration=duration)

    def flip(self, animate=True, duration=FLIP_MS):
        if self._flipping:
            return
        if animate and self.isVisible():
            self._flipping = True
            flip_swap(self, self._turn, duration, bg=self._colors["bg"])
        else:
            self._turn()

    def refresh_theme(self, colors):
        self._colors = colors
        self._apply_styles()
        self._refresh_faces()
        self.update()

    # ------------------------------------------------------------ internals

    def _turn(self):
        self._side = 1 - self._side
        self._refresh_faces()
        self._flipping = False
        self.update()  # repaint the side dots

    def _refresh_faces(self):
        rec = self._record
        c = self._colors
        status = str(rec.get("Status") or "").strip()
        self.status_chip.setText(tr(status) if status else "")
        self.status_chip.setVisible(bool(status))
        if self._side == 0:
            self.caption.setText(str(rec.get("Language1") or "").upper())
            self.word.setText(str(rec.get("Word1") or ""))
            self.word.setStyleSheet(
                f"color:{c['text']};background:transparent;"
                "font-size:24pt;font-weight:700;")
            self.body.setText("")
            self.hint.setText(self._hint_text)
        else:
            self.caption.setText(str(rec.get("Language2") or "").upper())
            self.word.setText(str(rec.get("Word2") or ""))
            self.word.setStyleSheet(
                f"color:{c['accent_text'] if 'accent_text' in c else c['text']};"
                "background:transparent;font-size:21pt;font-weight:700;")
            self.body.setText(self._definition)
            self.hint.setText("")
        self.divider.setVisible(self._side == 1 and bool(self.body.text()))
        self.body.setVisible(bool(self.body.text()))
        self.hint.setVisible(bool(self.hint.text()))

    def _apply_styles(self):
        c = self._colors
        self.caption.setStyleSheet(
            f"color:{c['text_dim']};background:transparent;"
            "font-size:8.5pt;font-weight:600;letter-spacing:2px;")
        self.status_chip.setStyleSheet(
            f"color:{c['text_dim']};background:{_soft(c['accent'], 26)};"
            "font-size:8.5pt;font-weight:600;"
            "padding:3px 10px;border-radius:9px;")
        self.body.setStyleSheet(
            f"color:{c['text_dim']};background:transparent;font-size:11pt;")
        self.hint.setStyleSheet(
            f"color:{_soft(c['text_dim'], 150)};background:transparent;"
            "font-size:9pt;")
        self.divider.setStyleSheet(
            f"background:{c['border']};border:none;")
        self.speak_btn.setIcon(icons.icon("volume", c["text_dim"], 16))

    def paintEvent(self, _event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(self._colors["surface"]))
        p.setPen(QPen(QColor(self._colors["border"]), 1))
        p.drawRoundedRect(rect, 18, 18)
        # front/back indicator dots
        p.setPen(Qt.NoPen)
        cy = self.height() - 13
        cx = self.width() / 2
        for i, x in enumerate((cx - 7, cx + 7)):
            if i == self._side:
                p.setBrush(QColor(self._colors["accent"]))
            else:
                dot = QColor(self._colors["text_dim"])
                dot.setAlpha(70)
                p.setBrush(dot)
            p.drawEllipse(QRectF(x - 3, cy - 3, 6, 6))
        p.end()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(
                event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class FlashcardsPage(QWidget):
    """Deck picker → review session → completion summary, plus autoplay sync."""

    play_requested = Signal(list)               # records → start read-aloud
    status_change_requested = Signal(str, str, str)  # word_id, status, label
    player_toggle_requested = Signal()          # pause/resume the word player
    player_prev_requested = Signal()
    player_next_requested = Signal()
    player_stop_requested = Signal()

    STATE_PICKER, STATE_SESSION, STATE_COMPLETE = 0, 1, 2

    def __init__(self, db_adapter, colors, deck_provider, settings_provider,
                 parent=None):
        super().__init__(parent)
        self.db_adapter = db_adapter
        self._colors = colors
        self._deck_provider = deck_provider
        self._settings_provider = settings_provider

        self._deck = []
        self._index = 0
        self._correct = 0
        self._graded = set()
        self._definitions = {}
        self._deck_kind = "due"
        self._autoplay = False
        self._autoplay_paused = False
        self._autoplay_listened = 0
        self._speak_cancel = None  # threading.Event of the pronunciation in flight

        self.setFocusPolicy(Qt.StrongFocus)
        self._build_ui()
        self._bind_shortcuts()
        self._apply_styles()

    # ----------------------------------------------------------------- UI

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 30)
        self._stack = QStackedLayout()
        self._stack.setStackingMode(QStackedLayout.StackOne)
        outer.addLayout(self._stack)

        settings = self._settings_provider()
        from app.config import get_bool, get_int
        deck_size = max(1, min(200, get_int(settings, "flashcards_deck_size", 20)))
        shuffle_on = get_bool(settings, "flashcards_shuffle", False)
        pronounce_on = get_bool(settings, "flashcards_pronounce", True)

        # ---- state 0: deck picker ------------------------------------
        picker = QWidget()
        pw = QVBoxLayout(picker)
        pw.addStretch(2)
        panel_row = QHBoxLayout()
        panel_row.addStretch(1)
        self.picker_panel = _Panel(self._colors)
        self.picker_panel.setMaximumWidth(620)
        pv = QVBoxLayout(self.picker_panel)
        pv.setContentsMargins(36, 28, 36, 30)
        pv.setSpacing(8)
        panel_row.addWidget(self.picker_panel, 4)
        panel_row.addStretch(1)

        self.logo = _DeckLogo(self._colors)
        logo_row = QHBoxLayout()
        logo_row.addStretch(1)
        logo_row.addWidget(self.logo)
        logo_row.addStretch(1)
        pv.addLayout(logo_row)
        self.picker_title = QLabel(tr("Flashcards"), alignment=Qt.AlignCenter)
        self.picker_sub = QLabel(tr("Practice your vocabulary"),
                                 alignment=Qt.AlignCenter)
        pv.addWidget(self.picker_title)
        pv.addWidget(self.picker_sub)
        pv.addSpacing(16)

        # deck source: exclusive chips, two centered rows so long labels
        # never get squeezed into clipping
        self._deck_group = QButtonGroup(self)
        self._deck_group.setExclusive(True)
        chips_grid = QGridLayout()
        chips_grid.setHorizontalSpacing(8)
        chips_grid.setVerticalSpacing(8)
        self._deck_chips = {}
        for i, (kind, label) in enumerate((("due", tr("Due cards")),
                                           ("filtered", tr("Current filter")),
                                           ("newest", tr("Newest")),
                                           ("selected", tr("Selected words")))):
            chip = QPushButton(label, objectName="chipButton")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            self._deck_group.addButton(chip)
            self._deck_chips[kind] = chip
            chips_grid.addWidget(chip, i // 2, i % 2)
        self._deck_chips["due"].setChecked(True)
        self._deck_group.buttonToggled.connect(
            lambda _btn, on: on and self._refresh_picker_info())
        chips_row = QHBoxLayout()
        chips_row.addStretch(1)
        chips_row.addLayout(chips_grid)
        chips_row.addStretch(1)
        pv.addLayout(chips_row)
        pv.addSpacing(10)

        options = QHBoxLayout()
        options.setSpacing(8)
        options.addStretch(1)
        self.size_label = QLabel(tr("Deck size"))
        self.size_spin = QSpinBox()
        self.size_spin.setRange(1, 200)
        self.size_spin.setValue(deck_size)
        self.size_spin.setMinimumWidth(72)
        self.size_spin.valueChanged.connect(self._persist_deck_prefs)
        self.size_spin.valueChanged.connect(self._refresh_picker_info)
        self.shuffle_btn = QPushButton(tr("Shuffle"), objectName="chipButton")
        self.shuffle_btn.setCheckable(True)
        self.shuffle_btn.setChecked(shuffle_on)
        self.shuffle_btn.setCursor(Qt.PointingHandCursor)
        self.shuffle_btn.toggled.connect(self._persist_deck_prefs)
        self.pronounce_btn = QPushButton(tr("Auto-pronounce"),
                                         objectName="chipButton")
        self.pronounce_btn.setCheckable(True)
        self.pronounce_btn.setChecked(pronounce_on)
        self.pronounce_btn.setCursor(Qt.PointingHandCursor)
        self.pronounce_btn.setToolTip(
            tr("Speak each card as it appears and when it flips"))
        self.pronounce_btn.toggled.connect(self._persist_deck_prefs)
        options.addWidget(self.size_label)
        options.addWidget(self.size_spin)
        options.addSpacing(8)
        options.addWidget(self.shuffle_btn)
        options.addWidget(self.pronounce_btn)
        options.addStretch(1)
        pv.addLayout(options)
        pv.addSpacing(18)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.start_btn = QPushButton(tr("Start session"), objectName="primaryButton")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self._start_clicked)
        self.play_btn = QPushButton(tr("Play deck"), objectName="chipButton")
        self.play_btn.setCursor(Qt.PointingHandCursor)
        self.play_btn.clicked.connect(self._play_clicked)
        actions.addWidget(self.start_btn)
        actions.addWidget(self.play_btn)
        actions.addStretch(1)
        pv.addLayout(actions)
        pv.addSpacing(6)
        self.picker_info = QLabel("", alignment=Qt.AlignCenter)
        pv.addWidget(self.picker_info)

        pw.addLayout(panel_row)
        pw.addStretch(3)
        self._stack.addWidget(picker)

        # ---- state 1: session ----------------------------------------
        session = QWidget()
        sv = QVBoxLayout(session)
        sv.setSpacing(10)
        top = QHBoxLayout()
        self.progress_label = QLabel("")
        self.correct_label = QLabel("")
        self.end_btn = QPushButton(objectName="iconButton")
        self.end_btn.setToolTip(tr("End session"))
        self.end_btn.setCursor(Qt.PointingHandCursor)
        self.end_btn.setIconSize(QSize(16, 16))
        self.end_btn.clicked.connect(self._end_session_clicked)
        top.addWidget(self.progress_label)
        top.addStretch(1)
        top.addWidget(self.correct_label)
        top.addSpacing(8)
        top.addWidget(self.end_btn)
        sv.addLayout(top)
        self.slim_bar = _SlimBar(self._colors)
        sv.addWidget(self.slim_bar)

        sv.addStretch(1)
        card_row = QHBoxLayout()
        card_row.addStretch(1)
        self.card = FlashcardWidget(self._colors)
        self.card.clicked.connect(self._card_clicked)
        self.card.speak_clicked.connect(self._speak_current_clicked)
        card_row.addWidget(self.card, 4)
        card_row.addStretch(1)
        sv.addLayout(card_row)
        sv.addStretch(1)

        self.autoplay_caption = QLabel(
            tr("Listening — pause to review manually"), alignment=Qt.AlignCenter)
        sv.addWidget(self.autoplay_caption)

        # transport row (autoplay only): prev / pause-resume / next / stop
        transport = QHBoxLayout()
        transport.setSpacing(6)
        transport.addStretch(1)

        def _transport_button(tooltip, slot):
            btn = QPushButton(objectName="iconButton")
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIconSize(QSize(18, 18))
            btn.clicked.connect(slot)
            transport.addWidget(btn)
            return btn

        self.prev_btn = _transport_button(
            tr("Previous word"), self.player_prev_requested.emit)
        self.pause_btn = _transport_button(
            tr("Pause"), self.player_toggle_requested.emit)
        self.next_btn = _transport_button(
            tr("Next word"), self.player_next_requested.emit)
        self.stop_btn = _transport_button(
            tr("Stop"), self.player_stop_requested.emit)
        transport.addStretch(1)
        self._transport_buttons = (self.prev_btn, self.pause_btn,
                                   self.next_btn, self.stop_btn)
        sv.addLayout(transport)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addStretch(1)
        self.flip_btn = QPushButton(tr("Show answer"), objectName="primaryButton")
        self.flip_btn.setCursor(Qt.PointingHandCursor)
        self.flip_btn.clicked.connect(self.flip)
        self.hard_btn = QPushButton(tr("Hard") + "  ·  1")
        self.good_btn = QPushButton(tr("Good") + "  ·  2")
        self.easy_btn = QPushButton(tr("Easy") + "  ·  3")
        for btn, grade in ((self.hard_btn, "hard"), (self.good_btn, "good"),
                           (self.easy_btn, "easy")):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setMinimumWidth(104)
            btn.clicked.connect(lambda _=False, g=grade: self._grade(g))
        bottom.addWidget(self.flip_btn)
        bottom.addWidget(self.hard_btn)
        bottom.addWidget(self.good_btn)
        bottom.addWidget(self.easy_btn)
        bottom.addStretch(1)
        sv.addLayout(bottom)
        self._stack.addWidget(session)

        # ---- state 2: complete ---------------------------------------
        complete = QWidget()
        cw = QVBoxLayout(complete)
        cw.addStretch(2)
        done_row = QHBoxLayout()
        done_row.addStretch(1)
        self.complete_panel = _Panel(self._colors)
        cv = QVBoxLayout(self.complete_panel)
        cv.setContentsMargins(36, 32, 36, 32)
        cv.setSpacing(8)
        done_row.addWidget(self.complete_panel, 4)
        done_row.addStretch(1)
        self.complete_icon = QLabel(alignment=Qt.AlignCenter)
        cv.addWidget(self.complete_icon)
        self.complete_title = QLabel(tr("Session complete!"),
                                     alignment=Qt.AlignCenter)
        self.complete_sub = QLabel("", alignment=Qt.AlignCenter)
        cv.addWidget(self.complete_title)
        cv.addWidget(self.complete_sub)
        cv.addSpacing(14)
        done = QHBoxLayout()
        done.setSpacing(10)
        done.addStretch(1)
        self.continue_btn = QPushButton(tr("Continue"), objectName="primaryButton")
        self.continue_btn.setCursor(Qt.PointingHandCursor)
        self.continue_btn.clicked.connect(self._continue_clicked)
        self.new_session_btn = QPushButton(tr("New session"), objectName="chipButton")
        self.new_session_btn.setCursor(Qt.PointingHandCursor)
        self.new_session_btn.clicked.connect(self._show_picker)
        done.addWidget(self.continue_btn)
        done.addWidget(self.new_session_btn)
        done.addStretch(1)
        cv.addLayout(done)
        cw.addLayout(done_row)
        cw.addStretch(3)
        self._stack.addWidget(complete)

    def _bind_shortcuts(self):
        def bind(key, slot):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        bind(Qt.Key_Space, self._on_space)
        bind(Qt.Key_1, lambda: self._grade("hard"))
        bind(Qt.Key_2, lambda: self._grade("good"))
        bind(Qt.Key_3, lambda: self._grade("easy"))
        bind(Qt.Key_Left, self._prev_card)
        bind(Qt.Key_Right, self._next_card_skip)

    # -------------------------------------------------------------- deck

    def on_shown(self):
        """Called by the main window whenever the page becomes current."""
        if self._stack.currentIndex() == self.STATE_PICKER:
            self._refresh_picker()
            self.logo.replay()
        self.setFocus(Qt.OtherFocusReason)

    def _checked_deck_kind(self):
        for kind, chip in self._deck_chips.items():
            if chip.isChecked():
                return kind
        return "due"

    def _refresh_picker(self):
        has_selection = bool(self._fetch_deck("selected", 1))
        chip = self._deck_chips["selected"]
        chip.setEnabled(has_selection)
        if has_selection:
            chip.setChecked(True)
        elif chip.isChecked():
            self._deck_chips["due"].setChecked(True)
        self._refresh_picker_info()

    def _refresh_picker_info(self):
        kind = self._checked_deck_kind()
        if kind == "due":
            try:
                due = len(dbq.srs_due_word_ids(500))
            except Exception:
                due = 0
            if due:
                text = tr("{n} cards ready to review").format(
                    n="500+" if due >= 500 else due)
            else:
                text = tr("No cards due — great job!")
        elif kind == "selected":
            n = len(self._fetch_deck("selected", 200))
            text = tr("{n} selected words").format(n=n)
        else:
            n = len(self._fetch_deck(kind, self.size_spin.value()))
            text = tr("{n} cards ready to review").format(n=n) if n else ""
        self.picker_info.setText(text)

    def _fetch_deck(self, kind, n):
        try:
            return list(self._deck_provider(kind, n) or [])
        except Exception as exc:
            logging.error(f"Flashcards deck fetch failed: {exc}")
            return []

    def _persist_deck_prefs(self, *_args):
        try:
            from app.config import save_settings
            settings = self._settings_provider()
            settings["flashcards_deck_size"] = str(self.size_spin.value())
            settings["flashcards_shuffle"] = str(self.shuffle_btn.isChecked())
            settings["flashcards_pronounce"] = str(self.pronounce_btn.isChecked())
            save_settings(settings)
        except Exception as exc:
            logging.error(f"Saving flashcard prefs failed: {exc}")

    # ------------------------------------------------------------ session

    def _start_clicked(self):
        kind = self._checked_deck_kind()
        records = self._fetch_deck(kind, self.size_spin.value())
        if not records:
            self.picker_info.setText(
                tr("No cards due — great job!") if kind == "due"
                else tr("No words to practice."))
            return
        self._deck_kind = kind
        if self.shuffle_btn.isChecked():
            records = records[:]
            random.shuffle(records)
        self.start_session(records)

    def _play_clicked(self):
        kind = self._checked_deck_kind()
        records = self._fetch_deck(kind, self.size_spin.value())
        if not records:
            self.picker_info.setText(
                tr("No cards due — great job!") if kind == "due"
                else tr("No words to practice."))
            return
        self._deck_kind = kind
        self.play_requested.emit(records)

    def start_session(self, records, autoplay=False):
        self._cancel_speech()
        self._deck = list(records)
        self._index = 0
        self._correct = 0
        self._graded = set()
        self._definitions = {}
        self._autoplay = autoplay
        self._autoplay_paused = False
        self._autoplay_listened = 0
        self._stack.setCurrentIndex(self.STATE_SESSION)
        self._show_card(0, animate=False)
        self.setFocus(Qt.OtherFocusReason)

    def _show_card(self, i, animate=True):
        if not self._deck:
            return
        i = max(0, min(i, len(self._deck) - 1))
        self._index = i
        rec = self._deck[i]
        if animate and self.card.isVisible():
            fade_swap(self.card, 160)
        self.card.set_card(rec, hint_text=tr("Space or click to flip"))
        self._refresh_session_header()
        self._update_controls()
        if not self._autoplay:  # in autoplay the word player provides the audio
            self._auto_pronounce(0)
            self._prefetch_upcoming()

    def _refresh_session_header(self):
        total = len(self._deck)
        self.progress_label.setText(
            tr("Card {current} of {total}").format(
                current=self._index + 1, total=total))
        self.correct_label.setText(
            tr("{n} correct").format(n=self._correct))
        self.correct_label.setVisible(not self._autoplay)
        self.slim_bar.set_progress(self._index + 1, total)

    def _definition_for(self, record):
        wid = record.get("ID")
        if wid is None:
            return ""
        if wid not in self._definitions:
            text = ""
            try:
                row = self.db_adapter.get_word(wid) or {}
                parts = [str(row.get("Definition") or "").strip(),
                         str(row.get("Definition2") or "").strip()]
                text = "\n\n".join(part for part in parts if part)
            except Exception as exc:
                logging.error(f"Definition lookup failed: {exc}")
            self._definitions[wid] = text
        return self._definitions[wid]

    # ---------------------------------------------------------- pronounce

    def _pronounce_enabled(self):
        from app.config import get_bool
        return get_bool(self._settings_provider(), "flashcards_pronounce", True)

    def _cancel_speech(self):
        if self._speak_cancel is not None:
            self._speak_cancel.set()
            self._speak_cancel = None

    def _pronounce_side(self, side):
        """Speak the given side of the current card, superseding any
        pronunciation still in flight."""
        if not self._deck:
            return
        rec = self._deck[self._index]
        if side == 0:
            text, language = rec.get("Word1"), rec.get("Language1")
        else:
            text, language = rec.get("Word2"), rec.get("Language2")
        text = str(text or "").strip()
        if not text or language not in audio.lang_codes:
            return
        self._cancel_speech()
        cancel = threading.Event()
        self._speak_cancel = cancel
        run_in_thread(
            audio.speak_word, text, language, cancel_event=cancel,
            on_error=lambda msg: logging.warning(
                f"Flashcard pronunciation failed: {msg}"))

    def _auto_pronounce(self, side):
        """Auto-speak on card change / flip (manual review only)."""
        if self._autoplay and not self._autoplay_paused:
            return  # the word player owns the audio
        if self._pronounce_enabled():
            self._pronounce_side(side)

    def _speak_current_clicked(self):
        if self._autoplay and not self._autoplay_paused:
            return
        self._pronounce_side(self.card.side)

    def _prefetch_upcoming(self):
        """Warm the audio cache for what's likely spoken next — this card's
        back and the next card's both sides — so flips and advances play
        without the synthesis delay."""
        if not self._pronounce_enabled() or not self._deck:
            return
        rec = self._deck[self._index]
        targets = [(rec.get("Word2"), rec.get("Language2"))]
        if self._index + 1 < len(self._deck):
            nxt = self._deck[self._index + 1]
            targets += [(nxt.get("Word1"), nxt.get("Language1")),
                        (nxt.get("Word2"), nxt.get("Language2"))]
        for text, language in targets:
            text = str(text or "").strip()
            if text and language in audio.lang_codes:
                run_in_thread(audio.prefetch_word, text, language)

    def flip(self):
        if (not self._deck or self._stack.currentIndex() != self.STATE_SESSION
                or self.card._flipping):
            return
        target = 1 - self.card.side
        if target == 1:
            self.card.set_definition(self._definition_for(self._deck[self._index]))
        self.card.flip()
        self._update_controls(side=target)
        self._auto_pronounce(target)

    def _card_clicked(self):
        if self._autoplay and not self._autoplay_paused:
            self.player_toggle_requested.emit()
        else:
            self.flip()

    def _on_space(self):
        if self._stack.currentIndex() != self.STATE_SESSION:
            return
        if self._autoplay and not self._autoplay_paused:
            self.player_toggle_requested.emit()
        else:
            self.flip()

    def _grade(self, grade):
        if (self._stack.currentIndex() != self.STATE_SESSION or not self._deck
                or (self._autoplay and not self._autoplay_paused)):
            return
        if self.card.side != 1:
            return
        rec = self._deck[self._index]
        wid = rec.get("ID")
        if wid is None or wid in self._graded:
            return
        try:
            state = srs.apply_grade(dbq.srs_get(wid), grade, datetime.now())
            dbq.srs_upsert(wid, state)
            dbq.log_review(wid, datetime.now().isoformat(timespec="seconds"))
        except Exception as exc:
            logging.error(f"Recording flashcard grade failed: {exc}")
            return
        self._graded.add(wid)
        if grade in ("easy", "good"):
            self._correct += 1
        mapped = srs.status_from_progress(
            state["review_count"], state["ease_factor"], state["correct_count"])
        target = srs.promotion_target(rec.get("Status"), mapped)
        if target:
            rec["Status"] = target
            self.status_change_requested.emit(
                str(wid), target, str(rec.get("Word1") or ""))
        self._refresh_session_header()
        if self._autoplay:
            self._update_controls()  # stay on the card; the player owns position
        elif self._index + 1 < len(self._deck):
            self._show_card(self._index + 1)
        else:
            self._complete()

    def _prev_card(self):
        if self._stack.currentIndex() != self.STATE_SESSION or not self._deck:
            return
        if self._autoplay:
            if self._autoplay_paused:
                self.player_prev_requested.emit()
            return
        if self._index > 0:
            self._show_card(self._index - 1)

    def _next_card_skip(self):
        if self._stack.currentIndex() != self.STATE_SESSION or not self._deck:
            return
        if self._autoplay:
            if self._autoplay_paused:
                self.player_next_requested.emit()
            return
        if self._index + 1 < len(self._deck):
            self._show_card(self._index + 1)

    def _end_session_clicked(self):
        if self._autoplay:
            self.player_stop_requested.emit()
        self._show_picker()

    def _complete(self):
        total = len(self._deck)
        if self._autoplay_listened:
            summary = tr("You listened to {n} of {total} cards.").format(
                n=self._autoplay_listened, total=total)
        else:
            summary = tr("Correct: {n} of {total}").format(
                n=self._correct, total=total)
        self.complete_sub.setText(summary)
        self.complete_icon.setPixmap(
            icons.pixmap("check", self._colors["success"], 40))
        self.continue_btn.setVisible(self._deck_kind == "due"
                                     and not self._autoplay_listened)
        self._stack.setCurrentIndex(self.STATE_COMPLETE)

    def _continue_clicked(self):
        records = self._fetch_deck(self._deck_kind, self.size_spin.value())
        if not records:
            self._show_picker()
            return
        if self.shuffle_btn.isChecked():
            random.shuffle(records)
        self.start_session(records)

    def hideEvent(self, event):  # noqa: N802 — leaving the page silences it
        self._cancel_speech()
        super().hideEvent(event)

    def _show_picker(self):
        self._cancel_speech()
        self._deck = []
        self._autoplay = False
        self._autoplay_paused = False
        self._stack.setCurrentIndex(self.STATE_PICKER)
        self._refresh_picker()
        self.logo.replay()

    # ----------------------------------------------------------- autoplay

    def enter_autoplay(self, records):
        """Follow a read-aloud session: cards advance and flip with the audio."""
        self.start_session(records, autoplay=True)
        self._refresh_session_header()
        self._update_controls()

    def on_autoplay_index(self, i):
        if not self._autoplay or i >= len(self._deck):
            return
        self._autoplay_listened = max(self._autoplay_listened, i + 1)
        self._show_card(i)

    def on_autoplay_part(self, slot):
        if not self._autoplay or not self._deck:
            return
        target = 1 if slot else 0
        if target == 1 and self.card.side == 0:
            self.card.set_definition(self._definition_for(self._deck[self._index]))
        self.card.show_side(target, duration=FLIP_MS_AUTOPLAY)

    def on_autoplay_state(self, paused):
        if not self._autoplay:
            return
        self._autoplay_paused = bool(paused)
        if not paused:
            self._cancel_speech()  # don't talk over the resuming player
        self._update_controls()

    def exit_autoplay(self):
        if not self._autoplay:
            return
        ran_to_end = bool(self._deck) and self._index >= len(self._deck) - 1
        self._autoplay = False
        self._autoplay_paused = False
        if self._stack.currentIndex() != self.STATE_SESSION:
            return
        if ran_to_end:
            self._complete()
        else:
            self._update_controls()  # hand the session over to manual review

    # ------------------------------------------------------------ controls

    def _update_controls(self, side=None):
        side = self.card.side if side is None else side
        listening = self._autoplay and not self._autoplay_paused
        self.autoplay_caption.setVisible(listening)
        self.card.speak_btn.setVisible(not listening)
        for btn in self._transport_buttons:
            btn.setVisible(self._autoplay)
        if self._autoplay:
            self.pause_btn.setIcon(icons.icon(
                "play" if self._autoplay_paused else "pause",
                self._colors["text"], 18))
            self.pause_btn.setToolTip(
                tr("Resume") if self._autoplay_paused else tr("Pause"))
        manual_review = not listening
        self.flip_btn.setVisible(manual_review and side == 0)
        graded = (bool(self._deck)
                  and self._deck[self._index].get("ID") in self._graded)
        for btn in (self.hard_btn, self.good_btn, self.easy_btn):
            btn.setVisible(manual_review and side == 1)
            btn.setEnabled(not graded)
        self.end_btn.setVisible(True)

    # -------------------------------------------------------------- theme

    def refresh_theme(self, colors):
        self._colors = colors
        self.card.refresh_theme(colors)
        self.slim_bar.refresh_theme(colors)
        self.picker_panel.refresh_theme(colors)
        self.complete_panel.refresh_theme(colors)
        self.logo.refresh_theme(colors)
        self._apply_styles()
        if self._autoplay:
            self._update_controls()

    def _apply_styles(self):
        c = self._colors
        dim = f"color:{c['text_dim']};background:transparent;"
        self.picker_title.setStyleSheet(
            f"color:{c['text']};background:transparent;"
            "font-size:16pt;font-weight:700;")
        self.picker_sub.setStyleSheet(dim + "font-size:10.5pt;")
        self.picker_info.setStyleSheet(dim + "font-size:10pt;")
        self.size_label.setStyleSheet(dim + "font-size:10pt;")
        self.progress_label.setStyleSheet(dim + "font-weight:600;")
        self.correct_label.setStyleSheet(
            f"color:{c['success']};background:transparent;font-weight:600;")
        self.autoplay_caption.setStyleSheet(dim + "font-size:9.5pt;")
        self.complete_title.setStyleSheet(
            f"color:{c['text']};background:transparent;"
            "font-size:16pt;font-weight:700;")
        self.complete_sub.setStyleSheet(dim + "font-size:11pt;")
        self.end_btn.setIcon(icons.icon("x", c["text_dim"], 16))
        self.prev_btn.setIcon(icons.icon("skip-back", c["text"], 18))
        self.next_btn.setIcon(icons.icon("skip-forward", c["text"], 18))
        self.stop_btn.setIcon(icons.icon("stop", c["danger"], 18))
        self.pause_btn.setIcon(icons.icon(
            "play" if self._autoplay_paused else "pause", c["text"], 18))
        self.play_btn.setIcon(icons.icon("play", c["text"], 14))

        def grade_style(tint):
            return (f"QPushButton {{ background:{_soft(tint, 34)};"
                    f"color:{c['text']}; border:1px solid {_soft(tint, 90)};"
                    "border-radius:9px; padding:8px 20px; font-weight:600; }"
                    f"QPushButton:hover {{ background:{_soft(tint, 60)}; }}"
                    f"QPushButton:disabled {{ color:{c['text_dim']};"
                    f"background:{_soft(tint, 16)}; }}")

        self.hard_btn.setStyleSheet(grade_style(c["danger"]))
        self.good_btn.setStyleSheet(grade_style(c["warning"]))
        self.easy_btn.setStyleSheet(grade_style(c["success"]))
