"""Playback-driven learning progression (pure, Qt-free, testable).

Passive listening promotes a word along a familiarity ladder, but only after
*many* completed listens — audio-only exposure is weak, so the thresholds are
deliberately high. Each rung's listen-count is configured independently:

    New ──(reviewing_at)──▶ Reviewing ──(learning_at)──▶ Learning ──(mastered_at)──▶ Mastered

Defaults are 3 / 15 / 100. Promotion never demotes, and never touches words
the user owns the meaning of (``Mastered``, ``Ignored``, or any status outside
the ladder).
"""
from __future__ import annotations

# Increasing familiarity. Matches the app's canonical status order.
LADDER = ["New", "Reviewing", "Learning", "Mastered"]
_RANK = {name: i for i, name in enumerate(LADDER)}

DEFAULT_REVIEWING_LISTENS = 3
DEFAULT_LEARNING_LISTENS = 15
DEFAULT_MASTERED_LISTENS = 100

# Statuses that listening may promote *from*. Anything else (Mastered,
# Ignored, "To Learn", custom values) is left untouched.
_PROMOTABLE = {"", "new", "reviewing", "learning"}


def normalize_thresholds(reviewing=DEFAULT_REVIEWING_LISTENS,
                         learning=DEFAULT_LEARNING_LISTENS,
                         mastered=DEFAULT_MASTERED_LISTENS) -> dict:
    """Per-rung cumulative listen counts, clamped strictly increasing so the
    ladder is always well-formed regardless of the saved values."""
    r = max(1, int(reviewing))
    l = max(r + 1, int(learning))
    m = max(l + 1, int(mastered))
    return {"Reviewing": r, "Learning": l, "Mastered": m}


def level_for_count(n: int, thresholds) -> str:
    """The highest ladder status whose threshold is met by ``n`` listens."""
    th = thresholds if isinstance(thresholds, dict) else normalize_thresholds(*thresholds)
    if n >= th["Mastered"]:
        return "Mastered"
    if n >= th["Learning"]:
        return "Learning"
    if n >= th["Reviewing"]:
        return "Reviewing"
    return "New"


def next_status(current, n: int, thresholds):
    """Return the status ``current`` should be promoted to after ``n`` total
    completed listens, or ``None`` if it should stay unchanged.

    Never demotes; only acts on promotable statuses (empty / New / Reviewing /
    Learning). ``thresholds`` is a dict from :func:`normalize_thresholds` (or a
    ``(reviewing, learning, mastered)`` tuple).
    """
    key = (current or "").strip().lower()
    if key not in _PROMOTABLE:
        return None
    current_rank = 0 if key == "" else _RANK.get((current or "New").strip(), 0)
    target = level_for_count(n, thresholds)
    if _RANK[target] > current_rank:
        return target
    return None
