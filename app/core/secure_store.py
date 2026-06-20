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

"""Secure storage for Supabase auth sessions (access + refresh tokens).

The refresh token is the crown jewel — anyone holding it can mint new access
tokens for the account — so it must never land in plaintext ``settings.cfg`` or
``.env``. Two backends, in order of preference:

1. **OS keychain** via ``keyring`` (GNOME Keyring / KWallet / macOS Keychain /
   Windows Credential Locker). Best option when available.
2. **Encrypted local file** ``.session.enc`` in the per-user data dir (the app's
   CWD after ``main._setup_paths``), used when no usable keychain exists — common
   on minimal/headless Linux, a locked login keyring, or the exFAT source tree
   which has no secure storage at all. Encrypted with a machine-bound Fernet key;
   this is obfuscation-grade, not keychain-grade, hence the strong preference for
   the keychain.

Sessions are stored **per account uid** so several accounts can stay remembered
on one device and be switched between without re-entering a password. The keychain
holds one entry per uid (``supabase_session::<uid>``); the encrypted-file fallback
holds a single JSON **map** ``{"<uid>": {"access_token": ..., "refresh_token": ...}}``.
Which uids exist is tracked by :class:`~app.core.accounts.AccountRegistry` (the
keychain can't be enumerated); this store only persists the secrets.
"""
import base64
import json
import logging
import os

_SERVICE = "Lingueez"
_ACCOUNT_PREFIX = "supabase_session::"
_LEGACY_ACCOUNT = "supabase_session"  # pre-multi-account single slot
_FALLBACK_FILE = ".session.enc"


class SecureStore:
    """Per-uid secret store: keychain first, encrypted file as fallback."""

    def __init__(self, data_dir=None):
        # CWD is the per-user data dir at runtime (main._setup_paths chdirs there).
        self._dir = data_dir or os.getcwd()
        self._keyring = self._probe_keyring()

    @staticmethod
    def _probe_keyring():
        """Return a usable keyring module, or None. Rejects the no-op 'fail'
        backend that keyring resolves to when no real keychain is present."""
        try:
            import keyring
            backend = keyring.get_keyring()
            name = f"{type(backend).__module__}.{type(backend).__name__}".lower()
            if "fail" in name or "null" in name:
                logging.info("No OS keychain available; using encrypted-file session store.")
                return None
            return keyring
        except Exception as exc:  # ImportError, NoKeyringError, D-Bus issues, …
            logging.info(f"keyring unavailable ({exc}); using encrypted-file session store.")
            return None

    @staticmethod
    def _acct(uid: str) -> str:
        return f"{_ACCOUNT_PREFIX}{uid}"

    # ---- public API (per uid) -----------------------------------------
    def save(self, uid: str, data: dict) -> None:
        blob = json.dumps(data)
        if self._keyring is not None:
            try:
                self._keyring.set_password(_SERVICE, self._acct(uid), blob)
                self._file_drop(uid)  # don't leave a stale duplicate behind
                return
            except Exception as exc:
                logging.warning(f"Keychain write failed, falling back to encrypted file: {exc}")
        self._file_put(uid, data)

    def load(self, uid: str) -> dict | None:
        if self._keyring is not None:
            try:
                blob = self._keyring.get_password(_SERVICE, self._acct(uid))
                if blob:
                    return json.loads(blob)
            except Exception as exc:
                logging.warning(f"Keychain read failed, trying encrypted file: {exc}")
        return self._file_get(uid)

    def clear(self, uid: str) -> None:
        if self._keyring is not None:
            try:
                self._keyring.delete_password(_SERVICE, self._acct(uid))
            except Exception:
                pass  # absent or backend gone — nothing to remove
        self._file_drop(uid)

    def clear_all(self, uids=()) -> None:
        """Remove the encrypted-file map entirely and, for each given uid, the
        matching keychain entry (the keychain can't be enumerated, so callers pass
        the uids they know from the registry)."""
        for uid in uids:
            if self._keyring is not None:
                try:
                    self._keyring.delete_password(_SERVICE, self._acct(uid))
                except Exception:
                    pass
        self._remove_file()

    # ---- legacy single-slot migration ---------------------------------
    def load_legacy(self) -> dict | None:
        """The pre-multi-account session, if one is still stored single-slot."""
        if self._keyring is not None:
            try:
                blob = self._keyring.get_password(_SERVICE, _LEGACY_ACCOUNT)
                if blob:
                    return json.loads(blob)
            except Exception:
                pass
        raw = self._read_map()
        if "access_token" in raw:  # old top-level (non-uid-keyed) blob
            return raw
        return None

    def clear_legacy(self) -> None:
        if self._keyring is not None:
            try:
                self._keyring.delete_password(_SERVICE, _LEGACY_ACCOUNT)
            except Exception:
                pass
        if "access_token" in self._read_map():
            self._remove_file()

    # ---- encrypted-file fallback (uid -> session map) -----------------
    @property
    def _file_path(self) -> str:
        return os.path.join(self._dir, _FALLBACK_FILE)

    def _fernet(self):
        """Machine-bound Fernet. Obfuscation-grade: a static salt + the hostname
        means the file can't be read by copying it to another machine, but it is
        not a substitute for a real keychain."""
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import platform

        secret = f"{platform.node()}::lingueez-session-v1".encode("utf-8")
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"lingueez-session-store-v1",
            iterations=200_000,
        )
        return Fernet(base64.urlsafe_b64encode(kdf.derive(secret)))

    def _read_map(self) -> dict:
        """Decrypt the fallback file into a dict (may be the legacy top-level
        blob or the uid-keyed map); empty dict if absent/unreadable."""
        try:
            if not os.path.exists(self._file_path):
                return {}
            with open(self._file_path, "rb") as fh:
                token = fh.read()
            return json.loads(self._fernet().decrypt(token).decode("utf-8"))
        except Exception as exc:
            logging.info(f"No restorable session in encrypted file ({exc}).")
            return {}

    def _write_map(self, mapping: dict) -> None:
        try:
            blob = json.dumps(mapping)
            token = self._fernet().encrypt(blob.encode("utf-8"))
            tmp = self._file_path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(token)
            os.replace(tmp, self._file_path)
        except Exception as exc:
            # Refuse to persist in plaintext if encryption is unavailable — the
            # user simply re-logs in next launch, which is the safe failure mode.
            logging.warning(f"Could not persist session securely ({exc}); not saving tokens.")

    def _file_get(self, uid: str) -> dict | None:
        raw = self._read_map()
        entry = raw.get(uid)
        return entry if isinstance(entry, dict) else None

    def _file_put(self, uid: str, data: dict) -> None:
        raw = self._read_map()
        if "access_token" in raw:  # discard a legacy top-level blob
            raw = {}
        raw[uid] = data
        self._write_map(raw)

    def _file_drop(self, uid: str) -> None:
        raw = self._read_map()
        if "access_token" in raw:
            return  # legacy blob, not a uid map — leave for clear_legacy
        if uid in raw:
            raw.pop(uid, None)
            if raw:
                self._write_map(raw)
            else:
                self._remove_file()

    def _remove_file(self) -> None:
        try:
            os.remove(self._file_path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logging.debug(f"Could not remove session file: {exc}")
