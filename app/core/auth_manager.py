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

"""User-account session management on top of Supabase Auth (GoTrue).

Wraps the shared :class:`SupabaseClient`'s auth client. Every method returns an
``(ok: bool, message: Optional[str])`` tuple and never raises into the UI, so
callers can drive it straight from a background worker and toast the message.

Sessions are persisted through :class:`~app.core.secure_store.SecureStore`
(OS keychain, encrypted-file fallback) — *not* through GoTrue's own storage — so
we keep full control of where the refresh token lives. On every successful sign
in / refresh we re-stamp the access token onto the shared client so Row-Level
Security resolves ``auth.uid()`` to this user for sync *and* direct CRUD.

Google sign-in uses the standard desktop pattern: a short-lived loopback HTTP
server on ``127.0.0.1:LOOPBACK_PORT`` catches the PKCE ``?code=`` redirect, which
is then exchanged for a session. ``LOOPBACK_PORT`` must be registered in the
Supabase project's Auth → Redirect URLs.
"""
import logging
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

from app.core.accounts import get_account_registry
from app.core.secure_store import SecureStore
from app.core.supabase_client import get_supabase
from app.i18n import tr

# Must exactly match a redirect URL registered in Supabase
# (Auth → URL Configuration → Redirect URLs): http://127.0.0.1:53682
LOOPBACK_PORT = 53682
LOOPBACK_REDIRECT = f"http://127.0.0.1:{LOOPBACK_PORT}"

Result = Tuple[bool, Optional[str]]

# restore_session outcomes
RESTORE_OK = "ok"               # a valid session was re-established
RESTORE_NONE = "none"           # nothing remembered — stay local-only
RESTORE_NEEDS_REAUTH = "reauth"  # an account is remembered but its token is stale


class _OAuthCatcher(HTTPServer):
    """One-shot loopback server that captures the OAuth redirect's code."""
    code: Optional[str] = None
    error: Optional[str] = None

    def wait_for_code(self, timeout: float) -> Optional[str]:
        self.timeout = 0.5
        deadline = time.time() + timeout
        while self.code is None and self.error is None and time.time() < deadline:
            self.handle_request()  # blocks up to self.timeout
        return self.code


