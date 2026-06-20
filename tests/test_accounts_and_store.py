# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the multi-account registry and per-uid secure store.

Run with the project venv:  python -m unittest tests.test_accounts_and_store
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.accounts import AccountRegistry      # noqa: E402
from app.core.secure_store import SecureStore       # noqa: E402

UID_A = "11111111-1111-1111-1111-111111111111"
UID_B = "22222222-2222-2222-2222-222222222222"


class AccountRegistryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.reg = AccountRegistry(data_dir=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_defaults(self):
        self.assertIsNone(self.reg.get_active())
        self.assertFalse(self.reg.local_import_done())
        self.assertEqual(self.reg.list_accounts(), [])

    def test_upsert_set_active_and_list(self):
        self.reg.upsert(UID_A, "a@example.com")
        self.reg.upsert(UID_B, "b@example.com")
        self.reg.set_active(UID_A)
        self.assertEqual(self.reg.get_active(), UID_A)
        uids = {a["uid"] for a in self.reg.list_accounts()}
        self.assertEqual(uids, {UID_A, UID_B})
        self.assertEqual(self.reg.get(UID_A)["email"], "a@example.com")

    def test_upsert_preserves_fields(self):
        self.reg.upsert(UID_A, "a@example.com")
        self.reg.mark_synced(UID_A)
        synced_at = self.reg.get(UID_A)["last_synced_at"]
        self.assertIsNotNone(synced_at)
        # A second upsert without changing email keeps last_synced_at.
        self.reg.upsert(UID_A, None)
        self.assertEqual(self.reg.get(UID_A)["last_synced_at"], synced_at)
        self.assertEqual(self.reg.get(UID_A)["email"], "a@example.com")

    def test_needs_reauth_toggle_and_mark_synced_clears_it(self):
        self.reg.upsert(UID_A, "a@example.com")
        self.reg.mark_needs_reauth(UID_A, True)
        self.assertTrue(self.reg.get(UID_A)["needs_reauth"])
        self.reg.mark_synced(UID_A)
        self.assertFalse(self.reg.get(UID_A)["needs_reauth"])

    def test_remove_clears_active(self):
        self.reg.upsert(UID_A, "a@example.com")
        self.reg.set_active(UID_A)
        self.reg.remove(UID_A)
        self.assertIsNone(self.reg.get(UID_A))
        self.assertIsNone(self.reg.get_active())

    def test_local_import_flag_persists(self):
        self.reg.set_local_import_done(True)
        self.assertTrue(AccountRegistry(data_dir=self._tmp.name).local_import_done())

    def test_persists_across_instances(self):
        self.reg.upsert(UID_A, "a@example.com")
        self.reg.set_active(UID_A)
        fresh = AccountRegistry(data_dir=self._tmp.name)
        self.assertEqual(fresh.get_active(), UID_A)
        self.assertEqual(fresh.get(UID_A)["email"], "a@example.com")


class SecureStoreFileBackendTests(unittest.TestCase):
    """Exercise the encrypted-file fallback explicitly (keychain disabled), so the
    test is deterministic regardless of the host's keychain."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SecureStore(data_dir=self._tmp.name)
        self.store._keyring = None  # force the file backend

    def tearDown(self):
        self._tmp.cleanup()

    def _sess(self, tag):
        return {"access_token": f"at-{tag}", "refresh_token": f"rt-{tag}"}

    def test_round_trip_per_uid(self):
        self.store.save(UID_A, self._sess("a"))
        self.store.save(UID_B, self._sess("b"))
        self.assertEqual(self.store.load(UID_A), self._sess("a"))
        self.assertEqual(self.store.load(UID_B), self._sess("b"))
        self.assertIsNone(self.store.load("no-such-uid"))

    def test_clear_one_keeps_others(self):
        self.store.save(UID_A, self._sess("a"))
        self.store.save(UID_B, self._sess("b"))
        self.store.clear(UID_A)
        self.assertIsNone(self.store.load(UID_A))
        self.assertEqual(self.store.load(UID_B), self._sess("b"))

    def test_clear_all_removes_file(self):
        self.store.save(UID_A, self._sess("a"))
        self.store.clear_all([UID_A])
        self.assertIsNone(self.store.load(UID_A))
        self.assertFalse(os.path.exists(self.store._file_path))

    def test_legacy_migration_path(self):
        # Simulate a pre-multi-account single-slot file (top-level token blob).
        legacy = {"access_token": "old-at", "refresh_token": "old-rt"}
        self.store._write_map(legacy)
        self.assertEqual(self.store.load_legacy(), legacy)
        # A uid-keyed read must not mistake the legacy blob for an account.
        self.assertIsNone(self.store.load(UID_A))
        # Saving under a uid discards the legacy blob and starts a clean map.
        self.store.save(UID_A, self._sess("a"))
        self.assertEqual(self.store.load(UID_A), self._sess("a"))
        self.assertIsNone(self.store.load_legacy())

    def test_clear_legacy_removes_old_blob(self):
        self.store._write_map({"access_token": "x", "refresh_token": "y"})
        self.store.clear_legacy()
        self.assertIsNone(self.store.load_legacy())


if __name__ == "__main__":
    unittest.main()
