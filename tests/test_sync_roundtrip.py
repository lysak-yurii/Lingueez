# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""End-to-end sync round-trip over a fake in-memory cloud.

Device A pushes its UUID-keyed data up; device B pulls it down. Because the id
is shared, B ends up with the exact same ids as A, and pulling a second time is
idempotent (no duplicate rows, ids unchanged) — the property the old integer-id
scheme could not guarantee.

Run:  python -m unittest tests.test_sync_roundtrip
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import db  # noqa: E402
from app.core.database_adapter import DatabaseAdapter  # noqa: E402
from app.core.sync_manager import SyncManager  # noqa: E402


class FakeCloud:
    """Minimal stand-in for SupabaseClient backed by plain dicts."""

    def __init__(self):
        self.words = {}
        self.texts = {}
        self.tags = {}            # tag_id -> tag_name
        self.word_tags = set()    # (word_id, tag_id)

    # --- push targets ---
    def get_tags(self):
        return [{"tag_id": k, "tag_name": v} for k, v in self.tags.items()]

    def insert_tag(self, name, tag_id=None):
        for tid, nm in self.tags.items():       # adopt existing by name
            if nm == name:
                return {"tag_id": tid, "tag_name": nm}
        tid = tag_id or db.new_id()
        self.tags[tid] = name
        return {"tag_id": tid, "tag_name": name}

    def upsert_word(self, w):
        wid = w.get("ID") or w.get("id")
        self.words[wid] = dict(w, ID=wid)
        return {"ID": wid}

    def upsert_text(self, t):
        tid = t.get("ID") or t.get("id")
        self.texts[tid] = dict(t, ID=tid)
        return {"ID": tid}

    def add_tag_to_word(self, word_id, tag_id):
        self.word_tags.add((word_id, tag_id))
        return True

    # --- pull sources ---
    def get_words(self):
        return [dict(v) for v in self.words.values()]

    def get_texts(self):
        return [dict(v) for v in self.texts.values()]

    def get_all_word_tags(self):
        return [{"word_id": a, "tag_id": b} for a, b in self.word_tags]


def _read_local(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    words = [dict(r) for r in c.execute("SELECT * FROM words")]
    texts = [dict(r) for r in c.execute("SELECT * FROM texts")]
    tags = [dict(r) for r in c.execute("SELECT * FROM tags")]
    wt = [dict(r) for r in c.execute("SELECT * FROM word_tags")]
    c.close()
    return words, texts, tags, wt


def _sm(local_db, cloud):
    sm = SyncManager.__new__(SyncManager)
    sm.supabase = cloud
    sm.db_adapter = None
    sm.local_db = local_db
    return sm


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.a_path = os.path.join(self._tmp.name, "a.db")
        self.b_path = os.path.join(self._tmp.name, "b.db")
        db.initialize_database(self.a_path)
        db.initialize_database(self.b_path)
        self.cloud = FakeCloud()

        # Seed device A with three tagged words and a text.
        a = DatabaseAdapter(use_cloud=False)
        a.set_local_db(self.a_path)
        for w1, w2 in [("cat", "Katze"), ("dog", "Hund"), ("bird", "Vogel")]:
            w = a.insert_word({"Language1": "en", "Word1": w1, "Language2": "de", "Word2": w2})
            a.add_tag_to_word(w["ID"], "animals")
        a.insert_text({"Title": "T", "Text": "hello", "Language": "en"})

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _push_a(self):
        words, texts, tags, wt = _read_local(self.a_path)
        return _sm(self.a_path, self.cloud)._push_all_local_to_cloud(words, texts, tags, wt)

    def _pull_b(self):
        smB = _sm(self.b_path, self.cloud)
        smB._pull_all_cloud_to_local(
            self.cloud.get_words(), self.cloud.get_texts(),
            self.cloud.get_tags(), self.cloud.get_all_word_tags())

    def test_push_then_pull_preserves_ids(self):
        self.assertEqual(self._push_a(), 0)
        self._pull_b()

        a_words = {w["ID"]: w["Word1"] for w in _read_local(self.a_path)[0]}
        b_words = {w["ID"]: w["Word1"] for w in _read_local(self.b_path)[0]}
        self.assertEqual(a_words, b_words)  # same ids AND content on both devices

        # Tags + links carried across.
        b = _read_local(self.b_path)
        self.assertEqual(len(b[2]), 1)       # one tag
        self.assertEqual(len(b[3]), 3)       # three word_tags links
        self.assertEqual(len(b[1]), 1)       # one text

    def test_second_pull_is_idempotent(self):
        self._push_a()
        self._pull_b()
        first = {t: len(_read_local(self.b_path)[i]) for i, t in enumerate("wt g l".split())}
        ids_first = sorted(w["ID"] for w in _read_local(self.b_path)[0])

        self._pull_b()  # pull again
        second = {t: len(_read_local(self.b_path)[i]) for i, t in enumerate("wt g l".split())}
        ids_second = sorted(w["ID"] for w in _read_local(self.b_path)[0])

        self.assertEqual(first, second)      # no duplicate rows
        self.assertEqual(ids_first, ids_second)

    def test_fk_integrity_after_pull(self):
        self._push_a()
        self._pull_b()
        c = sqlite3.connect(self.b_path)
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()


if __name__ == "__main__":
    unittest.main()
