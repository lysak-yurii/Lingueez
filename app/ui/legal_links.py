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

"""Open the hosted legal documents with an automatic fallback.

The Privacy Policy / Terms live at the custom domain (``lingueez.app``) but each doc is
also a file in the GitHub repo, which GitHub renders as a page. If the custom domain is
ever unreachable (e.g. the domain lapses or DNS breaks), the in-app links transparently
fall back to the repo file so they never dead-end. The reachability probe runs on a
worker thread so the click never blocks the UI; the browser opens on the GUI thread.
"""
import socket
from urllib.parse import urlparse

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from app.ui.workers import run_in_thread
from app.version import (PRIVACY_URL, PRIVACY_URL_FALLBACK, TERMS_URL,
                         TERMS_URL_FALLBACK)

# Each primary (custom-domain) URL mapped to its GitHub repo-file fallback.
_FALLBACKS = {
    PRIVACY_URL: PRIVACY_URL_FALLBACK,
    TERMS_URL: TERMS_URL_FALLBACK,
}


def host_reachable(url, timeout=2.5):
    """True if the URL's host accepts a TLS connection within *timeout* seconds.
    A cheap liveness probe (no HTTP request) reused by the About dialog."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        with socket.create_connection((host, 443), timeout=timeout):
            return True
    except OSError:
        return False


_host_reachable = host_reachable  # backwards-compatible alias


def open_legal(url):
    """Open *url* in the browser, falling back to its mirror if the primary host is
    unreachable. URLs without a known mirror are opened directly."""
    fallback = _FALLBACKS.get(url)
    if not fallback:
        QDesktopServices.openUrl(QUrl(url))
        return

    def _open(target):  # runs on the GUI thread (run_in_thread marshals the result)
        QDesktopServices.openUrl(QUrl(target))

    run_in_thread(
        lambda: url if _host_reachable(url) else fallback,
        on_result=_open,
        on_error=lambda *_: _open(fallback),
    )
