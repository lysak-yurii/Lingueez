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

"""Statistics computation for the dashboard.

Pure data layer — no Qt imports — so it stays trivially testable and the UI
stays dumb. ``compute_stats`` turns the in-memory words DataFrame (the same
one ``MainWindow`` already builds via ``words_to_dataframe``) plus tag and
definition counts into a flat :class:`DashboardStats` value object. All date
handling is tolerant: unparseable / missing ``created_at`` rows are dropped
from the time series but still counted in totals.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from app.i18n import lang_label, month_abbr

# Canonical status ordering for the donut. The DB may hold statuses outside
# this list (older imports, custom values); those are appended afterwards so
# nothing is ever silently dropped.
STATUS_ORDER = ["New", "To Learn", "Reviewing", "Learning", "Mastered", "Ignored"]

# Labels (lower-cased) that count as "done" vs "untouched" for the headline
# KPI; everything else is treated as actively in progress.
_MASTERED = {"mastered"}
_UNTOUCHED = {"new"}
_EXCLUDE_FROM_PROGRESS = _MASTERED | _UNTOUCHED | {"ignored"}


@dataclass
class DashboardStats:
    total_words: int = 0
    favorites: int = 0
    language_count: int = 0
    definitions_filled: int = 0
    definitions_total: int = 0

    status_counts: "OrderedDict[str, int]" = field(default_factory=OrderedDict)

    added_today: int = 0
    added_this_week: int = 0
    added_this_month: int = 0
    current_streak: int = 0
    longest_streak: int = 0
    busiest_day_count: int = 0

    top_language_pairs: list = field(default_factory=list)  # [(label, count)]
    top_tags: list = field(default_factory=list)            # [(name, count)]

    # Complete daily series first_active_day .. today, zero-filled.
    daily: list = field(default_factory=list)               # [(date, count)]
    has_dates: bool = False

    # --- review / playback activity -----------------------------------------
    reviews_total: int = 0
    reviews_today: int = 0
    reviews_this_week: int = 0
    review_streak: int = 0
    reviews_daily: list = field(default_factory=list)       # [(date, count)]
    most_reviewed: list = field(default_factory=list)       # [(label, count)]
    has_reviews: bool = False

    # --- derived convenience -------------------------------------------------
    @property
    def definitions_pct(self) -> float:
        if not self.definitions_total:
            return 0.0
        return 100.0 * self.definitions_filled / self.definitions_total

    @property
    def mastered(self) -> int:
        return sum(v for k, v in self.status_counts.items()
                   if k.strip().lower() in _MASTERED)

    @property
    def mastered_pct(self) -> float:
        if not self.total_words:
            return 0.0
        return 100.0 * self.mastered / self.total_words

    @property
    def in_progress(self) -> int:
        return sum(v for k, v in self.status_counts.items()
                   if k.strip().lower() not in _EXCLUDE_FROM_PROGRESS)


def _parse_dates(series) -> pd.Series:
    """Tolerantly parse a column of ISO-ish timestamps, dropping anything
    unparseable. Returns a Series of :class:`datetime.date` (may be empty)."""
    if series is None or len(series) == 0:
        return pd.Series([], dtype="datetime64[ns]")
    dt = pd.to_datetime(series, errors="coerce")
    dt = dt.dropna()
    return dt


def _streaks(active_days: set) -> tuple[int, int]:
    """Return (current_streak, longest_streak) over a set of active dates.

    The current streak counts back from today; an empty *today* does not break
    it (the streak only ends once a full inactive day has passed)."""
    if not active_days:
        return 0, 0

    today = date.today()
    cursor = today if today in active_days else today - timedelta(days=1)
    current = 0
    while cursor in active_days:
        current += 1
        cursor -= timedelta(days=1)

    longest = 0
    run = 0
    prev = None
    for day in sorted(active_days):
        if prev is not None and (day - prev).days == 1:
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        prev = day
    return current, longest


def _fill_daily(pairs) -> list:
    """Zero-fill a ``[(date, count)]`` series from its first day to today.

    Accepts ``date``/``datetime``/ISO-string keys. Returns ``[(date, int)]``
    ascending with no gaps (so charts/heatmaps render an unbroken timeline)."""
    norm = {}
    for d, c in pairs or []:
        if isinstance(d, str):
            ts = pd.to_datetime(d, errors="coerce")
            if pd.isna(ts):
                continue
            d = ts.date()
        elif hasattr(d, "date") and not isinstance(d, date):
            d = d.date()
        norm[d] = norm.get(d, 0) + int(c)
    if not norm:
        return []
    start = min(norm)
    end = max(date.today(), max(norm))
    full = pd.date_range(start=start, end=end, freq="D")
    return [(ts.date(), norm.get(ts.date(), 0)) for ts in full]


def _daily_series(dates: pd.Series) -> list:
    """Zero-filled list of (date, count) from the first active day to today."""
    if dates.empty:
        return []
    counts = dates.dt.normalize().value_counts()
    return _fill_daily([(ts.date(), int(n)) for ts, n in counts.items()])


def compute_stats(df, tag_counts=None, definition_counts=None,
                  reviews=None) -> DashboardStats:
    """Build :class:`DashboardStats` from the words DataFrame.

    ``tag_counts`` is ``{tag_name: count}`` (see ``db.get_tag_usage_counts``)
    and ``definition_counts`` is ``(filled, total)`` (see
    ``db.get_definition_counts``). Both are optional; missing data degrades to
    empty sections rather than raising.
    """
    stats = DashboardStats()
    tag_counts = tag_counts or {}
    if definition_counts:
        stats.definitions_filled, stats.definitions_total = definition_counts

    try:
        if df is None or len(df) == 0:
            stats.top_tags = _top(tag_counts, 8)
            return stats

        stats.total_words = int(len(df))

        if "favorite" in df:
            stats.favorites = int(df["favorite"].fillna(0).astype(bool).sum())

        # languages (union of both directions, non-empty strings)
        langs = set()
        for col in ("Language1", "Language2"):
            if col in df:
                langs |= {str(v).strip() for v in df[col]
                          if isinstance(v, str) and str(v).strip()}
        stats.language_count = len(langs)

        # status distribution, ordered canonically then by remaining frequency
        if "Status" in df:
            raw = df["Status"].fillna("").map(lambda v: str(v).strip() or "New")
            vc = raw.value_counts()
            ordered = OrderedDict()
            for name in STATUS_ORDER:
                if name in vc.index:
                    ordered[name] = int(vc[name])
            for name, n in vc.items():
                if name not in ordered:
                    ordered[name] = int(n)
            stats.status_counts = ordered

        # language pairs
        if "Language1" in df and "Language2" in df:
            pair_counts = {}
            for l1, l2 in zip(df["Language1"], df["Language2"]):
                a = str(l1).strip() if isinstance(l1, str) else ""
                b = str(l2).strip() if isinstance(l2, str) else ""
                if not a and not b:
                    continue
                label = f"{lang_label(a) or '—'} → {lang_label(b) or '—'}"
                pair_counts[label] = pair_counts.get(label, 0) + 1
            stats.top_language_pairs = _top(pair_counts, 8)

        stats.top_tags = _top(tag_counts, 8)

        # --- time series -----------------------------------------------------
        dates = _parse_dates(df["created_at"]) if "created_at" in df else pd.Series([], dtype="datetime64[ns]")
        if not dates.empty:
            stats.has_dates = True
            today = date.today()
            day_dates = dates.dt.date
            active_days = set(day_dates)

            stats.added_today = int((day_dates == today).sum())
            week_start = today - timedelta(days=today.weekday())
            stats.added_this_week = int((day_dates >= week_start).sum())
            month_start = today.replace(day=1)
            stats.added_this_month = int((day_dates >= month_start).sum())

            stats.current_streak, stats.longest_streak = _streaks(active_days)
            stats.daily = _daily_series(dates)
            if stats.daily:
                stats.busiest_day_count = max(c for _, c in stats.daily)
    except Exception:
        logging.exception("compute_stats failed; returning partial stats")

    _apply_reviews(stats, reviews)
    return stats


def _apply_reviews(stats: DashboardStats, reviews):
    """Fold review-history aggregates (see ``db.get_review_aggregates``) into
    the stats object. Tolerant of a missing/empty payload."""
    if not reviews:
        return
    try:
        stats.reviews_total = int(reviews.get("total", 0))
        stats.most_reviewed = list(reviews.get("most_reviewed", []))
        stats.reviews_daily = _fill_daily(reviews.get("daily", []))
        if stats.reviews_total:
            stats.has_reviews = True
        if stats.reviews_daily:
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
            active = set()
            for d, c in stats.reviews_daily:
                if c:
                    active.add(d)
                    if d == today:
                        stats.reviews_today += c
                    if d >= week_start:
                        stats.reviews_this_week += c
            stats.review_streak = _streaks(active)[0]
    except Exception:
        logging.exception("_apply_reviews failed")


def _top(counts: dict, n: int) -> list:
    items = [(k, int(v)) for k, v in counts.items() if v]
    items.sort(key=lambda kv: (-kv[1], kv[0].lower()))
    return items[:n]


# --------------------------------------------------------------------------- #
# Resampling helpers for the activity chart / heatmap (kept here so the UI
# does no data wrangling).
# --------------------------------------------------------------------------- #

def resample(daily: list, granularity: str = "day", max_points: int = 0) -> list:
    """Aggregate a ``[(date, count)]`` daily series by day/week/month.

    Returns ``[(date, count)]`` where the date is the period's start. When
    ``max_points`` > 0 only the most recent N points are returned.
    """
    if not daily:
        return []
    granularity = (granularity or "day").lower()

    if granularity == "day":
        out = list(daily)
    else:
        freq = "W-MON" if granularity == "week" else "MS"
        idx = pd.to_datetime([d for d, _ in daily])
        ser = pd.Series([c for _, c in daily], index=idx)
        if granularity == "week":
            grouped = ser.resample("W-MON", label="left", closed="left").sum()
        else:
            grouped = ser.resample("MS").sum()
        out = [(ts.date(), int(v)) for ts, v in grouped.items()]

    if max_points and len(out) > max_points:
        out = out[-max_points:]
    return out


def heatmap_weeks(daily: list, weeks: int = 27) -> dict:
    """Build a GitHub-style calendar grid for the last ``weeks`` weeks.

    Returns ``{"columns": [[(date, count) | None x7], ...], "max": int,
    "month_labels": [(column_index, "Jun"), ...]}`` where each column is a week
    (Mon..Sun) and ``None`` marks days outside the [first_day, today] range.
    """
    today = date.today()
    # align the grid to whole weeks ending this week (Monday-based)
    end = today + timedelta(days=(6 - today.weekday()))  # Sunday of this week
    start = end - timedelta(days=weeks * 7 - 1)
    lookup = {d: c for d, c in daily}
    first_day = daily[0][0] if daily else today

    columns = []
    month_labels = []
    last_month = None
    cur = start
    col = 0
    peak = 0
    while cur <= end:
        week_col = []
        for _ in range(7):
            if cur < first_day or cur > today:
                week_col.append(None)
            else:
                c = lookup.get(cur, 0)
                peak = max(peak, c)
                week_col.append((cur, c))
            cur += timedelta(days=1)
        # month label when the month changes at the top of a column
        top = columns and None  # noop placeholder
        month_of_col = month_abbr(start + timedelta(days=col * 7))
        month_num = (start + timedelta(days=col * 7)).month
        if month_num != last_month:
            month_labels.append((col, month_of_col))
            last_month = month_num
        columns.append(week_col)
        col += 1

    return {"columns": columns, "max": peak, "month_labels": month_labels}
