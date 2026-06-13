"""Reusable, theme-reactive chart widgets drawn with QPainter.

No external charting dependency. Every widget reads ``theme.current_colors()``
inside ``paintEvent`` so it repaints in the correct palette after a theme
switch / crossfade (same approach as ``mini_player.py``). Each chart plays a
subtle grow-in animation the first time it is shown, and again whenever its
data changes while visible.

Widgets:
    Card            - rounded surface container with an optional title
    KpiCard         - headline number with animated count-up + accent strip
    DonutChart      - proportional ring with an inline legend
    BarListChart    - horizontal ranked bars with animated width
    AreaChart       - line + gradient area over time, with hover readout
    ActivityHeatmap - GitHub-style contribution calendar
    SegmentedControl- pill toggle (Day / Week / Month)
    FlowLayout      - wrapping layout so KPI cards reflow on resize
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import (
    QEasingCurve, QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
    QStyle, QStyleOption, QToolTip, QVBoxLayout, QWidget,
)

from app.ui import theme


# --------------------------------------------------------------------------- #
# small color helpers
# --------------------------------------------------------------------------- #
def _c(key: str) -> QColor:
    """A theme color by semantic key, resolved live (theme-reactive)."""
    return QColor(theme.current_colors().get(key, "#888888"))


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    """Linear blend of two colors, t in [0, 1]."""
    t = max(0.0, min(1.0, t))
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def _alpha(color: QColor, a: int) -> QColor:
    c = QColor(color)
    c.setAlpha(a)
    return c


# The default cyclic palette used for status slices / generic series. Keys are
# resolved against the live theme so everything recolors on a theme switch.
SERIES_KEYS = ["accent", "success", "warning", "danger", "text_dim", "accent_text"]


def status_color_key(label: str, index: int) -> str:
    """Pick a semantic color key for a status label, with sensible defaults
    for the well-known statuses and a cyclic fallback for the rest."""
    key = label.strip().lower()
    mapping = {
        "new": "text_dim",
        "to learn": "warning",
        "reviewing": "accent",
        "learning": "accent_text",
        "mastered": "success",
        "ignored": "border",
    }
    if key in mapping:
        return mapping[key]
    return SERIES_KEYS[index % len(SERIES_KEYS)]


# --------------------------------------------------------------------------- #
# animation mixin
# --------------------------------------------------------------------------- #
class _Animated:
    """Drives a 0->1 ``_progress`` value used by paintEvent. Mix into a
    QWidget subclass and call ``_init_anim`` in __init__."""

    def _init_anim(self, duration=820):
        self._progress = 0.0
        self._played = False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_progress)

    def _on_progress(self, v):
        self._progress = float(v)
        self.update()

    def _play(self):
        if not self._played:
            self._played = True
            self._anim.stop()
            self._anim.start()

    def _restart(self):
        """Re-run the intro animation (called when data changes while shown)."""
        self._played = True
        self._anim.stop()
        self._anim.start()

    def _on_data_changed(self):
        if self.isVisible():
            self._restart()
        else:
            # show the final state immediately if revealed without a showEvent,
            # but allow the intro to play when the page is first shown
            self._progress = 0.0
            self._played = False

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._play()


# --------------------------------------------------------------------------- #
# Card containers
# --------------------------------------------------------------------------- #
class Card(QFrame):
    """Rounded surface tile (background/border come from QSS #StatCard)."""

    def __init__(self, title=None, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(20, 18, 20, 18)
        self._v.setSpacing(14)
        self.title_label = None
        if title:
            self.title_label = QLabel(title, objectName="CardTitle")
            self._v.addWidget(self.title_label)

    def add(self, widget, stretch=0):
        self._v.addWidget(widget, stretch)

    def add_layout(self, layout):
        self._v.addLayout(layout)


class KpiCard(QFrame):
    """A single headline metric: small caption, big animated number, sub-line,
    and a colored accent strip on the left."""

    def __init__(self, caption, accent_key="accent", parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setMinimumWidth(190)
        self.setMinimumHeight(112)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._accent_key = accent_key
        self._target = 0
        self._suffix = ""

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._strip = QFrame(objectName="KpiStrip")
        self._strip.setFixedWidth(4)
        row.addWidget(self._strip)

        body = QVBoxLayout()
        body.setContentsMargins(18, 16, 18, 16)
        body.setSpacing(3)
        self.caption = QLabel(caption.upper(), objectName="KpiCaption")
        self.value = QLabel("0", objectName="KpiValue")
        self.value.setFont(_kpi_font())
        self.sub = QLabel("", objectName="KpiSub")
        self.sub.setVisible(False)
        body.addStretch(1)
        body.addWidget(self.caption)
        body.addWidget(self.value)
        body.addWidget(self.sub)
        body.addStretch(1)
        row.addLayout(body, 1)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(900)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._tick)
        self._played = False
        self.refresh_theme()

    def set_value(self, value, sub=None, suffix=""):
        self._target = int(value)
        self._suffix = suffix
        if sub:
            self.sub.setText(sub)
            self.sub.setVisible(True)
        else:
            self.sub.setVisible(False)
        if self.isVisible():
            self._animate_to(self._target)
        else:
            self._played = False
            self.value.setText(self._fmt(self._target))

    def _animate_to(self, target):
        self._anim.stop()
        self._anim.setStartValue(0)
        self._anim.setEndValue(int(target))
        self._anim.start()

    def _tick(self, v):
        self.value.setText(self._fmt(int(v)))

    def _fmt(self, n):
        return f"{n:,}{self._suffix}"

    def refresh_theme(self):
        accent = theme.current_colors().get(self._accent_key, "#4f8cff")
        self._strip.setStyleSheet(
            f"#KpiStrip{{background:{accent};border-top-left-radius:14px;"
            f"border-bottom-left-radius:14px;}}")

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if not self._played:
            self._played = True
            self._animate_to(self._target)


def _kpi_font():
    f = QFont()
    f.setPointSizeF(23)
    f.setWeight(QFont.DemiBold)
    return f


# --------------------------------------------------------------------------- #
# DonutChart
# --------------------------------------------------------------------------- #
class DonutChart(_Animated, QWidget):
    """Proportional ring with an inline legend on the right.

    ``set_data`` takes ``[(label, count, color_key), ...]``."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_anim()
        self._items = []
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, items):
        self._items = list(items or [])
        self._on_data_changed()
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(4, 4, -4, -4)

        total = sum(c for _, c, _ in self._items)
        # ring lives on the left; legend on the right (or below if narrow)
        side = min(rect.height(), rect.width() * 0.52)
        side = max(120.0, side)
        ring_rect = QRectF(rect.left(), rect.top() + (rect.height() - side) / 2,
                           side, side)

        text_dim = _c("text_dim")
        if total <= 0:
            pen = QPen(_alpha(text_dim, 60), side * 0.16)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            inset = side * 0.08
            p.drawEllipse(ring_rect.adjusted(inset, inset, -inset, -inset))
            p.setPen(text_dim)
            p.setFont(_label_font(10))
            p.drawText(ring_rect, Qt.AlignCenter, "No data yet")
            return

        thickness = side * 0.18
        arc_rect = ring_rect.adjusted(thickness / 2 + 2, thickness / 2 + 2,
                                      -thickness / 2 - 2, -thickness / 2 - 2)
        start = 90.0  # 12 o'clock
        swept_total = 360.0 * self._progress
        drawn = 0.0
        for label, count, key in self._items:
            span = 360.0 * count / total
            allow = max(0.0, min(span, swept_total - drawn))
            if allow <= 0:
                break
            pen = QPen(_c(key), thickness)
            pen.setCapStyle(Qt.FlatCap)
            p.setPen(pen)
            # Qt angles are counter-clockwise in 1/16°, so negate for clockwise
            p.drawArc(arc_rect, int(start * 16), int(-allow * 16))
            start -= span
            drawn += span

        # center readout
        p.setPen(_c("text"))
        big = _label_font(20)
        big.setWeight(QFont.DemiBold)
        p.setFont(big)
        center_top = QRectF(ring_rect.left(), ring_rect.center().y() - side * 0.18,
                            ring_rect.width(), side * 0.26)
        p.drawText(center_top, Qt.AlignCenter, f"{int(total * self._progress):,}")
        p.setPen(text_dim)
        p.setFont(_label_font(9))
        center_bot = QRectF(ring_rect.left(), ring_rect.center().y() + side * 0.02,
                            ring_rect.width(), side * 0.18)
        p.drawText(center_bot, Qt.AlignCenter, "words")

        # legend
        lx = ring_rect.right() + 26
        lw = rect.right() - lx
        if lw < 80:
            return
        rows = len(self._items)
        row_h = min(30.0, max(20.0, rect.height() / (rows + 1)))
        ly = rect.top() + (rect.height() - row_h * rows) / 2
        fm = QFontMetrics(_label_font(10))
        for label, count, key in self._items:
            dot = QRectF(lx, ly + row_h / 2 - 5, 10, 10)
            p.setPen(Qt.NoPen)
            p.setBrush(_c(key))
            p.drawRoundedRect(dot, 3, 3)
            pct = 100.0 * count / total
            p.setPen(_c("text"))
            p.setFont(_label_font(10))
            name = fm.elidedText(label, Qt.ElideRight, int(lw - 96))
            p.drawText(QRectF(lx + 18, ly, lw - 96, row_h),
                       Qt.AlignVCenter | Qt.AlignLeft, name)
            p.setPen(text_dim)
            p.drawText(QRectF(lx + lw - 86, ly, 86, row_h),
                       Qt.AlignVCenter | Qt.AlignRight, f"{count:,}  ·  {pct:.0f}%")
            ly += row_h


# --------------------------------------------------------------------------- #
# BarListChart
# --------------------------------------------------------------------------- #
class BarListChart(_Animated, QWidget):
    """Ranked horizontal bars. ``set_data`` takes ``[(label, count), ...]``."""

    def __init__(self, accent_key="accent", parent=None):
        super().__init__(parent)
        self._init_anim()
        self._items = []
        self._accent_key = accent_key
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, items):
        self._items = list(items or [])
        n = max(1, len(self._items))
        self.setMinimumHeight(n * 40 + 8)
        self._on_data_changed()
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        if not self._items:
            p.setPen(_c("text_dim"))
            p.setFont(_label_font(10))
            p.drawText(rect, Qt.AlignCenter, "No data yet")
            return

        peak = max(c for _, c in self._items) or 1
        row_h = min(44.0, rect.height() / len(self._items))
        bar_h = min(22.0, row_h - 16)
        label_font = _label_font(10)
        fm = QFontMetrics(label_font)
        count_w = 56
        track = _alpha(_c("text_dim"), 28)
        accent = _c(self._accent_key)
        y = rect.top() + (rect.height() - row_h * len(self._items)) / 2

        for label, count in self._items:
            # label line
            p.setPen(_c("text"))
            p.setFont(label_font)
            name = fm.elidedText(str(label), Qt.ElideRight, int(rect.width() - count_w - 8))
            p.drawText(QRectF(rect.left(), y, rect.width() - count_w, row_h - bar_h - 2),
                       Qt.AlignBottom | Qt.AlignLeft, name)
            p.setPen(_c("text_dim"))
            p.drawText(QRectF(rect.right() - count_w, y, count_w, row_h - bar_h - 2),
                       Qt.AlignBottom | Qt.AlignRight, f"{count:,}")
            # bar
            by = y + row_h - bar_h
            full_w = rect.width()
            track_rect = QRectF(rect.left(), by, full_w, bar_h)
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(track_rect, bar_h / 2, bar_h / 2)
            frac = (count / peak) * self._progress
            bw = max(bar_h, full_w * frac)
            grad = QLinearGradient(rect.left(), 0, rect.left() + bw, 0)
            grad.setColorAt(0, _mix(accent, _c("bg"), 0.15))
            grad.setColorAt(1, accent)
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(rect.left(), by, bw, bar_h), bar_h / 2, bar_h / 2)
            y += row_h


# --------------------------------------------------------------------------- #
# AreaChart
# --------------------------------------------------------------------------- #
class AreaChart(_Animated, QWidget):
    """Line + gradient area of (date, count) points with a hover readout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_anim()
        self._points = []
        self._hover = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, points):
        self._points = list(points or [])
        self._hover = -1
        self._on_data_changed()
        self.update()

    def _plot_rect(self):
        return self.rect().adjusted(46, 14, -12, -28)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        area = self._plot_rect()
        text_dim = _c("text_dim")

        if len(self._points) < 2:
            p.setPen(text_dim)
            p.setFont(_label_font(10))
            p.drawText(self.rect(), Qt.AlignCenter, "Not enough activity yet")
            return

        counts = [c for _, c in self._points]
        peak = max(counts) or 1
        n = len(self._points)

        # gridlines + y labels (0, mid, peak)
        p.setFont(_label_font(8))
        for frac in (0.0, 0.5, 1.0):
            val = round(peak * frac)
            gy = area.bottom() - area.height() * frac
            p.setPen(QPen(_alpha(text_dim, 45), 1, Qt.DashLine))
            p.drawLine(QPointF(area.left(), gy), QPointF(area.right(), gy))
            p.setPen(text_dim)
            p.drawText(QRectF(0, gy - 9, area.left() - 6, 18),
                       Qt.AlignVCenter | Qt.AlignRight, f"{val:,}")

        def pt(i):
            x = area.left() + area.width() * (i / (n - 1))
            y = area.bottom() - area.height() * (counts[i] / peak)
            return QPointF(x, y)

        accent = _c("accent")
        reveal = max(1, int(round((n - 1) * self._progress)))

        # area fill
        path = QPainterPath()
        path.moveTo(area.left(), area.bottom())
        for i in range(reveal + 1):
            path.lineTo(pt(i))
        path.lineTo(pt(reveal).x(), area.bottom())
        path.closeSubpath()
        grad = QLinearGradient(0, area.top(), 0, area.bottom())
        grad.setColorAt(0, _alpha(accent, 90))
        grad.setColorAt(1, _alpha(accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawPath(path)

        # line
        line = QPainterPath()
        line.moveTo(pt(0))
        for i in range(1, reveal + 1):
            line.lineTo(pt(i))
        p.setPen(QPen(accent, 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)

        # x labels (first / mid / last), kept inside the widget bounds
        p.setPen(text_dim)
        p.setFont(_label_font(8))
        for i, al in ((0, Qt.AlignLeft), (n // 2, Qt.AlignHCenter), (n - 1, Qt.AlignRight)):
            d = self._points[i][0]
            label = d.strftime("%d %b") if hasattr(d, "strftime") else str(d)
            x = area.left() + area.width() * (i / (n - 1))
            if al == Qt.AlignLeft:
                box = QRectF(x, area.bottom() + 6, 80, 16)
            elif al == Qt.AlignRight:
                box = QRectF(x - 80, area.bottom() + 6, 80, 16)
            else:
                box = QRectF(x - 40, area.bottom() + 6, 80, 16)
            p.drawText(box, al | Qt.AlignTop, label)

        # hover marker + tooltip
        if 0 <= self._hover < n and self._progress >= 1.0:
            hp = pt(self._hover)
            p.setPen(QPen(_alpha(accent, 120), 1, Qt.DashLine))
            p.drawLine(QPointF(hp.x(), area.top()), QPointF(hp.x(), area.bottom()))
            p.setPen(Qt.NoPen)
            p.setBrush(_c("surface"))
            p.drawEllipse(hp, 5, 5)
            p.setBrush(accent)
            p.drawEllipse(hp, 3.2, 3.2)

    def mouseMoveEvent(self, event):  # noqa: N802
        n = len(self._points)
        area = self._plot_rect()
        if n < 2 or not area.contains(event.position().toPoint()):
            if self._hover != -1:
                self._hover = -1
                self.update()
            return
        rel = (event.position().x() - area.left()) / max(1.0, area.width())
        i = int(round(rel * (n - 1)))
        i = max(0, min(n - 1, i))
        if i != self._hover:
            self._hover = i
            self.update()
        d, c = self._points[i]
        label = d.strftime("%d %b %Y") if hasattr(d, "strftime") else str(d)
        QToolTip.showText(event.globalPosition().toPoint(),
                          f"{c:,} word{'s' if c != 1 else ''}\n{label}", self)

    def leaveEvent(self, event):  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.update()


# --------------------------------------------------------------------------- #
# ActivityHeatmap
# --------------------------------------------------------------------------- #
class ActivityHeatmap(_Animated, QWidget):
    """GitHub-style contribution calendar. ``set_data`` takes the dict produced
    by ``app.core.stats.heatmap_weeks``."""

    CELL = 15
    GAP = 4
    TOP = 18      # space for month labels
    LEFT = 32     # space for weekday labels

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_anim(duration=900)
        self._grid = {"columns": [], "max": 0, "month_labels": []}
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_data(self, grid):
        self._grid = grid or {"columns": [], "max": 0, "month_labels": []}
        cols = len(self._grid.get("columns", []))
        self.setMinimumHeight(self.TOP + 7 * self.CELL + 6 * self.GAP + 6)
        self.setMinimumWidth(self.LEFT + cols * (self.CELL + self.GAP))
        self._on_data_changed()
        self.update()

    def _cell_color(self, count, peak):
        base = _c("surface_alt")
        if count <= 0 or peak <= 0:
            return _mix(base, _c("bg"), 0.3)
        # 4 intensity buckets blended toward the accent
        t = count / peak
        bucket = 0.3 + 0.7 * min(1.0, t)
        return _mix(_alpha(_c("accent"), 80), _c("accent"), bucket)

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cols = self._grid.get("columns", [])
        peak = self._grid.get("max", 0)
        if not cols:
            p.setPen(_c("text_dim"))
            p.setFont(_label_font(10))
            p.drawText(self.rect(), Qt.AlignCenter, "No activity yet")
            return

        step = self.CELL + self.GAP
        reveal = self._progress * len(cols)

        # weekday labels (Mon / Wed / Fri)
        p.setFont(_label_font(8))
        p.setPen(_c("text_dim"))
        for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            y = self.TOP + row * step
            p.drawText(QRectF(0, y, self.LEFT - 6, self.CELL),
                       Qt.AlignVCenter | Qt.AlignRight, name)

        # month labels
        for col, name in self._grid.get("month_labels", []):
            x = self.LEFT + col * step
            p.drawText(QRectF(x, 0, 40, self.TOP - 2),
                       Qt.AlignVCenter | Qt.AlignLeft, name)

        for ci, week in enumerate(cols):
            if ci > reveal:
                break
            x = self.LEFT + ci * step
            for ri, cell in enumerate(week):
                if cell is None:
                    continue
                _, count = cell
                y = self.TOP + ri * step
                p.setPen(Qt.NoPen)
                p.setBrush(self._cell_color(count, peak))
                p.drawRoundedRect(QRectF(x, y, self.CELL, self.CELL), 3, 3)

    def mouseMoveEvent(self, event):  # noqa: N802
        cols = self._grid.get("columns", [])
        if not cols:
            return
        step = self.CELL + self.GAP
        px = event.position().x() - self.LEFT
        py = event.position().y() - self.TOP
        ci = int(px // step)
        ri = int(py // step)
        if 0 <= ci < len(cols) and 0 <= ri < 7:
            cell = cols[ci][ri]
            if cell is not None:
                d, count = cell
                label = d.strftime("%d %b %Y") if hasattr(d, "strftime") else str(d)
                QToolTip.showText(
                    event.globalPosition().toPoint(),
                    f"{count:,} word{'s' if count != 1 else ''}  ·  {label}", self)
                return
        QToolTip.hideText()


# --------------------------------------------------------------------------- #
# SegmentedControl
# --------------------------------------------------------------------------- #
class SegmentedControl(QWidget):
    """A small pill toggle. Emits ``changed(value)`` with the option text."""

    changed = Signal(str)

    def __init__(self, options, current=None, parent=None):
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._buttons = []
        for opt in options:
            b = QPushButton(opt, objectName="SegItem")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, o=opt: self._select(o))
            lay.addWidget(b)
            self._buttons.append(b)
        self._select(current or options[0], emit=False)

    def value(self):
        for b in self._buttons:
            if b.isChecked():
                return b.text()
        return None

    def _select(self, opt, emit=True):
        for b in self._buttons:
            b.setChecked(b.text() == opt)
        if emit:
            self.changed.emit(opt)


# --------------------------------------------------------------------------- #
# fonts
# --------------------------------------------------------------------------- #
def _label_font(point_size):
    f = QFont()
    f.setPointSizeF(point_size)
    return f


# --------------------------------------------------------------------------- #
# FlowLayout (standard Qt example, lightly adapted) — wraps items to width
# --------------------------------------------------------------------------- #
class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, h_spacing=14, v_spacing=14):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h = h_spacing
        self._v = v_spacing
        self._items = []

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def horizontalSpacing(self):  # noqa: N802
        return self._h

    def verticalSpacing(self):  # noqa: N802
        return self._v

    def count(self):
        return len(self._items)

    def itemAt(self, index):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        right = rect.right() - m.right()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._h
            if next_x - self._h > right and line_height > 0:
                x = rect.x() + m.left()
                y = y + line_height + self._v
                next_x = x + hint.width() + self._h
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + m.bottom()
