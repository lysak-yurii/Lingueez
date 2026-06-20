# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the integer-id -> UUID database migration (db.migrate_database).

Seeds a legacy INTEGER-AUTOINCREMENT database across every table (including an
orphaned word_tags link, an orphaned review event, and a bin item) and asserts
the migration: re-keys every id to a UUID, rewrites all foreign-key references,
drops orphan links while preserving review counts, rewrites the bin payload id,
removes the cloud_id column, clears the moot sync queues, stamps the schema
version, and is idempotent.

Run:  python -m unittest tests.test_migration
"""
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import db  # noqa: E402

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def _legacy_schema(cur):
    """Create the pre-UUID schema (integer ids + words.cloud_id)."""
    cur.executescript("""
        CREATE TABLE words (
            ID INTEGER PRIMARY KEY AUTOINCREMENT, RowNumber INTEGER, Source TEXT,
            Definition TEXT, Definition2 TEXT, Status TEXT, Language1 TEXT,
            Word1 TEXT, Language2 TEXT, Word2 TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP, edited_at DATETIME,
            favorite BOOLEAN DEFAULT 0, cloud_id INTEGER, UNIQUE(Word1, Word2));
        CREATE TABLE texts (
            ID INTEGER PRIMARY KEY AUTOINCREMENT, RowNumber INTEGER, Title TEXT,
            Words TEXT, Text TEXT, Language TEXT, Category TEXT, Level TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP, edited_at DATETIME);
        CREATE TABLE tags (
            tag_id INTEGER PRIMARY KEY AUTOINCREMENT, tag_name TEXT UNIQUE NOT NULL);
        CREATE TABLE word_tags (
            word_id INTEGER NOT NULL, tag_id INTEGER NOT NULL,
            PRIMARY KEY (word_id, tag_id));
        CREATE TABLE sync_deletions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL, deleted_at DATETIME, synced_at DATETIME,
            UNIQUE(table_name, record_id));
        CREATE TABLE sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, operation_type TEXT NOT NULL,
            table_name TEXT NOT NULL, record_id INTEGER NOT NULL,
            operation_data TEXT, created_at DATETIME, synced_at DATETIME);
        CREATE TABLE review_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, word_id INTEGER NOT NULL,
            played_at DATETIME DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE bin_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL,
            record_id INTEGER NOT NULL, payload TEXT NOT NULL, tags TEXT,
            deleted_at DATETIME, UNIQUE(table_name, record_id));
        CREATE INDEX idx_review_events_word ON review_events(word_id);
        CREATE INDEX idx_review_events_day ON review_events(played_at);
        CREATE TABLE sync_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL,
            value TEXT, updated_at DATETIME);
    """)


def _seed(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    _legacy_schema(cur)
    # 3 words, 2 texts, 2 tags.
    cur.execute("INSERT INTO words (ID, Word1, Word2, Status, cloud_id) VALUES (1,'cat','Katze','New',101)")
    cur.execute("INSERT INTO words (ID, Word1, Word2, Status, cloud_id) VALUES (2,'dog','Hund','New',102)")
    cur.execute("INSERT INTO words (ID, Word1, Word2, Status) VALUES (3,'bird','Vogel','New')")
    cur.execute("INSERT INTO texts (ID, Title, Text) VALUES (1,'T1','hello')")
    cur.execute("INSERT INTO texts (ID, Title, Text) VALUES (2,'T2','world')")
    cur.execute("INSERT INTO tags (tag_id, tag_name) VALUES (1,'animals')")
    cur.execute("INSERT INTO tags (tag_id, tag_name) VALUES (2,'nouns')")
    # word_tags: valid links + one ORPHAN (word_id 99 does not exist).
    cur.execute("INSERT INTO word_tags (word_id, tag_id) VALUES (1,1)")
    cur.execute("INSERT INTO word_tags (word_id, tag_id) VALUES (1,2)")
    cur.execute("INSERT INTO word_tags (word_id, tag_id) VALUES (2,1)")
    cur.execute("INSERT INTO word_tags (word_id, tag_id) VALUES (99,1)")  # orphan
    # review_events: 2 for word 1, 1 for word 2, 1 ORPHAN (word 99 deleted).
    cur.executemany("INSERT INTO review_events (word_id) VALUES (?)", [(1,), (1,), (2,), (99,)])
    # bin item for a previously hard-deleted word (record_id 50, not in words).
    cur.execute("INSERT INTO bin_items (table_name, record_id, payload) VALUES ('words', 50, ?)",
                (json.dumps({"ID": 50, "Word1": "gone", "Word2": "weg"}),))
    # pending sync state (should be cleared) + metadata.
    cur.execute("INSERT INTO sync_queue (operation_type, table_name, record_id) VALUES ('INSERT','words',3)")
    cur.execute("INSERT INTO sync_deletions (table_name, record_id) VALUES ('words', 7)")
    cur.execute("INSERT INTO sync_metadata (key, value) VALUES ('last_sync_time','2026-01-01')")
    cur.execute("INSERT INTO sync_metadata (key, value) VALUES ('synced_account_id','acc-123')")
    conn.commit()
    conn.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "legacy.db")
        # migrate_database backs up into ./backups — run from the temp dir.
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        _seed(self.path)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _conn(self):
        c = sqlite3.connect(self.path)
        return c, c.cursor()

    def test_full_migration(self):
        db.migrate_database(self.path)
        c, cur = self._conn()

        # Schema version stamped.
        self.assertEqual(cur.execute("PRAGMA user_version").fetchone()[0], db.SCHEMA_VERSION)

        # All ids are UUIDs and the cloud_id column is gone.
        self.assertNotIn("cloud_id", [r[1] for r in cur.execute("PRAGMA table_info(words)")])
        for col, table in (("ID", "words"), ("ID", "texts"), ("tag_id", "tags"),
                           ("word_id", "review_events")):
            for (val,) in cur.execute(f"SELECT {col} FROM {table}"):
                self.assertRegex(str(val), UUID_RE, f"{table}.{col} not a UUID: {val}")

        # Counts preserved for data tables; orphan word_tags link dropped.
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM words").fetchone()[0], 3)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM texts").fetchone()[0], 2)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM tags").fetchone()[0], 2)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM word_tags").fetchone()[0], 3)  # 4 - 1 orphan
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM review_events").fetchone()[0], 4)  # incl orphan

        # Foreign-key integrity is clean (no dangling word_tags refs).
        self.assertEqual(cur.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(cur.execute("PRAGMA integrity_check").fetchone()[0], "ok")

        # Bin payload id rewritten to match the new record_id (both UUIDs, equal).
        rid, payload = cur.execute("SELECT record_id, payload FROM bin_items").fetchone()
        self.assertRegex(str(rid), UUID_RE)
        self.assertEqual(json.loads(payload)["ID"], rid)

        # Pending sync queues cleared; last_sync reset but ownership kept.
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0], 0)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM sync_deletions").fetchone()[0], 0)
        self.assertIsNone(cur.execute(
            "SELECT value FROM sync_metadata WHERE key='last_sync_time'").fetchone()[0])
        self.assertEqual(cur.execute(
            "SELECT value FROM sync_metadata WHERE key='synced_account_id'").fetchone()[0], "acc-123")
        c.close()

    def test_word_tag_links_follow_remap(self):
        db.migrate_database(self.path)
        c, cur = self._conn()
        # The word 'cat' must still carry exactly its two tags after re-keying.
        rows = cur.execute("""
            SELECT t.tag_name FROM words w
            JOIN word_tags wt ON wt.word_id = w.ID
            JOIN tags t ON t.tag_id = wt.tag_id
            WHERE w.Word1 = 'cat' ORDER BY t.tag_name
        """).fetchall()
        self.assertEqual([r[0] for r in rows], ["animals", "nouns"])
        c.close()

    def test_idempotent(self):
        db.migrate_database(self.path)
        c, cur = self._conn()
        snapshot = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("words", "texts", "tags", "word_tags", "review_events")}
        ids = sorted(r[0] for r in cur.execute("SELECT ID FROM words"))
        c.close()

        db.migrate_database(self.path)  # second run is a no-op
        c, cur = self._conn()
        after = {t: cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in snapshot}
        ids_after = sorted(r[0] for r in cur.execute("SELECT ID FROM words"))
        c.close()
        self.assertEqual(snapshot, after)
        self.assertEqual(ids, ids_after)  # ids unchanged on re-run

    def test_backup_created(self):
        db.migrate_database(self.path)
        backups = os.listdir(os.path.join(self._tmp.name, "backups"))
        self.assertTrue(any(".pre-uuid-" in b for b in backups), backups)


if __name__ == "__main__":
    unittest.main()
