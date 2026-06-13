"""Snapshot-based view transitions.

Animating static snapshots instead of live widgets keeps transitions
artifact-free: heavy pages (large table views) never relayout or repaint
mid-animation.
"""
from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, Qt,
)
from PySide6.QtWidgets import (
    QApplication, QGraphicsOpacityEffect, QLabel, QStackedWidget, QWidget,
)


def crossfade_during(widget, work, duration=260):
    """Mask a blocking restyle behind a snapshot, then crossfade to the result.

    Grabs `widget` as it looks now and holds that frozen image on top while
    `work()` runs — the heavy, event-loop-blocking part such as a full
    `setStyleSheet()` — then fades the stale snapshot out to reveal the freshly
    styled widget underneath. The user sees a clean old→new dissolve instead of
    a half-painted window locking up.
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
    # Flush once so the snapshot is actually on screen before work() blocks
    # the event loop; otherwise the freeze shows through.
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
