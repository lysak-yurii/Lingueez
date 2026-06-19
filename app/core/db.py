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

"""Database schema initialization and small direct-SQL helpers.

The schema is byte-compatible with the original app's dictionary.db so
existing databases (and the Supabase mirror) keep working unchanged.
"""
import logging
import sqlite3

DB_PATH = 'dictionary.db'

# Active local database. The app keeps one SQLite file per account
# (``dictionary_<uid>.db``) plus the logged-out, local-only ``dictionary.db``.
# Switching accounts repoints this so each account's words, sync queues and
# sync_metadata stay isolated. Helpers below resolve the path at *call time*
# (a ``db_path=DB_PATH`` default would bind once at import and never follow a
# switch), so bare callers automatically hit the active account's file.
_active_db_path = DB_PATH


def get_active_db_path() -> str:
    return _active_db_path


def set_active_db_path(path: str) -> None:
    global _active_db_path
    _active_db_path = path or DB_PATH


def account_db_path(uid) -> str:
    """Local DB filename for an account id; the local-only file when uid is falsy."""
    return DB_PATH if not uid else f'dictionary_{uid}.db'


def _ensure_column(cursor, table, column, decl):
    """Additive migration: add a column to pre-existing databases."""
    cols = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        logging.info("Added column %s.%s", table, column)


def initialize_database(db_path=None):
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS words (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            RowNumber INTEGER,
            Source Text,
            Definition Text,
            Definition2 Text,
            Status TEXT,
            Language1 TEXT,
            Word1 TEXT,
            Language2 TEXT,
            Word2 TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            edited_at DATETIME,
            favorite BOOLEAN DEFAULT 0,
            UNIQUE(Word1, Word2)
        )
    ''')

    # cloud_id maps a local word to its Supabase row id; the pull and direct-CRUD
    # code store and match on it. Older local DBs predate this column, and a fresh
    # one above doesn't declare it — add it additively, or EVERY pulled word fails
    # with "no such column: cloud_id" while sync still reports success.
    _ensure_column(cursor, 'words', 'cloud_id', 'INTEGER')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS texts (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            RowNumber INTEGER,
            Title Text,
            Words Text,
            Text Text,
            Language TEXT,
            Category TEXT,
            Level TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            edited_at DATETIME,
            UNIQUE(ID)
        )
    ''')

    _ensure_column(cursor, 'texts', 'Level', 'TEXT')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_name TEXT UNIQUE NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS word_tags (
            word_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            FOREIGN KEY (word_id) REFERENCES words(ID),
            FOREIGN KEY (tag_id) REFERENCES tags(tag_id),
            PRIMARY KEY (word_id, tag_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_deletions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            synced_at DATETIME,
            UNIQUE(table_name, record_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_type TEXT NOT NULL,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            operation_data TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            synced_at DATETIME
        )
    ''')

    # Local-only review history (never synced to the cloud). Append-only;
    # powers playback-driven status progression and the review dashboard.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word_id INTEGER NOT NULL,
            played_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Local trash ("Bin"). Deleting a word/text hard-deletes its row but first
    # stashes the full payload (and tags, for words) here, so it can be restored
    # even without cloud sync. A grace period purges old entries. payload/tags are
    # JSON; record_id preserves the original row ID for restore.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bin_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL,
            payload TEXT NOT NULL,
            tags TEXT,
            deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(table_name, record_id)
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_deletions_table_record ON sync_deletions(table_name, record_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_deletions_synced ON sync_deletions(synced_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_queue_synced ON sync_queue(synced_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_sync_queue_table_record ON sync_queue(table_name, record_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_review_events_word ON review_events(word_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_review_events_day ON review_events(played_at)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_bin_items_deleted ON bin_items(deleted_at)')

    conn.commit()
    conn.close()
    logging.info("Database initialized successfully.")


def get_all_tags(db_path=None):
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT tag_name FROM tags ORDER BY tag_name COLLATE NOCASE")
    tags = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tags


def get_tags_for_word(word_id, db_path=None):
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT tags.tag_name FROM tags
        JOIN word_tags ON tags.tag_id = word_tags.tag_id
        WHERE word_tags.word_id = ?
        ORDER BY tags.tag_name COLLATE NOCASE
    ''', (word_id,))
    tags = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tags


def get_word_ids_for_tag(tag_name, db_path=None):
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT word_id FROM word_tags
        JOIN tags ON word_tags.tag_id = tags.tag_id
        WHERE tags.tag_name = ?
    ''', (tag_name,))
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return ids


def get_word_ids_matching_tag_query(query, db_path=None):
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT word_id FROM word_tags
        JOIN tags ON word_tags.tag_id = tags.tag_id
        WHERE tags.tag_name LIKE ?
    ''', (f'%{query}%',))
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return ids


