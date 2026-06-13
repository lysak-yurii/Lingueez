"""Statistics dashboard page.

Assembles the QPainter chart widgets from ``app.ui.charts`` into a scrollable,
modern dashboard. Data comes from ``app.core.stats.compute_stats`` over the
words DataFrame the main window already holds, plus tag/definition counts from
``app.core.db``. The page is dumb: it computes once in :meth:`set_data` and
pushes values into the widgets.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from app.core import stats as stats_mod
from app.ui import charts
from app.ui import theme


class _ProgressRow(QWidget):
    """A slim labelled progress bar (used for definition completion)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filled = 0
        self._total = 0
        self.setMinimumHeight(44)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_values(self, filled, total):
        self._filled = int(filled)
        self._total = int(total)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        c = theme.current_colors
        text = charts._c("text")
        dim = charts._c("text_dim")
        accent = charts._c("accent")

        pct = (self._filled / self._total * 100.0) if self._total else 0.0
        p.setPen(text)
        f = QFont(); f.setPointSizeF(10.5); f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.drawText(QRectF(rect.left(), rect.top(), rect.width(), 20),
                   Qt.AlignVCenter | Qt.AlignLeft, "Definitions written")
        p.setPen(dim)
        p.setFont(charts._label_font(10))
        p.drawText(QRectF(rect.left(), rect.top(), rect.width(), 20),
                   Qt.AlignVCenter | Qt.AlignRight,
                   f"{self._filled:,} / {self._total:,}  ·  {pct:.0f}%")

        by = rect.top() + 28
        bar_h = 9.0
        p.setPen(Qt.NoPen)
        p.setBrush(charts._alpha(dim, 28))
        p.drawRoundedRect(QRectF(rect.left(), by, rect.width(), bar_h),
                          bar_h / 2, bar_h / 2)
        if self._total:
            w = max(bar_h, rect.width() * (self._filled / self._total))
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(rect.left(), by, w, bar_h),
                              bar_h / 2, bar_h / 2)

    def refresh_theme(self):
        self.update()


