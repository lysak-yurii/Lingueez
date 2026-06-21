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

"""First-run welcome dialog that pitches cloud sync, built on FramelessDialog.

Shown once on a fresh, signed-out launch. The primary action funnels into the
app's shared sign-in flow; "Continue on this device" simply closes and the user
keeps working locally (sync can still be enabled later from the cloud icon or
Settings). Marking it as seen is the caller's job (MainWindow._show_welcome)."""
from PySide6.QtCore import (Property, QEasingCurve, QParallelAnimationGroup,
                            QPropertyAnimation, QRectF, Qt)
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from app.i18n import tr
from app.ui.dialogs.base import FramelessDialog, quiet_frame, quiet_row
from app.version import APP_NAME

# Resting display size of the app icon (px) and the box it lives in. The box has
# headroom so the entrance overshoot and the idle float never clip or reflow.
ICON_BASE = 62
ICON_BOX = 88


class AnimatedAppIcon(QWidget):
    """The app icon, custom-painted so scale / vertical offset / opacity are all
    animatable independently of the layout. On show it scales in with a soft
    overshoot and fades up; then it settles into a slow, barely-there float —
    elegant and alive without the 'loading spinner' feel of a hard pulse."""

    def __init__(self, pixmap, dpr, parent=None):
        super().__init__(parent)
        self._dpr = dpr
        # Pre-scale ONCE to the largest size we'll draw (base size × overshoot
        # headroom × dpr). Per-frame we then only blit this small pixmap, instead
        # of re-sampling the 549px source every tick — that resampling was the
        # cause of the dropped frames / "low fps" look.
        maxpx = max(1, int(ICON_BASE * 1.2 * dpr))
        self._base = pixmap.scaled(
            maxpx, maxpx, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._scale = 0.6      # animated 0.6 -> 1.0 on entrance
        self._offset = 10.0    # animated vertical px, eases to 0 then floats
        self._opacity = 0.0    # animated 0 -> 1 on entrance
        self.setFixedSize(ICON_BOX, ICON_BOX)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    # --- animatable Qt properties ---------------------------------------
    def _get_scale(self):
        return self._scale

    def _set_scale(self, v):
        self._scale = v
        self.update()

    def _get_offset(self):
        return self._offset

    def _set_offset(self, v):
        self._offset = v
        self.update()

    def _get_opacity(self):
        return self._opacity

    def _set_opacity(self, v):
        self._opacity = v
        self.update()

    scaleF = Property(float, _get_scale, _set_scale)
    offsetY = Property(float, _get_offset, _set_offset)
    opacityF = Property(float, _get_opacity, _set_opacity)

    # --- paint -----------------------------------------------------------
    def paintEvent(self, event):
        if self._base.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(max(0.0, min(1.0, self._opacity)))
        size = ICON_BASE * self._scale
        x = (self.width() - size) / 2.0
        y = (self.height() - size) / 2.0 + self._offset
        # Draw the pre-scaled base; QRectF keeps the position subpixel-smooth.
        p.drawPixmap(QRectF(x, y, size, size), self._base, QRectF(self._base.rect()))

    # --- choreography ----------------------------------------------------
    def play(self):
        """Run the entrance, then hand off to the perpetual idle float."""
        entrance = QParallelAnimationGroup(self)
        for prop, start, end, dur, curve in (
                (b"opacityF", 0.0, 1.0, 460, QEasingCurve.OutCubic),
                (b"scaleF", 0.6, 1.0, 700, QEasingCurve.OutBack),
                (b"offsetY", 10.0, 0.0, 700, QEasingCurve.OutCubic)):
            a = QPropertyAnimation(self, prop, self)
            a.setStartValue(start)
            a.setEndValue(end)
            a.setDuration(dur)
            a.setEasingCurve(curve)
            entrance.addAnimation(a)
        entrance.finished.connect(self._start_float)
        entrance.start()
        self._entrance = entrance  # keep a ref so it isn't GC'd

    def _start_float(self):
        """A slow, ~5px vertical drift that loops forever — the resting state."""
        float_anim = QPropertyAnimation(self, b"offsetY", self)
        float_anim.setDuration(3800)
        float_anim.setKeyValueAt(0.0, 0.0)
        float_anim.setKeyValueAt(0.5, -5.0)
        float_anim.setKeyValueAt(1.0, 0.0)
        float_anim.setEasingCurve(QEasingCurve.InOutSine)
        float_anim.setLoopCount(-1)
        float_anim.start()
        self._float = float_anim


class WelcomeDialog(FramelessDialog):
    """Skippable first-run sync pitch. exec() is truthy when the user chose to
    sign in / create an account, falsy when they chose to stay local."""

    def __init__(self, parent=None):
        # No title text: the in-body heading below is the title, so a duplicate in
        # the chrome bar would just repeat it. The bar keeps the close button.
        super().__init__(parent, title="")
        self.setMinimumWidth(420)
        layout = self.content_layout

        self._build_animated_icon(layout)

        title = QLabel(tr("Welcome to {app}").format(app=APP_NAME))
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        title.setWordWrap(True)
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            tr("Sign in to keep your vocabulary safe and study it on every device."))
        subtitle.setObjectName("dimLabel")
        subtitle.setWordWrap(True)
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        # Three benefit rows in the shared "quiet frame" used by the sync dialogs.
        frame, rows = quiet_frame(self.colors)
        benefits = (
            ("swap", tr("Sync across your devices"),
             tr("Your words follow you to every computer.")),
            ("cloud", tr("Automatic cloud backup"),
             tr("Never lose your progress.")),
            ("globe", tr("Study anywhere"),
             tr("Pick up right where you left off.")),
        )
        last = len(benefits) - 1
        for i, (icon_name, head, sub) in enumerate(benefits):
            rows.addWidget(self._benefit_row(icon_name, head, sub, divider=i < last))
        layout.addWidget(frame)

        note = QLabel(
            tr("Your data is yours — sign in only to sync it."))
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        primary = QPushButton(tr("Sign in / Create account"), objectName="primaryButton")
        primary.setCursor(Qt.PointingHandCursor)
        primary.setDefault(True)
        primary.clicked.connect(self.accept)
        layout.addWidget(primary)

        skip = QPushButton(tr("Continue on this device"))
        skip.setFlat(True)
        skip.setCursor(Qt.PointingHandCursor)
        skip.clicked.connect(self.reject)
        layout.addWidget(skip, 0, Qt.AlignCenter)

    def _build_animated_icon(self, layout):
        """Add the centered, self-animating app icon. Degrades to nothing if the
        asset is missing. The entrance is kicked off in showEvent so it plays once
        the dialog is actually on screen."""
        self._icon = None
        src = QPixmap("assets/icons/icon.png")
        if src.isNull():
            return
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        dpr = screen.devicePixelRatio() if screen else 2.0
        self._icon = AnimatedAppIcon(src, dpr, self)
        layout.addWidget(self._icon, 0, Qt.AlignHCenter)

    def showEvent(self, event):
        super().showEvent(event)
        # Play once per open; guard so a re-show (e.g. focus toggles) doesn't restart it.
        if getattr(self, "_icon", None) is not None and not getattr(self, "_icon_played", False):
            self._icon_played = True
            self._icon.play()

    def _benefit_row(self, icon_name, head, sub, *, divider):
        """A quiet_row whose primary cell stacks a bold heading over a dim line."""
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        cell = QWidget()
        v = QVBoxLayout(cell)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        h = QLabel(head)
        h.setStyleSheet(f"color:{self.colors['text']}; font-size:13px; font-weight:600;")
        s = QLabel(sub)
        s.setStyleSheet(f"color:{self.colors['text_dim']}; font-size:11.5px;")
        s.setWordWrap(True)
        v.addWidget(h)
        v.addWidget(s)
        # Reuse quiet_row's leading-icon + divider chrome, then drop in the stacked
        # cell in place of its single-line label.
        row = quiet_row(self.colors, "", icon=icon_name, divider=divider)
        lay = row.layout()
        old = lay.itemAt(lay.count() - 1).widget()  # the empty primary label
        lay.replaceWidget(old, cell)
        old.deleteLater()
        lay.setStretch(lay.indexOf(cell), 1)  # keep the text cell taking the row width
        return row
