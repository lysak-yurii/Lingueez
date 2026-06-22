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

"""The dashboard wordmark: the app title painted with a brand gradient and a
sheen. On each launch the letters track in (tighten from slightly spread) while
a bright sheen sweeps across; afterwards a soft sheen passes periodically for
the life of the window — a subtle, living brand mark."""

from PySide6.QtCore import (Property, QEasingCurve, QEvent,
                            QParallelAnimationGroup, QPointF, QPropertyAnimation,
                            QSequentialAnimationGroup, QSize, Qt, QTimer)
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QLinearGradient,
                           QPainter, QPainterPath)
from PySide6.QtWidgets import QWidget

HOLD_MS = 420            # anticipation pause before the entrance plays
TITLE_PT_BUMP = 4        # matches #AppTitle = base_font_size + 4 (theme.py)
IDLE_PAUSE_MS = 3200     # gap between idle sheen passes
IDLE_SWEEP_MS = 1700     # duration of one idle sheen pass


class BrandWordmark(QWidget):
    """Animated, theme-aware app title. Replaces the plain #AppTitle QLabel.

    Paints the text from a QPainterPath (crisp, gradient-fillable). The font is
    derived from the widget's QSS-applied font (base size + 4, bold) and tracked
    via FontChange so it stays correct across theme/scaling changes. Call
    `play()` once per launch (from the window's showEvent); `set_colors()` on
    theme change."""

    def __init__(self, text, colors, parent=None):
        super().__init__(parent)
        self._text = text
        self._colors = colors

        # animatable state — starts in the "before" pose so the entrance has
        # something to animate from (invisible until play() runs)
        self._spacing = 12.0   # extra px between letters (tracking-in)
        self._sweep = -0.2     # sheen band position across the word
        self._sheen_amp = 1.0  # sheen strength (bright entrance → soft idle)
        self._opacity = 0.0     # fade-in

        self._played = False
        self._group = None
        self._idle = None

        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._recompute()

    # --- theme -----------------------------------------------------------
    def set_colors(self, colors):
        self._colors = colors
        self.update()

    # --- font + metrics --------------------------------------------------
    def _title_font(self):
        f = QFont(self.font())
        f.setPointSizeF(self.font().pointSizeF() + TITLE_PT_BUMP)
        f.setWeight(QFont.Bold)
        return f

    def _recompute(self):
        fm = QFontMetricsF(self._title_font())
        self._ascent = fm.ascent()
        self._text_w = fm.horizontalAdvance(self._text)
        self._text_h = fm.height()
        self._advances = []
        acc = 0.0
        for ch in self._text:
            self._advances.append(acc)
            acc += fm.horizontalAdvance(ch)
        self.updateGeometry()
        self.update()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.FontChange:
            self._recompute()

    def sizeHint(self):
        # a little slack on width covers the transient tracking spread
        return QSize(int(self._text_w + 2), int(self._text_h + 4))

    # --- animatable properties ------------------------------------------
    def _g_spacing(self):
        return self._spacing

    def _s_spacing(self, v):
        self._spacing = v
        self.update()

    def _g_sweep(self):
        return self._sweep

    def _s_sweep(self, v):
        self._sweep = v
        self.update()

    def _g_op(self):
        return self._opacity

    def _s_op(self, v):
        self._opacity = v
        self.update()

    spacingF = Property(float, _g_spacing, _s_spacing)
    sweep = Property(float, _g_sweep, _s_sweep)
    opacityF = Property(float, _g_op, _s_op)

    # --- paint -----------------------------------------------------------
    def _gradient(self, x0, x1):
        c = self._colors
        g = QLinearGradient(QPointF(x0, 0), QPointF(x1, 0))
        g.setColorAt(0.0, QColor(c.get("accent_pressed", c["accent"])))
        g.setColorAt(0.5, QColor(c["accent"]))
        g.setColorAt(1.0, QColor(c.get("accent_hover", c["accent"])))
        return g

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.setOpacity(max(0.0, min(1.0, self._opacity)))

        f = self._title_font()
        extra = self._spacing
        n = len(self._text)
        spaced_w = self._text_w + extra * (n - 1)
        base = (self.height() - self._text_h) / 2.0 + self._ascent

        path = QPainterPath()
        for i, ch in enumerate(self._text):
            path.addText(self._advances[i] + i * extra, base, f, ch)
        p.fillPath(path, self._gradient(0.0, spaced_w))

        peak = int(max(0, min(255, self._sheen_amp * 150)))
        if peak > 0:
            p.save()
            p.setClipPath(path)
            band_w = spaced_w * 0.42
            center = -band_w + self._sweep * (spaced_w + 2 * band_w)
            g = QLinearGradient(QPointF(center - band_w, 0),
                                QPointF(center + band_w, 0))
            g.setColorAt(0.0, QColor(255, 255, 255, 0))
            g.setColorAt(0.5, QColor(255, 255, 255, peak))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(self.rect(), g)
            p.restore()

    # --- choreography ----------------------------------------------------
    def play(self):
        """Track-in entrance + sheen sweep, once per launch; then idle shimmer."""
        if self._played:
            return
        self._played = True
        self._spacing = 12.0
        self._sweep = -0.2
        self._sheen_amp = 1.0
        self._opacity = 0.0

        grp = QParallelAnimationGroup(self)
        track = QPropertyAnimation(self, b"spacingF", self)
        track.setStartValue(12.0)
        track.setEndValue(0.0)
        track.setDuration(820)
        track.setEasingCurve(QEasingCurve.OutCubic)
        sweep = QPropertyAnimation(self, b"sweep", self)
        sweep.setStartValue(-0.2)
        sweep.setEndValue(1.2)
        sweep.setDuration(1000)
        sweep.setEasingCurve(QEasingCurve.InOutCubic)
        fade = QPropertyAnimation(self, b"opacityF", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(620)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        grp.addAnimation(track)
        grp.addAnimation(sweep)
        grp.addAnimation(fade)
        grp.finished.connect(self._start_idle_sheen)
        self._group = grp
        QTimer.singleShot(HOLD_MS, grp.start)

    def _start_idle_sheen(self):
        self._sheen_amp = 0.5
        self._sweep = -0.25
        seq = QSequentialAnimationGroup(self)
        seq.addPause(IDLE_PAUSE_MS)
        sweep = QPropertyAnimation(self, b"sweep", self)
        sweep.setStartValue(-0.25)
        sweep.setEndValue(1.25)
        sweep.setDuration(IDLE_SWEEP_MS)
        sweep.setEasingCurve(QEasingCurve.InOutSine)
        seq.addAnimation(sweep)
        seq.setLoopCount(-1)
        seq.start()
        self._idle = seq
