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

Row identity is a client-generated UUIDv4 (stored as TEXT) shared verbatim with
the Supabase mirror, so a row has the *same* id locally and in the cloud and sync
is a plain upsert-by-id. Older databases used INTEGER AUTOINCREMENT ids bridged
to the cloud by a ``cloud_id`` column; ``migrate_database`` re-keys those to UUIDs
exactly once, gated by ``PRAGMA user_version`` (see SCHEMA_VERSION).
"""
import json
import logging
import os
import shutil
import sqlite3
import uuid
from datetime import datetime

DB_PATH = 'dictionary.db'

# Local schema version, tracked via ``PRAGMA user_version``.
#   0/1 — legacy INTEGER AUTOINCREMENT ids + words.cloud_id bridge column.
#   2   — UUID (TEXT) primary keys shared with the cloud; no cloud_id.
# ``migrate_database`` upgrades 0/1 → 2 once; fresh databases are stamped 2.
SCHEMA_VERSION = 2


def new_id() -> str:
    """A fresh row id: a UUIDv4 string, identical in SQLite and Supabase."""
    return str(uuid.uuid4())

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


def reset_sync_state(db_path: str) -> None:
    """Wipe a DB file's sync bookkeeping so the next sync is treated as a first-time
    (union) sync that re-pushes everything, instead of an already-done incremental.

    Clears the ``sync_queue``, ``sync_deletions`` and ``sync_metadata`` tables (which
    hold ``first_sync_completed`` / ``synced_account_id`` / ``last_sync_time``) and
    removes the ``<db>.last_sync`` marker file — clearing the metadata alone is not
    enough, because ``SyncManager._is_first_sync`` also consults that file. Used after
    a whole-file change (account adoption, backup restore) that bypassed the sync
    queue. Safe to call on a fresh DB (missing tables/markers are ignored)."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            cur = conn.cursor()
            # Also clear sync_quarantine: a restored/adopted DB is an explicit
            # re-seed (push everything), so stale-deletion holds from the old state
            # must not block its upload.
            for table in ("sync_queue", "sync_deletions", "sync_metadata", "sync_quarantine"):
                try:
                    cur.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    pass  # table doesn't exist yet
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logging.warning(f"Could not reset sync state for {db_path}: {exc}")
    for marker in (f"{db_path}.last_sync", ".last_sync"):
        try:
            if os.path.exists(marker):
                os.remove(marker)
        except OSError as exc:
            logging.warning(f"Could not remove stale sync marker {marker}: {exc}")


def _ensure_column(cursor, table, column, decl):
    """Additive migration: add a column to pre-existing databases."""
    cols = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        logging.info("Added column %s.%s", table, column)


# --------------------------------------------------------------------------- #
# Cloud-mirrored table definitions (single-sourced; used by both
# initialize_database and the UUID migration).
# --------------------------------------------------------------------------- #

