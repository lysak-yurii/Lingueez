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

"""Secure storage for the Supabase auth session (access + refresh tokens).

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

Stored value is one JSON blob: ``{"access_token": ..., "refresh_token": ...}``.
"""
import base64
import json
import logging
import os

_SERVICE = "Lingueez"
_ACCOUNT = "supabase_session"
_FALLBACK_FILE = ".session.enc"


class SecureStore:
    """Single-slot secret store: keychain first, encrypted file as fallback."""

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

    # ---- public API ----------------------------------------------------
    def save(self, data: dict) -> None:
        blob = json.dumps(data)
        if self._keyring is not None:
            try:
                self._keyring.set_password(_SERVICE, _ACCOUNT, blob)
                self._remove_file()  # don't leave a stale duplicate behind
                return
            except Exception as exc:
                logging.warning(f"Keychain write failed, falling back to encrypted file: {exc}")
        self._save_file(blob)

    def load(self) -> dict | None:
        if self._keyring is not None:
            try:
                blob = self._keyring.get_password(_SERVICE, _ACCOUNT)
                if blob:
                    return json.loads(blob)
            except Exception as exc:
                logging.warning(f"Keychain read failed, trying encrypted file: {exc}")
        return self._load_file()

    def clear(self) -> None:
        if self._keyring is not None:
            try:
                self._keyring.delete_password(_SERVICE, _ACCOUNT)
            except Exception:
                pass  # absent or backend gone — nothing to remove
        self._remove_file()

    # ---- encrypted-file fallback --------------------------------------
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

    def _save_file(self, blob: str) -> None:
        try:
            token = self._fernet().encrypt(blob.encode("utf-8"))
            tmp = self._file_path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(token)
            os.replace(tmp, self._file_path)
        except Exception as exc:
            # Refuse to persist in plaintext if encryption is unavailable — the
            # user simply re-logs in next launch, which is the safe failure mode.
            logging.warning(f"Could not persist session securely ({exc}); not saving tokens.")

    def _load_file(self) -> dict | None:
        try:
            if not os.path.exists(self._file_path):
                return None
            with open(self._file_path, "rb") as fh:
                token = fh.read()
            return json.loads(self._fernet().decrypt(token).decode("utf-8"))
        except Exception as exc:
            logging.info(f"No restorable session in encrypted file ({exc}).")
            return None

    def _remove_file(self) -> None:
        try:
            os.remove(self._file_path)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logging.debug(f"Could not remove session file: {exc}")
