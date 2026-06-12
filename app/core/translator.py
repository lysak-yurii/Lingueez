"""DeepL translation, GUI-free."""
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


class TranslationError(Exception):
    pass


def code_to_language(code):
    for name, c in DEEPL_LANGUAGE_CODES.items():
        if c == code:
            return name
    return None


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


def translate(word, target_language, source_language=None):
    """Translate *word* into *target_language* (a language name).

    *source_language* of None or "Detect language" lets DeepL auto-detect.
    Returns (translation, detected_source_language_name).
    Raises TranslationError on failure.
    """
    if not word.strip():
        raise TranslationError("Please enter a word to translate.")
    if target_language not in DEEPL_LANGUAGE_CODES:
        raise TranslationError(f"Unsupported target language: {target_language}")

    settings = load_settings()
    api_key = settings.get("api_key", "")
    api_url = settings.get("api_url", "https://api.deepl.com/v2/translate")
    if not api_key or api_key == "YOUR_DEEPL_API_KEY_HERE":
        raise TranslationError("DeepL API key is not set. Configure it in Settings → APIs → DeepL.")

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
