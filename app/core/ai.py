"""AI provider abstraction (definitions and generated texts).

Two interchangeable backends — OpenAI (ChatGPT) and Google Gemini — behind
one facade. The active backend is selected by the ``ai_provider`` setting;
each backend reads its own model/prompt settings (``chatgpt_*`` /
``gemini_*`` keys in settings.cfg) and its API key from .env.

All generation failures are raised as :class:`AIError` with a message fit
for showing to the user directly.
"""
import logging
import os
import threading

from dotenv import load_dotenv

from app.config import load_settings, get_float, get_int
from app.core.backup_management import backup_database
from app.core.database_adapter import DatabaseAdapter

DEFAULT_PROVIDER = "openai"


class AIError(Exception):
    """A generation failure with a user-presentable message."""


# --------------------------------------------------------------- providers

class _Provider:
    """Base class: API-key handling, client caching, error translation."""

    id = ""
    label = ""
    env_key = ""
    settings_prefix = ""

    def __init__(self):
        self._lock = threading.Lock()
        self._client = None
        self._client_key = None

    def api_key(self):
        load_dotenv()
        return (os.getenv(self.env_key) or "").strip()

    def has_api_key(self):
        return bool(self.api_key())

    def reset(self):
        """Drop the cached client (call after the API key changes)."""
        with self._lock:
            self._client = None
            self._client_key = None

    def _cached_client(self):
        key = self.api_key()
        if not key:
            raise AIError(f"{self.label} API key is not set. "
                          f"Configure it in Settings → APIs → AI.")
        with self._lock:
            if self._client is None or self._client_key != key:
                self._client = self._make_client(key)
                self._client_key = key
            return self._client

    def complete(self, prompt, model, max_tokens, temperature, role="user"):
        """Run one prompt; returns non-empty text or raises AIError."""
        if not model:
            raise AIError(f"No {self.label} model configured. "
                          f"Set one in Settings → APIs → AI.")
        try:
            text = self._complete(prompt, model, max_tokens, temperature, role)
        except AIError:
            raise
        except Exception as exc:
            logging.error(f"{self.label} request failed: {exc}")
            raise AIError(self._friendly_error(exc)) from exc
        text = (text or "").strip()
        if not text:
            raise AIError(f"{self.label} returned an empty response. "
                          f"Try again or raise 'Max tokens' in Settings.")
        return text

    # subclasses implement:
    def _make_client(self, api_key):
        raise NotImplementedError

    def _complete(self, prompt, model, max_tokens, temperature, role):
        raise NotImplementedError

    def _friendly_error(self, exc):
        raise NotImplementedError


