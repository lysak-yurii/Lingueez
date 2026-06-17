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

"""Check GitHub Releases for a newer version (notify-only).

The update *feed* is the GitHub Releases API: CI attaches the built artifacts
to a Release tagged ``vX.Y.Z`` and this module compares that tag against the
running :data:`app.version.APP_VERSION`. When a newer release exists the caller
shows release notes with a Download button that opens the release page in the
browser — the app never downloads or installs anything itself.

All network errors are swallowed (return ``None``): an update check must never
disrupt startup or surface a failure to the user.
"""
import logging
import os
import time
from dataclasses import dataclass

import requests

from app.version import APP_VERSION

GITHUB_OWNER = "lysak-yurii"
GITHUB_REPO = "Lingueez"
GITHUB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}"
LATEST_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
# Timestamp file gating the once-per-interval startup check, matching the
# .last_sync / .last_cleanup convention used elsewhere in the app.
CHECK_STAMP_FILE = ".last_update_check"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # at most once a day
_REQUEST_TIMEOUT = 8


@dataclass(frozen=True)
class UpdateInfo:
    """A release that is newer than the running version."""
    version: str          # normalized, e.g. "2.1.0" (no leading 'v')
    url: str              # release page (html_url) to open in the browser
    notes: str            # release body / changelog (may be empty)
    published_at: str     # ISO timestamp from the API (may be empty)


def parse_version(text):
    """Parse a version/tag string into a comparable tuple of ints.

    Accepts an optional leading ``v`` and ignores any pre-release/build suffix
    (e.g. ``v2.1.0-beta`` -> ``(2, 1, 0)``). Returns ``None`` when no numeric
    component can be found.
    """
    if not text:
        return None
    text = str(text).strip().lstrip("vV")
    parts = []
    for chunk in text.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break  # stop at the first non-digit (e.g. "0-beta" -> "0")
        if num == "":
            break
        parts.append(int(num))
    return tuple(parts) or None


def is_newer(remote, current):
    """True when version string *remote* is strictly newer than *current*."""
    r, c = parse_version(remote), parse_version(current)
    if r is None or c is None:
        return False
    return r > c


def check_for_update(current=APP_VERSION):
    """Return :class:`UpdateInfo` if the latest release is newer, else ``None``.

    Safe to call from a background thread; never raises.
    """
    try:
        resp = requests.get(
            LATEST_URL,
            timeout=_REQUEST_TIMEOUT,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Lingueez/{current}",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - network/parse errors are non-fatal
        logging.info(f"Update check skipped: {exc}")
        return None

    tag = data.get("tag_name") or ""
    if not is_newer(tag, current):
        return None

    return UpdateInfo(
        version=str(tag).strip().lstrip("vV"),
        url=data.get("html_url") or f"{GITHUB_URL}/releases",
        notes=(data.get("body") or "").strip(),
        published_at=data.get("published_at") or "",
    )


def should_check_now(path=CHECK_STAMP_FILE, interval=CHECK_INTERVAL_SECONDS):
    """True when enough time has elapsed since the last recorded check."""
    try:
        last = os.path.getmtime(path)
    except OSError:
        return True
    return (time.time() - last) >= interval


def record_check(path=CHECK_STAMP_FILE):
    """Stamp the throttle file so the next startup check waits a full interval."""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(int(time.time())))
    except OSError as exc:
        logging.info(f"Could not write update-check stamp: {exc}")
