# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for SyncManager._push_all_local_to_cloud (UUID id model).

Words, texts and tags share one UUID id with the cloud, so the push is a plain
upsert-by-id. This covers the original "33 local -> only 8 in cloud" regression:
the push must surface per-record failures (return a nonzero count) instead of
swallowing them and reporting a clean sync, and word_tags links must reference
the ids the cloud actually returned.

Run:  python -m unittest tests.test_sync_push
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.sync_manager import SyncManager  # noqa: E402


def _word(wid, w1, w2):
    return {"ID": wid, "Language1": "en", "Word1": w1, "Language2": "de", "Word2": w2}


class PushAllLocalToCloudTests(unittest.TestCase):
    def setUp(self):
        # Build a SyncManager without running its heavy __init__ (singletons /
        # network); we only exercise the pure push loop with fakes.
        self.sm = SyncManager.__new__(SyncManager)
        self.supabase = MagicMock()
        self.db_adapter = MagicMock()
        self.sm.supabase = self.supabase
        self.sm.db_adapter = self.db_adapter
        # No pre-existing cloud tags by default.
        self.supabase.get_tags.return_value = []

    def test_all_words_push_with_no_failures(self):
        words = [_word("u-cat", "cat", "Katze"), _word("u-dog", "dog", "Hund")]
        # Cloud keeps the same id (normal case).
        self.supabase.upsert_word.side_effect = lambda w: {"ID": w["ID"]}

        failures = self.sm._push_all_local_to_cloud(words, [], [], [])

        self.assertEqual(failures, 0)
        self.assertEqual(self.supabase.upsert_word.call_count, 2)

    def test_rejected_word_counts_as_failure(self):
        words = [_word("u-cat", "cat", "Katze"), _word("u-blk", "blocked", "Blockiert")]
        # Second word is rejected by the cloud -> upsert returns None.
        self.supabase.upsert_word.side_effect = (
            lambda w: None if w["Word1"] == "blocked" else {"ID": w["ID"]})

        failures = self.sm._push_all_local_to_cloud(words, [], [], [])

        self.assertEqual(failures, 1)

    def test_exception_during_push_counts_as_failure(self):
        words = [_word("u-cat", "cat", "Katze")]
        self.supabase.upsert_word.side_effect = RuntimeError("boom")

        failures = self.sm._push_all_local_to_cloud(words, [], [], [])

        self.assertEqual(failures, 1)

    def test_word_tags_use_cloud_returned_ids(self):
        # The cloud adopts a different id for this word (content collision); the
        # word_tags link must reference that cloud id, not the local one.
        words = [_word("u-local", "cat", "Katze")]
        self.supabase.upsert_word.side_effect = lambda w: {"ID": "u-cloud"}
        tags = [{"tag_id": "t-1", "tag_name": "animals"}]
        self.supabase.insert_tag.side_effect = lambda name, tag_id=None: {"tag_id": tag_id}

        failures = self.sm._push_all_local_to_cloud(
            words, [], tags, [{"word_id": "u-local", "tag_id": "t-1"}])

        self.assertEqual(failures, 0)
        self.supabase.add_tag_to_word.assert_called_once_with("u-cloud", "t-1")

    def test_word_tag_link_failure_does_not_fail_the_push(self):
        words = [_word("u-cat", "cat", "Katze")]
        self.supabase.upsert_word.side_effect = lambda w: {"ID": w["ID"]}
        tags = [{"tag_id": "t-1", "tag_name": "animals"}]
        self.supabase.insert_tag.side_effect = lambda name, tag_id=None: {"tag_id": tag_id}
        self.supabase.add_tag_to_word.side_effect = RuntimeError("link boom")

        failures = self.sm._push_all_local_to_cloud(
            words, [], tags, [{"word_id": "u-cat", "tag_id": "t-1"}])

        # Link rows are recreated idempotently next sync; they must not fail sync.
        self.assertEqual(failures, 0)


if __name__ == "__main__":
    unittest.main()
