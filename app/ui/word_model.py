"""Table model + filtering for the word list.

The filtering pipeline (favorites → language swap → language → status →
search → tags) reproduces the original app's behavior on a pandas
DataFrame.
"""
import pandas as pd
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

from app.core import db as dbq

COLUMNS = ["ID", "RowNumber", "Status", "Language1", "Word1", "Language2", "Word2", "Source", "created_at"]
HEADERS = ["ID", "№", "Status", "Language", "Word", "Translation", "Word", "Source", "Created at"]

COL_ID = 0
COL_ROWNUM = 1
COL_STATUS = 2
COL_LANG1 = 3
COL_WORD1 = 4
COL_LANG2 = 5
COL_WORD2 = 6
COL_SOURCE = 7
COL_CREATED = 8

EMPTY_DF_COLUMNS = ["ID", "Status", "Language1", "Word1", "Language2", "Word2",
                    "Source", "created_at", "edited_at", "favorite"]


_DIM_COLS = frozenset((COL_STATUS, COL_LANG1, COL_LANG2, COL_SOURCE, COL_CREATED))

# Plain ints — comparing against Qt enum members is measurably slower in
# the data() hot path.
_ROLE_DISPLAY = int(Qt.DisplayRole)
_ROLE_EDIT = int(Qt.EditRole)
_ROLE_FOREGROUND = int(Qt.ForegroundRole)
_ROLE_BACKGROUND = int(Qt.BackgroundRole)


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

    def set_dataframe(self, df):
        self.beginResetModel()
        self._df = df.reset_index(drop=True)
        self._rows = [
            (_fmt(t.ID), None, _fmt(t.Status), _fmt(t.Language1), _fmt(t.Word1),
             _fmt(t.Language2), _fmt(t.Word2), _fmt(t.Source), _fmt(t.created_at)[:19])
            for t in self._df.itertuples(index=False)
        ]
        self._favorites = self._df["favorite"].fillna(0).astype(bool).tolist()
        self.endResetModel()

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
        if role == _ROLE_BACKGROUND:
            return self._fav_brush if self._favorites[index.row()] else None
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
        if self.lang1 or self.lang2:
            selected = self.lang1 or self.lang2
            swap_mask = (df['Language1'] != selected) & (df['Language2'] == selected)
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
