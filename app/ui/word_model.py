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

"""Table model + filtering for the word list.

The filtering pipeline (favorites → language swap → language → status →
search → tags) reproduces the original app's behavior on a pandas
DataFrame.
"""
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

from app.core import db as dbq
from app.i18n import lang_label, tr

COLUMNS = ["ID", "RowNumber", "Status", "Language1", "Word1", "Language2", "Word2", "Source", "created_at"]
HEADERS = ["ID", "№", tr("Status"), tr("Language"), tr("Word"),
           tr("Translation"), tr("Word"), tr("Source"), tr("Created at")]

COL_ID = 0
COL_ROWNUM = 1
COL_STATUS = 2
COL_LANG1 = 3
COL_WORD1 = 4
COL_LANG2 = 5
COL_WORD2 = 6
COL_SOURCE = 7
COL_CREATED = 8

# Raw Source tags -> human-readable English labels (localized for display).
# Unknown tags fall back to a title-cased form of the raw value.
SOURCE_LABELS = {
    "manual": "Added manually",
    "reader": "From reader",
    "excel_import": "Excel import",
}


def source_label(value):
    """Friendly, localized label for a word's Source tag."""
    raw = _fmt(value).strip()
    if not raw:
        return ""
    english = SOURCE_LABELS.get(raw.lower(), raw.replace("_", " ").capitalize())
    return tr(english)

EMPTY_DF_COLUMNS = ["ID", "Status", "Language1", "Word1", "Language2", "Word2",
                    "Source", "created_at", "edited_at", "favorite"]


_DIM_COLS = frozenset((COL_STATUS, COL_LANG1, COL_LANG2, COL_SOURCE, COL_CREATED))

# Plain ints — comparing against Qt enum members is measurably slower in
# the data() hot path.
_ROLE_DISPLAY = int(Qt.DisplayRole)
_ROLE_EDIT = int(Qt.EditRole)
_ROLE_FOREGROUND = int(Qt.ForegroundRole)
_ROLE_BACKGROUND = int(Qt.BackgroundRole)
_ROLE_TEXTALIGN = int(Qt.TextAlignmentRole)

# Metadata columns shown right-aligned (sit against the right edge as
# secondary info, away from the words).
_RIGHT_COLS = frozenset((COL_SOURCE, COL_CREATED))
_ALIGN_RIGHT = int(Qt.AlignRight | Qt.AlignVCenter)


