"""Settings persistence.

Reads/writes the same flat ``key=value`` settings.cfg format as the
original app so a settings file can be moved between both versions.
"""
import logging
import os
import threading

SETTINGS_FILE = "settings.cfg"

DEFAULTS = {
    # Appearance
    "appearance_mode": "System",        # System | Light | Dark
    "widget_scaling": "1.0",
    "table_density": "Normal",          # Compact | Normal | Comfortable | Spacious
    # Audio
    "pause_duration": "0.5",
    "number_of_repeats": "1",
    "max_concurrent_workers": "2",
    "requests_per_sec": "5",
    # Reader
    "reader_translate_target": "English",  # word-popup translation target
    "reader_zoom": "0",                 # font-size pt offset (Ctrl+scroll / Ctrl +/-)
    "reader_paper_mode": "off",         # reading-pane page: off | white | sepia
    # TTS
    "tts_provider": "gTTS",
    "google_cloud_tts_credentials_path": "",
    "google_cloud_tts_voice_type": "standard",
    "google_cloud_tts_voice_name": "",
    # PDF export
    "left_margin": "10.0",
    "right_margin": "10.0",
    "top_margin": "10.0",
    "bottom_margin": "10.0",
    "page_size": "Letter",
    "font_name": "Helvetica",
    "font_size": "10.0",
    "leading": "12.0",
    "alignment": "CENTER",
    "pdf_auto_widths": "True",
    "pdf_col_width_ID": "0.5",
    "pdf_col_width_RowNumber": "0.5",
    "pdf_col_width_Status": "0.8",
    "pdf_col_width_Language1": "1.0",
    "pdf_col_width_Word1": "1.6",
    "pdf_col_width_Language2": "1.0",
    "pdf_col_width_Word2": "1.6",
    "pdf_col_width_Source": "1.0",
    "pdf_col_width_created_at": "1.2",
    "pdf_col_width_Definition": "2.5",
    "pdf_col_width_Definition2": "2.5",
    "header_bg_color": "#808080",
    "bg_color": "#f8f4dc",
    "text_color": "#000000",
    "grid_color": "#000000",
    "bg_image": "",
    "exclude_columns": "ID,Source,created_at,Definition,Definition2",
    # Excel export
    "exclude_columns_excel": "ID,Source,Definition,Definition2",
    "excel_format": "Excel",
    "csv_delimiter": ",",
    "sheet_name": "Sheet1",
    "start_row": "0",
    "start_column": "0",
    "alternate_row_color": "#e0e0e0",
    "auto_column_width": "True",
    "freeze_panes": "False",
    # TXT export
    "txt_delimiter": "\\t",
    "txt_include_headers": "True",
    "txt_header_lines": "#separator:tab\\n#html:true\\n",
    "exclude_columns_txt": "ID,Source,Definition,Definition2",
    # DeepL
    "api_key": "",
    "api_url": "https://api.deepl.com/v2/translate",
    # AI provider
    "ai_provider": "openai",          # openai | gemini
    # ChatGPT
    "chatgpt_model": "gpt-4o-mini",
    "chatgpt_max_tokens": "400",
    "chatgpt_temperature": "0.3",
    "chatgpt_role": "assistant",
    "chatgpt_content": (
        "Define the word: {word} in {language1} and in {language2}. "
        "Also provide example sentences (in different contexts) with that word, "
        "and synonyms solely in {language1}. Markups: '***' for 'Definition', "
        "'Example Sentences' and 'Synonyms'; other possible markups: '**' and '*'."
    ),
    "chatgpt_texts_model": "gpt-4o-mini",
    "chatgpt_texts_max_tokens": "300",
    "chatgpt_texts_temperature": "0.7",
    "chatgpt_texts_role": "assistant",
    "chatgpt_texts_content": (
        "Generate a title and a comprehensive text using the following words: {words} "
        "in the following language: {language}.Separate the title and text with a "
        "delimiter like \"\\n\\n\". And do not use any markups('**' etc.)"
    ),
    "chatgpt_texts_topic_model": "gpt-4o-mini",
    "chatgpt_texts_topic_max_tokens": "1200",
    "chatgpt_texts_topic_temperature": "0.7",
    "chatgpt_texts_topic_role": "assistant",
    "chatgpt_texts_topic_content": (
        "Write a text in {language} for a language learner at CEFR level {level} "
        "about: {topic}. Length: about {length} words. Use vocabulary and grammar "
        "appropriate for {level}. Start with a short title, then an empty line, "
        "then the text. Do not use any markups('**' etc.)."
    ),
    "chatgpt_texts_adapt_model": "gpt-4o-mini",
    "chatgpt_texts_adapt_max_tokens": "2000",
    "chatgpt_texts_adapt_temperature": "0.5",
    "chatgpt_texts_adapt_role": "assistant",
    "chatgpt_texts_adapt_content": (
        "Rewrite the following text in {language} for a language learner at CEFR "
        "level {level}. Keep the meaning and key facts; adjust vocabulary and "
        "sentence complexity to {level}. Start with a short title, then an empty "
        "line, then the rewritten text. Do not use any markups('**' etc.). "
        "Text: {text}"
    ),
    # Gemini
    "gemini_model": "gemini-2.5-flash",
    "gemini_thinking_budget": "0",    # 0 = off, -1 = dynamic (model decides)
    "gemini_max_tokens": "400",
    "gemini_temperature": "0.3",
    "gemini_content": (
        "Define the word: {word} in {language1} and in {language2}. "
        "Also provide example sentences (in different contexts) with that word, "
        "and synonyms solely in {language1}. Markups: '***' for 'Definition', "
        "'Example Sentences' and 'Synonyms'; other possible markups: '**' and '*'."
    ),
    "gemini_texts_model": "gemini-2.5-flash",
    "gemini_texts_max_tokens": "400",
    "gemini_texts_temperature": "0.7",
    "gemini_texts_content": (
        "Generate a title and a comprehensive text using the following words: {words} "
        "in the following language: {language}. Separate the title and text with a "
        "delimiter like \"\\n\\n\". And do not use any markups('**' etc.)"
    ),
    "gemini_texts_topic_model": "gemini-2.5-flash",
    "gemini_texts_topic_max_tokens": "1200",
    "gemini_texts_topic_temperature": "0.7",
    "gemini_texts_topic_content": (
        "Write a text in {language} for a language learner at CEFR level {level} "
        "about: {topic}. Length: about {length} words. Use vocabulary and grammar "
        "appropriate for {level}. Start with a short title, then an empty line, "
        "then the text. Do not use any markups('**' etc.)."
    ),
    "gemini_texts_adapt_model": "gemini-2.5-flash",
    "gemini_texts_adapt_max_tokens": "2000",
    "gemini_texts_adapt_temperature": "0.5",
    "gemini_texts_adapt_content": (
        "Rewrite the following text in {language} for a language learner at CEFR "
        "level {level}. Keep the meaning and key facts; adjust vocabulary and "
        "sentence complexity to {level}. Start with a short title, then an empty "
        "line, then the rewritten text. Do not use any markups('**' etc.). "
        "Text: {text}"
    ),
    # Excel import
    "excel_import_placeholders": "(  ),'',N/A,---,None,null",
    "excel_import_skip_placeholders": "True",
    "excel_import_skip_empty": "True",
    "excel_import_normalize": "True",
    # Text sources (Add Text dialog)
    "rss_feeds_user": "[]",           # single-line JSON list of {name, url, language}
    "addtext_language": "",           # last-used language in the Add Text dialog
    "addtext_level": "",              # last-used CEFR level in the Add Text dialog
    # Sync
    "enable_sync": "False",
    "cleanup_grace_period_days": "30",
}

_lock = threading.Lock()


def load_settings(path=SETTINGS_FILE):
    """Return settings dict (defaults overlaid with the file contents)."""
    settings = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                settings[key.strip()] = value
    except FileNotFoundError:
        logging.warning("Settings file not found, using defaults.")
    return settings


def save_settings(settings, path=SETTINGS_FILE):
    """Persist all known keys (and any extra ones) back to settings.cfg."""
    with _lock:
        ordered = dict(DEFAULTS)
        ordered.update(settings)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("# Dictionary App Configuration File\n")
            for key, value in ordered.items():
                fh.write(f"{key}={value}\n")
        os.replace(tmp, path)


def get_bool(settings, key, default=False):
    return str(settings.get(key, default)).strip().lower() in ("true", "1", "yes")


def get_float(settings, key, default=0.0):
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def get_int(settings, key, default=0):
    try:
        return int(float(settings.get(key, default)))
    except (TypeError, ValueError):
        return default
