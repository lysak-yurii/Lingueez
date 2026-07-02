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

"""Snapshot-based view transitions.

Animating static snapshots instead of live widgets keeps transitions
artifact-free: heavy pages (large table views) never relayout or repaint
mid-animation.
"""
from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation,
    QRectF, Qt, QVariantAnimation,
)
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget,
)


def crossfade_during(widget, work, duration=260, message=None):
    """Mask a blocking restyle behind a snapshot, then crossfade to the result.

    Grabs `widget` as it looks now and holds that frozen image on top while
    `work()` runs — the heavy, event-loop-blocking part such as a full
    `setStyleSheet()` — then fades the stale snapshot out to reveal the freshly
    styled widget underneath. The user sees a clean old→new dissolve instead of
    a half-painted window locking up.

    `message` (optional): show a dimmed "working" card with this text over the
    snapshot before `work()` blocks. The card can't animate while the GUI thread
    is busy (all painting is on this thread), but it makes the unavoidable pause
    read as deliberate "Applying…" rather than a frozen window.
    """
    if not widget.isVisible():
        work()
        return
    snap = QLabel(widget)
    snap.setAttribute(Qt.WA_TransparentForMouseEvents)
    snap.setPixmap(widget.grab())
    snap.setGeometry(widget.rect())
    snap.show()
    snap.raise_()

    if message:
        dim = QWidget(snap)
        dim.setGeometry(snap.rect())
        dim.setStyleSheet("background: rgba(0, 0, 0, 110);")
        dim.show()
        card = QLabel(message, snap)
        card.setAlignment(Qt.AlignCenter)
        card.setStyleSheet(
            "color: white; background: rgba(28, 28, 30, 235); padding: 16px 26px;"
            "border-radius: 12px; font-size: 14px; font-weight: 600;")
        card.adjustSize()
        card.move((snap.width() - card.width()) // 2,
                  (snap.height() - card.height()) // 2)
        card.show()
        card.raise_()

    # Flush once so the snapshot (and the "Applying…" card) is actually on screen
    # before work() blocks the event loop; otherwise the freeze shows through.
    QApplication.processEvents()
    QApplication.processEvents()

    work()

    effect = QGraphicsOpacityEffect(snap)
    snap.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", snap)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.InOutCubic)
    anim.finished.connect(snap.deleteLater)
    anim.start(QPropertyAnimation.DeleteWhenStopped)


def fade_swap(widget, duration=140):
    """Crossfade `widget` to its next contents.

    Grabs a snapshot of the current state and fades it out over whatever
    the caller renders underneath right after this call.
    """
    if not widget.isVisible():
        return
    snap = QLabel(widget)
    snap.setAttribute(Qt.WA_TransparentForMouseEvents)
    snap.setPixmap(widget.grab())
    snap.setGeometry(widget.rect())
    effect = QGraphicsOpacityEffect(snap)
    snap.setGraphicsEffect(effect)
    snap.show()
    snap.raise_()
    anim = QPropertyAnimation(effect, b"opacity", snap)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.finished.connect(snap.deleteLater)
    anim.start(QPropertyAnimation.DeleteWhenStopped)


class _FlipSnapshot(QWidget):
    """Overlay that paints a snapshot horizontally squashed toward its center,
    with the page background filling the exposed sides — a card seen mid-turn."""

    def __init__(self, parent, bg):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pix = None
        self._factor = 1.0
        self._bg = bg

    def set_pixmap(self, pix):
        self._pix = pix
        self.update()

    def set_factor(self, factor):
        self._factor = float(factor)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._bg)
        if self._pix is not None and self._factor > 0.01:
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            w = self.width() * self._factor
            target = QRectF((self.width() - w) / 2, 0, w, self.height())
            painter.drawPixmap(target, self._pix, QRectF(self._pix.rect()))
        painter.end()


def flip_swap(widget, work, duration=220, bg=None):
    """Fake a horizontal card flip while `work()` swaps the widget's contents.

    Phase one squashes a snapshot of the current state to a vertical sliver
    (the card edge-on), `work()` re-renders the widget underneath, and phase
    two expands a fresh snapshot back to full width. `bg` is the color behind
    the turning card — pass the page background so the reveal reads as the
    card rotating in place; defaults to the widget's palette window color.
    """
    if not widget.isVisible():
        work()
        return
    color = QColor(bg) if bg is not None else widget.palette().color(QPalette.Window)
    overlay = _FlipSnapshot(widget, color)
    overlay.setGeometry(widget.rect())
    overlay.set_pixmap(widget.grab())
    overlay.show()
    overlay.raise_()
    half = max(1, int(duration) // 2)

    def _expand():
        work()
        if widget.layout() is not None:
            widget.layout().activate()
        overlay.set_pixmap(widget.grab())
        anim = QVariantAnimation(overlay)
        anim.setDuration(half)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(overlay.set_factor)
        anim.finished.connect(overlay.deleteLater)
        anim.start(QVariantAnimation.DeleteWhenStopped)

    shrink = QVariantAnimation(overlay)
    shrink.setDuration(half)
    shrink.setStartValue(1.0)
    shrink.setEndValue(0.0)
    shrink.setEasingCurve(QEasingCurve.InCubic)
    shrink.valueChanged.connect(overlay.set_factor)
    shrink.finished.connect(_expand)
    shrink.start(QVariantAnimation.DeleteWhenStopped)


class AnimatedStackedWidget(QStackedWidget):
    """QStackedWidget with a slide + crossfade transition between pages."""

    DURATION = 220
    SLIDE = 42

    def __init__(self, parent=None):
        super().__init__(parent)
        self._overlay = None

    def set_current_index_animated(self, index):
        old = self.currentIndex()
        if index == old:
            return
        if self._overlay is not None or not self.isVisible():
            self.setCurrentIndex(index)
            return

        direction = 1 if index > old else -1
        old_pix = self.currentWidget().grab()
        self.setCurrentIndex(index)
        new_pix = self.currentWidget().grab()

        overlay = QWidget(self)
        overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        overlay.setAutoFillBackground(True)
        overlay.setGeometry(self.rect())
        self._overlay = overlay

        group = QParallelAnimationGroup(overlay)
        for pix, start, end, fade_in in (
            (old_pix, QPoint(0, 0), QPoint(-direction * self.SLIDE, 0), False),
            (new_pix, QPoint(direction * self.SLIDE, 0), QPoint(0, 0), True),
        ):
            label = QLabel(overlay)
            label.setPixmap(pix)
            label.setGeometry(self.rect())
            label.move(start)
            effect = QGraphicsOpacityEffect(label)
            label.setGraphicsEffect(effect)
            slide = QPropertyAnimation(label, b"pos", label)
            slide.setStartValue(start)
            slide.setEndValue(end)
            fade = QPropertyAnimation(effect, b"opacity", label)
            fade.setStartValue(0.0 if fade_in else 1.0)
            fade.setEndValue(1.0 if fade_in else 0.0)
            for anim in (slide, fade):
                anim.setDuration(self.DURATION)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                group.addAnimation(anim)

        overlay.show()
        overlay.raise_()
        group.finished.connect(self._end_transition)
        group.start(QParallelAnimationGroup.DeleteWhenStopped)

    def _end_transition(self):
        if self._overlay is not None:
            self._overlay.deleteLater()
            self._overlay = None
