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

"""Lightweight i18n: tr() lookup + ntr() plural selector.

set_language() is called once at startup (before any UI import) from main.py.
All UI files import tr() and wrap string literals with it.
"""

_lang = "en"
_translations: dict = {}
# localized language label -> canonical (English) language name
_lang_reverse: dict = {}

# English month/weekday names — the defaults used when the active locale does
# not provide its own. strftime('%B'/'%A'/'%b') would always emit English (the
# C locale), so dates are formatted through these tables instead.
_EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
              "August", "September", "October", "November", "December"]
_EN_MONTHS_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                   "Sep", "Oct", "Nov", "Dec"]
_EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                "Saturday", "Sunday"]
_EN_WEEKDAYS_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# active locale's date names; replaced in set_language() if the locale supplies
# MONTHS / MONTHS_ABBR / WEEKDAYS / WEEKDAYS_ABBR lists.
_dates = {"months": _EN_MONTHS, "months_abbr": _EN_MONTHS_ABBR,
          "weekdays": _EN_WEEKDAYS, "weekdays_abbr": _EN_WEEKDAYS_ABBR}


def _language_tokens():
    """The set of canonical (English) language names used across the app,
    plus the special combo tokens. Imported lazily to avoid import cycles."""
    names = {"Detect language", "All languages"}
    try:
        from app.core.translator import DEEPL_LANGUAGE_CODES
        names |= set(DEEPL_LANGUAGE_CODES)
    except Exception:
        pass
    try:
        from app.core.audio import lang_codes
        names |= set(lang_codes)
    except Exception:
        pass
    return names


def set_language(lang: str) -> None:
    global _lang, _translations, _lang_reverse, _dates
    _lang = lang
    mod = None
    if lang == "en":
        _translations = {}
    else:
        try:
            import importlib
            mod = importlib.import_module(f"locales.{lang}")
            _translations = mod.TRANSLATIONS
        except Exception:
            _translations = {}
    # Date names come from the locale module when present, else English.
    _dates = {
        "months": getattr(mod, "MONTHS", _EN_MONTHS),
        "months_abbr": getattr(mod, "MONTHS_ABBR", _EN_MONTHS_ABBR),
        "weekdays": getattr(mod, "WEEKDAYS", _EN_WEEKDAYS),
        "weekdays_abbr": getattr(mod, "WEEKDAYS_ABBR", _EN_WEEKDAYS_ABBR),
    }
    # Build the reverse map (localized label -> canonical name) so editable
    # language combos can be read back as the English value stored in the DB.
    _lang_reverse = {tr(name): name for name in _language_tokens()}


def tr(text: str) -> str:
    """Return the translation of *text* for the current language, or *text* itself."""
    return _translations.get(text, text)


def ntr(count: int, one: str, few: str, many: str = None) -> str:
    """Return the right plural form for *count*.

    For English: one (count==1) or few/many otherwise.
    For Ukrainian: uses modular arithmetic — Ukrainian has three plural forms:
      - one  → 1, 21, 31, 41, …  (mod10==1 and mod100 not in 11-14)
      - few  → 2-4, 22-24, …     (mod10 in 2-4 and mod100 not in 11-14)
      - many → everything else (0, 5-20, 11-14, 25-30, …)
    """
    if _lang != "uk":
        return one if count == 1 else (many or few)
    mod100 = count % 100
    if 11 <= mod100 <= 14:
        return many or few
    mod10 = count % 10
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many or few


# --------------------------------------------------------------------------
# Language names: stored/queried in English (the canonical DeepL/gTTS keys),
# only the displayed label is localized — same pattern as word statuses.

def lang_label(name: str) -> str:
    """Canonical (English) language name -> localized display label."""
    return tr(name)


def lang_value(label: str) -> str:
    """Localized display label -> canonical (English) language name."""
    return _lang_reverse.get(label, label)


def fill_lang_combo(combo, names, head=()) -> None:
    """Populate a combo with language items: display = localized label,
    item data = canonical English name (read back with :func:`get_lang`)."""
    for token in head:
        combo.addItem(lang_label(token), token)
    for name in names:
        combo.addItem(lang_label(name), name)


def set_lang(combo, name: str) -> None:
    """Select the item whose canonical language == *name* (English)."""
    idx = combo.findData(name)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setCurrentText(lang_label(name))


def get_lang(combo) -> str:
    """Return the canonical (English) language for the combo's current value,
    whether picked from the list or typed into an editable combo."""
    text = combo.currentText()
    idx = combo.findText(text)
    if idx >= 0:
        data = combo.itemData(idx)
        if data is not None:
            return data
    return lang_value(text)


# --------------------------------------------------------------------------
# Date names: strftime('%B'/'%A'/'%b') only ever yields English, so month and
# weekday names are looked up here instead and formatted per locale.

def month_name(d) -> str:
    """Localized full month name for a date/datetime (replaces strftime %B)."""
    return _dates["months"][d.month - 1]


def month_abbr(d) -> str:
    """Localized abbreviated month name (replaces strftime %b)."""
    return _dates["months_abbr"][d.month - 1]


def weekday_name(d) -> str:
    """Localized weekday name for a date/datetime (replaces strftime %A)."""
    return _dates["weekdays"][d.weekday()]


def weekday_abbr_by_index(index: int) -> str:
    """Localized short weekday name by index (0 = Monday … 6 = Sunday)."""
    return _dates["weekdays_abbr"][index]


def full_date(d) -> str:
    """Localized long date: 'June 13, 2026' (en) / '13 червня 2026' (day-month-year)."""
    if _lang == "en":
        return f"{month_name(d)} {d.day}, {d.year}"
    return f"{d.day} {month_name(d)} {d.year}"
