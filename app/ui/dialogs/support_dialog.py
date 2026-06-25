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

"""Optional 'Support Lingueez' dialog — a non-intrusive outbound link launcher.

Lingueez is free and open-source; this dialog simply offers two hosted donation
pages (GitHub Sponsors and a Stripe Payment Link) opened in the user's browser.
It embeds no payment form, stores no state, and never gates app functionality.
"""
from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

from app.i18n import tr
from app.ui import icons
from app.ui.dialogs.base import FramelessDialog
from app.version import DONATE_URL, SPONSORS_URL


class SupportDialog(FramelessDialog):
    """Two hosted-donation links (GitHub Sponsors + Stripe) with honest copy."""

    def __init__(self, parent=None):
        super().__init__(parent, title=tr("Support Lingueez"))
        self.setMinimumWidth(420)
        c = self.colors

        # --- Heading: heart glyph + free/open-source framing ---
        head = QHBoxLayout()
        head.setSpacing(10)
        glyph = QLabel()
        glyph.setPixmap(icons.icon("heart", c["accent"], 22).pixmap(QSize(22, 22)))
        head.addWidget(glyph, 0, Qt.AlignVCenter)
        title = QLabel(tr("Lingueez is free and open-source."))
        title.setWordWrap(True)
        title.setStyleSheet(f"font-size:15px; font-weight:600; color:{c['text']};")
        head.addWidget(title, 1)
        self.content_layout.addLayout(head)

        # Honest, non-guilting, concrete: names what support actually pays for,
        # separates "free software" from "optional support", no pressure.
        body = QLabel(tr(
            "If you enjoy Lingueez and find it useful, a one-off contribution helps "
            "cover the servers behind optional cloud sync and supports continued "
            "development. There's no paywall — every feature stays free either way."))
        body.setWordWrap(True)
        body.setStyleSheet(f"color:{c['text_dim']}; font-size:12.5px;")
        self.content_layout.addWidget(body)

        # --- The two channels (max two buttons, per OSS best practice). Stripe is
        # primary: card / Apple Pay / Google Pay with no account to create, which is
        # the path most language-learner users will take. GitHub Sponsors is the
        # secondary, developer-oriented option (needs a GitHub account). ---
        stripe = QPushButton(tr("Support Lingueez's development"),
                             objectName="primaryButton")
        stripe.setIcon(icons.icon("heart", "#ffffff", 15))
        stripe.setIconSize(QSize(15, 15))
        stripe.setCursor(Qt.PointingHandCursor)
        stripe.setDefault(True)
        stripe.clicked.connect(lambda: self._open(DONATE_URL))
        self.content_layout.addWidget(stripe)

        sponsors = QPushButton(tr("GitHub Sponsors"), objectName="tonalButton")
        sponsors.setCursor(Qt.PointingHandCursor)
        sponsors.clicked.connect(lambda: self._open(SPONSORS_URL))
        self.content_layout.addWidget(sponsors)

        # Removes the common "is this a subscription?" fear. Scoped to the Stripe
        # path, which is genuinely one-time (Sponsors lets the supporter choose).
        note = QLabel(tr("The Stripe option is one-time — no subscription. "
                         "Payments are handled securely by Stripe or GitHub."))
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{c['text_dim']}; font-size:11.5px;")
        self.content_layout.addWidget(note)

        # Absorb any extra height here so the widgets above stay packed together
        # instead of the layout spreading them apart when the dialog is resized.
        self.content_layout.addStretch(1)

        # --- Close ---
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton(tr("Close"))
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.reject)
        row.addWidget(close)
        self.content_layout.addLayout(row)

    def _open(self, url):
        QDesktopServices.openUrl(QUrl(url))
