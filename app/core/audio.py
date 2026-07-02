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

"""Text-to-speech playback and audio-file generation.

Port of the original dictionary/audio.py with the tkinter message boxes
removed: errors are reported through logging / the optional ``logger``
callback so the module is GUI-framework independent.
"""
import concurrent.futures
import logging
import os
import random
import subprocess
import tempfile
import threading
import time

import pygame
from gtts import gTTS

from app.config import load_settings, get_int
from app.core.shell_utils import (
    NoConsolePopen, no_console_call, no_console_run, read_ffmpeg_path,
)

try:
    from app.core.google_cloud_tts import (
        get_google_cloud_language_code, synthesize_text_to_speech,
    )
    GOOGLE_CLOUD_TTS_AVAILABLE = True
except ImportError:
    GOOGLE_CLOUD_TTS_AVAILABLE = False
    logging.warning("Google Cloud TTS not available. Install google-cloud-texttospeech to use it.")

# Patch subprocess before pydub imports it so no console windows pop up on Windows
subprocess.Popen = NoConsolePopen
subprocess.run = no_console_run
subprocess.call = no_console_call

from pydub import AudioSegment  # noqa: E402

read_ffmpeg_path()

try:
    pygame.mixer.init()
except pygame.error as exc:  # no audio device (e.g. headless CI)
    logging.warning(f"pygame mixer init failed: {exc}")

# Language name -> gTTS code
lang_codes = {
    'Detect language': 'en', 'English': 'en', 'German': 'de', 'Spanish': 'es',
    'Ukrainian': 'uk', 'French': 'fr', 'Italian': 'it', 'Portuguese': 'pt',
    'Russian': 'ru', 'Greek': 'el', 'Arabic': 'ar', 'Bengali': 'bn',
    'Cantonese': 'zh-HK', 'Hindi': 'hi', 'Japanese': 'ja', 'Korean': 'ko',
    'Mandarin': 'zh-CN', 'Polish': 'pl', 'Turkish': 'tr', 'Vietnamese': 'vi',
    'Afrikaans': 'af', 'Albanian': 'sq', 'Amharic': 'am', 'Armenian': 'hy',
    'Azerbaijani': 'az', 'Basque': 'eu', 'Belarusian': 'be', 'Bosnian': 'bs',
    'Bulgarian': 'bg', 'Catalan': 'ca', 'Cebuano': 'ceb', 'Chichewa': 'ny',
    'Croatian': 'hr', 'Czech': 'cs', 'Danish': 'da', 'Dutch': 'nl',
    'Estonian': 'et', 'Filipino': 'fil', 'Finnish': 'fi', 'Galician': 'gl',
    'Georgian': 'ka', 'Gujarati': 'gu', 'Haitian Creole': 'ht', 'Hausa': 'ha',
    'Hawaiian': 'haw', 'Hebrew': 'he', 'Hmong': 'hmn', 'Hungarian': 'hu',
    'Icelandic': 'is', 'Igbo': 'ig', 'Indonesian': 'id', 'Irish': 'ga',
    'Javanese': 'jv', 'Kannada': 'kn', 'Kazakh': 'kk', 'Khmer': 'km',
    'Kinyarwanda': 'rw', 'Kyrgyz': 'ky', 'Lao': 'lo', 'Latin': 'la',
    'Latvian': 'lv', 'Lithuanian': 'lt', 'Luxembourgish': 'lb',
    'Macedonian': 'mk', 'Malagasy': 'mg', 'Malay': 'ms', 'Malayalam': 'ml',
    'Maltese': 'mt', 'Maori': 'mi', 'Marathi': 'mr', 'Mongolian': 'mn',
    'Myanmar (Burmese)': 'my', 'Nepali': 'ne', 'Norwegian': 'no', 'Odia': 'or',
    'Pashto': 'ps', 'Persian': 'fa', 'Punjabi': 'pa', 'Romanian': 'ro',
    'Samoan': 'sm', 'Scots Gaelic': 'gd', 'Serbian': 'sr', 'Sesotho': 'st',
    'Shona': 'sn', 'Sindhi': 'sd', 'Sinhala': 'si', 'Slovak': 'sk',
    'Slovenian': 'sl', 'Somali': 'so', 'Sundanese': 'su', 'Swahili': 'sw',
    'Swedish': 'sv', 'Tajik': 'tg', 'Tamil': 'ta', 'Tatar': 'tt',
    'Telugu': 'te', 'Thai': 'th', 'Turkmen': 'tk', 'Urdu': 'ur',
    'Uyghur': 'ug', 'Uzbek': 'uz', 'Welsh': 'cy', 'Xhosa': 'xh',
    'Yiddish': 'yi', 'Yoruba': 'yo', 'Zulu': 'zu',
}

