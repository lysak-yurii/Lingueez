# Lingueez — a desktop app for studying vocabulary across languages.
# Copyright (C) 2024-2026 Yurii Lysak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the SM-2 flashcard scheduler and its local storage.

Run:  python -m unittest tests.test_srs
"""

import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import db  # noqa: E402
from app.core import srs  # noqa: E402

NOW = datetime(2026, 7, 2, 12, 0, 0)


def _iso(days):
    return (NOW + timedelta(days=days)).isoformat(timespec="seconds")


class ApplyGradeFirstReviewTests(unittest.TestCase):
    def test_first_review_easy(self):
        state = srs.apply_grade(None, "easy", NOW)
        self.assertEqual(
            state,
            {
                "ease_factor": 2.5,
                "interval_days": 1,
                "next_review": _iso(1),
                "review_count": 1,
                "correct_count": 1,
            },
        )

    def test_first_review_good(self):
        state = srs.apply_grade(None, "good", NOW)
        self.assertEqual((state["interval_days"], state["correct_count"]), (1, 1))
        self.assertEqual(state["ease_factor"], 2.5)

    def test_first_review_hard_records_incorrect(self):
        state = srs.apply_grade(None, "hard", NOW)
        self.assertEqual((state["interval_days"], state["correct_count"]), (1, 0))
        self.assertEqual(state["review_count"], 1)

    def test_zero_review_count_row_treated_as_first_review(self):
        card = {"ease_factor": 1.5, "interval_days": 30, "review_count": 0, "correct_count": 0}
        state = srs.apply_grade(card, "good", NOW)
        self.assertEqual(state["ease_factor"], 2.5)
        self.assertEqual(state["interval_days"], 1)

    def test_unknown_grade_raises(self):
        with self.assertRaises(ValueError):
            srs.apply_grade(None, "meh", NOW)


class ApplyGradeSequenceTests(unittest.TestCase):
    def test_easy_grows_interval_and_caps_ease(self):
        state = srs.apply_grade(None, "easy", NOW)
        state = srs.apply_grade(state, "easy", NOW)
        # int(1 * 2.5 * 1.5) = 3; ease already at the 2.5 cap
        self.assertEqual(state["interval_days"], 3)
        self.assertEqual(state["ease_factor"], 2.5)
        state = srs.apply_grade(state, "easy", NOW)
        # int(3 * 2.5 * 1.5) = 11
        self.assertEqual(state["interval_days"], 11)
        self.assertEqual(state["review_count"], 3)
        self.assertEqual(state["correct_count"], 3)

    def test_good_grows_interval_without_bonus(self):
        state = srs.apply_grade(None, "good", NOW)
        state = srs.apply_grade(state, "good", NOW)
        # int(1 * 2.5) = 2
        self.assertEqual(state["interval_days"], 2)
        self.assertEqual(state["ease_factor"], 2.5)

    def test_hard_resets_interval_and_lowers_ease(self):
        state = srs.apply_grade(None, "easy", NOW)
        state = srs.apply_grade(state, "easy", NOW)
        state = srs.apply_grade(state, "hard", NOW)
        self.assertEqual(state["interval_days"], 1)
        self.assertEqual(state["ease_factor"], 2.3)
        self.assertEqual(state["correct_count"], 2)  # unchanged by the miss

    def test_ease_floors_at_1_3(self):
        state = srs.apply_grade(None, "hard", NOW)
        for _ in range(10):
            state = srs.apply_grade(state, "hard", NOW)
        self.assertEqual(state["ease_factor"], 1.3)

    def test_recovery_after_miss_uses_lowered_ease(self):
        state = srs.apply_grade(None, "easy", NOW)
        state = srs.apply_grade(state, "hard", NOW)  # ease 2.3, interval 1
        state = srs.apply_grade(state, "good", NOW)  # int(1 * 2.3) = 2
        self.assertEqual(state["interval_days"], 2)
        self.assertEqual(state["ease_factor"], 2.4)

    def test_interval_caps_at_ten_years(self):
        card = {"ease_factor": 2.5, "interval_days": 3000, "review_count": 50, "correct_count": 50}
        state = srs.apply_grade(card, "easy", NOW)
        self.assertEqual(state["interval_days"], srs.MAX_INTERVAL_DAYS)
        self.assertEqual(state["next_review"], _iso(srs.MAX_INTERVAL_DAYS))

    def test_next_review_is_now_plus_interval(self):
        state = srs.apply_grade(None, "good", NOW)
        state = srs.apply_grade(state, "good", NOW)
        self.assertEqual(state["next_review"], _iso(state["interval_days"]))


class StatusFromProgressTests(unittest.TestCase):
    def test_zero_reviews_is_new(self):
        self.assertEqual(srs.status_from_progress(0, 2.5, 0), "New")

    def test_mastered_boundary(self):
        self.assertEqual(srs.status_from_progress(5, 2.3, 5), "Mastered")
        self.assertEqual(srs.status_from_progress(5, 2.29, 5), "Learning")
        self.assertEqual(srs.status_from_progress(5, 2.3, 4), "Learning")

    def test_learning_boundary(self):
        self.assertEqual(srs.status_from_progress(3, 2.0, 3), "Learning")
        self.assertEqual(srs.status_from_progress(3, 1.9, 3), "Reviewing")
        self.assertEqual(srs.status_from_progress(3, 2.0, 2), "Reviewing")

    def test_reviewed_but_struggling_is_reviewing(self):
        self.assertEqual(srs.status_from_progress(1, 1.3, 0), "Reviewing")


class PromotionTargetTests(unittest.TestCase):
    def test_promotes_new_upward(self):
        self.assertEqual(srs.promotion_target("New", "Reviewing"), "Reviewing")
        self.assertEqual(srs.promotion_target("New", "Mastered"), "Mastered")
        self.assertEqual(srs.promotion_target("", "Learning"), "Learning")
        self.assertEqual(srs.promotion_target(None, "Reviewing"), "Reviewing")

    def test_to_learn_is_promotable(self):
        self.assertEqual(srs.promotion_target("To Learn", "Learning"), "Learning")

    def test_never_demotes(self):
        self.assertIsNone(srs.promotion_target("Mastered", "Reviewing"))
        self.assertIsNone(srs.promotion_target("Learning", "Reviewing"))
        self.assertIsNone(srs.promotion_target("Reviewing", "Reviewing"))

    def test_ignored_and_custom_untouched(self):
        self.assertIsNone(srs.promotion_target("Ignored", "Mastered"))
        self.assertIsNone(srs.promotion_target("Custom", "Mastered"))


class SrsStorageTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)
        db.initialize_database(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def _add_word(self, status="New"):
        wid = str(uuid.uuid4())
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO words (ID, Word1, Word2, Status) VALUES (?, ?, ?, ?)",
            (wid, f"w-{wid[:8]}", f"t-{wid[:8]}", status),
        )
        conn.commit()
        conn.close()
        return wid

    def test_get_missing_returns_none(self):
        self.assertIsNone(db.srs_get("nope", db_path=self.db_path))

    def test_upsert_roundtrip_and_update(self):
        wid = self._add_word()
        first = srs.apply_grade(None, "good", NOW)
        db.srs_upsert(wid, first, db_path=self.db_path)
        row = db.srs_get(wid, db_path=self.db_path)
        self.assertEqual(row["review_count"], 1)
        self.assertEqual(row["ease_factor"], 2.5)

        second = srs.apply_grade(row, "easy", NOW)
        db.srs_upsert(wid, second, db_path=self.db_path)
        row = db.srs_get(wid, db_path=self.db_path)
        self.assertEqual(row["review_count"], 2)
        self.assertEqual(row["interval_days"], second["interval_days"])

    def test_due_ordering_and_filters(self):
        overdue = self._add_word()
        never = self._add_word()
        future = self._add_word()
        ignored = self._add_word(status="Ignored")

        db.srs_upsert(
            overdue,
            {
                "ease_factor": 2.5,
                "interval_days": 1,
                "next_review": _iso(-3),
                "review_count": 4,
                "correct_count": 3,
            },
            db_path=self.db_path,
        )
        db.srs_upsert(
            future,
            {
                "ease_factor": 2.5,
                "interval_days": 10,
                "next_review": _iso(10),
                "review_count": 2,
                "correct_count": 2,
            },
            db_path=self.db_path,
        )

        ids = db.srs_due_word_ids(
            10, now_iso=NOW.isoformat(timespec="seconds"), db_path=self.db_path
        )
        self.assertNotIn(future, ids)  # not due yet
        self.assertNotIn(ignored, ids)  # opted out
        self.assertIn(overdue, ids)
        self.assertIn(never, ids)  # never graded counts as due

    def test_due_limit(self):
        for _ in range(5):
            self._add_word()
        ids = db.srs_due_word_ids(
            2, now_iso=NOW.isoformat(timespec="seconds"), db_path=self.db_path
        )
        self.assertEqual(len(ids), 2)


if __name__ == "__main__":
    unittest.main()
