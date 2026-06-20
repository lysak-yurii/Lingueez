# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Cross-device content-collision handling.

When the same (word1, word2) pair was created independently under two different
UUIDs, pushing the second one trips the per-user words_user_word_key constraint.
The client must then adopt the existing cloud row's id, and the local re-key
helper must re-point the word and everything that references it.

Run:  python -m unittest tests.test_collision
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import db  # noqa: E402
from app.core.database_adapter import DatabaseAdapter  # noqa: E402
from app.core.supabase_client import SupabaseClient  # noqa: E402


class UpsertCollisionTests(unittest.TestCase):
    """SupabaseClient.upsert_word adopts the existing row on a 23505 collision."""

    def _client(self):
        sc = SupabaseClient.__new__(SupabaseClient)
        sc.client = MagicMock()
        # The upsert itself raises a unique-constraint error.
        sc.client.table.return_value.upsert.return_value.execute.side_effect = \
            Exception("duplicate key value violates unique constraint (23505)")
        # _map_to_supabase_format is pure; bind it so id/content map through.
        sc._map_to_supabase_format = SupabaseClient._map_to_supabase_format.__get__(sc)
        return sc

    def test_adopts_live_cloud_row(self):
        sc = self._client()
        sc.find_word_by_content = MagicMock(return_value={"ID": "cloud-x"})
        sc.find_soft_deleted_word = MagicMock(return_value=None)
        sc.update_word = MagicMock(return_value={"ID": "cloud-x", "Word1": "cat"})

        result = sc.upsert_word({"ID": "local-y", "Language1": "en", "Word1": "cat",
                                 "Language2": "de", "Word2": "Katze"})

        self.assertEqual(result["ID"], "cloud-x")
        sc.update_word.assert_called_once()
        self.assertEqual(sc.update_word.call_args[0][0], "cloud-x")

    def test_restores_soft_deleted_cloud_row(self):
        sc = self._client()
        sc.find_word_by_content = MagicMock(return_value=None)
        sc.find_soft_deleted_word = MagicMock(return_value={"ID": "cloud-del"})
        sc.restore_word_with_data = MagicMock(return_value={"ID": "cloud-del"})

        result = sc.upsert_word({"ID": "local-y", "Language1": "en", "Word1": "cat",
                                 "Language2": "de", "Word2": "Katze"})

        self.assertEqual(result["ID"], "cloud-del")
        sc.restore_word_with_data.assert_called_once()


class RekeyLocalTests(unittest.TestCase):
    """_rekey_word_sqlite re-points the word and its dependent rows."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.path = os.path.join(self._tmp.name, "d.db")
        db.set_active_db_path(self.path)
        db.initialize_database(self.path)
        self.a = DatabaseAdapter(use_cloud=False)
        self.a.set_local_db(self.path)

    def tearDown(self):
        os.chdir(self._cwd)
        db.set_active_db_path(db.DB_PATH)
        self._tmp.cleanup()

    def test_rekey_moves_tags_and_reviews(self):
        w = self.a.insert_word({"Language1": "en", "Word1": "cat", "Language2": "de", "Word2": "Katze"})
        old = w["ID"]
        self.a.add_tag_to_word(old, "animals")
        db.log_review(old, db_path=self.path)

        new = db.new_id()
        self.a._rekey_word_sqlite(old, new)

        conn = sqlite3.connect(self.path)
        cur = conn.cursor()
        self.assertIsNone(cur.execute("SELECT 1 FROM words WHERE ID=?", (old,)).fetchone())
        self.assertIsNotNone(cur.execute("SELECT 1 FROM words WHERE ID=?", (new,)).fetchone())
        self.assertEqual(cur.execute("SELECT word_id FROM word_tags").fetchone()[0], new)
        self.assertEqual(cur.execute("SELECT word_id FROM review_events").fetchone()[0], new)
        self.assertEqual(cur.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()

    def test_rekey_to_existing_target_drops_duplicate(self):
        a = self.a.insert_word({"Language1": "en", "Word1": "a", "Language2": "de", "Word2": "A"})
        b = self.a.insert_word({"Language1": "en", "Word1": "b", "Language2": "de", "Word2": "B"})
        # Re-key a -> b's id: b already exists, so a is dropped (no PK clash).
        self.a._rekey_word_sqlite(a["ID"], b["ID"])
        conn = sqlite3.connect(self.path)
        n = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