all_temp_files = set()
temp_files_lock = threading.Lock()


def is_language_supported(name):
    """True if *name* (canonical English language name) can be synthesized."""
    return name in lang_codes


def get_tts_settings():
    """Return (provider, credentials_path, voice_type, voice_name)."""
    settings = load_settings()
    provider = settings.get("tts_provider", "gTTS").lower()
    if "google" in provider and "cloud" in provider:
        provider = "google_cloud_tts"
    else:
        provider = "gtts"

    credentials_path = settings.get("google_cloud_tts_credentials_path", "")
    voice_type = settings.get("google_cloud_tts_voice_type", "standard").lower()
    voice_name = settings.get("google_cloud_tts_voice_name", "")

    if provider == "google_cloud_tts" and google_cloud_tts_problem(settings):
        # Known-broken config: don't attempt Google Cloud per word — the
        # default-credentials probe costs ~12s for every synthesis call.
        logging.warning("Google Cloud TTS selected but not usable. Falling back to gTTS.")
        provider = "gtts"

    return provider, credentials_path, voice_type, voice_name


def google_cloud_tts_problem(settings=None):
    """Why Google Cloud TTS would fall back to gTTS, or None if it looks OK."""
    settings = settings or load_settings()
    provider = settings.get("tts_provider", "gTTS").lower()
    if not ("google" in provider and "cloud" in provider):
        return None
    if not GOOGLE_CLOUD_TTS_AVAILABLE:
        return "the google-cloud-texttospeech package is not installed"
    path = settings.get("google_cloud_tts_credentials_path", "").strip()
    if not path:
        return "no credentials file is configured"
    if not os.path.exists(path):
        return f"the credentials file does not exist:\n{path}"
    return None


def synthesize_speech(text, language_code, cancellation_event=None):
    """Generate an mp3 for *text*; returns the temp-file path or None."""
    provider, credentials_path, voice_type, voice_name = get_tts_settings()

    if cancellation_event and cancellation_event.is_set():
        return None

    if provider == "google_cloud_tts" and GOOGLE_CLOUD_TTS_AVAILABLE:
        try:
            if get_google_cloud_language_code(language_code):
                result = synthesize_text_to_speech(
                    text=text,
                    app_language_code=language_code,
                    credentials_path=credentials_path or None,
                    voice_type=voice_type,
                    voice_name=voice_name or None,
                )
                if result:
                    return result
                logging.warning(f"Google Cloud TTS failed for '{text}', falling back to gTTS")
            else:
                logging.warning(f"Language '{language_code}' not supported by Google Cloud TTS, using gTTS")
        except Exception as exc:
            logging.error(f"Google Cloud TTS error: {exc}, falling back to gTTS")

    try:
        if cancellation_event and cancellation_event.is_set():
            return None
        tts = gTTS(text=text, lang=language_code)
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
            tts.save(tmp_file.name)
            return tmp_file.name
    except Exception as exc:
        logging.error(f"gTTS error: {exc}")
        return None


def stop_playback():
    """Stop pygame playback and delete tracked temp files."""
    try:
        pygame.mixer.music.stop()
        pygame.mixer.music.unload()
    except pygame.error:
        pass

    with temp_files_lock:
        pending = list(all_temp_files)
    for filename in pending:
        _remove_temp_file(filename)


def _remove_temp_file(filename):
    if os.path.exists(filename):
        try:
            os.remove(filename)
        except Exception as exc:
            logging.error(f"Error removing file: {exc}")
            return
    with temp_files_lock:
        all_temp_files.discard(filename)


