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

"""Custom item delegates — colored status pills.

paint() runs for every visible status cell on every repaint (scrolling,
resizing, hover), so pills are rendered once per (status, theme, font)
into a pixmap cache and blitted afterwards.
"""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPixmap
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionViewItem

from app.i18n import tr
from app.ui.word_model import ROLE_FAV_STRIPE

# status -> (base color, background alpha); desaturated hues so the
# pills read as quiet metadata instead of pulling the eye off the words
PILL_COLORS = {
    "new":       ("#5e7db4", 26),
    "to learn":  ("#8d7cc0", 26),
    "learning":  ("#b2924f", 26),
    "reviewing": ("#b2924f", 26),
    "mastered":  ("#55966c", 26),
    "ignored":   ("#8b98a5", 22),
}
DEFAULT_PILL = ("#8b98a5", 22)


class RowTintDelegate(QStyledItemDelegate):
    """Default table delegate that honors the model's BackgroundRole.

    The app stylesheet defines QTableView::item rules, which makes the
    stylesheet style skip model background brushes entirely — the
    now-playing and queued row tints would never show. Filling the rect
    before the styled paint restores them. Favorite rows are marked
    afterwards with a thin accent bar at their left edge."""

    def paint(self, painter, option, index):
        if not (option.state & QStyle.State_Selected):
            bg = index.data(Qt.BackgroundRole)
            if bg is not None:
                painter.fillRect(option.rect, bg)
        super().paint(painter, option, index)
        # Favorite marker: a thin accent bar at the row's left edge, drawn
        # over selection so the cue survives a selected row.
        stripe = index.data(ROLE_FAV_STRIPE)
        if stripe is not None:
            r = option.rect
            inset = max(3, r.height() // 5)  # keep clear of the row borders
            painter.fillRect(r.left(), r.top() + inset, 3,
                             r.height() - 2 * inset, stripe)


class StatusPillDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache = {}  # (text, dark, font key, dpr) -> QPixmap

    def sizeHint(self, option, index):
        # So resizeColumnToContents() can fit the (localized) pill, not just
        # the raw status text. Mirrors _render_pill's width plus the 6px left
        # paint offset and the 10px clip margin used in paint().
        base = super().sizeHint(option, index)
        text = str(index.data() or "")
        if not text:
            return base
        font = QFont(option.font)
        font.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        font.setWeight(QFont.Normal)
        metrics = QFontMetrics(font)
        pill_w = metrics.horizontalAdvance(tr(text)) + 20
        return QSize(pill_w + 16, max(base.height(), metrics.height() + 12))

    def paint(self, painter, option, index):
        text = str(index.data() or "")  # canonical (English) status — drives the color
        if not text:
            return super().paint(painter, option, index)
        label = tr(text)  # localized text actually shown in the pill

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
            pm = self._render_pill(text, label, dark, option.font, dpr)
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
    def _render_pill(text, label, dark, base_font, dpr):
        font = QFont(base_font)
        font.setPointSizeF(max(7.0, base_font.pointSizeF() - 1))
        font.setWeight(QFont.Normal)
        metrics = QFontMetrics(font)
        w = metrics.horizontalAdvance(label) + 20
        h = metrics.height() + 6

        base_hex, alpha = PILL_COLORS.get(text.strip().lower(), DEFAULT_PILL)
        bg = QColor(base_hex)
        # readable pill text on both themes
        if dark:
            bg.setAlpha(alpha + 16)
            fg = QColor(base_hex).lighter(130)
        else:
            bg.setAlpha(alpha + 30)
            fg = QColor(base_hex).darker(150)

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
        p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, label)
        p.end()
        return pm