class StatsPage(QWidget):
    """Scrollable statistics dashboard added as a third page in the stack."""

    GRANULARITY = {"Day": ("day", 120), "Week": ("week", 52), "Month": ("month", 0)}

    def __init__(self, db_adapter, colors, parent=None):
        super().__init__(parent)
        self.db_adapter = db_adapter
        self._colors = colors
        self._stats = stats_mod.DashboardStats()
        self._kpis = []
        self._charts = []
        self._build_ui()

    # ----------------------------------------------------------------- UI
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(objectName="StatsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget(objectName="StatsContent")
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 30)
        root.setSpacing(14)
        scroll.setWidget(content)

        # ---- Overview: KPI cards -------------------------------------
        root.addWidget(self._section("Overview"))
        kpi_host = QWidget()
        flow = charts.FlowLayout(kpi_host, h_spacing=14, v_spacing=14)
        self.kpi_total = charts.KpiCard("Total words", "accent")
        self.kpi_mastered = charts.KpiCard("Mastered", "success")
        self.kpi_progress = charts.KpiCard("In progress", "accent_text")
        self.kpi_languages = charts.KpiCard("Languages", "warning")
        self.kpi_streak = charts.KpiCard("Current streak", "danger")
        self.kpi_week = charts.KpiCard("Added this week", "accent")
        self._kpis = [self.kpi_total, self.kpi_mastered, self.kpi_progress,
                      self.kpi_languages, self.kpi_streak, self.kpi_week]
        for k in self._kpis:
            flow.addWidget(k)
        root.addWidget(kpi_host)

        # ---- Learning status: donut ----------------------------------
        root.addSpacing(6)
        root.addWidget(self._section("Learning status"))
        donut_card = charts.Card("Status distribution")
        self.donut = charts.DonutChart()
        donut_card.add(self.donut, 1)
        root.addWidget(donut_card)
        self._charts.append(self.donut)

        # ---- Activity: area + heatmap --------------------------------
        root.addSpacing(6)
        root.addWidget(self._section("Activity"))

        area_card = charts.Card()
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Words added over time", objectName="CardTitle")
        self.granularity = charts.SegmentedControl(["Day", "Week", "Month"], "Week")
        self.granularity.changed.connect(self._on_granularity)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.granularity)
        area_card.add_layout(header)
        self.area = charts.AreaChart()
        area_card.add(self.area, 1)
        root.addWidget(area_card)
        self._charts.append(self.area)

        heat_card = charts.Card("Activity calendar")
        self.heatmap = charts.ActivityHeatmap()
        heat_wrap = QHBoxLayout()
        heat_wrap.setContentsMargins(0, 0, 0, 0)
        heat_wrap.addWidget(self.heatmap)
        heat_wrap.addStretch(1)
        heat_card.add_layout(heat_wrap)
        root.addWidget(heat_card)
        self._charts.append(self.heatmap)

        # ---- Review activity: KPIs + reviews over time + most reviewed ----
        root.addSpacing(6)
        root.addWidget(self._section("Review activity"))
        rev_kpi_host = QWidget()
        rev_flow = charts.FlowLayout(rev_kpi_host, h_spacing=14, v_spacing=14)
        self.kpi_rev_week = charts.KpiCard("Reviewed this week", "success")
        self.kpi_rev_total = charts.KpiCard("Total reviews", "accent")
        self.kpi_rev_streak = charts.KpiCard("Review streak", "danger")
        for k in (self.kpi_rev_week, self.kpi_rev_total, self.kpi_rev_streak):
            rev_flow.addWidget(k)
            self._kpis.append(k)
        root.addWidget(rev_kpi_host)

        rev_area_card = charts.Card()
        rev_header = QHBoxLayout()
        rev_header.setContentsMargins(0, 0, 0, 0)
        rev_title = QLabel("Reviews over time", objectName="CardTitle")
        self.rev_granularity = charts.SegmentedControl(["Day", "Week", "Month"], "Week")
        self.rev_granularity.changed.connect(self._on_review_granularity)
        rev_header.addWidget(rev_title)
        rev_header.addStretch(1)
        rev_header.addWidget(self.rev_granularity)
        rev_area_card.add_layout(rev_header)
        self.rev_area = charts.AreaChart()
        rev_area_card.add(self.rev_area, 1)
        root.addWidget(rev_area_card)
        self._charts.append(self.rev_area)

        rev_heat_card = charts.Card("Review calendar")
        self.rev_heatmap = charts.ActivityHeatmap()
        rev_heat_wrap = QHBoxLayout()
        rev_heat_wrap.setContentsMargins(0, 0, 0, 0)
        rev_heat_wrap.addWidget(self.rev_heatmap)
        rev_heat_wrap.addStretch(1)
        rev_heat_card.add_layout(rev_heat_wrap)
        root.addWidget(rev_heat_card)
        self._charts.append(self.rev_heatmap)

        rev_bars_card = charts.Card("Most reviewed words")
        self.rev_bars = charts.BarListChart("accent_text")
        rev_bars_card.add(self.rev_bars, 1)
        root.addWidget(rev_bars_card)
        self._charts.append(self.rev_bars)

        # ---- Breakdown: language pairs + tags + definitions ----------
        root.addSpacing(6)
        root.addWidget(self._section("Breakdown"))
        cols = QHBoxLayout()
        cols.setSpacing(14)
        lang_card = charts.Card("Top language pairs")
        self.lang_bars = charts.BarListChart("accent")
        lang_card.add(self.lang_bars, 1)
        tag_card = charts.Card("Top tags")
        self.tag_bars = charts.BarListChart("accent_text")
        tag_card.add(self.tag_bars, 1)
        cols.addWidget(lang_card, 1)
        cols.addWidget(tag_card, 1)
        root.addLayout(cols)
        self._charts += [self.lang_bars, self.tag_bars]

        defs_card = charts.Card()
        self.defs = _ProgressRow()
        defs_card.add(self.defs)
        root.addWidget(defs_card)

        root.addStretch(1)

    def _section(self, text):
        lbl = QLabel(text.upper(), objectName="SectionHeader")
        return lbl

    # --------------------------------------------------------------- data
    def set_data(self, df, tag_counts=None, definition_counts=None, reviews=None):
        """Compute stats from the words DataFrame and push to every widget.

        Exception-guarded: on failure the dashboard shows empty states rather
        than breaking the app."""
        try:
            self._stats = stats_mod.compute_stats(
                df, tag_counts, definition_counts, reviews)
        except Exception:
            logging.exception("StatsPage.set_data: compute failed")
            self._stats = stats_mod.DashboardStats()
        self._apply(self._stats)

    def _apply(self, s: stats_mod.DashboardStats):
        total = s.total_words
        self.kpi_total.set_value(total)
        self.kpi_mastered.set_value(
            s.mastered, sub=f"{s.mastered_pct:.0f}% of all words" if total else "—")
        self.kpi_progress.set_value(s.in_progress, sub="actively learning")
        self.kpi_languages.set_value(
            s.language_count,
            sub=f"{len(s.top_language_pairs)} pairs" if s.top_language_pairs else "—")
        self.kpi_streak.set_value(
            s.current_streak, suffix="d",
            sub=f"best {s.longest_streak}d" if s.longest_streak else "—")
        self.kpi_week.set_value(s.added_this_week, sub=f"{s.added_today} today")

        donut_items = []
        for i, (label, count) in enumerate(s.status_counts.items()):
            donut_items.append((label, count, charts.status_color_key(label, i)))
        self.donut.set_data(donut_items)

        self._refresh_area()
        self.heatmap.set_data(stats_mod.heatmap_weeks(s.daily, weeks=27))
        self.lang_bars.set_data(s.top_language_pairs)
        self.tag_bars.set_data(s.top_tags)
        self.defs.set_values(s.definitions_filled, s.definitions_total)

        # review activity
        self.kpi_rev_week.set_value(s.reviews_this_week, sub=f"{s.reviews_today} today")
        self.kpi_rev_total.set_value(s.reviews_total, sub="listens logged")
        self.kpi_rev_streak.set_value(
            s.review_streak, suffix="d",
            sub="keep it going" if s.review_streak else "—")
        self._refresh_review_area()
        self.rev_heatmap.set_data(stats_mod.heatmap_weeks(s.reviews_daily, weeks=27))
        self.rev_bars.set_data(s.most_reviewed)

    def _on_granularity(self, _value):
        self._refresh_area()

    def _refresh_area(self):
        gran, max_points = self.GRANULARITY.get(self.granularity.value(), ("week", 52))
        points = stats_mod.resample(self._stats.daily, gran, max_points)
        self.area.set_data(points)

    def _on_review_granularity(self, _value):
        self._refresh_review_area()

    def _refresh_review_area(self):
        gran, max_points = self.GRANULARITY.get(self.rev_granularity.value(), ("week", 52))
        points = stats_mod.resample(self._stats.reviews_daily, gran, max_points)
        self.rev_area.set_data(points)

    # -------------------------------------------------------------- theme
    def refresh_theme(self, colors):
        self._colors = colors
        for k in self._kpis:
            k.refresh_theme()
        for w in self._charts:
            w.update()
        self.defs.refresh_theme()
