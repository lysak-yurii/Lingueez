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

"""Registry of the accounts known on this device.

Stores *non-secret* account metadata in ``accounts.json`` in the per-user data
dir (the app's CWD after ``main._setup_paths``), alongside ``window_geometry.json``
and ``.last_sync``. The refresh tokens themselves never live here — they go in the
:class:`~app.core.secure_store.SecureStore` (keychain / encrypted file), keyed by
the same uid.

This file is the source of truth for *which* accounts exist and *which* one is
active, because the OS keychain can't be reliably enumerated. Each account's local
SQLite file is derived from its uid via :func:`app.core.db.account_db_path`, so it
is intentionally not duplicated here.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

_REGISTRY_FILE = "accounts.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccountRegistry:
    """Thread-safe accessor for ``accounts.json``."""

    def __init__(self, data_dir: Optional[str] = None):
        # CWD is the per-user data dir at runtime (main._setup_paths chdirs there).
        self._dir = data_dir or os.getcwd()
        self._lock = threading.Lock()

    @property
    def _path(self) -> str:
        return os.path.join(self._dir, _REGISTRY_FILE)

    # ---- low-level load/save ------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            data = {}
        except Exception as exc:
            logging.warning(f"Could not read {_REGISTRY_FILE} ({exc}); starting fresh.")
            data = {}
        data.setdefault("active_uid", None)
        data.setdefault("local_import_done", False)
        data.setdefault("accounts", {})
        return data

    def _save(self, data: dict) -> None:
        try:
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except Exception as exc:
            logging.warning(f"Could not write {_REGISTRY_FILE}: {exc}")

    # ---- queries -------------------------------------------------------
    def list_accounts(self) -> List[dict]:
        """All known accounts as dicts including their uid, newest first."""
        with self._lock:
            data = self._load()
        out = []
        for uid, info in data["accounts"].items():
            entry = dict(info)
            entry["uid"] = uid
            out.append(entry)
        out.sort(key=lambda e: e.get("added_at") or "", reverse=True)
        return out

    def get(self, uid: str) -> Optional[dict]:
        with self._lock:
            info = self._load()["accounts"].get(uid)
        if info is None:
            return None
        entry = dict(info)
        entry["uid"] = uid
        return entry

    def get_active(self) -> Optional[str]:
        with self._lock:
            return self._load()["active_uid"]

    def local_import_done(self) -> bool:
        with self._lock:
            return bool(self._load()["local_import_done"])

    # ---- mutations -----------------------------------------------------
    def set_active(self, uid: Optional[str]) -> None:
        with self._lock:
            data = self._load()
            data["active_uid"] = uid or None
            self._save(data)

    def set_local_import_done(self, done: bool = True) -> None:
        with self._lock:
            data = self._load()
            data["local_import_done"] = bool(done)
            self._save(data)

    def upsert(self, uid: str, email: Optional[str], name: Optional[str] = None) -> None:
        """Add or update an account, preserving fields not given here."""
        with self._lock:
            data = self._load()
            entry = data["accounts"].get(uid, {})
            entry.setdefault("added_at", _now_iso())
            if email is not None:
                entry["email"] = email
            if name:
                entry["name"] = name
            entry.setdefault("last_synced_at", None)
            entry["needs_reauth"] = entry.get("needs_reauth", False)
            data["accounts"][uid] = entry
            self._save(data)

    def remove(self, uid: str) -> None:
        with self._lock:
            data = self._load()
            data["accounts"].pop(uid, None)
            if data["active_uid"] == uid:
                data["active_uid"] = None
            self._save(data)

    def mark_synced(self, uid: str) -> None:
        with self._lock:
            data = self._load()
            entry = data["accounts"].get(uid)
            if entry is not None:
                entry["last_synced_at"] = _now_iso()
                entry["needs_reauth"] = False
                self._save(data)

    def mark_needs_reauth(self, uid: str, needs: bool = True) -> None:
        with self._lock:
            data = self._load()
            entry = data["accounts"].get(uid)
            if entry is not None:
                entry["needs_reauth"] = bool(needs)
                self._save(data)

    def contribution_suppressed(self, uid: str) -> bool:
        """Whether this account opted out of the 'add local words?' auto-prompt."""
        with self._lock:
            entry = self._load()["accounts"].get(uid) or {}
            return bool(entry.get("contribution_suppressed", False))

    def set_contribution_suppressed(self, uid: str, suppressed: bool = True) -> None:
        with self._lock:
            data = self._load()
            entry = data["accounts"].get(uid)
            if entry is not None:
                entry["contribution_suppressed"] = bool(suppressed)
                self._save(data)


# ---------------------------------------------------------------------------
# Process-wide shared registry.
# ---------------------------------------------------------------------------
_shared_registry: Optional[AccountRegistry] = None
_shared_lock = threading.Lock()


def get_account_registry() -> AccountRegistry:
    """Return the process-wide AccountRegistry (created on first use)."""
    global _shared_registry
    with _shared_lock:
        if _shared_registry is None:
            _shared_registry = AccountRegistry()
        return _shared_registry
