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

"""Translation, GUI-free.

Two providers are supported:

* **Google Translate** (free, no API key) — the default.
* **DeepL** (paid API key) — opt-in via Settings → Translation & AI → Translation.

When DeepL is selected but a request fails, the translation transparently
falls back to Google so the user still gets a result. The first such fallback
in a process is reported through :func:`set_fallback_listener`; later ones are
silent (the throttle lives here so the listener fires at most once per run).
"""
import logging

import requests

from app.config import load_settings

# Language name -> DeepL code (same set as the original app)
DEEPL_LANGUAGE_CODES = {
    'German': 'DE', 'English': 'EN', 'Ukrainian': 'UK', 'Greek': 'EL',
    'French': 'FR', 'Spanish': 'ES', 'Portuguese': 'PT', 'Italian': 'IT',
    'Dutch': 'NL', 'Polish': 'PL', 'Russian': 'RU', 'Japanese': 'JA',
    'Chinese': 'ZH', 'Bulgarian': 'BG', 'Croatian': 'HR', 'Czech': 'CS',
    'Danish': 'DA', 'Estonian': 'ET', 'Finnish': 'FI', 'Hungarian': 'HU',
    'Latvian': 'LV', 'Lithuanian': 'LT', 'Norwegian': 'NO', 'Romanian': 'RO',
    'Slovak': 'SK', 'Slovenian': 'SL', 'Swedish': 'SV',
}

# Google Translate uses the same ISO 639-1 codes as DeepL, just lowercased.
GOOGLE_LANGUAGE_CODES = {name: code.lower() for name, code in DEEPL_LANGUAGE_CODES.items()}
# Reverse map for turning a detected Google code back into a language name.
_GOOGLE_CODE_TO_NAME = {code: name for name, code in GOOGLE_LANGUAGE_CODES.items()}

GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"


class TranslationError(Exception):
    pass


# --------------------------------------------------------------------------
# Fallback notification (DeepL -> Google), reported once per process
# --------------------------------------------------------------------------

_fallback_listener = None
_deepl_fallback_notified = False


def set_fallback_listener(fn):
    """Register a callable invoked (at most once per run) on a DeepL->Google
    fallback. *fn* receives a short human-readable message and may be called
    from a worker thread, so it must marshal to the GUI thread itself."""
    global _fallback_listener
    _fallback_listener = fn


def _notify_fallback(message):
    global _deepl_fallback_notified
    if _deepl_fallback_notified:
        return
    _deepl_fallback_notified = True
    if _fallback_listener is None:
        return
    try:
        _fallback_listener(message)
    except Exception:  # never let a notification problem break translation
        logging.exception("Translation fallback listener failed")


def code_to_language(code):
    for name, c in DEEPL_LANGUAGE_CODES.items():
        if c == code:
            return name
    return None


# --------------------------------------------------------------------------
# Google Translate (free)
# --------------------------------------------------------------------------