def _create_words(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS words (
            ID TEXT PRIMARY KEY,
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


def _create_texts(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS texts (
            ID TEXT PRIMARY KEY,
            RowNumber INTEGER,
            Title Text,
            Words Text,
            Text Text,
            Language TEXT,
            Category TEXT,
            Level TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            edited_at DATETIME
        )
    ''')


def _create_tags(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tags (
            tag_id TEXT PRIMARY KEY,
            tag_name TEXT UNIQUE NOT NULL
        )
    ''')


def _create_word_tags(cursor):
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS word_tags (
            word_id TEXT NOT NULL,
            tag_id TEXT NOT NULL,
            FOREIGN KEY (word_id) REFERENCES words(ID),
            FOREIGN KEY (tag_id) REFERENCES tags(tag_id),
            PRIMARY KEY (word_id, tag_id)
        )
    ''')


def initialize_database(db_path=None):
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Write-Ahead Logging lets a reader and a writer coexist instead of blocking
    # each other, sharply reducing "database is locked" contention with the
    # background sync thread. The setting is persistent per database file, so
    # stamping it once at init is enough.
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error as exc:
        logging.warning(f"Could not enable WAL on {db_path}: {exc}")

    # Primary keys are UUIDv4 strings (TEXT), generated client-side and shared
    # verbatim with the Supabase mirror — see the module docstring. The four
    # cloud-mirrored tables are defined by helpers so initialize and migrate share
    # a single schema definition.
    _create_words(cursor)
    _create_texts(cursor)
    _ensure_column(cursor, 'texts', 'Level', 'TEXT')
    _create_tags(cursor)
    _create_word_tags(cursor)

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_deletions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
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
            record_id TEXT NOT NULL,
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
            word_id TEXT NOT NULL,
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
            record_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            tags TEXT,
            deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(table_name, record_id)
        )
    ''')

    # Items the user permanently deleted from the Bin. Their cloud row stays
    # soft-deleted (the tombstone propagates the removal and is physically purged by
    # the grace-period cleanup), so this marker hides it from THIS device's Bin in the
    # meantime — without it the live cloud soft-delete would re-surface the item.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS bin_purged (
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            purged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (table_name, record_id)
        )
    ''')

    # Rows present locally but deleted on another device while this one was offline
    # past the tombstone-retention window. They're held here pending the user's
    # review (keep or remove) and are EXCLUDED from the union upload so a reconcile
    # can't silently re-upload (resurrect) them before the user decides.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_quarantine (
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            detected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (table_name, record_id)
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

    # Re-key any pre-existing INTEGER-id database to UUIDs (no-op once at
    # SCHEMA_VERSION). On a brand-new DB the tables above are already TEXT-keyed,
    # so this just stamps the version and returns.
    migrate_database(db_path)

    logging.info("Database initialized successfully.")


def _words_id_is_text(cursor) -> bool:
    """True when words.ID is already declared TEXT (UUID schema)."""
    for row in cursor.execute("PRAGMA table_info(words)"):
        if row[1] == 'ID':
            return (row[2] or '').upper().startswith('TEXT')
    return False


def migrate_database(db_path=None):
    """Idempotently upgrade a legacy INTEGER-id database to UUID (TEXT) ids.

    Gated by ``PRAGMA user_version``: once a file reaches SCHEMA_VERSION this is a
    no-op. The re-key assigns one fresh UUID per existing row and rewrites every
    foreign-key reference (word_tags, review_events, bin_items) to match, drops the
    obsolete ``words.cloud_id`` bridge column, and clears the now-moot pending sync
    queues (the cloud is re-seeded from scratch after this migration). The whole
    rewrite runs in a single transaction, and the file is copied to ``backups/``
    first, so a crash leaves the original intact.
    """
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        version = cursor.execute("PRAGMA user_version").fetchone()[0]
        if version >= SCHEMA_VERSION:
            return
        if _words_id_is_text(cursor):
            # Fresh DB created with the new schema (or already migrated): nothing
            # to re-key, just record the version.
            cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
            return
    except sqlite3.OperationalError:
        # No words table yet (shouldn't happen — initialize_database runs first).
        conn.close()
        return

    logging.info("Migrating %s to UUID ids (schema v%d)…", db_path, SCHEMA_VERSION)
    _backup_before_migration(db_path)

    # --- Build int -> UUID maps for every entity that owns a primary key. ---
    word_map = {row[0]: new_id() for row in cursor.execute("SELECT ID FROM words")}
    text_map = {row[0]: new_id() for row in cursor.execute("SELECT ID FROM texts")}
    tag_map = {row[0]: new_id() for row in cursor.execute("SELECT tag_id FROM tags")}
    # Orphaned review events (their word was deleted) keep a stable synthetic UUID
    # so daily/total review counts survive even though they never re-join a word.
    # Kept separate from word_map so these synthetic ids never leak into word_tags.
    review_word_map = dict(word_map)
    for (wid,) in cursor.execute("SELECT DISTINCT word_id FROM review_events"):
        if wid not in review_word_map:
            review_word_map[wid] = new_id()

    cursor.execute("PRAGMA foreign_keys = OFF")
    cursor.execute("BEGIN")
    try:
        # words ------------------------------------------------------------
        word_cols = [r[1] for r in cursor.execute("PRAGMA table_info(words)")
                     if r[1] != 'cloud_id']  # drop the bridge column
        rows = cursor.execute(
            f"SELECT {', '.join(word_cols)} FROM words").fetchall()
        cursor.execute("ALTER TABLE words RENAME TO _old_words")
        _create_words(cursor)
        id_idx = word_cols.index('ID')
        for r in rows:
            r = list(r)
            r[id_idx] = word_map[r[id_idx]]
            cursor.execute(
                f"INSERT INTO words ({', '.join(word_cols)}) "
                f"VALUES ({', '.join('?' * len(word_cols))})", r)

        # texts ------------------------------------------------------------
        text_cols = [r[1] for r in cursor.execute("PRAGMA table_info(texts)")]
        rows = cursor.execute(
            f"SELECT {', '.join(text_cols)} FROM texts").fetchall()
        cursor.execute("ALTER TABLE texts RENAME TO _old_texts")
        _create_texts(cursor)
        id_idx = text_cols.index('ID')
        for r in rows:
            r = list(r)
            r[id_idx] = text_map[r[id_idx]]
            cursor.execute(
                f"INSERT INTO texts ({', '.join(text_cols)}) "
                f"VALUES ({', '.join('?' * len(text_cols))})", r)

        # tags -------------------------------------------------------------
        rows = cursor.execute("SELECT tag_id, tag_name FROM tags").fetchall()
        cursor.execute("ALTER TABLE tags RENAME TO _old_tags")
        _create_tags(cursor)
        for tid, name in rows:
            cursor.execute("INSERT INTO tags (tag_id, tag_name) VALUES (?, ?)",
                           (tag_map[tid], name))

        # word_tags (drop links whose word or tag no longer exists) ---------
        rows = cursor.execute("SELECT word_id, tag_id FROM word_tags").fetchall()
        cursor.execute("ALTER TABLE word_tags RENAME TO _old_word_tags")
        _create_word_tags(cursor)
        for wid, tid in rows:
            nwid, ntid = word_map.get(wid), tag_map.get(tid)
            if nwid and ntid:
                cursor.execute("INSERT OR IGNORE INTO word_tags (word_id, tag_id) "
                               "VALUES (?, ?)", (nwid, ntid))

        # review_events.word_id (rebuilt; its index references the column) -----
        rows = cursor.execute(
            "SELECT id, word_id, played_at FROM review_events").fetchall()
        cursor.execute("DROP INDEX IF EXISTS idx_review_events_word")
        cursor.execute("DROP INDEX IF EXISTS idx_review_events_day")
        cursor.execute("ALTER TABLE review_events RENAME TO _old_review_events")
        cursor.execute('''
            CREATE TABLE review_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word_id TEXT NOT NULL,
                played_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        for rid, wid, played_at in rows:
            cursor.execute("INSERT INTO review_events (id, word_id, played_at) "
                           "VALUES (?, ?, ?)", (rid, review_word_map[wid], played_at))
        cursor.execute("DROP TABLE IF EXISTS _old_review_events")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_review_events_word "
                       "ON review_events(word_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_review_events_day "
                       "ON review_events(played_at)")

        # bin_items.record_id + the ID embedded in its JSON payload --------
        bin_rows = cursor.execute(
            "SELECT id, table_name, record_id, payload FROM bin_items").fetchall()
        for bid, table_name, rec_id, payload in bin_rows:
            tmap = word_map if table_name == 'words' else text_map
            # The binned row was hard-deleted, so it usually isn't in the map;
            # mint a fresh id and keep record_id and payload['ID'] consistent.
            new_rec = tmap.get(rec_id) or new_id()
            try:
                data = json.loads(payload)
                if 'ID' in data:
                    data['ID'] = new_rec
                new_payload = json.dumps(data)
            except (ValueError, TypeError):
                new_payload = payload
            cursor.execute("UPDATE bin_items SET record_id = ?, payload = ? "
                           "WHERE id = ?", (str(new_rec), new_payload, bid))

        # Pending sync state is moot — the cloud is re-seeded post-migration.
        cursor.execute("DELETE FROM sync_queue")
        cursor.execute("DELETE FROM sync_deletions")
        cursor.execute(
            "UPDATE sync_metadata SET value = NULL "
            "WHERE key IN ('last_sync_time', 'first_sync_completed')")

        # Drop the renamed originals.
        for t in ('_old_words', '_old_texts', '_old_tags', '_old_word_tags'):
            cursor.execute(f"DROP TABLE IF EXISTS {t}")
        # words/texts/tags no longer AUTOINCREMENT — clear their stale counters
        # (sqlite_sequence itself stays; the local-only tables still use it).
        try:
            cursor.execute("DELETE FROM sqlite_sequence "
                           "WHERE name IN ('words', 'texts', 'tags')")
        except sqlite3.OperationalError:
            pass

        cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except Exception:
        conn.rollback()
        logging.exception("UUID migration failed for %s — rolled back", db_path)
        raise
    finally:
        cursor.execute("PRAGMA foreign_keys = ON")

    conn.execute("VACUUM")
    conn.close()
    logging.info("Migrated %s to UUID ids.", db_path)


def _backup_before_migration(db_path):
    """Copy the DB to backups/ before an irreversible schema rewrite."""
    try:
        os.makedirs('backups', exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base = os.path.basename(db_path)
        dest = os.path.join('backups', f'{base}.pre-uuid-{stamp}.db')
        shutil.copy2(db_path, dest)
        logging.info("Backed up %s -> %s before UUID migration", db_path, dest)
    except Exception:
        logging.exception("Could not back up %s before migration", db_path)


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
                       (str(word_id), played_at_iso))
    else:
        cursor.execute('INSERT INTO review_events (word_id) VALUES (?)', (str(word_id),))
    conn.commit()
    conn.close()


def get_play_count(word_id, db_path=None):
    """Total completed listens recorded for a word."""
    db_path = db_path or get_active_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM review_events WHERE word_id = ?', (str(word_id),))
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
