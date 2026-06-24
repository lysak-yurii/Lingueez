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
from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QRectF,
                            QSequentialAnimationGroup, Qt)
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from app.i18n import tr
from app.ui.dialogs.base import FramelessDialog, quiet_frame, quiet_row
from app.version import APP_NAME, PRIVACY_URL, TERMS_URL

# Resting display size of the app icon (px) and the box it lives in. The box has
# generous vertical headroom so the cover's drop and the idle float never clip.
ICON_BASE = 62
ICON_BOX = 104
# The logo is a book: a blue top block (cover + "L·Z") over a yellow bottom block
# (base), separated by a fully transparent gap. Cutting the single source at this
# height ratio yields two layers that reconstruct the mark exactly when joined.
SPLIT_RATIO = 0.71


class AnimatedAppIcon(QWidget):
    """The app icon as two custom-painted layers — the blue cover and the yellow
    base — so they can animate independently of the layout. On show, the base is
    already in place and the cover drops down onto it like a book closing, with a
    soft landing damp; then the whole mark settles into a slow, barely-there
    float. Elegant and alive without the 'loading spinner' feel of a hard pulse."""

    DROP = 18.0     # how far above its resting seam the cover starts (display px)
    HOLD_MS = 520   # pre-roll: hold the open-book pose so the close isn't missed

    def __init__(self, pixmap, dpr, parent=None):
        super().__init__(parent)
        self._dpr = dpr
        # Slice the source at the transparent gap, then pre-scale each layer ONCE
        # to the size we'll draw (× dpr for HiDPI crispness). Per frame we only
        # blit these small pixmaps instead of re-sampling the 549px source every
        # tick — that resampling was the cause of the dropped frames / low-fps look.
        w, h = pixmap.width(), pixmap.height()
        split = round(h * SPLIT_RATIO)
        self._split_ratio = (split / h) if h else SPLIT_RATIO
        maxpx = max(1, int(ICON_BASE * dpr))
        self._top = pixmap.copy(0, 0, w, split).scaledToWidth(
            maxpx, Qt.SmoothTransformation)
        self._bottom = pixmap.copy(0, split, w, h - split).scaledToWidth(
            maxpx, Qt.SmoothTransformation)

        self._gap_top = self.DROP  # cover's drop, animated DROP -> 0 on entrance
        self._offset = 0.0         # whole-mark vertical px: landing damp, then float
        self._opacity = 0.0        # animated 0 -> 1 on entrance
        self.setFixedSize(ICON_BOX, ICON_BOX)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    # --- animatable Qt properties ---------------------------------------
    def _get_gap_top(self):
        return self._gap_top

    def _set_gap_top(self, v):
        self._gap_top = v
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

    gapTop = Property(float, _get_gap_top, _set_gap_top)
    offsetY = Property(float, _get_offset, _set_offset)
    opacityF = Property(float, _get_opacity, _set_opacity)

    # --- paint -----------------------------------------------------------
    def paintEvent(self, event):
        if self._top.isNull() or self._bottom.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(max(0.0, min(1.0, self._opacity)))
        size = float(ICON_BASE)
        top_h = size * self._split_ratio
        bot_h = size * (1.0 - self._split_ratio)
        x = (self.width() - size) / 2.0
        base_y = (self.height() - size) / 2.0 + self._offset
        # Cover (top) carries the drop offset; base (bottom) stays put. At
        # gap_top == 0 the two layers are contiguous and form the whole mark.
        # QRectF keeps every position subpixel-smooth.
        p.drawPixmap(QRectF(x, base_y - self._gap_top, size, top_h),
                     self._top, QRectF(self._top.rect()))
        p.drawPixmap(QRectF(x, base_y + top_h, size, bot_h),
                     self._bottom, QRectF(self._bottom.rect()))

    # --- choreography ----------------------------------------------------
    def play(self):
        """Fade the open book in, hold a beat so the user's gaze can land, then
        close the cover onto the base and hand off to the landing damp.

        The pre-roll hold is the key: starting the close on the very first paint
        means it finishes before the eye reaches the freshly-shown dialog. We show
        the 'before' pose (cover raised) during the hold so the close is legible."""
        self._gap_top = self.DROP
        self._offset = 0.0
        self._opacity = 0.0

        # Fade the raised-cover pose in immediately, parallel to the hold below.
        fade = QPropertyAnimation(self, b"opacityF", self)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setDuration(340)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        fade.start()
        self._fade = fade  # keep a ref so it isn't GC'd

        # Hold the open book, then close it — unhurried so it reads clearly.
        seq = QSequentialAnimationGroup(self)
        seq.addPause(self.HOLD_MS)
        drop = QPropertyAnimation(self, b"gapTop", self)
        drop.setStartValue(self.DROP)
        drop.setEndValue(0.0)
        drop.setDuration(780)
        drop.setEasingCurve(QEasingCurve.OutCubic)
        seq.addAnimation(drop)
        seq.finished.connect(self._land)
        seq.start()
        self._entrance = seq  # keep a ref so it isn't GC'd

    def _land(self):
        """A 2.5px micro-bounce of the whole mark — the soft landing on contact."""
        land = QPropertyAnimation(self, b"offsetY", self)
        land.setDuration(320)
        land.setKeyValueAt(0.0, 0.0)
        land.setKeyValueAt(0.5, 2.5)
        land.setKeyValueAt(1.0, 0.0)
        land.setEasingCurve(QEasingCurve.OutCubic)
        land.finished.connect(self._start_float)
        land.start()
        self._landing = land

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

    def stop_animations(self):
        """Stop every animation so the looping float doesn't keep ticking on Qt's
        global animation timer after the dialog closes. Left running it dangles once
        the dialog is torn down and crashes QUnifiedTimer (a use-after-free);
        stopping unregisters them synchronously, making teardown safe."""
        for name in ("_fade", "_entrance", "_landing", "_float"):
            anim = getattr(self, name, None)
            if anim is not None:
                anim.stop()


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
            tr("Your data is yours — sign in only to sync it.") + "<br>"
            + tr('<a href="{privacy}">Privacy Policy</a> · '
                 '<a href="{terms}">Terms</a>').format(
                     privacy=PRIVACY_URL, terms=TERMS_URL))
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        note.setAlignment(Qt.AlignCenter)
        from app.ui.legal_links import open_legal
        note.linkActivated.connect(open_legal)
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

    def done(self, result):
        # Halt the icon's animations the instant the user dismisses this dialog —
        # before the caller (MainWindow._show_welcome) immediately opens the sign-in
        # dialog. The icon's resting float loops forever; left running it would keep
        # ticking on the global animation timer and dangle once this dialog is torn
        # down, crashing the sign-in dialog's event loop (QUnifiedTimer use-after-free).
        if getattr(self, "_icon", None) is not None:
            self._icon.stop_animations()
        super().done(result)

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
