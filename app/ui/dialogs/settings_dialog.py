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

"""Settings dialog. Persists to settings.cfg (and API keys to .env)."""
import os
import shutil

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QKeySequenceEdit,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from app.config import get_bool, get_float, get_int, load_settings, save_settings
from app.core import exporters, translator
from app.system.autostart import get_autostart_enabled, set_autostart
from app.ui.dialogs.base import FramelessDialog
from app.ui.widgets import ColorButton, ColumnPicker
from app.ui.workers import run_in_thread


def _read_env():
    env = {}
    if os.path.exists('.env'):
        with open('.env', encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line and '=' in line and not line.startswith('#'):
                    key, value = line.split('=', 1)
                    env[key.strip()] = value.strip()
    return env


def _write_env(updates):
    env = _read_env()
    env.update(updates)
    with open('.env', 'w', encoding='utf-8') as fh:
        for key, value in env.items():
            fh.write(f"{key}={value}\n")
    for key, value in updates.items():
        os.environ[key] = value


def _scrollable(widget):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setWidget(widget)
    return scroll


class SettingsDialog(FramelessDialog):
    def __init__(self, parent):
        super().__init__(parent, title="Settings")
        self.setMinimumSize(720, 560)
        self.settings = load_settings()
        self.env = _read_env()
        # Hidden flag: add "show_advanced=True" to settings.cfg by hand to expose
        # the AI prompt-template editors. Deliberately absent from DEFAULTS so the
        # key never appears in the file on its own.
        self.show_advanced = get_bool(self.settings, "show_advanced", False)

        layout = self.content_layout
        layout.setContentsMargins(16, 16, 16, 12)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._appearance_tab(), "Appearance")
        self.tabs.addTab(self._audio_tab(), "Audio")
        self.tabs.addTab(self._learning_tab(), "Learning")
        self.tabs.addTab(self._export_tab(), "Export")
        self.tabs.addTab(self._import_tab(), "Import")
        self.tabs.addTab(self._apis_tab(), "APIs")
        self.tabs.addTab(self._system_tab(), "System")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ----------------------------------------------------------- helpers

    def _line(self, key, width=None):
        edit = QLineEdit(str(self.settings.get(key, "")))
        if width:
            edit.setMaximumWidth(width)
        setattr(self, f"w_{key}", edit)
        return edit

    @staticmethod
    def _secret(edit):
        """Mask a line edit (API keys) and add an eye toggle to reveal it."""
        from app.ui import icons, theme
        dim = theme.current_colors()["text_dim"]
        edit.setEchoMode(QLineEdit.Password)
        action = edit.addAction(icons.icon("eye", dim, 16),
                                QLineEdit.TrailingPosition)
        action.setToolTip("Show / hide")

        def toggle():
            hidden = edit.echoMode() == QLineEdit.Password
            edit.setEchoMode(QLineEdit.Normal if hidden else QLineEdit.Password)
            action.setIcon(icons.icon("eye-off" if hidden else "eye", dim, 16))

        action.triggered.connect(toggle)
        return edit

    def _spin(self, key, lo, hi, default=0):
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(get_int(self.settings, key, default))
        setattr(self, f"w_{key}", spin)
        return spin

    def _dspin(self, key, lo, hi, default=0.0, step=0.1):
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setValue(get_float(self.settings, key, default))
        setattr(self, f"w_{key}", spin)
        return spin

    def _check(self, key, default=False):
        box = QCheckBox()
        box.setChecked(get_bool(self.settings, key, default))
        setattr(self, f"w_{key}", box)
        return box

    def _color(self, key, clearable=False):
        btn = ColorButton(str(self.settings.get(key, "")), clearable)
        setattr(self, f"w_{key}", btn)
        return btn

    def _columns(self, key, width_spins=None):
        cols = [(c, exporters.EXPORT_COLUMN_LABELS[c]) for c in exporters.EXPORT_COLUMNS]
        cols += [(c, exporters.EXTRA_LABELS[c]) for c in exporters.EXTRA_COLUMNS]
        picker = ColumnPicker(cols, str(self.settings.get(key, "")), width_spins)
        setattr(self, f"w_{key}", picker)
        return picker

    def _combo(self, key, values, default=None):
        combo = QComboBox()
        combo.addItems(values)
        current = str(self.settings.get(key, default or values[0]))
        if combo.findText(current) >= 0:
            combo.setCurrentText(current)
        setattr(self, f"w_{key}", combo)
        return combo

    def _ai_provider_page(self, prefix, key_label, key_edit, note_html, extra_rows=()):
        """One provider's settings page: API key + Definitions/Texts groups."""
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow(key_label, key_edit)
        note = QLabel(note_html)
        note.setOpenExternalLinks(True)
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        for label, widget in extra_rows:
            form.addRow(label, widget)
        for title, task in (("Definitions", prefix),
                            ("Generated Texts (from words)", f"{prefix}_texts"),
                            ("Generated Texts (by topic)", f"{prefix}_texts_topic"),
                            ("Text Adaptation (to level)", f"{prefix}_texts_adapt")):
            group = QGroupBox(title)
            g_form = QFormLayout(group)
            g_form.addRow("Model", self._line(f"{task}_model", 220))
            g_form.addRow("Max tokens", self._spin(f"{task}_max_tokens", 16, 8000, 400))
            g_form.addRow("Temperature", self._dspin(f"{task}_temperature", 0, 2, 0.5))
            if self.show_advanced:
                content = QTextEdit(str(self.settings.get(f"{task}_content", "")))
                content.setMaximumHeight(90)
                setattr(self, f"w_{task}_content", content)
                g_form.addRow("Prompt template", content)
            form.addRow(group)
        return _scrollable(page)

    # -------------------------------------------------------------- tabs

    def _appearance_tab(self):
        from app.ui.theme import TABLE_DENSITY, TABLE_DENSITY_DEFAULT
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow("Appearance mode", self._combo("appearance_mode", ["System", "Light", "Dark"]))
        form.addRow("Widget scaling", self._dspin("widget_scaling", 0.5, 3.0, 1.0))
        self.settings.setdefault("table_density", TABLE_DENSITY_DEFAULT)
        form.addRow("Table size", self._combo("table_density", list(TABLE_DENSITY.keys()), TABLE_DENSITY_DEFAULT))
        return _scrollable(widget)

    def _export_tab(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # Excel / CSV
        excel = QWidget()
        layout = QVBoxLayout(excel)
        layout.setContentsMargins(18, 18, 18, 18)
        fmt_group = QGroupBox("Format")
        form = QFormLayout(fmt_group)
        form.addRow("Data format", self._combo("excel_format", ["Excel", "CSV"]))
        form.addRow("Columns to export", self._columns("exclude_columns_excel"))
        layout.addWidget(fmt_group)
        xls_group = QGroupBox("Excel options")
        form = QFormLayout(xls_group)
        form.addRow("Sheet name", self._line("sheet_name", 160))
        form.addRow("Start row", self._spin("start_row", 0, 100))
        form.addRow("Start column", self._spin("start_column", 0, 100))
        form.addRow("Shade alternate rows", self._color("alternate_row_color", clearable=True))
        form.addRow("Auto column width", self._check("auto_column_width", True))
        form.addRow("Freeze header row", self._check("freeze_panes", False))
        layout.addWidget(xls_group)
        csv_group = QGroupBox("CSV options")
        form = QFormLayout(csv_group)
        form.addRow("Delimiter", self._line("csv_delimiter", 80))
        layout.addWidget(csv_group)
        layout.addStretch(1)
        tabs.addTab(_scrollable(excel), "Excel / CSV")

        # TXT
        txt = QWidget()
        form = QFormLayout(txt)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow("Columns to export", self._columns("exclude_columns_txt"))
        form.addRow("Delimiter (\\t = tab)", self._line("txt_delimiter", 80))
        form.addRow("Include header lines", self._check("txt_include_headers", True))
        form.addRow("Header lines", self._line("txt_header_lines"))
        note = QLabel("Header lines are written at the top of the file — import tools like "
                      "Anki read them (e.g. #separator:tab, #html:true). "
                      "Column names themselves are not written.")
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(_scrollable(txt), "TXT")

        # PDF
        pdf = QWidget()
        layout = QVBoxLayout(pdf)
        layout.setContentsMargins(18, 18, 18, 18)

        page_group = QGroupBox("Page && text")
        form = QFormLayout(page_group)
        form.addRow("Page size", self._combo("page_size", ["Letter", "A4"]))
        font_row = QHBoxLayout()
        self.w_font_name = QComboBox()
        self.w_font_name.addItems(exporters.BUILTIN_FONTS + exporters.list_font_names())
        current_font = str(self.settings.get("font_name", "Helvetica"))
        if self.w_font_name.findText(current_font) < 0:
            self.w_font_name.addItem(current_font)
        self.w_font_name.setCurrentText(current_font)
        font_row.addWidget(self.w_font_name, 1)
        add_font = QPushButton("Add font…")
        add_font.setToolTip("Copy a .ttf file into the app's fonts folder and use it")
        add_font.clicked.connect(self._add_font)
        font_row.addWidget(add_font)
        font_w = QWidget()
        font_w.setLayout(font_row)
        form.addRow("Font", font_w)
        form.addRow("Font size", self._dspin("font_size", 4, 40, 10))
        form.addRow("Line spacing (pt)", self._dspin("leading", 4, 60, 12))
        form.addRow("Text alignment", self._combo("alignment", ["LEFT", "CENTER", "RIGHT"]))
        margins = QHBoxLayout()
        for key in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            margins.addWidget(self._dspin(key, 0, 100, 10))
        margins_w = QWidget()
        margins_w.setLayout(margins)
        form.addRow("Margins L/R/T/B (pt)", margins_w)
        layout.addWidget(page_group)

        col_group = QGroupBox("Columns")
        form = QFormLayout(col_group)
        width_spins = {c: self._dspin(f"pdf_col_width_{c}", 0.1, 10,
                                      exporters.PDF_WIDTH_DEFAULTS[c])
                       for c in exporters.EXPORT_COLUMNS + exporters.EXTRA_COLUMNS}
        picker = self._columns("exclude_columns", width_spins=width_spins)
        auto = self._check("pdf_auto_widths", True)
        auto.toggled.connect(lambda on: picker.set_widths_enabled(not on))
        picker.set_widths_enabled(not auto.isChecked())
        form.addRow("Automatic widths (fit page)", auto)
        form.addRow("Columns / width", picker)
        layout.addWidget(col_group)

        style_group = QGroupBox("Style")
        form = QFormLayout(style_group)
        form.addRow("Header background", self._color("header_bg_color"))
        form.addRow("Header text", self._color("text_color"))
        form.addRow("Row background", self._color("bg_color"))
        form.addRow("Grid lines", self._color("grid_color"))
        if self.settings.get("bg_image") == "No background image":
            self.settings["bg_image"] = ""
        bg_row = QHBoxLayout()
        bg_row.addWidget(self._line("bg_image"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._pick_bg_image)
        bg_row.addWidget(browse)
        clear_bg = QPushButton("Clear")
        clear_bg.clicked.connect(lambda: self.w_bg_image.clear())
        bg_row.addWidget(clear_bg)
        bg_w = QWidget()
        bg_w.setLayout(bg_row)
        form.addRow("Background image", bg_w)
        layout.addWidget(style_group)
        layout.addStretch(1)
        tabs.addTab(_scrollable(pdf), "PDF")

        # Audio export (MP3)
        audio_export = QWidget()
        form = QFormLayout(audio_export)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow("Pause between words (s)", self._dspin("pause_duration", 0, 10, 0.5))
        form.addRow("Repeats per pair", self._spin("number_of_repeats", 1, 10, 1))
        form.addRow("Concurrent workers", self._spin("max_concurrent_workers", 1, 16, 2))
        form.addRow("Requests per second", self._spin("requests_per_sec", 1, 50, 5))
        note = QLabel("Used only when exporting words to an MP3 file. "
                      "The voice itself is configured in the Audio tab.")
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(_scrollable(audio_export), "Audio (MP3)")

        return tabs

    def _audio_tab(self):
        """Text-to-speech settings (used by Read Aloud and MP3 export alike)."""
        audio = QWidget()
        form = QFormLayout(audio)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow("TTS provider", self._combo("tts_provider", ["gTTS", "google_cloud_tts"]))
        cred_row = QHBoxLayout()
        cred_row.addWidget(self._line("google_cloud_tts_credentials_path"))
        cred_browse = QPushButton("Browse…")
        cred_browse.clicked.connect(self._pick_credentials)
        cred_row.addWidget(cred_browse)
        cred_w = QWidget()
        cred_w.setLayout(cred_row)
        form.addRow("Google Cloud credentials", cred_w)
        form.addRow("Voice type", self._combo("google_cloud_tts_voice_type", ["standard", "wavenet"]))
        form.addRow("Voice name (optional)", self._line("google_cloud_tts_voice_name"))

        playback_group = QGroupBox("Read Aloud playback")
        pform = QFormLayout(playback_group)
        pform.addRow("Pause between words (s)", self._dspin("playback_pause", 0, 10, 0.5))
        pform.addRow("Repeats per word", self._spin("playback_repeats", 1, 10, 1))
        form.addRow(playback_group)

        note = QLabel("The voice used everywhere words are spoken: in-app Read Aloud "
                      "and MP3 export. gTTS is free and needs no setup. Google Cloud TTS "
                      "needs a service-account JSON key (Cloud Console → IAM & Admin → "
                      "Service Accounts → Keys) and billing enabled on the project — "
                      "usage within the free monthly quota is not charged.")
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        return _scrollable(audio)

    def _learning_tab(self):
        """Playback-driven learning progression (Read Aloud → word Status)."""
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(18, 18, 18, 18)

        promote = self._check("playback_promote", True)
        form.addRow("Promote status while listening", promote)

        rev = self._spin("playback_reviewing_listens", 1, 9998, 3)
        learn = self._spin("playback_learning_listens", 2, 9999, 15)
        mast = self._spin("playback_mastered_listens", 3, 10000, 100)
        form.addRow("Listens to reach Reviewing", rev)
        form.addRow("Listens to reach Learning", learn)
        form.addRow("Listens to reach Mastered", mast)

        def reorder(*_):
            # keep the ladder strictly increasing: Reviewing < Learning < Mastered
            learn.setMinimum(rev.value() + 1)
            rev.setMaximum(learn.value() - 1)
            mast.setMinimum(learn.value() + 1)
            learn.setMaximum(mast.value() - 1)
        for s in (rev, learn, mast):
            s.valueChanged.connect(reorder)
        reorder()

        def toggle_enabled(on):
            for s in (rev, learn, mast):
                s.setEnabled(on)
        promote.toggled.connect(toggle_enabled)
        toggle_enabled(promote.isChecked())

        note = QLabel("Fully listening to a word in Read Aloud promotes it along the "
                      "familiarity ladder New → Reviewing → Learning → Mastered. Each "
                      "number is the total completed listens needed to reach that level — "
                      "passive audio exposure is weak, so high values are normal. Words "
                      "you set to Mastered or Ignored yourself are never changed, and a "
                      "word is never demoted.")
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        return _scrollable(page)

    def _import_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(18, 18, 18, 18)

        options_group = QGroupBox("Excel import")
        form = QFormLayout(options_group)
        form.addRow("Placeholder values", self._line("excel_import_placeholders"))
        form.addRow("Skip placeholder rows", self._check("excel_import_skip_placeholders", True))
        form.addRow("Skip empty rows", self._check("excel_import_skip_empty", True))
        form.addRow("Normalize language pairs", self._check("excel_import_normalize", True))
        layout.addWidget(options_group)

        help_group = QGroupBox("How to import")
        help_layout = QVBoxLayout(help_group)
        note = QLabel(
            "<ol style='margin:0'>"
            "<li>Prepare an Excel file with the columns <b>Language1, Language2, Word1, "
            "Word2</b> — named like that in a header row (extra columns are ignored), or "
            "without headers, with the first four columns in exactly that order.</li>"
            "<li>Open the app menu → <i>Import Excel to Database…</i> and choose the file.</li>"
            "<li>Review the proposed rows and click <i>Import</i>.</li>"
            "</ol>")
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        help_layout.addWidget(note)
        template_btn = QPushButton("Save import template…")
        template_btn.setToolTip("Save a ready-made .xlsx with the right headers and example rows")
        template_btn.clicked.connect(self._save_import_template)
        btn_row = QHBoxLayout()
        btn_row.addWidget(template_btn)
        btn_row.addStretch(1)
        help_layout.addLayout(btn_row)
        layout.addWidget(help_group)

        layout.addStretch(1)
        return _scrollable(widget)

    def _apis_tab(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # Translation (Google free / DeepL)
        translation = QWidget()
        tr_layout = QVBoxLayout(translation)
        tr_layout.setContentsMargins(18, 18, 18, 18)

        selector = QFormLayout()
        self.translation_provider_combo = QComboBox()
        self.translation_provider_combo.addItem("Google Translate (free)", "google")
        self.translation_provider_combo.addItem("DeepL", "deepl")
        current_provider = str(self.settings.get("translation_provider", "google")).strip().lower()
        self.translation_provider_combo.setCurrentIndex(
            max(self.translation_provider_combo.findData(current_provider), 0))
        selector.addRow("Active provider", self.translation_provider_combo)
        tr_layout.addLayout(selector)

        self.google_note = QLabel("Google Translate is free and needs no API key.")
        self.google_note.setObjectName("dimLabel")
        self.google_note.setWordWrap(True)
        tr_layout.addWidget(self.google_note)

        # DeepL-specific fields, only shown when DeepL is the active provider.
        self.deepl_group = QGroupBox("DeepL")
        form = QFormLayout(self.deepl_group)
        form.addRow("API key", self._secret(self._line("api_key")))
        form.addRow("API URL", self._line("api_url"))
        note = QLabel('Get a key at <a href="https://www.deepl.com/pro-api">deepl.com/pro-api</a>. '
                      'Use https://api-free.deepl.com/v2/translate for free-tier keys.')
        note.setOpenExternalLinks(True)
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        usage = QWidget()
        usage_row = QHBoxLayout(usage)
        usage_row.setContentsMargins(0, 0, 0, 0)
        usage_row.setSpacing(10)
        self.deepl_usage_btn = QPushButton("Check usage")
        self.deepl_usage_btn.clicked.connect(self._check_deepl_usage)
        self.deepl_usage_bar = QProgressBar()
        self.deepl_usage_bar.setRange(0, 1000)
        self.deepl_usage_bar.setVisible(False)
        self.deepl_usage_label = QLabel("")
        self.deepl_usage_label.setObjectName("dimLabel")
        usage_row.addWidget(self.deepl_usage_btn)
        usage_row.addWidget(self.deepl_usage_bar, 1)
        usage_row.addWidget(self.deepl_usage_label, 1)
        form.addRow("Usage", usage)
        tr_layout.addWidget(self.deepl_group)
        tr_layout.addStretch(1)

        self.translation_provider_combo.currentIndexChanged.connect(
            self._update_translation_provider)
        self._update_translation_provider()
        tabs.addTab(_scrollable(translation), "Translation")

        # AI (OpenAI / Gemini)
        ai_w = QWidget()
        ai_layout = QVBoxLayout(ai_w)
        ai_layout.setContentsMargins(18, 18, 18, 0)
        selector = QFormLayout()
        self.ai_provider_combo = QComboBox()
        self.ai_provider_combo.addItem("OpenAI (ChatGPT)", "openai")
        self.ai_provider_combo.addItem("Google Gemini", "gemini")
        current = str(self.settings.get("ai_provider", "openai")).strip().lower()
        self.ai_provider_combo.setCurrentIndex(max(self.ai_provider_combo.findData(current), 0))
        selector.addRow("Active provider", self.ai_provider_combo)
        ai_layout.addLayout(selector)

        self.openai_key_edit = self._secret(QLineEdit(self.env.get("OPENAI_API_KEY", "")))
        self.gemini_key_edit = self._secret(QLineEdit(self.env.get("GOOGLE_API_KEY", "")))

        ai_tabs = QTabWidget()
        ai_tabs.setDocumentMode(True)
        ai_tabs.addTab(
            self._ai_provider_page(
                "chatgpt", "OpenAI API key (.env)", self.openai_key_edit,
                'Billed per use — get a key at <a href="https://platform.openai.com/api-keys">'
                'platform.openai.com/api-keys</a>. Models: gpt-4o-mini, gpt-4o, gpt-4.1-mini… '
                'API usage — see <a href="https://platform.openai.com/usage">dashboard</a>.'),
            "OpenAI")
        ai_tabs.addTab(
            self._ai_provider_page(
                "gemini", "Google API key (.env)", self.gemini_key_edit,
                'Free tier available — get a key at <a href="https://aistudio.google.com/app/apikey">'
                'aistudio.google.com/app/apikey</a>. Models: gemini-2.5-flash, gemini-2.5-flash-lite… '
                'API usage — see <a href="https://aistudio.google.com/usage">AI Studio</a>.',
                extra_rows=[("Thinking budget (0 = off, -1 = auto)",
                             self._spin("gemini_thinking_budget", -1, 24576, 0))]),
            "Gemini")
        ai_tabs.setCurrentIndex(self.ai_provider_combo.currentIndex())
        self.ai_provider_combo.currentIndexChanged.connect(ai_tabs.setCurrentIndex)
        ai_layout.addWidget(ai_tabs, 1)
        tabs.addTab(ai_w, "AI")

        # Sync
        sync = QWidget()
        form = QFormLayout(sync)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow("Enable cloud sync", self._check("enable_sync", False))
        self.supabase_url_edit = QLineEdit(self.env.get("SUPABASE_URL", ""))
        form.addRow("Supabase URL (.env)", self.supabase_url_edit)
        self.supabase_key_edit = self._secret(QLineEdit(self.env.get("SUPABASE_KEY", "")))
        form.addRow("Supabase key (.env)", self.supabase_key_edit)
        form.addRow("Bin cleanup grace (days)", self._spin("cleanup_grace_period_days", 1, 365, 30))
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_supabase)
        form.addRow(test_btn)
        note = QLabel("Restart the app after enabling sync for the first time.")
        note.setObjectName("dimLabel")
        form.addRow(note)
        tabs.addTab(_scrollable(sync), "Sync")

        return tabs

    def _system_tab(self):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(18, 18, 18, 18)
        self.autostart_check = QCheckBox("Start automatically on login (minimized to tray)")
        self.autostart_check.setChecked(get_autostart_enabled())
        form.addRow(self.autostart_check)
        autostart_note = QLabel("Autostart launches the app with the --minimized flag, so it "
                                "begins hidden in the tray.")
        autostart_note.setObjectName("dimLabel")
        autostart_note.setWordWrap(True)
        form.addRow(autostart_note)

        self.hotkey_edit = QKeySequenceEdit(
            QKeySequence(self.settings.get("hotkey", "Ctrl+Shift+V")))
        try:
            self.hotkey_edit.setMaximumSequenceLength(1)
            self.hotkey_edit.setClearButtonEnabled(True)
        except AttributeError:  # Qt < 6.4/6.5
            pass
        form.addRow("Add Word hotkey (global)", self.hotkey_edit)
        hotkey_note = QLabel("Click the field and press the desired key combination — it opens "
                             "'Add Word' with the clipboard content from anywhere. "
                             "Leave empty to disable.")
        hotkey_note.setObjectName("dimLabel")
        hotkey_note.setWordWrap(True)
        form.addRow(hotkey_note)
        return _scrollable(widget)

    # ----------------------------------------------------------- actions

    def _add_font(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add Font", "",
                                              "TrueType fonts (*.ttf)")
        if not path:
            return
        try:
            os.makedirs("fonts", exist_ok=True)
            shutil.copy(path, os.path.join("fonts", os.path.basename(path)))
        except Exception as exc:
            QMessageBox.warning(self, "Add Font", f"Could not copy the font file:\n{exc}")
            return
        name = os.path.splitext(os.path.basename(path))[0]
        if self.w_font_name.findText(name) < 0:
            self.w_font_name.addItem(name)
        self.w_font_name.setCurrentText(name)

    def _save_import_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Import Template",
                                              "import-template.xlsx", "Excel files (*.xlsx)")
        if not path:
            return
        try:
            from app.core.importer import create_import_template
            create_import_template(path)
            QMessageBox.information(self, "Import Template",
                                    f"Template saved to:\n{path}\n\n"
                                    "Fill it with your words (replace the example rows) "
                                    "and import it via the app menu → Import Excel to Database.")
        except Exception as exc:
            QMessageBox.critical(self, "Import Template", f"Could not save the template:\n{exc}")

    def _pick_bg_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Background Image", "",
                                              "Images (*.png *.jpg *.jpeg)")
        if path:
            self.w_bg_image.setText(path)

    def _pick_credentials(self):
        path, _ = QFileDialog.getOpenFileName(self, "Google Cloud Credentials", "",
                                              "JSON files (*.json)")
        if path:
            self.w_google_cloud_tts_credentials_path.setText(path)

    def _test_supabase(self):
        _write_env({
            "SUPABASE_URL": self.supabase_url_edit.text().strip(),
            "SUPABASE_KEY": self.supabase_key_edit.text().strip(),
        })
        try:
            from app.core.supabase_client import SupabaseClient
            client = SupabaseClient()
            if client.is_connected():
                QMessageBox.information(self, "Supabase", "Connection successful! ✅")
            else:
                QMessageBox.warning(self, "Supabase",
                                    "Could not connect. Check the URL/key and your internet connection.")
        except Exception as exc:
            QMessageBox.critical(self, "Supabase", f"Connection test failed:\n{exc}")

    def _update_translation_provider(self):
        """Show the DeepL fields only when DeepL is the selected provider."""
        is_deepl = self.translation_provider_combo.currentData() == "deepl"
        self.deepl_group.setVisible(is_deepl)
        self.google_note.setVisible(not is_deepl)

    def _check_deepl_usage(self):
        """Fetch DeepL quota with the key currently typed in the form."""
        api_key = self.w_api_key.text().strip()
        api_url = self.w_api_url.text().strip()
        self.deepl_usage_btn.setEnabled(False)
        self.deepl_usage_bar.setVisible(False)
        self.deepl_usage_label.setStyleSheet("")
        self.deepl_usage_label.setText("Checking…")

        def done(result):
            count, limit = result
            self.deepl_usage_btn.setEnabled(True)
            if limit > 0:
                percent = count / limit * 100
                self.deepl_usage_bar.setValue(min(1000, round(percent * 10)))
                self.deepl_usage_bar.setFormat(f"{percent:.1f}%")
                self.deepl_usage_bar.setVisible(True)
                self.deepl_usage_label.setText(
                    f"{count:,} / {limit:,} characters this period")
            else:
                self.deepl_usage_label.setText(f"{count:,} characters used")

        def fail(message):
            from app.ui import theme
            self.deepl_usage_btn.setEnabled(True)
            self.deepl_usage_label.setStyleSheet(
                f"color: {theme.current_colors()['danger']};")
            self.deepl_usage_label.setText(message)

        run_in_thread(translator.get_usage, api_key, api_url,
                      on_result=done, on_error=fail)

    def save(self):
        updated = dict(self.settings)
        for key in list(updated.keys()):
            widget = getattr(self, f"w_{key}", None)
            if widget is None:
                continue
            if isinstance(widget, ColumnPicker):
                updated[key] = widget.exclude_csv()
            elif isinstance(widget, ColorButton):
                updated[key] = widget.color()
            elif isinstance(widget, QLineEdit):
                updated[key] = widget.text()
            elif isinstance(widget, QTextEdit):
                updated[key] = widget.toPlainText().replace("\n", "\\n")
            elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                updated[key] = str(widget.value())
            elif isinstance(widget, QCheckBox):
                updated[key] = "True" if widget.isChecked() else "False"
            elif isinstance(widget, QComboBox):
                updated[key] = widget.currentText()

        seq = self.hotkey_edit.keySequence().toString(QKeySequence.PortableText)
        updated["hotkey"] = seq.split(", ")[0]  # first chord only

        updated["ai_provider"] = self.ai_provider_combo.currentData()
        updated["translation_provider"] = self.translation_provider_combo.currentData()

        save_settings(updated)

        env_updates = {}
        if self.openai_key_edit.text().strip() != self.env.get("OPENAI_API_KEY", ""):
            env_updates["OPENAI_API_KEY"] = self.openai_key_edit.text().strip()
        if self.gemini_key_edit.text().strip() != self.env.get("GOOGLE_API_KEY", ""):
            env_updates["GOOGLE_API_KEY"] = self.gemini_key_edit.text().strip()
        if self.supabase_url_edit.text().strip() != self.env.get("SUPABASE_URL", ""):
            env_updates["SUPABASE_URL"] = self.supabase_url_edit.text().strip()
        if self.supabase_key_edit.text().strip() != self.env.get("SUPABASE_KEY", ""):
            env_updates["SUPABASE_KEY"] = self.supabase_key_edit.text().strip()
        if env_updates:
            _write_env(env_updates)
            if "OPENAI_API_KEY" in env_updates or "GOOGLE_API_KEY" in env_updates:
                from app.core import ai
                ai.reset_clients()

        try:
            set_autostart(self.autostart_check.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, "Autostart", f"Could not update autostart entry:\n{exc}")

        from app.core.audio import google_cloud_tts_problem
        problem = google_cloud_tts_problem()
        if problem:
            QMessageBox.warning(self, "Google Cloud TTS",
                                f"Google Cloud TTS is selected but {problem}\n\n"
                                f"Audio will fall back to gTTS until this is fixed.")

        self.accept()
