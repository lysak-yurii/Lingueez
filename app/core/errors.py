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
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Domain-level exceptions shared between the data layer and the UI."""

from typing import Optional


class DuplicateWordError(Exception):
    """Raised when a write would violate the UNIQUE(Word1, Word2) constraint.

    Carries the offending word pair and, when known, the ID of the existing row
    so the UI can offer to open it instead of showing a raw constraint error.
    """

    def __init__(self, word1: str, word2: str, existing_id: Optional[str] = None):
        self.word1 = word1
        self.word2 = word2
        self.existing_id = existing_id
        super().__init__(f"{word1} – {word2} already exists")