def get_tag_usage_counts(db_path=None):
    """Return {tag_name: usage_count} across all words."""
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT tags.tag_name, COUNT(word_tags.word_id)
        FROM tags LEFT JOIN word_tags ON tags.tag_id = word_tags.tag_id
        GROUP BY tags.tag_id ORDER BY tags.tag_name COLLATE NOCASE
    ''')
    counts = dict(cursor.fetchall())
    conn.close()
    return counts


def get_definition_counts(db_path=None):
    """Return (filled, total) where filled counts words with a non-empty
    primary Definition. Used by the statistics dashboard."""
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            SUM(CASE WHEN Definition IS NOT NULL AND TRIM(Definition) != ''
                     THEN 1 ELSE 0 END),
            COUNT(*)
        FROM words
    ''')
    filled, total = cursor.fetchone()
    conn.close()
    return int(filled or 0), int(total or 0)


# --------------------------------------------------------------------------- #
# Review history (local-only). Each fully-listened word logs one event.
# --------------------------------------------------------------------------- #

def log_review(word_id, played_at_iso=None, db_path=None):
    """Append one review event for a word (one completed listen)."""
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if played_at_iso:
        cursor.execute('INSERT INTO review_events (word_id, played_at) VALUES (?, ?)',
                       (int(word_id), played_at_iso))
    else:
        cursor.execute('INSERT INTO review_events (word_id) VALUES (?)', (int(word_id),))
    conn.commit()
    conn.close()


def get_play_count(word_id, db_path=None):
    """Total completed listens recorded for a word."""
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM review_events WHERE word_id = ?', (int(word_id),))
    n = cursor.fetchone()[0]
    conn.close()
    return int(n or 0)


def get_review_aggregates(top=8, db_path=None):
    """Aggregates for the statistics dashboard.

    Returns ``{"daily": [(YYYY-MM-DD, count), ...] ascending, "total": int,
    "most_reviewed": [("Word1 → Word2", count), ...]}``. Orphaned events
    (word deleted) are skipped from ``most_reviewed`` but still counted in the
    daily totals and ``total``.
    """
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT date(played_at) AS d, COUNT(*)
        FROM review_events
        WHERE played_at IS NOT NULL
        GROUP BY d ORDER BY d
    ''')
    daily = [(d, int(n)) for d, n in cursor.fetchall() if d]

    cursor.execute('SELECT COUNT(*) FROM review_events')
    total = int(cursor.fetchone()[0] or 0)

    cursor.execute('''
        SELECT w.Word1, w.Word2, COUNT(*) AS c
        FROM review_events r JOIN words w ON w.ID = r.word_id
        GROUP BY r.word_id ORDER BY c DESC, w.Word1 COLLATE NOCASE
        LIMIT ?
    ''', (int(top),))
    most_reviewed = []
    for w1, w2, c in cursor.fetchall():
        a = (w1 or "").strip()
        b = (w2 or "").strip()
        label = f"{a} → {b}" if b else (a or "—")
        most_reviewed.append((label, int(c)))

    conn.close()
    return {"daily": daily, "total": total, "most_reviewed": most_reviewed}