class _OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server API)
        qs = parse_qs(urlparse(self.path).query)
        self.server.code = qs.get("code", [None])[0]
        self.server.error = (qs.get("error_description") or qs.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = self.server.code is not None
        heading = "✓ Signed in" if ok else "Sign-in failed"
        sub = ("You can close this tab and return to Lingueez."
               if ok else "You can close this tab and try again in the app.")
        self.wfile.write(
            f"<!doctype html><html><body style='font-family:sans-serif;"
            f"text-align:center;margin-top:4rem'><h2>{heading}</h2>"
            f"<p>{sub}</p></body></html>".encode("utf-8"))

    def log_message(self, *args):  # silence default stderr logging
        pass


class AuthManager:
    """Sign in / up / out, session restore, and account info for the app."""

    def __init__(self, supabase=None):
        self.sb = supabase or get_supabase()
        self.store = SecureStore()
        self.registry = get_account_registry()
        self._session = None
        self._user = None

    # ---- state ---------------------------------------------------------
    def is_logged_in(self) -> bool:
        return self._session is not None

    def current_user(self) -> Optional[str]:
        """Signed-in user's email, or None."""
        return getattr(self._user, "email", None) if self._user else None

    def current_user_id(self) -> Optional[str]:
        return getattr(self._user, "id", None) if self._user else None

    def current_user_name(self) -> Optional[str]:
        """Display name from the signed-in user's metadata (set at sign-up, or by
        Google), falling back to the email. Read from the in-memory session user —
        no network/database call."""
        if not self._user:
            return None
        meta = getattr(self._user, "user_metadata", None) or {}
        for key in ("display_name", "full_name", "name"):
            value = meta.get(key)
            if value:
                return value
        return getattr(self._user, "email", None)

    # ---- email + password ---------------------------------------------
    def sign_in(self, email: str, password: str) -> Result:
        auth = self.sb.get_auth()
        if auth is None:
            return False, self._not_configured()
        try:
            res = auth.sign_in_with_password({"email": email.strip(), "password": password})
        except Exception as exc:
            return False, self._friendly(exc)
        if not getattr(res, "session", None):
            return False, tr("Sign-in failed. Check your email and password.")
        self._on_session(res.session)
        return True, None

    def sign_up(self, email: str, password: str, name: str = "") -> Result:
        auth = self.sb.get_auth()
        if auth is None:
            return False, self._not_configured()
        try:
            credentials = {"email": email.strip(), "password": password}
            name = (name or "").strip()
            if name:
                # Stored as user_metadata; `display_name` is what the Supabase
                # dashboard shows (matching how a Google account's name appears),
                # with full_name/name set too for clients that read those.
                credentials["options"] = {"data": {
                    "display_name": name, "full_name": name, "name": name}}
            res = auth.sign_up(credentials)
        except Exception as exc:
            return False, self._friendly(exc)
        if getattr(res, "session", None):
            # Email confirmation disabled on the project — straight in.
            self._on_session(res.session)
            return True, None
        # No session. Tell two cases apart:
        #  • Duplicate sign-up. To resist email enumeration, GoTrue doesn't error on
        #    an existing address — it returns an obfuscated user with an empty
        #    identities list. Surface that as a clear "already registered" instead.
        user = getattr(res, "user", None)
        identities = getattr(user, "identities", None) if user else None
        if identities is not None and len(identities) == 0:
            return False, tr("That email is already registered. Try signing in instead.")
        #  • Confirmation is ON: Supabase emailed a 6-digit code. We're still logged
        #    out, so the dialog moves to its verify step and calls verify_signup_otp().
        return True, tr("We emailed you a 6-digit code. Enter it to finish signing up.")

    def verify_signup_otp(self, email: str, token: str) -> Result:
        """Confirm a brand-new account with the 6-digit code from the signup email
        (the desktop-friendly alternative to clicking a confirmation link)."""
        auth = self.sb.get_auth()
        if auth is None:
            return False, self._not_configured()
        try:
            res = auth.verify_otp({
                "email": email.strip(),
                "token": token.strip(),
                "type": "signup",
            })
        except Exception as exc:
            return False, self._friendly(exc)
        if not getattr(res, "session", None):
            return False, tr("That code didn't work. Check it and try again.")
        self._on_session(res.session)
        return True, None

    def reset_password(self, email: str) -> Result:
        """Email a 6-digit password-reset code (link-free, desktop-friendly).
        Requires the project's "Reset password" template to use {{ .Token }}."""
        auth = self.sb.get_auth()
        if auth is None:
            return False, self._not_configured()
        try:
            auth.reset_password_for_email(email.strip())
        except Exception as exc:
            return False, self._friendly(exc)
        return True, tr("If that account exists, a 6-digit reset code is on its way.")

    def verify_recovery_otp(self, email: str, token: str, new_password: str) -> Result:
        """Finish a password reset: exchange the 6-digit recovery code for a
        session, then set the new password. Leaves the user signed in."""
        auth = self.sb.get_auth()
        if auth is None:
            return False, self._not_configured()
        try:
            res = auth.verify_otp({
                "email": email.strip(),
                "token": token.strip(),
                "type": "recovery",
            })
        except Exception as exc:
            return False, self._friendly(exc)
        if not getattr(res, "session", None):
            return False, tr("That code didn't work. Check it and try again.")
        # The recovery session lets us set a new password for this account.
        self._on_session(res.session)
        try:
            auth.update_user({"password": new_password})
        except Exception as exc:
            return False, self._friendly(exc)
        return True, None

    def resend_confirmation(self, email: str) -> Result:
        auth = self.sb.get_auth()
        if auth is None:
            return False, self._not_configured()
        try:
            auth.resend({"type": "signup", "email": email.strip()})
        except Exception as exc:
            return False, self._friendly(exc)
        return True, tr("Confirmation email re-sent.")

    # ---- Google OAuth (desktop loopback + PKCE) -----------------------
    def sign_in_with_google(self, timeout: float = 180.0) -> Result:
        """Blocking; run on a worker thread. Opens the system browser, catches
        the redirect on the loopback port, and exchanges the code for a session."""
        auth = self.sb.get_auth()
        if auth is None:
            return False, self._not_configured()
        try:
            server = _OAuthCatcher(("127.0.0.1", LOOPBACK_PORT), _OAuthHandler)
        except OSError as exc:
            return False, tr("Could not start the local sign-in helper on port {port} "
                             "({error}). Close whatever is using it and retry.").format(
                                 port=LOOPBACK_PORT, error=exc)
        try:
            try:
                resp = auth.sign_in_with_oauth({
                    "provider": "google",
                    "options": {
                        "redirect_to": LOOPBACK_REDIRECT,
                        "skip_browser_redirect": True,
                    },
                })
            except Exception as exc:
                return False, self._friendly(exc)

            url = getattr(resp, "url", None)
            if not url:
                return False, tr("Could not start Google sign-in.")
            webbrowser.open(url)

            code = server.wait_for_code(timeout)
            if server.error:
                return False, tr("Google sign-in failed: {error}").format(error=server.error)
            if not code:
                return False, tr("Google sign-in was cancelled or timed out.")
            try:
                res = auth.exchange_code_for_session({"auth_code": code})
            except Exception as exc:
                return False, self._friendly(exc)
            if not getattr(res, "session", None):
                return False, tr("Google sign-in failed.")
            self._on_session(res.session)
            return True, None
        finally:
            try:
                server.server_close()
            except Exception:
                pass

    # ---- leaving / removing accounts ----------------------------------
    def sign_out_to_local(self) -> Result:
        """Leave the active account and go local-only, but keep it *remembered*
        so it can be switched back to without re-entering the password. The refresh
        token is intentionally not revoked server-side (that's ``forget_account``)."""
        self._session = None
        self._user = None
        self.sb.set_auth_token(None)  # revert PostgREST to the anon key
        self.registry.set_active(None)
        return True, None

    def forget_account(self, uid: str) -> Result:
        """Remove an account from this device: delete its stored token and registry
        entry. If it is the active session, revoke it remotely (best effort) and
        drop to local-only."""
        if uid and uid == self.current_user_id():
            auth = self.sb.get_auth()
            try:
                if auth is not None:
                    auth.sign_out()
            except Exception as exc:
                logging.info(f"Remote sign-out failed (clearing locally anyway): {exc}")
            self._session = None
            self._user = None
            self.sb.set_auth_token(None)
        self.store.clear(uid)
        self.registry.remove(uid)
        return True, None

    # ---- session lifecycle --------------------------------------------
    def restore_session(self) -> str:
        """Re-establish the active account's saved session on startup. Returns one
        of ``RESTORE_OK`` / ``RESTORE_NONE`` / ``RESTORE_NEEDS_REAUTH`` so the UI can
        tell "nothing to restore" apart from "an account is remembered but its token
        expired" (which deserves a visible prompt rather than a silent drop to
        local-only)."""
        auth = self.sb.get_auth()
        if auth is None:
            return RESTORE_NONE
        if self._migrate_legacy_session():
            return RESTORE_OK
        uid = self.registry.get_active()
        if not uid:
            return RESTORE_NONE
        data = self.store.load(uid)
        if not data or not data.get("refresh_token"):
            self.registry.mark_needs_reauth(uid, True)
            return RESTORE_NEEDS_REAUTH
        try:
            res = auth.set_session(data.get("access_token") or "", data["refresh_token"])
        except Exception as exc:
            logging.info(f"Stored session for active account is no longer valid ({exc}).")
            self.registry.mark_needs_reauth(uid, True)
            return RESTORE_NEEDS_REAUTH
        if not getattr(res, "session", None):
            self.registry.mark_needs_reauth(uid, True)
            return RESTORE_NEEDS_REAUTH
        self._on_session(res.session)
        logging.info("Restored Supabase session for %s", self.current_user())
        return RESTORE_OK

    def switch_to(self, uid: str) -> Result:
        """Activate a remembered account from its stored session — the no-password
        fast-switch path. Marks the account as needing re-auth (and reports it) when
        the stored token is gone or stale."""
        auth = self.sb.get_auth()
        if auth is None:
            return False, self._not_configured()
        data = self.store.load(uid)
        stale_msg = tr("Your saved sign-in for this account expired. Sign in again.")
        if not data or not data.get("refresh_token"):
            self.registry.mark_needs_reauth(uid, True)
            return False, stale_msg
        try:
            res = auth.set_session(data.get("access_token") or "", data["refresh_token"])
        except Exception as exc:
            logging.info(f"Stored session for {uid} is no longer valid ({exc}).")
            self.registry.mark_needs_reauth(uid, True)
            return False, stale_msg
        if not getattr(res, "session", None):
            self.registry.mark_needs_reauth(uid, True)
            return False, stale_msg
        self._on_session(res.session)
        return True, None

    def _migrate_legacy_session(self) -> bool:
        """One-time upgrade: fold a pre-multi-account single-slot session into the
        uid-keyed store + registry so existing installs stay signed in. Returns True
        when it migrated *and* left the user logged in."""
        if self.registry.get_active():
            return False  # already on the multi-account model
        legacy = self.store.load_legacy()
        if not legacy or not legacy.get("refresh_token"):
            return False
        auth = self.sb.get_auth()
        if auth is None:
            return False
        try:
            res = auth.set_session(legacy.get("access_token") or "", legacy["refresh_token"])
            session = getattr(res, "session", None)
        except Exception as exc:
            logging.info(f"Could not migrate legacy session ({exc}); discarding it.")
            self.store.clear_legacy()
            return False
        if session is None:
            self.store.clear_legacy()
            return False
        self._on_session(session)   # saves under the uid + marks it active
        self.store.clear_legacy()
        logging.info("Migrated legacy session to per-account store for %s", self.current_user())
        return True

    def refresh_if_needed(self) -> None:
        """Proactively refresh the access token before a sync run; a stale token
        would 401 every PostgREST call. Safe no-op when logged out."""
        if not self.is_logged_in():
            return
        auth = self.sb.get_auth()
        if auth is None:
            return
        try:
            res = auth.refresh_session()
            if getattr(res, "session", None):
                self._on_session(res.session)
        except Exception as exc:
            logging.warning(f"Token refresh failed: {exc}")

    # ---- internals -----------------------------------------------------
    def _on_session(self, session) -> None:
        self._session = session
        self._user = getattr(session, "user", None)
        self.sb.set_auth_token(session.access_token)
        uid = self.current_user_id()
        if not uid:
            return
        try:
            self.store.save(uid, {
                "access_token": session.access_token,
                "refresh_token": session.refresh_token,
            })
            meta = getattr(self._user, "user_metadata", None) or {}
            name = next((meta.get(k) for k in ("display_name", "full_name", "name")
                         if meta.get(k)), None)
            self.registry.upsert(uid, self.current_user(), name)
            self.registry.set_active(uid)
            self.registry.mark_needs_reauth(uid, False)
        except Exception as exc:
            logging.warning(f"Could not persist session: {exc}")

    @staticmethod
    def _friendly(exc: Exception) -> str:
        """Turn a raw GoTrue/network exception into a localized, user-facing message.
        The server's own messages are English-only, so map the common ones to tr()
        strings; fall back to the raw text (untranslated) only for the unexpected."""
        raw = (getattr(exc, "message", None) or str(exc) or "").strip()
        low = raw.lower()
        if "invalid login credentials" in low:
            return tr("Wrong email or password.")
        if "unable to validate email address" in low or "invalid format" in low:
            return tr("That doesn't look like a valid email address.")
        if "email not confirmed" in low:
            return tr("Your email isn't confirmed yet. Enter the 6-digit code we emailed you.")
        if "already registered" in low or "already been registered" in low:
            return tr("That email is already registered. Try signing in instead.")
        if "rate limit" in low or "too many requests" in low or "for security purposes" in low:
            return tr("Too many attempts. Please wait a minute and try again.")
        if "expired" in low or "invalid" in low and ("token" in low or "otp" in low or "code" in low):
            return tr("That code didn't work. Check it and try again.")
        if "password should be at least" in low or "password should be" in low:
            return tr("Your password is too short — use at least 6 characters.")
        if "signups not allowed" in low or "signup is disabled" in low:
            return tr("Sign-ups are disabled on this server.")
        if any(k in low for k in ("network", "connection", "timed out", "timeout",
                                  "getaddrinfo", "name or service not known",
                                  "failed to establish", "temporary failure", "unreachable")):
            return tr("Can't reach the server. Check your internet connection.")
        # Unexpected server message: tr() leaves unknown strings unchanged, so this
        # shows the original text rather than a key.
        return tr(raw) if raw else tr("Something went wrong.")

    @staticmethod
    def _not_configured() -> str:
        return tr("Cloud sync is not configured yet. Add the Supabase URL and key "
                  "in Settings → Sync first.")


# ---------------------------------------------------------------------------
# Process-wide shared AuthManager (bound to the shared SupabaseClient).
# ---------------------------------------------------------------------------
_shared_auth: Optional[AuthManager] = None
_shared_lock = threading.Lock()


def get_auth_manager() -> AuthManager:
    """Return the process-wide AuthManager (created on first use)."""
    global _shared_auth
    with _shared_lock:
        if _shared_auth is None:
            _shared_auth = AuthManager(get_supabase())
        return _shared_auth
