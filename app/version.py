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

APP_VERSION = "2.0.0"
BUILD_NUMBER = "2026062706"
APP_NAME = "Lingueez"
APP_ID = "Lingueez"  # WM_CLASS / desktop-file basename

# Hosted legal docs: primary (custom domain) + GitHub-repo fallback, picked by
# app/ui/legal_links.py when the primary is unreachable.
PRIVACY_URL = "https://lingueez.app/legal/privacy-policy"
TERMS_URL = "https://lingueez.app/legal/terms-of-service"
PRIVACY_URL_FALLBACK = "https://github.com/lysak-yurii/Lingueez/blob/main/docs/legal/privacy-policy.md"
TERMS_URL_FALLBACK = "https://github.com/lysak-yurii/Lingueez/blob/main/docs/legal/terms-of-service.md"

# Website + contact (shown in the About dialog). The Website link only appears when
# the domain is actually reachable.
WEBSITE_URL = "https://lingueez.app"
CONTACT_EMAIL = "support@lingueez.app"

# Optional financial support. These are hosted pages opened in the user's browser —
# the app never embeds a payment form.
# TODO(support): confirm the GitHub Sponsors profile is live at this handle.
SPONSORS_URL = "https://github.com/sponsors/lysak-yurii"  # TODO(support)
# Live Stripe Payment Link (pay-what-you-want, one-time).
DONATE_URL = "https://buy.stripe.com/14A8wRd41a8f1aI6rw43S02"

# Bumped whenever the Terms/Privacy change materially enough to require users to
# re-accept. The stored "policy_accepted_version" is compared against this on launch
# for signed-in accounts; a higher value here re-triggers the consent gate (see
# MainWindow._maybe_require_policy_consent).
POLICY_VERSION = "1.0"


def policy_needs_acceptance(stored_version: str) -> bool:
    """True when the user's last-accepted policy version is missing or older than
    the current POLICY_VERSION. Versions compare as dotted integer tuples so
    "1.10" > "1.2"; an empty/unparseable stored value parses to () and is < any
    real version, so a never-accepted state also returns True."""
    def parse(v):
        try:
            return tuple(int(p) for p in str(v).split("."))
        except (ValueError, AttributeError):
            return ()
    return parse(stored_version) < parse(POLICY_VERSION)