class _OpenAIProvider(_Provider):
    id = "openai"
    label = "ChatGPT"
    env_key = "OPENAI_API_KEY"
    settings_prefix = "chatgpt"

    def _make_client(self, api_key):
        from openai import OpenAI
        return OpenAI(api_key=api_key)

    def _complete(self, prompt, model, max_tokens, temperature, role):
        response = self._cached_client().chat.completions.create(
            model=model,
            messages=[{"role": role, "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content

    def _friendly_error(self, exc):
        import openai
        if isinstance(exc, openai.AuthenticationError):
            return ("Invalid OpenAI API key. "
                    "Check it in Settings → APIs → AI → OpenAI.")
        if isinstance(exc, openai.RateLimitError):
            if "insufficient_quota" in str(exc):
                return ("Your OpenAI account is out of credits. Add credits at "
                        "platform.openai.com/account/billing, or switch the AI "
                        "provider to Gemini in Settings → APIs → AI.")
            return "OpenAI rate limit reached. Wait a moment and try again."
        if isinstance(exc, openai.NotFoundError):
            return ("Unknown OpenAI model. "
                    "Check the model name in Settings → APIs → AI → OpenAI.")
        if isinstance(exc, openai.APIConnectionError):
            return "Could not reach OpenAI. Check your internet connection."
        return f"OpenAI error: {exc}"


class _GeminiProvider(_Provider):
    id = "gemini"
    label = "Gemini"
    env_key = "GOOGLE_API_KEY"
    settings_prefix = "gemini"

    def _make_client(self, api_key):
        from google import genai
        return genai.Client(api_key=api_key)

    def _complete(self, prompt, model, max_tokens, temperature, role):
        from google.genai import types
        config = types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        # Thinking models (gemini-2.5-flash…) spend reasoning tokens from the
        # same max_output_tokens budget; budget 0 disables that so small caps
        # still yield full answers. -1 = let the model decide (needed for pro).
        budget = get_int(load_settings(), "gemini_thinking_budget", 0)
        if budget >= 0:
            config.thinking_config = types.ThinkingConfig(thinking_budget=budget)
        response = self._cached_client().models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        return response.text

    def _friendly_error(self, exc):
        from google.genai import errors
        if isinstance(exc, errors.APIError):
            if exc.code == 429:
                return ("Gemini quota exhausted. The free tier resets daily; wait, "
                        "or create a new key at aistudio.google.com/app/apikey.")
            if exc.code in (401, 403):
                return ("Invalid Google API key. "
                        "Check it in Settings → APIs → AI → Gemini.")
            if exc.code == 404:
                return ("Unknown Gemini model. "
                        "Check the model name in Settings → APIs → AI → Gemini.")
            return f"Gemini error {exc.code}: {exc.message}"
        return f"Gemini error: {exc}"


_PROVIDERS = {p.id: p for p in (_OpenAIProvider(), _GeminiProvider())}


# ------------------------------------------------------------------ facade

def get_provider(settings=None):
    """The active provider per the ``ai_provider`` setting."""
    settings = settings or load_settings()
    pid = str(settings.get("ai_provider", DEFAULT_PROVIDER)).strip().lower()
    return _PROVIDERS.get(pid, _PROVIDERS[DEFAULT_PROVIDER])


def provider_label():
    """Display name of the active provider ('ChatGPT' / 'Gemini')."""
    return get_provider().label


def has_api_key():
    return get_provider().has_api_key()


def reset_clients():
    """Drop all cached API clients (call after keys change in .env)."""
    for provider in _PROVIDERS.values():
        provider.reset()


def _task_params(settings, provider, task=""):
    """Model/prompt settings for one task, e.g. chatgpt_texts_* keys."""
    prefix = provider.settings_prefix + (f"_{task}" if task else "")
    return {
        "model": str(settings.get(f"{prefix}_model", "")).strip(),
        "max_tokens": get_int(settings, f"{prefix}_max_tokens", 400),
        "temperature": get_float(settings, f"{prefix}_temperature", 0.5),
        "role": str(settings.get(f"{prefix}_role", "user")).strip() or "user",
        "template": str(settings.get(f"{prefix}_content", "")),
    }


def _render(template, **values):
    if not template.strip():
        raise AIError("The prompt template is empty. "
                      "Fix it in Settings → APIs → AI.")
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError) as exc:
        raise AIError(f"Invalid prompt template ({exc}). "
                      f"Fix it in Settings → APIs → AI.")


def get_definition(word, language1, language2):
    """Fetch a definition with the active provider; raises AIError."""
    settings = load_settings()
    provider = get_provider(settings)
    params = _task_params(settings, provider)
    prompt = _render(params.pop("template"),
                     word=word, language1=language1, language2=language2)
    return provider.complete(prompt, **params)


def lemma_translate(word, sentence, source_language, target_language):
    """Lemma form of *word* (as used in *sentence*) + its best translation.

    Returns (lemma, translation); raises AIError. Uses the definition-task
    model settings of the active provider with a fixed prompt.
    """
    settings = load_settings()
    provider = get_provider(settings)
    params = _task_params(settings, provider)
    params.pop("template")
    context = f' in the sentence: "{sentence.strip()}"' if sentence.strip() else ""
    prompt = (
        f'The {source_language} word "{word}" is used{context}. '
        f"Give its dictionary (lemma) form in {source_language} — e.g. the "
        f"infinitive for verbs, nominative singular for nouns (with the "
        f"article if customary for dictionary entries in {source_language}) — "
        f"and its best {target_language} translation for this exact context. "
        f"Answer with ONE line in exactly this format and nothing else:\n"
        f"lemma|translation"
    )
    content = provider.complete(prompt, **params)
    # last non-empty line guards against models that prepend chatter
    line = [ln for ln in content.splitlines() if ln.strip()][-1].strip()
    lemma, sep, translation = line.partition("|")
    if not sep or not lemma.strip() or not translation.strip():
        # tolerate a malformed answer: keep the clicked word as the entry
        return word, line.strip().strip("|")
    return lemma.strip(), translation.strip()


def generate_combined_text(words, language):
    """Generate (title, text) from a word list; raises AIError."""
    settings = load_settings()
    provider = get_provider(settings)
    params = _task_params(settings, provider, task="texts")
    prompt = _render(params.pop("template"), words=words, language=language)
    content = provider.complete(prompt, **params)
    return _split_title_text(content)


def generate_topic_text(language, level, topic, length_words):
    """Generate (title, text) for a language/level/topic; raises AIError."""
    settings = load_settings()
    provider = get_provider(settings)
    params = _task_params(settings, provider, task="texts_topic")
    prompt = _render(params.pop("template"), language=language, level=level,
                     topic=topic, length=length_words)
    # the configured cap is a floor — long texts need room (~3 tokens/word)
    params["max_tokens"] = max(params["max_tokens"], int(length_words) * 3 + 200)
    content = provider.complete(prompt, **params)
    return _split_title_text(content)


def adapt_text_to_level(text, language, level):
    """Rewrite *text* for a CEFR level; returns (title, text), raises AIError."""
    settings = load_settings()
    provider = get_provider(settings)
    params = _task_params(settings, provider, task="texts_adapt")
    snippet = text.strip()[:6000]
    prompt = _render(params.pop("template"), text=snippet,
                     language=language, level=level)
    params["max_tokens"] = max(params["max_tokens"], len(snippet) // 2 + 300)
    content = provider.complete(prompt, **params)
    return _split_title_text(content)


def _split_title_text(content):
    """Split model output into (title, text), tolerating missing delimiters."""
    title, _, text = content.partition("\n\n")
    if not text.strip():
        title, _, text = content.partition("\n")
    if not text.strip():
        return "", content.strip()
    return title.strip().strip("#*").strip(), text.strip()


# ------------------------------------------------------------- db helpers

def _make_db_adapter():
    settings = load_settings()
    enable_sync = settings.get("enable_sync", "false").lower() == "true"
    return DatabaseAdapter(use_cloud=enable_sync)


def update_definition_in_db(word, language1, language2, word_field, word_id):
    """Fetch a definition and store it. Returns (ok, message)."""
    try:
        definition = get_definition(word, language1, language2)
        db_adapter = _make_db_adapter()
        if not db_adapter.get_word(word_id):
            return False, f"Word with ID {word_id} not found."
        definition_column = 'Definition' if word_field == 'Word1' else 'Definition2'
        db_adapter.update_word(word_id, {definition_column: definition})
        return True, f"Definition for '{word}' was successfully updated."
    except AIError as exc:
        return False, str(exc)
    except Exception as exc:
        logging.error(f"Error updating definition: {exc}")
        return False, f"An error occurred: {exc}"
    finally:
        backup_database()


def save_generated_text_to_db(row_number, title, text, words, language,
                              level=None, category=None):
    """Returns (ok, message)."""
    try:
        db_adapter = _make_db_adapter()
        result = db_adapter.insert_text({
            'RowNumber': row_number,
            'Title': title,
            'Text': text,
            'Words': words,
            'Language': language,
            'Level': level,
            'Category': category,
        })
        if result:
            backup_database()
            return True, "Text saved successfully."
        return False, "Failed to save text to database."
    except Exception as exc:
        logging.error(f"Error saving text to database: {exc}")
        return False, f"An error occurred: {exc}"