def translate_with_google(word, target_code, source_code="auto"):
    """Translate *word* using Google's free web endpoint.

    *target_code* / *source_code* are lowercase ISO codes; *source_code* may be
    ``"auto"`` to let Google detect the language. Returns
    ``(translation, detected_source_code)``. Raises :class:`TranslationError`.
    """
    params = {
        "client": "gtx",
        "sl": source_code or "auto",
        "tl": target_code,
        "dt": "t",
        "q": word,
    }
    try:
        response = requests.get(
            GOOGLE_TRANSLATE_URL, params=params,
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    except requests.RequestException as exc:
        raise TranslationError(f"Network error during translation: {exc}") from exc

    if response.status_code != 200:
        raise TranslationError(
            f"Google Translate error {response.status_code}: {response.text[:100]}")

    try:
        data = response.json()
        # data[0] is a list of [translated_chunk, original_chunk, ...]; long
        # texts are split across several chunks, so join them all back up.
        translation = "".join(seg[0] for seg in data[0] if seg and seg[0])
        detected = data[2] if len(data) > 2 else None
    except (ValueError, IndexError, TypeError) as exc:
        raise TranslationError(
            f"Failed to parse Google Translate response: {exc}") from exc

    return translation, detected


def _translate_google_by_name(word, target_language, source_language):
    """Google translation keyed by language *names* (the app's UI vocabulary)."""
    target_code = GOOGLE_LANGUAGE_CODES.get(target_language)
    if not target_code:
        raise TranslationError(f"Unsupported target language: {target_language}")
    if source_language and source_language != "Detect language":
        source_code = GOOGLE_LANGUAGE_CODES.get(source_language, "auto")
    else:
        source_code = "auto"

    translation, detected_code = translate_with_google(word, target_code, source_code)
    detected_name = _GOOGLE_CODE_TO_NAME.get(detected_code) if detected_code else None
    return translation, detected_name


# --------------------------------------------------------------------------
# DeepL (paid API key)
# --------------------------------------------------------------------------

def get_usage(api_key=None, api_url=None):
    """DeepL usage for the current billing period.

    Returns (character_count, character_limit). Falls back to the saved
    settings when key/url are not given. Raises TranslationError on failure.
    """
    settings = load_settings()
    api_key = api_key or settings.get("api_key", "")
    api_url = api_url or settings.get("api_url", "https://api.deepl.com/v2/translate")
    if not api_key or api_key == "YOUR_DEEPL_API_KEY_HERE":
        raise TranslationError("DeepL API key is not set.")
    usage_url = api_url.rstrip("/").rsplit("/", 1)[0] + "/usage"
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    try:
        response = requests.get(usage_url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        raise TranslationError(f"Network error: {exc}") from exc
    if response.status_code != 200:
        raise TranslationError(f"Error {response.status_code}: {response.text}")
    data = response.json()
    return int(data.get("character_count", 0)), int(data.get("character_limit", 0))


def _translate_deepl(word, target_language, source_language, settings):
    """DeepL translation. Raises :class:`TranslationError` on any failure."""
    api_key = settings.get("api_key", "")
    api_url = settings.get("api_url", "https://api.deepl.com/v2/translate")
    if not api_key or api_key == "YOUR_DEEPL_API_KEY_HERE":
        raise TranslationError("DeepL API key is not set. Configure it in Settings → Translation & AI → Translation.")

    # DeepL dropped form-body auth_key in November 2025 — the key must be
    # sent as an Authorization header.
    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    params = {
        "text": word,
        "target_lang": DEEPL_LANGUAGE_CODES[target_language],
    }
    if source_language and source_language != "Detect language":
        source_code = DEEPL_LANGUAGE_CODES.get(source_language)
        if source_code:
            params["source_lang"] = source_code

    try:
        response = requests.post(api_url, data=params, headers=headers, timeout=30)
    except requests.RequestException as exc:
        raise TranslationError(f"Network error during translation: {exc}") from exc

    if response.status_code != 200:
        message = f"Error {response.status_code}: {response.text}"
        logging.error(message)
        raise TranslationError(message)

    data = response.json()
    translation = data['translations'][0]['text']
    detected_code = data['translations'][0].get('detected_source_language')
    detected_name = code_to_language(detected_code) if detected_code else None
    return translation, detected_name


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def translate(word, target_language, source_language=None):
    """Translate *word* into *target_language* (a language name).

    *source_language* of None or "Detect language" lets the provider
    auto-detect. Returns (translation, detected_source_language_name).

    The active provider comes from settings (``translation_provider``); when it
    is DeepL and the request fails, translation falls back to free Google.
    Raises :class:`TranslationError` on failure.
    """
    if not word.strip():
        raise TranslationError("Please enter a word to translate.")
    if target_language not in DEEPL_LANGUAGE_CODES:
        raise TranslationError(f"Unsupported target language: {target_language}")

    settings = load_settings()
    provider = str(settings.get("translation_provider", "google")).strip().lower()
    api_key = settings.get("api_key", "")

    if provider == "deepl":
        from app.i18n import tr
        if api_key and api_key != "YOUR_DEEPL_API_KEY_HERE":
            try:
                return _translate_deepl(word, target_language, source_language, settings)
            except TranslationError as exc:
                logging.warning("DeepL translation failed, falling back to Google: %s", exc)
                _notify_fallback(tr("DeepL request failed — using free Google Translate instead."))
        else:
            # DeepL selected but no key configured — fall back, and say so (otherwise the
            # switch to Google is silent and looks like the provider setting is ignored).
            logging.info("DeepL selected but no API key set — using Google Translate.")
            _notify_fallback(tr("DeepL key isn't set — using free Google Translate instead."))

    return _translate_google_by_name(word, target_language, source_language)
