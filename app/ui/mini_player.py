"""Compact floating player shown while words play and the app is hidden.

A thin, frameless, always-on-top bar: the current word + translation on the
left with a highlight that slides left→right as each side is spoken. The
transport controls (prev / pause / next) stay collapsed behind a chevron so the
words get the full width, and slide out when the pointer is over the right side.
The bar can be dragged (body), resized horizontally (left/right edges), and a
plain click restores the main window.
"""
from PySide6.QtCore import (
    QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPushButton, QWidget,
)

from app.ui import icons
from app.ui.animations import fade_swap
from app.ui.widgets import ElidedLabel


class MiniPlayer(QWidget):
    """Floating mini playback bar with a sliding word/translation highlight."""

    prev_clicked = Signal()
    toggle_clicked = Signal()
    next_clicked = Signal()
    restore_requested = Signal()  # plain click on the bar body
    moved = Signal()              # drag/resize finished — host persists geometry

    WIDTH = 360       # initial width (resizable)
    HEIGHT = 34
    MIN_WIDTH = 240
    STUB = 46         # min px kept for the inactive phrase so it never vanishes
    RESIZE_MARGIN = 6
    HOT_ZONE = 130    # right-side reveal zone
    HILITE_ANIM_MS = 220
    DOCK_ANIM_MS = 160

    def __init__(self, colors, parent=None):
        super().__init__(parent, objectName="MiniPlayer")
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)  # hand-painted rounded bg
        self.setMouseTracking(True)
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(self.MIN_WIDTH)
        screen = QApplication.primaryScreen()
        self.setMaximumWidth(screen.availableGeometry().width() if screen else 4000)
        self.resize(self.WIDTH, self.HEIGHT)

        self._colors = colors
        self._paused = False
        self._part = 0            # 0 = word, 1 = translation
        self._w1 = ""             # current word / translation text
        self._w2 = ""
        self._press_pos = None    # global press point, for drag/click telling
        self._dragging = False
        self._expanded = False
        self._cursor_horiz = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, self.RESIZE_MARGIN, 0)
        lay.setSpacing(4)

        # ---- left: the word pair, with a highlight pill behind the labels ----
        self._words = QWidget(objectName="MiniWords")
        wl = QHBoxLayout(self._words)
        wl.setContentsMargins(8, 0, 0, 0)  # room for the pill's left inset
        wl.setSpacing(8)
        self._pill = QWidget(self._words)  # behind the labels, animated
        self._pill.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.word_label = ElidedLabel(min_width=0)
        self.word_label.setObjectName("MiniWord")
        self.sep_label = QLabel("›", objectName="MiniSep")
        self.trans_label = ElidedLabel(min_width=0)
        self.trans_label.setObjectName("MiniWord")
        wl.addWidget(self.word_label)
        wl.addWidget(self.sep_label)
        wl.addWidget(self.trans_label)
        wl.addStretch(1)
        lay.addWidget(self._words, 1)

        # ---- right: transport controls, collapsed behind a chevron handle ----
        self._controls = QWidget(objectName="MiniControls")
        cl = QHBoxLayout(self._controls)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.prev_btn = self._button("skip-back", "Previous word", self.prev_clicked)
        self.play_btn = self._button("pause", "Pause", self.toggle_clicked)
        self.next_btn = self._button("skip-forward", "Next word", self.next_clicked)
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            cl.addWidget(b)
        self._controls_w = self._controls.sizeHint().width()
        self._controls.setMaximumWidth(0)  # start collapsed
        lay.addWidget(self._controls, 0)

        self._handle = self._button("chevron-left", "Show controls", None)
        lay.addWidget(self._handle, 0)

        self._dock_anim = QPropertyAnimation(self._controls, b"maximumWidth", self)
        self._dock_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._dock_anim.setDuration(self.DOCK_ANIM_MS)
        self._hilite_anim = QPropertyAnimation(self._pill, b"geometry", self)
        self._hilite_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._hilite_anim.setDuration(self.HILITE_ANIM_MS)
        self._apply_pill_style()
        self._apply_label_styles()

    # ------------------------------------------------------------- controls

    def _button(self, name, tooltip, signal, size=14):
        btn = QPushButton()
        btn.setIcon(icons.icon(name, self._colors["text"], size))
        btn.setIconSize(QSize(size, size))
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        if signal is not None:
            btn.clicked.connect(signal.emit)
        return btn

    # -------------------------------------------------------------- public

    def set_pair(self, word, translation):
        """Show a new word/translation pair, dissolving the previous one."""
        fade_swap(self._words, 180)  # snapshot old pair, fade out over the new
        self._w1 = str(word or "")
        self._w2 = str(translation or "")
        self.word_label.set_full_text(self._w1)
        self.trans_label.set_full_text(self._w2)
        self.sep_label.setVisible(bool(self._w2.strip()))
        self._part = 0
        self._relayout_words()
        self._move_pill(animate=False)

    def set_active_part(self, slot):
        """Highlight the word (0) or translation (1). The active phrase gets
        layout priority, so as the highlight crosses the two phrases swap which
        is shown in full — slide when nothing reflows, crossfade when it does."""
        self._part = 1 if slot else 0
        reflowed = self._relayout_words()
        if reflowed:
            fade_swap(self._words, 170)  # mask the width reflow of long phrases
            self._move_pill(animate=False)
        else:
            self._move_pill(animate=True)

    def set_paused(self, paused):
        self._paused = paused
        self.play_btn.setIcon(icons.icon(
            "play" if paused else "pause", self._colors["text"], 14))
        self.play_btn.setToolTip("Resume" if paused else "Pause")

    def refresh_theme(self, colors):
        self._colors = colors
        self.prev_btn.setIcon(icons.icon("skip-back", colors["text"], 14))
        self.next_btn.setIcon(icons.icon("skip-forward", colors["text"], 14))
        self._handle.setIcon(icons.icon(
            "chevron-right" if self._expanded else "chevron-left", colors["text"], 14))
        self.set_paused(self._paused)
        self._apply_pill_style()
        self._apply_label_styles()
        self.update()

    # ---------------------------------------------------------- word layout

    def _relayout_words(self):
        """Fit both labels inside the available width, giving the active phrase
        priority. Returns True if either label's width changed."""
        fm = self.word_label.fontMetrics()
        pad = 6
        w1 = fm.horizontalAdvance(self._w1) + pad if self._w1 else 0
        w2 = fm.horizontalAdvance(self._w2) + pad if self._w2 else 0

        inner = self._words.contentsRect().width()
        spacing = self._words.layout().spacing()
        sep_w = (self.sep_label.sizeHint().width() + 2 * spacing) if self._w2 else 0
        avail = inner - sep_w - spacing  # spacing before the trailing stretch
        if avail <= 0:  # not laid out yet — showEvent/resizeEvent will retry
            return False

        if w1 + w2 <= avail:
            a1, a2 = w1, w2
        else:
            active, _other = (w1, w2) if self._part == 0 else (w2, w1)
            active_w = min(active, max(self.STUB, avail - self.STUB))
            other_w = max(0, avail - active_w)
            a1, a2 = (active_w, other_w) if self._part == 0 else (other_w, active_w)

        changed = (self.word_label.width() != a1) or (self.trans_label.width() != a2)
        self.word_label.setFixedWidth(a1)
        self.trans_label.setFixedWidth(a2)
        self._words.layout().activate()
        return changed

    # -------------------------------------------------------------- internals

    def _apply_pill_style(self):
        self._pill.setStyleSheet(
            f"background:{self._colors['accent_soft']};"
            f"border-radius:8px;")

    def _apply_label_styles(self):
        active = self._colors["text"]
        dim = self._colors["text_dim"]
        self.word_label.setStyleSheet(
            f"color:{active if self._part == 0 else dim};"
            f"background:transparent;font-weight:600;")
        self.trans_label.setStyleSheet(
            f"color:{active if self._part == 1 else dim};"
            f"background:transparent;font-weight:600;")
        self.sep_label.setStyleSheet(
            f"color:{dim};background:transparent;")

    def _active_label(self):
        return self.trans_label if self._part == 1 else self.word_label

    def _move_pill(self, animate):
        self._apply_label_styles()
        self._words.layout().activate()
        label = self._active_label()
        if label.width() <= 0:  # not laid out yet — defer to first real paint
            return
        target = label.geometry().adjusted(-6, -3, 6, 3)
        self._pill.lower()
        self._hilite_anim.stop()
        if animate and self.isVisible() and self._pill.width() > 0:
            self._hilite_anim.setStartValue(self._pill.geometry())
            self._hilite_anim.setEndValue(target)
            self._hilite_anim.start()
        else:
            self._pill.setGeometry(target)

    # ------------------------------------------------------- reveal controls

    def _reveal_zone(self):
        return max(self.HOT_ZONE, self._controls_w + self._handle.width() + 24)

    def _set_expanded(self, expanded):
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._dock_anim.stop()
        self._dock_anim.setStartValue(self._controls.maximumWidth())
        self._dock_anim.setEndValue(self._controls_w if expanded else 0)
        self._dock_anim.start()
        self._handle.setIcon(icons.icon(
            "chevron-right" if expanded else "chevron-left", self._colors["text"], 14))

    def place_default(self):
        """Bottom-right of the primary screen, with a margin."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - self.width() - 24, geo.bottom() - self.HEIGHT - 24)

    # ----------------------------------------------------------- paint / drag

    def paintEvent(self, event):
        # A translucent top-level window does not reliably honour its QSS
        # background, so paint the rounded pill by hand (as WordPopup does).
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor(self._colors["surface_alt"]))
        painter.setPen(QPen(QColor(self._colors["border"]), 1))
        painter.drawRoundedRect(rect, 9, 9)

    def showEvent(self, event):
        super().showEvent(event)
        self._controls.ensurePolished()  # natural width now reflects the QSS
        self._controls_w = self._controls.sizeHint().width()
        if not self._expanded:
            self._controls.setMaximumWidth(0)
        self._relayout_words()  # geometry is valid now
        self._move_pill(animate=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_words()
        self._move_pill(animate=False)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._set_expanded(False)
        self.moved.emit()  # persist geometry whenever the bar goes away

    def leaveEvent(self, event):
        self._set_expanded(False)
        if self._cursor_horiz:
            self.unsetCursor()
            self._cursor_horiz = False
        super().leaveEvent(event)

    def _edge_at(self, x):
        if x <= self.RESIZE_MARGIN:
            return Qt.LeftEdge
        if x >= self.width() - self.RESIZE_MARGIN:
            return Qt.RightEdge
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            edge = self._edge_at(event.position().toPoint().x())
            if edge is not None:
                handle = self.windowHandle()
                if handle is not None:
                    self._press_pos = None
                    handle.startSystemResize(edge)
                    return
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.globalPosition().toPoint()
        if self._press_pos is not None:  # dragging the bar
            if not self._dragging:
                if (pos - self._press_pos).manhattanLength() < QApplication.startDragDistance():
                    return
                self._dragging = True
            self.move(pos - self._drag_offset)
            return
        # hover (no button): edge-resize cursor + reveal controls on the right
        x = event.position().toPoint().x()
        horiz = self._edge_at(x) is not None
        if horiz != self._cursor_horiz:
            self.setCursor(Qt.SizeHorCursor) if horiz else self.unsetCursor()
            self._cursor_horiz = horiz
        self._set_expanded(x >= self.width() - self._reveal_zone())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_pos is not None:
            was_drag = self._dragging
            self._press_pos = None
            self._dragging = False
            if was_drag:
                self.moved.emit()
            else:
                self.restore_requested.emit()
        super().mouseReleaseEvent(event)
