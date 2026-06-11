"""Custom item delegates — colored status pills.

paint() runs for every visible status cell on every repaint (scrolling,
resizing, hover), so pills are rendered once per (status, theme, font)
into a pixmap cache and blitted afterwards.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

# status -> (background rgba, text color)
PILL_COLORS = {
    "new":       ("#2f6fed", 38),
    "to learn":  ("#9a6ff0", 38),
    "learning":  ("#d29922", 38),
    "reviewing": ("#d29922", 38),
    "mastered":  ("#2da44e", 38),
    "ignored":   ("#8b98a5", 30),
}
DEFAULT_PILL = ("#8b98a5", 30)


class StatusPillDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache = {}  # (text, dark, font key, dpr) -> QPixmap

    def paint(self, painter, option, index):
        text = str(index.data() or "")
        if not text:
            return super().paint(painter, option, index)

        # cell background: selection > favorite tint > alternating row.
        # Painted directly (not via initStyleOption/drawControl) — the
        # style-sheet path is far too slow for a per-cell hot loop.
        rect = option.rect
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
        else:
            row_bg = index.data(Qt.BackgroundRole)
            if row_bg is not None:
                painter.fillRect(rect, row_bg)
            elif option.features & QStyleOptionViewItem.Alternate:
                painter.fillRect(rect, option.palette.alternateBase())

        dark = option.palette.window().color().lightness() < 128
        dpr = painter.device().devicePixelRatioF() if painter.device() else 1.0
        key = (text, dark, option.font.key(), dpr)
        pm = self._cache.get(key)
        if pm is None:
            pm = self._render_pill(text, dark, option.font, dpr)
            self._cache[key] = pm

        w = pm.width() / dpr
        h = pm.height() / dpr
        if w > rect.width() - 10:
            painter.save()
            painter.setClipRect(rect.adjusted(0, 0, -4, 0))
            painter.drawPixmap(
                QPointF(rect.x() + 6, rect.y() + (rect.height() - h) / 2), pm)
            painter.restore()
        else:
            painter.drawPixmap(
                QPointF(rect.x() + 6, rect.y() + (rect.height() - h) / 2), pm)

    @staticmethod
    def _render_pill(text, dark, base_font, dpr):
        font = QFont(base_font)
        font.setPointSizeF(max(7.0, base_font.pointSizeF() - 1))
        font.setWeight(QFont.DemiBold)
        metrics = QFontMetrics(font)
        w = metrics.horizontalAdvance(text) + 20
        h = metrics.height() + 6

        base_hex, alpha = PILL_COLORS.get(text.strip().lower(), DEFAULT_PILL)
        bg = QColor(base_hex)
        # readable pill text on both themes
        if dark:
            bg.setAlpha(alpha + 25)
            fg = QColor(base_hex).lighter(135)
        else:
            bg.setAlpha(alpha + 42)
            fg = QColor(base_hex).darker(165)

        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.setPen(fg)
        p.setFont(font)
        p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, text)
        p.end()
        return pm
