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

"""Sign in / sign up dialog (email+password and Google), built on FramelessDialog.

Open registration with email verification by **6-digit code** (not a confirmation
link), which is the desktop-friendly pattern: after sign-up Supabase emails a code,
the dialog switches to a verify step, and the typed code is exchanged for a session.

All network calls run on the worker pool (``run_in_thread``) so the UI never blocks;
results come back as ``(ok, message)`` tuples and are surfaced via toast. Emits
:attr:`authenticated` once a session is established so the main window / settings can
refresh their account status.
"""
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLayout, QLineEdit,
                               QPushButton, QWidget)

from app.config import load_settings, save_settings
from app.core.auth_manager import get_auth_manager
from app.i18n import tr
from app.ui.dialogs.base import FramelessDialog
from app.ui.toast import show_toast
from app.ui.workers import run_in_thread
from app.version import POLICY_VERSION, PRIVACY_URL, TERMS_URL


class AccountDialog(FramelessDialog):
    authenticated = Signal()

    # Password reset emails the user a recovery code, so it needs an SMTP sender
    # (and the "Reset password" template set to {{ .Token }}). With no SMTP wired
    # up we hide "Forgot password?"; flip this to True once email actually sends.
    _PASSWORD_RESET_ENABLED = False

    def __init__(self, parent, auth=None, prefill_name=""):
        super().__init__(parent, title=tr("Sign in"))
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.auth = auth or get_auth_manager()
        self._owner = parent
        # When upgrading an offline profile we arrive with its name; open straight on
        # the create-account form (pre-filled) since that's the intent — the user can
        # still toggle to "I already have an account".
        self._mode = "sign_up" if prefill_name else "sign_in"
        self._verify_email = None  # email awaiting its 6-digit code

        layout = self.content_layout

        # Sign-up only: the display name, stored on the account so it shows up
        # (e.g. in the Supabase dashboard) just like a Google account's name.
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("Name"))
        if prefill_name:
            self.name.setText(prefill_name)
        self.name.returnPressed.connect(self._submit)
        self.name.setVisible(False)
        layout.addWidget(self.name)

        self.email = QLineEdit()
        self.email.setPlaceholderText(tr("Email"))
        self.email.setMinimumWidth(340)  # anchors width to ~380 under SetFixedSize
        layout.addWidget(self.email)

        self.password = QLineEdit()
        self.password.setPlaceholderText(tr("Password"))
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self._submit)
        layout.addWidget(self.password)

        # Sign-up only: re-type the password to catch typos before the account is made.
        self.confirm_password = QLineEdit()
        self.confirm_password.setPlaceholderText(tr("Confirm password"))
        self.confirm_password.setEchoMode(QLineEdit.Password)
        self.confirm_password.returnPressed.connect(self._submit)
        self.confirm_password.setVisible(False)
        layout.addWidget(self.confirm_password)

        # Verify step only: the 6-digit code from the sign-up email.
        self.code = QLineEdit()
        self.code.setPlaceholderText(tr("6-digit code"))
        self.code.returnPressed.connect(self._submit)
        self.code.setVisible(False)
        layout.addWidget(self.code)

        # Inline feedback (wrong password, "already registered", code prompts…). A
        # label inside the dialog is far more reliable than a toast: toasts anchor to
        # their parent's corner, so on this small frameless modal they land off-screen
        # or behind the window and the user never sees them.
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        layout.addWidget(self.status)

        # Terms/Privacy consent, directly above the action button (the conventional
        # spot). It is ONE persistent row whose contents change per mode — rather than
        # two widgets that show/hide — so toggling sign-in ↔ create account never makes
        # it vanish (a word-wrapped label hidden then re-shown under this frameless
        # dialog's SetFixedSize doesn't repaint reliably on Wayland). Always shown on
        # the credential screens; hidden only on the verify/reset code steps. See
        # _apply_mode for the per-mode contents:
        #   • Create account: a REQUIRED checkbox + "I agree…".
        #   • Sign-in (incl. Google): no checkbox, a passive "By continuing…" notice.
        consent_row = QHBoxLayout()
        self.consent = QCheckBox()
        self.consent.setCursor(Qt.PointingHandCursor)
        consent_row.addWidget(self.consent, 0, Qt.AlignTop)
        self.consent_label = QLabel()
        from app.ui.legal_links import open_legal
        self.consent_label.linkActivated.connect(open_legal)
        self.consent_label.setWordWrap(True)
        self.consent_label.setObjectName("dimLabel")
        consent_row.addWidget(self.consent_label, 1)
        self.consent_widget = QWidget()
        self.consent_widget.setLayout(consent_row)
        layout.addWidget(self.consent_widget)

        self.primary = QPushButton(objectName="primaryButton")
        self.primary.setCursor(Qt.PointingHandCursor)
        self.primary.setDefault(True)
        self.primary.clicked.connect(self._submit)
        layout.addWidget(self.primary)

        self.sep = QLabel(tr("or"))
        self.sep.setObjectName("dimLabel")
        self.sep.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.sep)

        self.google_btn = QPushButton(tr("Sign in with Google"))
        self.google_btn.setCursor(Qt.PointingHandCursor)
        self.google_btn.clicked.connect(self._google)
        layout.addWidget(self.google_btn)

        row = QHBoxLayout()
        self.toggle_btn = QPushButton()
        self.toggle_btn.setFlat(True)
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self._toggle_mode)
        row.addWidget(self.toggle_btn)
        row.addStretch(1)
        self.forgot_btn = QPushButton(tr("Forgot password?"))
        self.forgot_btn.setFlat(True)
        self.forgot_btn.setCursor(Qt.PointingHandCursor)
        self.forgot_btn.clicked.connect(self._forgot)
        row.addWidget(self.forgot_btn)
        self.resend_btn = QPushButton(tr("Resend code"))
        self.resend_btn.setFlat(True)
        self.resend_btn.setCursor(Qt.PointingHandCursor)
        self.resend_btn.clicked.connect(self._resend)
        self.resend_btn.setVisible(False)
        row.addWidget(self.resend_btn)
        layout.addLayout(row)

        self._apply_mode()
        self.email.setFocus()
        # Always hug the content: the visible field set changes per mode (sign-up adds
        # name + confirm; code steps hide most) and the status label toggles. SetFixedSize
        # re-fits the window on every layout change — shrinking included, which a soft
        # adjustSize() won't do for a frameless window on Wayland.
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    # ---- mode ----------------------------------------------------------
    def _apply_mode(self):
        verify = self._mode == "verify"      # confirm a new sign-up
        reset = self._mode == "reset"        # set a new password via code
        sign_in = self._mode == "sign_in"
        sign_up = self._mode == "sign_up"
        code_step = verify or reset

        self.name.setVisible(sign_up)              # display name only when signing up
        self.email.setReadOnly(code_step)
        self.password.setVisible(not verify)  # hidden when confirming, shown as
        self.password.setPlaceholderText(     # the *new* password when resetting
            tr("New password") if reset else tr("Password"))
        self.confirm_password.setVisible(sign_up)  # re-type only when creating an account
        self.code.setVisible(code_step)
        self.sep.setVisible(not code_step)
        self.google_btn.setVisible(not code_step)
        self.forgot_btn.setVisible(sign_in and self._PASSWORD_RESET_ENABLED)
        self.resend_btn.setVisible(code_step)
        # One persistent consent row, always shown on the credential screens (hidden
        # only on the code steps). Its contents change per mode: a required checkbox +
        # "I agree…" when creating an account; no checkbox + a passive "By continuing…"
        # notice on the sign-in screen (which also covers Google).
        self.consent_widget.setVisible(not code_step)
        if not code_step:
            self.consent.setVisible(sign_up)
            text = (
                'I agree to the <a href="{terms}">Terms of Service</a> and '
                '<a href="{privacy}">Privacy Policy</a>.' if sign_up else
                'By continuing, you agree to the <a href="{terms}">Terms of Service</a> '
                'and <a href="{privacy}">Privacy Policy</a>.')
            self.consent_label.setText(
                tr(text).format(terms=TERMS_URL, privacy=PRIVACY_URL))

        if verify:
            self.setWindowTitle(tr("Confirm your email"))
            self.primary.setText(tr("Verify code"))
            self.toggle_btn.setText(tr("Use a different email"))
        elif reset:
            self.setWindowTitle(tr("Reset password"))
            self.primary.setText(tr("Set new password"))
            self.toggle_btn.setText(tr("Back to sign in"))
        elif sign_in:
            self.setWindowTitle(tr("Sign in"))
            self.primary.setText(tr("Sign in"))
            self.toggle_btn.setText(tr("Create an account"))
        else:  # sign_up
            self.setWindowTitle(tr("Create account"))
            self.primary.setText(tr("Create account"))
            self.toggle_btn.setText(tr("I already have an account"))

    def _toggle_mode(self):
        if self._mode == "verify":
            # Abandon the pending verification and start over.
            self._verify_email = None
            self.code.clear()
            self._mode = "sign_up"
        elif self._mode == "reset":
            self._verify_email = None
            self.code.clear()
            self.password.clear()
            self._mode = "sign_in"
        else:
            self._mode = "sign_up" if self._mode == "sign_in" else "sign_in"
            self.confirm_password.clear()  # don't carry a stale re-type between modes
            self.name.clear()
        self._clear_status()
        self._apply_mode()
        focus = {"verify": self.code, "reset": self.code,
                 "sign_up": self.name}.get(self._mode, self.email)
        focus.setFocus()

    def _set_busy(self, busy):
        for w in (self.primary, self.google_btn, self.toggle_btn, self.forgot_btn,
                  self.resend_btn, self.name, self.email, self.password,
                  self.confirm_password, self.code):
            w.setEnabled(not busy)

    def _set_status(self, text, kind="error"):
        color = {"error": "#e5534b", "info": "#8b949e",
                 "success": "#3fb950"}.get(kind, "#e5534b")
        self.status.setStyleSheet(f"color: {color}; background: transparent;")
        self.status.setText(text or "")
        self.status.setVisible(bool(text))

    def _clear_status(self):
        self.status.clear()
        self.status.setVisible(False)

    # ---- consent -------------------------------------------------------
    def _consent_ok(self):
        """True if the account-creation checkbox is ticked (we also record it for the
        audit trail). Only called for account creation; sign-in/Google aren't blocked."""
        if not self.consent.isChecked():
            self._set_status(
                tr("Please accept the Terms of Service and Privacy Policy to continue."))
            return False
        self._record_consent()
        return True

    def _record_consent(self):
        """Record the latest Terms/Privacy acceptance (version + timestamp) for the
        audit trail. The consent UI is always shown regardless; this is just a log."""
        settings = load_settings()
        settings["policy_accepted_version"] = POLICY_VERSION
        settings["policy_accepted_at"] = datetime.now(timezone.utc).isoformat()
        save_settings(settings)

    # ---- actions -------------------------------------------------------
    def _submit(self):
        self._clear_status()
        if self._mode == "verify":
            self._verify()
            return
        if self._mode == "reset":
            self._reset()
            return
        email, pw = self.email.text().strip(), self.password.text()
        if not email or not pw:
            self._set_status(tr("Enter your email and password."))
            return
        if self._mode == "sign_up":
            # Account creation requires explicit acceptance via the checkbox.
            if not self._consent_ok():
                return
            name = self.name.text().strip()
            if not name:
                self._set_status(tr("Enter your name."))
                return
            if pw != self.confirm_password.text():
                self._set_status(tr("Passwords don't match."))
                return
            self._set_busy(True)
            run_in_thread(self.auth.sign_up, email, pw, name,
                          on_result=self._on_auth_result, on_error=self._on_thread_error)
            return
        self._set_busy(True)
        run_in_thread(self.auth.sign_in, email, pw,
                      on_result=self._on_auth_result, on_error=self._on_thread_error)

    def _verify(self):
        code = self.code.text().strip()
        if not code:
            self._set_status(tr("Enter the 6-digit code from the email."))
            return
        self._set_busy(True)
        run_in_thread(self.auth.verify_signup_otp, self._verify_email, code,
                      on_result=self._on_auth_result, on_error=self._on_thread_error)

    def _reset(self):
        code, new_pw = self.code.text().strip(), self.password.text()
        if not code or not new_pw:
            self._set_status(tr("Enter the code and a new password."))
            return
        self._set_busy(True)
        run_in_thread(self.auth.verify_recovery_otp, self._verify_email, code, new_pw,
                      on_result=self._on_auth_result, on_error=self._on_thread_error)

    def _resend(self):
        if not self._verify_email:
            return
        self._clear_status()
        self._set_busy(True)
        # Resend the right code for the step we're on: a recovery code while
        # resetting, a sign-up confirmation code while verifying.
        fn = (self.auth.reset_password if self._mode == "reset"
              else self.auth.resend_confirmation)
        run_in_thread(fn, self._verify_email,
                      on_result=self._on_simple_result, on_error=self._on_thread_error)

    def _google(self):
        self._clear_status()
        # Google can create an account on first sign-in. In sign-up mode the explicit
        # checkbox applies; on the sign-in screen the passive "By continuing…" notice
        # is the acceptance, so we don't block — consent is recorded on success.
        if self._mode == "sign_up" and not self._consent_ok():
            return
        self._set_status(tr("Opening your browser to sign in with Google…"), "info")
        self._set_busy(True)
        run_in_thread(self.auth.sign_in_with_google,
                      on_result=self._on_auth_result, on_error=self._on_thread_error)

    def _forgot(self):
        self._clear_status()
        email = self.email.text().strip()
        if not email:
            self._set_status(tr("Enter your email above first."))
            return
        self._set_busy(True)
        run_in_thread(self.auth.reset_password, email,
                      on_result=self._on_reset_requested, on_error=self._on_thread_error)

    # ---- results -------------------------------------------------------
    def _on_auth_result(self, result):
        self._set_busy(False)
        ok, msg = result
        if not ok:
            self._set_status(msg or tr("Sign-in failed."))
            return
        if self.auth.is_logged_in():
            # Record acceptance for the paths without the explicit checkbox (email
            # sign-in, Google) once a session is actually established. Idempotent.
            self._record_consent()
            show_toast(self._owner or self, tr("Account"),
                       tr("Signed in as {email}").format(email=self.auth.current_user() or ""),
                       "success")
            self.authenticated.emit()
            self.accept()
        else:
            # Sign-up succeeded but needs email confirmation: move to the code step.
            self._verify_email = self.email.text().strip()
            self._mode = "verify"
            self._apply_mode()
            self.code.setFocus()
            self._set_status(
                msg or tr("Enter the 6-digit code we emailed you."), "info")

    def _on_reset_requested(self, result):
        self._set_busy(False)
        ok, msg = result
        if not ok:
            self._set_status(msg or tr("Couldn't send the code."))
            return
        # Move to the reset step: enter the emailed code + a new password.
        self._verify_email = self.email.text().strip()
        self._mode = "reset"
        self._apply_mode()
        self.code.setFocus()
        self._set_status(
            msg or tr("Enter the reset code we emailed you and a new password."),
            "info")

    def _on_simple_result(self, result):
        self._set_busy(False)
        ok, msg = result
        self._set_status(msg or (tr("Done.") if ok else tr("Failed.")),
                         "info" if ok else "error")

    def _on_thread_error(self, err):
        self._set_busy(False)
        self._set_status(str(err))