def _fmt(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


class WordTableModel(QAbstractTableModel):
    """Read-only model over the filtered word DataFrame.

    data() is the hottest path in the app (Qt queries several roles per
    visible cell on every repaint), so display values are precomputed
    into plain lists in set_dataframe() — never touch pandas there.
    """

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._df = pd.DataFrame(columns=EMPTY_DF_COLUMNS)
        self._rows = []
        self._favorites = []
        self._ids = []
        self._playing_id = None
        self._playing_row = -1
        self._queued_ids = frozenset()
        self._queued_rows = frozenset()
        self._header_overrides = {}
        self.set_colors(colors)

    def set_header_text(self, col, text):
        """Override a header label (e.g. blank under embedded filter combos)."""
        self._header_overrides[col] = text
        self.headerDataChanged.emit(Qt.Horizontal, col, col)

    def set_colors(self, colors):
        self._colors = colors
        self._fav_brush = QBrush(QColor(colors["favorite"]))
        self._dim_brush = QBrush(QColor(colors["text_dim"]))
        playing = QColor(colors["accent"])
        playing.setAlpha(56)  # subtle tint that reads on both themes
        self._playing_brush = QBrush(playing)
        queued = QColor(colors["accent"])
        queued.setAlpha(18)  # barely-there tint for words awaiting playback
        self._queued_brush = QBrush(queued)

    def set_dataframe(self, df):
        self.beginResetModel()
        self._df = df.reset_index(drop=True)
        # Language names are stored in English; localize only the display value.
        self._rows = [
            (_fmt(t.ID), None, _fmt(t.Status), lang_label(_fmt(t.Language1)),
             _fmt(t.Word1), lang_label(_fmt(t.Language2)), _fmt(t.Word2),
             source_label(t.Source), _fmt(t.created_at)[:19])
            for t in self._df.itertuples(index=False)
        ]
        self._favorites = self._df["favorite"].fillna(0).astype(bool).tolist()
        self._ids = self._df["ID"].tolist()
        self._playing_row = self._row_for_id(self._playing_id)
        self._queued_rows = self._rows_for_ids(self._queued_ids)
        self.endResetModel()

    def _row_for_id(self, word_id):
        if word_id is None:
            return -1
        try:
            return self._ids.index(word_id)
        except ValueError:
            return -1

    def _rows_for_ids(self, ids):
        if not ids:
            return frozenset()
        return frozenset(row for row, wid in enumerate(self._ids) if wid in ids)

    def set_queued_ids(self, ids):
        """Faintly tint the rows still waiting in the playback queue
        (an empty iterable clears the tint)."""
        self._queued_ids = frozenset(ids)
        old_rows = self._queued_rows
        self._queued_rows = self._rows_for_ids(self._queued_ids)
        last_col = len(COLUMNS) - 1
        for row in old_rows ^ self._queued_rows:
            self.dataChanged.emit(self.index(row, 0), self.index(row, last_col),
                                  [Qt.BackgroundRole])

    def set_playing_id(self, word_id):
        """Highlight the row of the word being read aloud (None clears).
        Returns the highlighted row index, or -1 if not visible."""
        old_row = self._playing_row
        self._playing_id = word_id
        self._playing_row = self._row_for_id(word_id)
        last_col = len(COLUMNS) - 1
        for row in {old_row, self._playing_row}:
            if 0 <= row < len(self._rows):
                self.dataChanged.emit(self.index(row, 0), self.index(row, last_col),
                                      [Qt.BackgroundRole])
        return self._playing_row

    def update_status(self, word_id, status):
        """Update one word's Status in place (no model reset) so the status
        pill repaints without disturbing selection, scroll or playback tints.
        Returns the affected row, or -1 if the word isn't currently shown."""
        row = self._row_for_id(word_id)
        if row < 0:
            return -1
        try:
            self._df.at[row, 'Status'] = status
        except Exception:
            pass
        cells = list(self._rows[row])
        cells[COL_STATUS] = _fmt(status)
        self._rows[row] = tuple(cells)
        idx = self.index(row, COL_STATUS)
        self.dataChanged.emit(idx, idx, [Qt.DisplayRole, Qt.EditRole])
        return row

    def dataframe(self):
        return self._df

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            override = self._header_overrides.get(section)
            return HEADERS[section] if override is None else override
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole:
            return _ALIGN_RIGHT if section in _RIGHT_COLS else None
        return None

    def data(self, index, role=_ROLE_DISPLAY):
        role = int(role)
        if role == _ROLE_DISPLAY or role == _ROLE_EDIT:
            row = index.row()
            col = index.column()
            if col == COL_ROWNUM:
                return row + 1
            return self._rows[row][col]
        if role == _ROLE_FOREGROUND:
            return self._dim_brush if index.column() in _DIM_COLS else None
        if role == _ROLE_TEXTALIGN:
            return _ALIGN_RIGHT if index.column() in _RIGHT_COLS else None
        if role == _ROLE_BACKGROUND:
            row = index.row()
            if row == self._playing_row:
                return self._playing_brush
            if row in self._queued_rows:
                return self._queued_brush
            return self._fav_brush if self._favorites[row] else None
        return None

    def row_record(self, row_index):
        return self._df.iloc[row_index].to_dict()


class WordFilter:
    """Holds filter state and produces a filtered/normalized DataFrame."""

    def __init__(self):
        self.lang1 = None
        self.lang2 = None
        self.status = None
        self.search_query = ""
        self.favorites_only = False
        self.selected_tag = None        # None or "All" => no tag restriction
        self.search_word1 = True
        self.search_word2 = True
        self.search_tags = True
        self.row_limit = None

    def apply(self, df):
        df = df.copy()
        if self.favorites_only:
            df = df[df['favorite'].fillna(0).astype(bool)]

        df = self._swap_words_and_languages(df)
        df = self._filter_by_languages(df)
        if self.status:
            df = df[df['Status'] == self.status]
        df = self._filter_by_search(df)
        df = self._filter_by_tag(df)

        if self.row_limit is not None:
            df = df.head(self.row_limit)
        return df

    def _swap_words_and_languages(self, df):
        # lang1 anchors the left column, lang2 the right column. Build a swap
        # mask that orients the matching language to the side it was picked on.
        if self.lang1:
            swap_mask = (df['Language1'] != self.lang1) & (df['Language2'] == self.lang1)
        elif self.lang2:
            swap_mask = (df['Language2'] != self.lang2) & (df['Language1'] == self.lang2)
        else:
            return df
        df.loc[swap_mask, ['Word1', 'Word2']] = df.loc[swap_mask, ['Word2', 'Word1']].values
        df.loc[swap_mask, ['Language1', 'Language2']] = df.loc[swap_mask, ['Language2', 'Language1']].values
        return df

    def _filter_by_languages(self, df):
        lang1, lang2 = self.lang1, self.lang2
        if lang1 and lang2:
            return df[((df['Language1'] == lang1) & (df['Language2'] == lang2)) |
                      ((df['Language1'] == lang2) & (df['Language2'] == lang1))]
        if lang1:
            return df[(df['Language1'] == lang1) | (df['Language2'] == lang1)]
        if lang2:
            return df[(df['Language1'] == lang2) | (df['Language2'] == lang2)]
        return df

    def _filter_by_search(self, df):
        query = self.search_query.strip().lower()
        if not query:
            return df

        word1 = df['Word1'].astype(str).str.lower()
        word2 = df['Word2'].astype(str).str.lower()

        if self.search_word1 or self.search_word2:
            matches = pd.Series(False, index=df.index)
            if self.search_word1:
                matches |= word1.str.contains(query, regex=False, na=False)
            if self.search_word2:
                matches |= word2.str.contains(query, regex=False, na=False)
        else:
            # Both column filters disabled: search both anyway (original behavior)
            matches = (word1.str.contains(query, regex=False, na=False)
                       | word2.str.contains(query, regex=False, na=False))

        if self.search_tags and (self.selected_tag in (None, "All")):
            tag_ids = dbq.get_word_ids_matching_tag_query(query)
            tag_matches = df['ID'].isin(tag_ids)
            return df[matches | tag_matches]
        return df[matches]

    def _filter_by_tag(self, df):
        if self.search_tags and self.selected_tag and self.selected_tag != "All":
            ids = dbq.get_word_ids_for_tag(self.selected_tag)
            df = df[df['ID'].isin(ids)]
        return df


def words_to_dataframe(words):
    """Convert adapter rows into the app DataFrame, newest first."""
    if not words:
        return pd.DataFrame(columns=EMPTY_DF_COLUMNS)
    rows = [[w.get('ID'), w.get('Status'), w.get('Language1'), w.get('Word1'),
             w.get('Language2'), w.get('Word2'), w.get('Source'),
             w.get('created_at'), w.get('edited_at'), w.get('favorite', False)]
            for w in words]
    df = pd.DataFrame(rows, columns=EMPTY_DF_COLUMNS)
    return df.sort_values(by=['created_at', 'ID'], ascending=[False, False],
                          na_position='last').reset_index(drop=True)