def cleanup_temp_files():
    temp_dir = tempfile.gettempdir()
    for filename in os.listdir(temp_dir):
        if filename.startswith('tmp') and filename.endswith('.mp3'):
            try:
                os.remove(os.path.join(temp_dir, filename))
            except Exception as exc:
                logging.error(f"Error removing file: {exc}")


def retry_request(func, *args, cancellation_event=None, logger=None, **kwargs):
    """Retry *func* with exponential backoff."""
    max_retries = 5
    for attempt in range(max_retries):
        if cancellation_event and cancellation_event.is_set():
            break
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                message = f"Error: {exc}. Retrying in {wait_time:.2f} seconds..."
                if logger:
                    logger(message, level='warning')
                else:
                    logging.error(message)
                time.sleep(wait_time)
            else:
                message = f"Max retries exceeded. Error: {exc}"
                if logger:
                    logger(message, level='error')
                else:
                    logging.error(message)
                raise


def save_audio_file(words, file_path, languages, progress_callback=None, is_cancelled=None,
                    all_temp_files=None, logger=None, pause_duration=1000, number_of_repeats=1):
    """Render word/translation pairs into a single mp3 at *file_path*."""
    try:
        progress_lock = threading.Lock()
        rate_limit_lock = threading.Lock()
        next_available_time = [time.time()]
        results = {}
        current = 0

        settings = load_settings()
        max_concurrent_workers = get_int(settings, "max_concurrent_workers", 2)
        requests_per_sec = 1 / max(get_int(settings, "requests_per_sec", 5), 1)

        def process_word_pair(index, word_pair):
            nonlocal current
            (word, translation), (lang_word, lang_translation) = word_pair
            temp_files = []
            try:
                if is_cancelled and is_cancelled.is_set():
                    return index, temp_files

                for text, lang in [(word, lang_word), (translation, lang_translation)]:
                    if is_cancelled and is_cancelled.is_set():
                        return index, temp_files
                    if lang not in lang_codes:
                        message = f"Unsupported language: {lang} for {word_pair}"
                        if logger:
                            logger(message, level='warning')
                        logging.warning(message)
                        continue

                    provider, _, _, _ = get_tts_settings()
                    if provider == "gtts":
                        with rate_limit_lock:
                            now = time.time()
                            if now < next_available_time[0]:
                                time.sleep(next_available_time[0] - now)
                                now = time.time()
                            next_available_time[0] = now + requests_per_sec

                    filename = synthesize_speech(text, lang_codes[lang], cancellation_event=is_cancelled)
                    if is_cancelled and is_cancelled.is_set():
                        return index, temp_files
                    if filename:
                        temp_files.append(filename)
                        if all_temp_files is not None:
                            all_temp_files.add(filename)

                silent_segment = AudioSegment.silent(duration=pause_duration)
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
                    silent_segment.export(tmp_file.name, format='mp3')
                    temp_files.append(tmp_file.name)
                    if all_temp_files is not None:
                        all_temp_files.add(tmp_file.name)

                with progress_lock:
                    current += 1
                    if progress_callback:
                        progress_callback(current, word)

            except Exception as exc:
                if logger:
                    logger(f"Error processing word '{word}': {exc}", level='error')
                logging.error(f"Error processing word '{word}': {exc}")
            return index, temp_files

        word_pairs_with_indices = list(enumerate(zip(words, languages, strict=False)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent_workers) as executor:
            futures = [executor.submit(process_word_pair, idx, wp)
                       for idx, wp in word_pairs_with_indices]
            for future in concurrent.futures.as_completed(futures):
                if is_cancelled and is_cancelled.is_set():
                    break
                index, temp_files = future.result()
                results[index] = temp_files

        text_segments_in_order = []
        for idx in sorted(results.keys()):
            for _ in range(number_of_repeats):
                text_segments_in_order.extend(results[idx])

        if text_segments_in_order and not (is_cancelled and is_cancelled.is_set()):
            if progress_callback:
                progress_callback('compiling_audio', None)
            combined = AudioSegment.empty()
            for segment in text_segments_in_order:
                combined += AudioSegment.from_file(segment, format="mp3")
            combined.export(file_path, format="mp3")

        for segment in text_segments_in_order:
            if os.path.exists(segment):
                try:
                    os.remove(segment)
                    if all_temp_files is not None:
                        all_temp_files.discard(segment)
                except Exception as exc:
                    if logger:
                        logger(f"Error removing file: {exc}", level='error')
            elif all_temp_files is not None:
                all_temp_files.discard(segment)

    except Exception as exc:
        if logger:
            logger(f"Error saving audio file: {exc}", level='error')
        raise


_speak_lock = threading.Lock()

# Small pronunciation cache so callers (flashcards) can prefetch upcoming
# words and speak_word starts instantly instead of waiting on synthesis.
# Keyed by (text, tts lang code). Files are also tracked in all_temp_files,
# so stop_playback()/shutdown cleanup may delete them underneath us at any
# time — lookups must tolerate a vanished file and re-synthesize.
_pronounce_cache = {}
_pronounce_cache_lock = threading.Lock()
_PRONOUNCE_CACHE_MAX = 16


def _pronounce_cache_get(key):
    with _pronounce_cache_lock:
        filename = _pronounce_cache.get(key)
    if filename and not os.path.exists(filename):
        with _pronounce_cache_lock:
            if _pronounce_cache.get(key) == filename:
                del _pronounce_cache[key]
        return None
    return filename


def _pronounce_cache_put(key, filename):
    evicted = []
    with _pronounce_cache_lock:
        old = _pronounce_cache.pop(key, None)
        if old and old != filename:
            evicted.append(old)
        _pronounce_cache[key] = filename
        while len(_pronounce_cache) > _PRONOUNCE_CACHE_MAX:
            oldest = next(iter(_pronounce_cache))
            evicted.append(_pronounce_cache.pop(oldest))
    for stale in evicted:
        _remove_temp_file(stale)


def prefetch_word(word, language, cancel_event=None):
    """Synthesize *word* ahead of time so a later speak_word plays instantly.

    Silently does nothing on bad input, cancellation, synthesis failure, or
    when the word is already cached — prefetching is best-effort.
    """
    word = (word or "").strip()
    if not word or language not in lang_codes:
        return
    key = (word, lang_codes[language])
    if _pronounce_cache_get(key):
        return
    filename = synthesize_speech(word, lang_codes[language],
                                 cancellation_event=cancel_event)
    if not filename:
        return
    with temp_files_lock:
        all_temp_files.add(filename)
    _pronounce_cache_put(key, filename)


def speak_word(word, language, cancel_event=None):
    """Speak a single word synchronously. Raises ValueError on bad input.

    Serialized with a lock: concurrent pygame.mixer.music access from
    multiple threads can crash SDL. ``cancel_event`` (a ``threading.Event``)
    aborts cleanly at any stage — before synthesis, before playback, or
    mid-playback — so rapid re-triggers (e.g. flipping flashcards) can
    supersede a stale pronunciation instead of queueing behind it.
    """
    if not word.strip():
        raise ValueError("Please enter a word to speak.")
    if language not in lang_codes:
        raise ValueError(f"Unsupported language: {language}")
    if cancel_event is not None and cancel_event.is_set():
        return

    key = (word.strip(), lang_codes[language])
    filename = _pronounce_cache_get(key)
    if not filename:
        filename = synthesize_speech(word, lang_codes[language],
                                     cancellation_event=cancel_event)
        if not filename:
            if cancel_event is not None and cancel_event.is_set():
                return
            raise RuntimeError("Failed to generate speech. Check your TTS provider settings.")
        with temp_files_lock:
            all_temp_files.add(filename)
        # keep it: an immediate replay (speaker button) is then instant
        _pronounce_cache_put(key, filename)

    with _speak_lock:
        if cancel_event is not None and cancel_event.is_set():
            return
        try:
            pygame.mixer.music.load(filename)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if cancel_event is not None and cancel_event.is_set():
                    pygame.mixer.music.stop()
                    break
                pygame.time.Clock().tick(10)
            pygame.mixer.music.unload()
        except pygame.error as exc:
            logging.error(f"Pronunciation playback error: {exc}")
