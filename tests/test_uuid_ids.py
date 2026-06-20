# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Local-only CRUD tests proving the adapter generates and round-trips UUID ids.

Run:  python -m unittest tests.test_uuid_ids
"""
import os
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import db  # noqa: E402
from app.core.database_adapter import DatabaseAdapter  # noqa: E402

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class UuidCrudTests(unittest.TestCase):
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

    def test_insert_word_generates_uuid_and_round_trips(self):
        w = self.a.insert_word({"Language1": "en", "Word1": "cat",
                                "Language2": "de", "Word2": "Katze", "Status": "New"})
        self.assertRegex(str(w["ID"]), UUID_RE)
        again = self.a.get_word(w["ID"])
        self.assertEqual(again["Word1"], "cat")
        self.assertEqual(again["ID"], w["ID"])

    def test_two_inserts_get_distinct_uuids(self):
        a = self.a.insert_word({"Language1": "en", "Word1": "a", "Language2": "de", "Word2": "A"})
        b = self.a.insert_word({"Language1": "en", "Word1": "b", "Language2": "de", "Word2": "B"})
        self.assertNotEqual(a["ID"], b["ID"])

    def test_text_insert_generates_uuid(self):
        t = self.a.insert_text({"Title": "T", "Text": "hello", "Language": "en"})
        self.assertRegex(str(t["ID"]), UUID_RE)
        self.assertEqual(self.a.get_text(t["ID"])["Title"], "T")

    def test_tag_gets_uuid_and_links_word(self):
        w = self.a.insert_word({"Language1": "en", "Word1": "cat", "Language2": "de", "Word2": "Katze"})
        self.assertTrue(self.a.add_tag_to_word(w["ID"], "animals"))
        names = [t["tag_name"] for t in self.a.get_word_tags(w["ID"])]
        self.assertIn("animals", names)
        conn = sqlite3.connect(self.path)
        tag_id = conn.execute("SELECT tag_id FROM tags WHERE tag_name='animals'").fetchone()[0]
        conn.close()
        self.assertRegex(str(tag_id), UUID_RE)

    def test_update_and_delete_by_uuid(self):
        w = self.a.insert_word({"Language1": "en", "Word1": "cat", "Language2": "de", "Word2": "Katze"})
        self.a.update_word(w["ID"], {"Status": "Reviewing"})
        self.assertEqual(self.a.get_word(w["ID"])["Status"], "Reviewing")
        self.assertTrue(self.a.delete_word(w["ID"]))
        self.assertIsNone(self.a.get_word(w["ID"]))

    def test_bin_restore_round_trip_with_string_id(self):
        # The Bin window passes record ids as the cell *text* (a string UUID).
        w = self.a.insert_word({"Language1": "en", "Word1": "cat", "Language2": "de", "Word2": "Katze"})
        self.a.add_tag_to_word(w["ID"], "animals")
        self.a.delete_word(w["ID"])
        self.assertIsNone(self.a.get_word(w["ID"]))

        binned = self.a.get_binned_items("words")
        rid = str(binned[0].get("ID") or binned[0].get("id"))   # exactly what the UI passes
        self.assertTrue(self.a.restore_word(rid))
        self.assertIsNotNone(self.a.get_word(w["ID"]))
        self.assertIn("animals", [t["tag_name"] for t in self.a.get_word_tags(w["ID"])])

        t = self.a.insert_text({"Title": "T", "Text": "hi", "Language": "en"})
        self.a.delete_text(t["ID"])
        tb = self.a.get_binned_items("texts")
        self.assertTrue(self.a.restore_text(str(tb[0].get("ID") or tb[0].get("id"))))
        self.assertIsNotNone(self.a.get_text(t["ID"]))


if __name__ == "__main__":
    unittest.main()
