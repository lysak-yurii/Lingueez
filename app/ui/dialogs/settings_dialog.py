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
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QKeySequenceEdit,
    QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QWidgetAction,
)

from app.config import get_bool, get_float, get_int, load_settings, save_settings
from app.core import exporters, translator
from app.core.auth_manager import get_auth_manager
from app.i18n import available_languages, tr
from app.system.autostart import get_autostart_enabled, set_autostart
from app.ui.dialogs.account_dialog import AccountDialog
from app.ui.dialogs.base import FramelessDialog
from app.ui.toast import show_toast
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
        super().__init__(parent, title=tr("Settings"))
        self.setMinimumSize(720, 560)
        self.settings = load_settings()
        self.env = _read_env()
        # Hidden flag: add "show_advanced=True" to settings.cfg by hand to expose
        # the AI prompt-template editors. Deliberately absent from DEFAULTS so the
        # key never appears in the file on its own.
        self.show_advanced = get_bool(self.settings, "show_advanced", False)

        layout = self.content_layout
        layout.setContentsMargins(16, 16, 16, 12)

        # Five task-based top-level tabs; related settings are grouped under each
        # (e.g. Read-aloud holds both the voice/playback and the progress thresholds).
        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(), tr("General"))
        self.tabs.addTab(self._read_aloud_tab(), tr("Read-aloud"))
        self.tabs.addTab(self._apis_tab(), tr("Translation & AI"))
        self.tabs.addTab(self._data_tab(), tr("Data"))
        self.tabs.addTab(self._sync_tab(), tr("Sync"))
        layout.addWidget(self.tabs, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton(tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        save_btn = QPushButton(tr("Save"), objectName="primaryButton")
        save_btn.clicked.connect(self.save)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

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
        action.setToolTip(tr("Show / hide"))

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

    def _check(self, key, default=False, label=""):
        box = QCheckBox(label)
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

    def _combo(self, key, values, default=None, display=None):
        # Each item keeps its canonical value as userData so the stored setting
        # stays language-independent; *display* (e.g. tr) only changes the label.
        combo = QComboBox()
        for v in values:
            combo.addItem(display(v) if display else v, v)
        current = str(self.settings.get(key, default or values[0]))
        idx = combo.findData(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
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
        for title, task in ((tr("Definitions"), prefix),
                            (tr("Generated Texts (from words)"), f"{prefix}_texts"),
                            (tr("Generated Texts (by topic)"), f"{prefix}_texts_topic"),
                            (tr("Text Adaptation (to level)"), f"{prefix}_texts_adapt")):
            group = QGroupBox(title)
            g_form = QFormLayout(group)
            g_form.addRow(tr("Model"), self._line(f"{task}_model", 220))
            g_form.addRow(tr("Max tokens"), self._spin(f"{task}_max_tokens", 16, 8000, 400))
            g_form.addRow(tr("Temperature"), self._dspin(f"{task}_temperature", 0, 2, 0.5))
            if self.show_advanced:
                content = QTextEdit(str(self.settings.get(f"{task}_content", "")))
                content.setMaximumHeight(90)
                setattr(self, f"w_{task}_content", content)
                g_form.addRow(tr("Prompt template"), content)
            form.addRow(group)
        return _scrollable(page)

    # -------------------------------------------------------------- tabs

    def _general_tab(self):
        """Look & feel + app behavior (startup, hotkey, updates)."""
        tabs = QTabWidget()
        tabs.addTab(self._appearance_tab(), tr("Appearance"))
        tabs.addTab(self._system_tab(), tr("Behavior"))
        return tabs

    def _read_aloud_tab(self):
        """Everything about Read-aloud: the voice/playback and the listening-driven
        status progression."""
        tabs = QTabWidget()
        tabs.addTab(self._audio_tab(), tr("Audio"))
        tabs.addTab(self._learning_tab(), tr("Progress"))
        return tabs

    def _data_tab(self):
        """Getting words in and out: import + the export formats."""
        tabs = QTabWidget()
        tabs.addTab(self._import_tab(), tr("Import"))
        tabs.addTab(self._export_tab(), tr("Export"))
        return tabs

    def _appearance_tab(self):
        from app.ui.theme import TABLE_DENSITY, TABLE_DENSITY_DEFAULT
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow(tr("Appearance mode"), self._combo("appearance_mode", ["System", "Light", "Dark"], display=tr))
        form.addRow(tr("Widget scaling"), self._dspin("widget_scaling", 0.5, 3.0, 1.0))
        self.settings.setdefault("table_density", TABLE_DENSITY_DEFAULT)
        form.addRow(tr("Table size"), self._combo("table_density", list(TABLE_DENSITY.keys()), TABLE_DENSITY_DEFAULT, display=tr))

        self.language_combo = QComboBox()
        for code, label in available_languages():
            self.language_combo.addItem(label, code)
        current_lang = str(self.settings.get("language", "en"))
        self._initial_language = current_lang
        idx = self.language_combo.findData(current_lang)
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)
        form.addRow(tr("Interface language"), self.language_combo)
        lang_note = QLabel(tr("Restart the app to apply the language change."))
        lang_note.setObjectName("dimLabel")
        form.addRow(lang_note)

        return _scrollable(widget)

    def _export_tab(self):
        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # Excel / CSV
        excel = QWidget()
        layout = QVBoxLayout(excel)
        layout.setContentsMargins(18, 18, 18, 18)
        fmt_group = QGroupBox(tr("Format"))
        form = QFormLayout(fmt_group)
        form.addRow(tr("Data format"), self._combo("excel_format", ["Excel", "CSV"]))
        form.addRow(tr("Columns to export"), self._columns("exclude_columns_excel"))
        layout.addWidget(fmt_group)
        xls_group = QGroupBox(tr("Excel options"))
        form = QFormLayout(xls_group)
        form.addRow(tr("Sheet name"), self._line("sheet_name", 160))
        form.addRow(tr("Start row"), self._spin("start_row", 0, 100))
        form.addRow(tr("Start column"), self._spin("start_column", 0, 100))
        form.addRow(tr("Shade alternate rows"), self._color("alternate_row_color", clearable=True))
        form.addRow(tr("Auto column width"), self._check("auto_column_width", True))
        form.addRow(tr("Freeze header row"), self._check("freeze_panes", False))
        layout.addWidget(xls_group)
        csv_group = QGroupBox(tr("CSV options"))
        form = QFormLayout(csv_group)
        form.addRow(tr("Delimiter"), self._line("csv_delimiter", 80))
        layout.addWidget(csv_group)
        layout.addStretch(1)
        tabs.addTab(_scrollable(excel), "Excel / CSV")

        # TXT
        txt = QWidget()
        form = QFormLayout(txt)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow(tr("Columns to export"), self._columns("exclude_columns_txt"))
        form.addRow(tr("Delimiter (\\t = tab)"), self._line("txt_delimiter", 80))
        form.addRow(tr("Include header lines"), self._check("txt_include_headers", True))
        form.addRow(tr("Header lines"), self._line("txt_header_lines"))
        note = QLabel(tr("Header lines are written at the top of the file — import tools like "
                         "Anki read them (e.g. #separator:tab, #html:true). "
                         "Column names themselves are not written."))
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(_scrollable(txt), "TXT")

        # PDF
        pdf = QWidget()
        layout = QVBoxLayout(pdf)
        layout.setContentsMargins(18, 18, 18, 18)

        page_group = QGroupBox(tr("Page && text"))
        form = QFormLayout(page_group)
        form.addRow(tr("Page size"), self._combo("page_size", ["Letter", "A4"]))
        font_row = QHBoxLayout()
        self.w_font_name = QComboBox()
        self.w_font_name.addItems(exporters.BUILTIN_FONTS + exporters.list_font_names())
        current_font = str(self.settings.get("font_name", exporters.DEFAULT_FONT))
        if self.w_font_name.findText(current_font) < 0:
            self.w_font_name.addItem(current_font)
        self.w_font_name.setCurrentText(current_font)
        font_row.addWidget(self.w_font_name, 1)
        add_font = QPushButton(tr("Add font…"))
        add_font.setToolTip(tr("Copy a .ttf file into the app's fonts folder and use it"))
        add_font.clicked.connect(self._add_font)
        font_row.addWidget(add_font)
        font_w = QWidget()
        font_w.setLayout(font_row)
        form.addRow(tr("Font"), font_w)
        form.addRow(tr("Font size"), self._dspin("font_size", 4, 40, 10))
        form.addRow(tr("Line spacing (pt)"), self._dspin("leading", 4, 60, 12))
        form.addRow(tr("Text alignment"), self._combo("alignment", ["LEFT", "CENTER", "RIGHT"]))
        margins = QHBoxLayout()
        for key in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            margins.addWidget(self._dspin(key, 0, 100, 10))
        margins_w = QWidget()
        margins_w.setLayout(margins)
        form.addRow(tr("Margins L/R/T/B (pt)"), margins_w)
        layout.addWidget(page_group)

        col_group = QGroupBox(tr("Columns"))
        form = QFormLayout(col_group)
        width_spins = {c: self._dspin(f"pdf_col_width_{c}", 0.1, 10,
                                      exporters.PDF_WIDTH_DEFAULTS[c])
                       for c in exporters.EXPORT_COLUMNS + exporters.EXTRA_COLUMNS}
        picker = self._columns("exclude_columns", width_spins=width_spins)
        auto = self._check("pdf_auto_widths", True)
        auto.toggled.connect(lambda on: picker.set_widths_enabled(not on))
        picker.set_widths_enabled(not auto.isChecked())
        form.addRow(tr("Automatic widths (fit page)"), auto)
        form.addRow(tr("Columns / width"), picker)
        layout.addWidget(col_group)

        style_group = QGroupBox(tr("Style"))
        form = QFormLayout(style_group)
        form.addRow(tr("Header background"), self._color("header_bg_color"))
        form.addRow(tr("Header text"), self._color("text_color"))
        form.addRow(tr("Row background"), self._color("bg_color"))
        form.addRow(tr("Grid lines"), self._color("grid_color"))
        if self.settings.get("bg_image") == "No background image":
            self.settings["bg_image"] = ""
        bg_row = QHBoxLayout()
        bg_row.addWidget(self._line("bg_image"))
        browse = QPushButton(tr("Browse…"))
        browse.clicked.connect(self._pick_bg_image)
        bg_row.addWidget(browse)
        clear_bg = QPushButton(tr("Clear"))
        clear_bg.clicked.connect(lambda: self.w_bg_image.clear())
        bg_row.addWidget(clear_bg)
        bg_w = QWidget()
        bg_w.setLayout(bg_row)
        form.addRow(tr("Background image"), bg_w)
        layout.addWidget(style_group)
        layout.addStretch(1)
        tabs.addTab(_scrollable(pdf), "PDF")

        # Audio export (MP3)
        audio_export = QWidget()
        form = QFormLayout(audio_export)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow(tr("Pause between words (s)"), self._dspin("pause_duration", 0, 10, 0.5))
        form.addRow(tr("Repeats per pair"), self._spin("number_of_repeats", 1, 10, 1))
        form.addRow(tr("Concurrent workers"), self._spin("max_concurrent_workers", 1, 16, 2))
        form.addRow(tr("Requests per second"), self._spin("requests_per_sec", 1, 50, 5))
        note = QLabel(tr("Used only when exporting words to an MP3 file. "
                         "The voice itself is configured in the Audio tab."))
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        tabs.addTab(_scrollable(audio_export), tr("Audio (MP3)"))

        return tabs

    def _audio_tab(self):
        """Text-to-speech settings (used by Read Aloud and MP3 export alike)."""
        audio = QWidget()
        form = QFormLayout(audio)
        form.setContentsMargins(18, 18, 18, 18)
        form.addRow(tr("TTS provider"), self._combo("tts_provider", ["gTTS", "google_cloud_tts"]))
        cred_row = QHBoxLayout()
        cred_row.addWidget(self._line("google_cloud_tts_credentials_path"))
        cred_browse = QPushButton(tr("Browse…"))
        cred_browse.clicked.connect(self._pick_credentials)
        cred_row.addWidget(cred_browse)
        cred_w = QWidget()
        cred_w.setLayout(cred_row)
        form.addRow(tr("Google Cloud credentials"), cred_w)
        form.addRow(tr("Voice type"), self._combo("google_cloud_tts_voice_type", ["standard", "wavenet"]))
        form.addRow(tr("Voice name (optional)"), self._line("google_cloud_tts_voice_name"))

        playback_group = QGroupBox(tr("Read Aloud playback"))
        pform = QFormLayout(playback_group)
        pform.addRow(tr("Pause between words (s)"), self._dspin("playback_pause", 0, 10, 0.5))
        pform.addRow(tr("Repeats per word"), self._spin("playback_repeats", 1, 10, 1))
        form.addRow(playback_group)

        note = QLabel(tr("The voice used everywhere words are spoken: in-app Read Aloud "
                         "and MP3 export. gTTS is free and needs no setup. Google Cloud TTS "
                         "needs a service-account JSON key (Cloud Console → IAM & Admin → "
                         "Service Accounts → Keys) and billing enabled on the project — "
                         "usage within the free monthly quota is not charged."))
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
        form.addRow(tr("Promote status while listening"), promote)

        rev = self._spin("playback_reviewing_listens", 1, 9998, 3)
        learn = self._spin("playback_learning_listens", 2, 9999, 15)
        mast = self._spin("playback_mastered_listens", 3, 10000, 100)
        form.addRow(tr("Listens to reach {status}").format(status=tr("Reviewing")), rev)
        form.addRow(tr("Listens to reach {status}").format(status=tr("Learning")), learn)
        form.addRow(tr("Listens to reach {status}").format(status=tr("Mastered")), mast)

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

        note = QLabel(tr("Fully listening to a word in Read Aloud promotes it along the "
                         "familiarity ladder New → Reviewing → Learning → Mastered. Each "
                         "number is the total completed listens needed to reach that level — "
                         "passive audio exposure is weak, so high values are normal. Words "
                         "you set to Mastered or Ignored yourself are never changed, and a "
                         "word is never demoted."))
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        return _scrollable(page)

    def _import_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(18, 18, 18, 18)

        options_group = QGroupBox(tr("Excel import"))
        form = QFormLayout(options_group)
        form.addRow(tr("Placeholder values"), self._line("excel_import_placeholders"))
        form.addRow(tr("Skip placeholder rows"), self._check("excel_import_skip_placeholders", True))
        form.addRow(tr("Skip empty rows"), self._check("excel_import_skip_empty", True))
        form.addRow(tr("Normalize language pairs"), self._check("excel_import_normalize", True))
        layout.addWidget(options_group)

        help_group = QGroupBox(tr("How to import"))
        help_layout = QVBoxLayout(help_group)
        note = QLabel(tr(
            "<ol style='margin:0'>"
            "<li>Prepare an Excel file with the columns <b>Language1, Language2, Word1, "
            "Word2</b> — named like that in a header row (extra columns are ignored), or "
            "without headers, with the first four columns in exactly that order.</li>"
            "<li>Open the app menu → <i>Import Excel to Database…</i> and choose the file.</li>"
            "<li>Review the proposed rows and click <i>Import</i>.</li>"
            "</ol>"))
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        help_layout.addWidget(note)
        template_btn = QPushButton(tr("Save import template…"))
        template_btn.setToolTip(tr("Save a ready-made .xlsx with the right headers and example rows"))
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
        self.translation_provider_combo.addItem(tr("Google Translate (free)"), "google")
        self.translation_provider_combo.addItem("DeepL", "deepl")
        current_provider = str(self.settings.get("translation_provider", "google")).strip().lower()
        self.translation_provider_combo.setCurrentIndex(
            max(self.translation_provider_combo.findData(current_provider), 0))
        selector.addRow(tr("Active provider"), self.translation_provider_combo)
        tr_layout.addLayout(selector)

        self.google_note = QLabel(tr("Google Translate is free and needs no API key."))
        self.google_note.setObjectName("dimLabel")
        self.google_note.setWordWrap(True)
        tr_layout.addWidget(self.google_note)

        # DeepL-specific fields, only shown when DeepL is the active provider.
        self.deepl_group = QGroupBox("DeepL")
        form = QFormLayout(self.deepl_group)
        form.addRow(tr("API key"), self._secret(self._line("api_key")))
        form.addRow(tr("API URL"), self._line("api_url"))
        note = QLabel(tr('Get a key at <a href="https://www.deepl.com/pro-api">deepl.com/pro-api</a>. '
                         'Use https://api-free.deepl.com/v2/translate for free-tier keys.'))
        note.setOpenExternalLinks(True)
        note.setObjectName("dimLabel")
        note.setWordWrap(True)
        form.addRow(note)
        usage = QWidget()
        usage_row = QHBoxLayout(usage)
        usage_row.setContentsMargins(0, 0, 0, 0)
        usage_row.setSpacing(10)
        self.deepl_usage_btn = QPushButton(tr("Check usage"))
        self.deepl_usage_btn.clicked.connect(self._check_deepl_usage)
        self.deepl_usage_bar = QProgressBar()
        self.deepl_usage_bar.setRange(0, 1000)
        self.deepl_usage_bar.setVisible(False)
        self.deepl_usage_label = QLabel("")
        self.deepl_usage_label.setObjectName("dimLabel")
        usage_row.addWidget(self.deepl_usage_btn)
        usage_row.addWidget(self.deepl_usage_bar, 1)
        usage_row.addWidget(self.deepl_usage_label, 1)
        form.addRow(tr("Usage"), usage)
        tr_layout.addWidget(self.deepl_group)
        tr_layout.addStretch(1)

        self.translation_provider_combo.currentIndexChanged.connect(
            self._update_translation_provider)
        self._update_translation_provider()
        tabs.addTab(_scrollable(translation), tr("Translation"))

        # AI (OpenAI / Gemini)
        ai_w = QWidget()
        ai_layout = QVBoxLayout(ai_w)
        ai_layout.setContentsMargins(18, 18, 18, 0)
        selector = QFormLayout()
        self.ai_provider_combo = QComboBox()
        self.ai_provider_combo.addItem(tr("OpenAI (ChatGPT)"), "openai")
        self.ai_provider_combo.addItem(tr("Google Gemini"), "gemini")
        current = str(self.settings.get("ai_provider", "openai")).strip().lower()
        self.ai_provider_combo.setCurrentIndex(max(self.ai_provider_combo.findData(current), 0))
        selector.addRow(tr("Active provider"), self.ai_provider_combo)
        ai_layout.addLayout(selector)

        self.openai_key_edit = self._secret(QLineEdit(self.env.get("OPENAI_API_KEY", "")))
        self.gemini_key_edit = self._secret(QLineEdit(self.env.get("GOOGLE_API_KEY", "")))

        ai_tabs = QTabWidget()
        ai_tabs.setDocumentMode(True)
        ai_tabs.addTab(
            self._ai_provider_page(
                "chatgpt", tr("OpenAI API key (.env)"), self.openai_key_edit,
                tr('Billed per use — get a key at <a href="https://platform.openai.com/api-keys">'
                   'platform.openai.com/api-keys</a>. Models: gpt-4o-mini, gpt-4o, gpt-4.1-mini… '
                   'API usage — see <a href="https://platform.openai.com/usage">dashboard</a>.')),
            "OpenAI")
        ai_tabs.addTab(
            self._ai_provider_page(
                "gemini", tr("Google API key (.env)"), self.gemini_key_edit,
                tr('Free tier available — get a key at <a href="https://aistudio.google.com/app/apikey">'
                   'aistudio.google.com/app/apikey</a>. Models: gemini-2.5-flash, gemini-2.5-flash-lite… '
                   'API usage — see <a href="https://aistudio.google.com/usage">AI Studio</a>.'),
                extra_rows=[(tr("Thinking budget (0 = off, -1 = auto)"),
                             self._spin("gemini_thinking_budget", -1, 24576, 0))]),
            "Gemini")
        ai_tabs.setCurrentIndex(self.ai_provider_combo.currentIndex())
        self.ai_provider_combo.currentIndexChanged.connect(ai_tabs.setCurrentIndex)
        ai_layout.addWidget(ai_tabs, 1)
        tabs.addTab(ai_w, tr("AI"))

        return tabs

    def _sync_tab(self):
        sync = QWidget()
        form = QFormLayout(sync)
        form.setContentsMargins(18, 18, 18, 18)

        from app.core.supabase_client import is_custom_server, custom_server_host
        custom_mode = is_custom_server()

        # --- Status (always visible): what is happening with the data right now ---
        form.addRow(tr("Status"), self._sync_status_label())

        if not custom_mode:
            # --- Accounts (built-in server: cloud sync follows being signed in) ---
            # Several accounts can be remembered on one device and switched between
            # without re-entering the password; the active one is what syncs. In
            # personal own-server mode there is no sign-in, so this is hidden.
            self.accounts_box = QVBoxLayout()
            self.accounts_box.setContentsMargins(0, 0, 0, 0)
            accounts_container = QWidget()
            accounts_container.setLayout(self.accounts_box)
            form.addRow(tr("Accounts"), accounts_container)

            # Only truly global actions live here. Per-account actions (add local
            # items / export / delete) hang off the active account's own row in a
            # tidy "⋮" menu, so this row stays short and never overflows.
            acct_row = QHBoxLayout()
            self.addaccount_btn = QPushButton(tr("Add account…"))
            self.addaccount_btn.clicked.connect(self._open_account_dialog)
            self.local_btn = QPushButton(tr("Turn off cloud sync"))
            self.local_btn.clicked.connect(self._use_local_only)
            for b in (self.addaccount_btn, self.local_btn):
                acct_row.addWidget(b)
            acct_row.addStretch(1)
            form.addRow(acct_row)
            self._refresh_account_ui()

        # Bring-your-own Supabase project (advanced, personal use). Hidden by
        # default — the app ships with a built-in central project, so normal users
        # just sign in. Reveal by setting show_advanced=True in settings.cfg; once a
        # custom server is configured the section also stays visible so it can be
        # tested or disconnected without hand-editing files.
        if self.show_advanced or custom_mode:
            group = QGroupBox(tr("Use your own Supabase server (personal)"))
            g = QFormLayout(group)
            # Leave clear room below the group title — at OS display scaling the default
            # top margin is too tight and clips the first line of the note.
            g.setContentsMargins(12, 18, 12, 12)

            note = QLabel(tr("Personal, single-user sync to a Supabase project you own. "
                             "No account or sign-in — the app connects with the project's "
                             "anon key. Run the schema SQL in your project, paste its URL "
                             "and anon key below, test it, then press “Use this server”.\n\n"
                             "Note: anyone with this URL and key can read the data, so "
                             "keep the project private and don't share the key."))
            note.setObjectName("dimLabel")
            note.setWordWrap(True)
            g.addRow(note)

            # Prefill from the active server, or the last-saved one (so disconnecting
            # never loses the credentials — they can be re-activated with one click).
            saved_url = self.env.get("SUPABASE_URL") or self.env.get("CUSTOM_SUPABASE_URL", "")
            saved_key = self.env.get("SUPABASE_KEY") or self.env.get("CUSTOM_SUPABASE_KEY", "")
            self.supabase_url_edit = QLineEdit(saved_url)
            g.addRow(tr("Supabase URL"), self.supabase_url_edit)
            self.supabase_key_edit = self._secret(QLineEdit(saved_key))
            g.addRow(tr("Supabase key (anon)"), self.supabase_key_edit)

            btn_row = QHBoxLayout()
            self.test_btn = QPushButton(tr("Test Connection"))
            self.test_btn.clicked.connect(self._test_supabase)
            btn_row.addWidget(self.test_btn)
            self.use_server_btn = None
            if custom_mode:
                disconnect_btn = QPushButton(tr("Disconnect — use the built-in server"),
                                             objectName="dangerButton")
                disconnect_btn.clicked.connect(self._disconnect_custom_server)
                btn_row.addWidget(disconnect_btn)
            else:
                self.use_server_btn = QPushButton(tr("Use this server"), objectName="primaryButton")
                self.use_server_btn.clicked.connect(self._use_custom_server)
                btn_row.addWidget(self.use_server_btn)
            btn_row.addStretch(1)
            g.addRow(btn_row)

            schema_row = QHBoxLayout()
            copy_schema_btn = QPushButton(tr("Copy schema SQL"))
            copy_schema_btn.clicked.connect(self._copy_schema_sql)
            schema_row.addWidget(copy_schema_btn)
            open_editor_btn = QPushButton(tr("Open SQL editor ↗"))
            open_editor_btn.clicked.connect(lambda: QDesktopServices.openUrl(
                QUrl("https://supabase.com/dashboard/project/_/sql/new")))
            schema_row.addWidget(open_editor_btn)
            schema_row.addStretch(1)
            g.addRow(schema_row)

            # Bin retention is a power-user concern — kept out of the normal
            # (built-in) view. The Bin auto-prunes at launch on the default grace
            # period regardless; this only exposes the knob to advanced users.
            g.addRow(tr("Bin cleanup grace (days)"),
                     self._spin("cleanup_grace_period_days", 1, 365, 30))

            form.addRow(group)

        return _scrollable(sync)

    def _sync_status_label(self):
        """Plain-language 'what's happening with my data' line (dot + sentence) shown
        at the top of the Sync tab. The dot turns green only once a live check confirms
        the active backend is actually reachable — so wrong credentials don't read as
        'connected'. Kept fresh via _update_sync_status() after account changes."""
        label = QLabel()
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        self.sync_status_lbl = label
        self._update_sync_status()
        return label

    def _update_sync_status(self):
        """(Re)compute the Sync-tab status line for the current mode, then verify
        reachability on a worker thread and recolour the dot. Safe to call repeatedly;
        a token guards against a stale probe overwriting a newer one."""
        label = getattr(self, "sync_status_lbl", None)
        if label is None:
            return
        from app.ui import theme
        from app.core.supabase_client import is_custom_server, custom_server_host, get_supabase
        colors = theme.current_colors()
        auth = get_auth_manager()

        def render(dot, color, text):
            try:
                label.setText(f'<span style="color:{color};">{dot}</span> {text}')
            except RuntimeError:
                pass  # dialog closed; label gone

        if is_custom_server():
            on_text = tr("Cloud sync is on — your own server ({host})").format(
                host=custom_server_host() or tr("your server"))
        elif auth.is_logged_in():
            name = auth.current_user_name()
            email = auth.current_user()
            who = (f"{name} ({email})" if name and email and name != email
                   else (email or name or auth.current_user_id() or ""))
            on_text = tr("Cloud sync is on — signed in as {who}").format(who=who)
        else:
            render("○", colors["text_dim"],
                   tr("Cloud sync is off — your words are saved on this device only"))
            return

        # Active backend: stay neutral until a live probe confirms it actually connects.
        render("●", colors["text_dim"], on_text + "  " + tr("(checking…)"))
        token = object()
        self._sync_status_token = token

        def probe():
            try:
                return get_supabase().is_connected()
            except Exception:
                return False

        def done(ok):
            if getattr(self, "_sync_status_token", None) is not token:
                return  # superseded by a newer refresh
            if ok:
                render("●", colors["success"], on_text)
            else:
                render("●", colors["danger"], on_text + "  " + tr("(can't connect)"))

        run_in_thread(probe, on_result=done, on_error=lambda _e: done(False))

    def _system_tab(self):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(18, 18, 18, 18)
        self.autostart_check = QCheckBox(tr("Start automatically on login (minimized to tray)"))
        self.autostart_check.setChecked(get_autostart_enabled())
        form.addRow(self.autostart_check)

        self.hotkey_edit = QKeySequenceEdit(
            QKeySequence(self.settings.get("hotkey", "Ctrl+Shift+V")))
        try:
            self.hotkey_edit.setMaximumSequenceLength(1)
            self.hotkey_edit.setClearButtonEnabled(True)
        except AttributeError:  # Qt < 6.4/6.5
            pass
        form.addRow(tr("Add Word hotkey (global)"), self.hotkey_edit)
        hotkey_note = QLabel(tr("Click the field and press the desired key combination — it opens "
                                "'Add Word' with the clipboard content from anywhere. "
                                "Leave empty to disable."))
        hotkey_note.setObjectName("dimLabel")
        hotkey_note.setWordWrap(True)
        form.addRow(hotkey_note)
        # On Wayland the global hotkey is registered with the desktop itself
        # (it shows up under the system's keyboard shortcuts), so flag that.
        if (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
                or os.environ.get("WAYLAND_DISPLAY")):
            wayland_note = QLabel(tr("On Wayland this shortcut is registered with your "
                                     "desktop and appears in the system keyboard settings."))
            wayland_note.setObjectName("dimLabel")
            wayland_note.setWordWrap(True)
            form.addRow(wayland_note)

        form.addRow(self._check("auto_check_updates", True,
                                tr("Check for updates on startup")))
        updates_note = QLabel(tr("Checks once a day for a newer version and lets you know; "
                                 "nothing is ever downloaded or installed automatically."))
        updates_note.setObjectName("dimLabel")
        updates_note.setWordWrap(True)
        form.addRow(updates_note)
        return _scrollable(widget)

    # ----------------------------------------------------------- actions

    def _add_font(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Add font…"), "",
                                              tr("TrueType fonts (*.ttf)"))
        if not path:
            return
        try:
            os.makedirs(exporters.FONTS_DIR, exist_ok=True)
            shutil.copy(path, os.path.join(exporters.FONTS_DIR, os.path.basename(path)))
        except Exception as exc:
            QMessageBox.warning(self, tr("Add font…"), tr("Could not copy the font file:\n{error}").format(error=exc))
            return
        name = os.path.splitext(os.path.basename(path))[0]
        if self.w_font_name.findText(name) < 0:
            self.w_font_name.addItem(name)
        self.w_font_name.setCurrentText(name)

    def _save_import_template(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Save import template…"),
                                              "import-template.xlsx", tr("Excel files (*.xlsx)"))
        if not path:
            return
        try:
            from app.core.importer import create_import_template
            create_import_template(path)
            QMessageBox.information(self, tr("Save import template…"),
                                    tr("Template saved to:\n{path}\n\n"
                                       "Fill it with your words (replace the example rows) "
                                       "and import it via the app menu → Import Excel to Database.").format(path=path))
        except Exception as exc:
            QMessageBox.critical(self, tr("Save import template…"), tr("Could not save the template:\n{error}").format(error=exc))

    def _pick_bg_image(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Background image"), "",
                                              tr("Images (*.png *.jpg *.jpeg)"))
        if path:
            self.w_bg_image.setText(path)

    def _pick_credentials(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Google Cloud credentials"), "",
                                              tr("JSON files (*.json)"))
        if path:
            self.w_google_cloud_tts_credentials_path.setText(path)

    def _copy_schema_sql(self):
        # The advanced section is the personal own-server path, so copy the
        # single-user (no-RLS) schema — not the multi-user hosted one.
        from app.core.supabase_schema import PERSONAL_SCHEMA_SQL
        QGuiApplication.clipboard().setText(PERSONAL_SCHEMA_SQL)
        QMessageBox.information(self, "Supabase",
                                tr("Schema SQL copied to the clipboard. Open your "
                                   "Supabase project's SQL editor, paste it, and press Run "
                                   "to create the tables."))

    def _use_custom_server(self):
        """Start syncing with the personal own-Supabase server in the fields. Saves the
        creds to both the active slot (SUPABASE_*) and the remembered slot (CUSTOM_*),
        then closes Settings so the main window applies the built-in→custom transition."""
        url = self.supabase_url_edit.text().strip()
        key = self.supabase_key_edit.text().strip()
        if not url or not key:
            QMessageBox.warning(self, "Supabase",
                                tr("Enter your server's URL and anon key first."))
            return
        # Verify the creds actually connect before switching — on a worker thread, so
        # a slow/unreachable server can't freeze the UI. Re-enable the button if the
        # user backs out.
        from app.core.supabase_client import probe_credentials
        self._set_server_busy(True)

        def done(result):
            ok, err = result
            self._set_server_busy(False)
            if not ok:
                detail = (tr("Could not connect to this server:\n{error}").format(error=err)
                          if err else tr("Could not connect to this server."))
                proceed = QMessageBox.warning(
                    self, "Supabase",
                    tr("{detail}\n\nCheck the URL and anon key, and that you've run the "
                       "schema SQL there. Use these details anyway?").format(detail=detail),
                    QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
                if proceed != QMessageBox.Yes:
                    return
            self._activate_custom_server(url, key)

        run_in_thread(lambda: probe_credentials(url, key), on_result=done,
                      on_error=lambda e: done((False, str(e))))

    def _set_server_busy(self, busy):
        for b in (getattr(self, "test_btn", None), getattr(self, "use_server_btn", None)):
            if b is not None:
                b.setEnabled(not busy)
        if getattr(self, "use_server_btn", None) is not None:
            self.use_server_btn.setText(tr("Connecting…") if busy else tr("Use this server"))

    def _activate_custom_server(self, url, key):
        _write_env({"SUPABASE_URL": url, "SUPABASE_KEY": key,
                    "CUSTOM_SUPABASE_URL": url, "CUSTOM_SUPABASE_KEY": key})
        try:
            from app.core.supabase_client import get_supabase
            get_supabase().reconfigure()
        except Exception:
            pass
        show_toast(self, tr("Sync"), tr("Now syncing with your own server."), "success")
        # Server-only close: skip the full settings save + app restyle (nothing visual
        # changed) so the switch is snappy — see MainWindow.open_settings.
        self._server_switch_only = True
        self.accept()

    def _disconnect_custom_server(self):
        """Leave personal own-server mode and revert to the built-in central project.
        The credentials are REMEMBERED (kept in the CUSTOM_* slot) so they can be
        re-activated with one click; only the active slot is cleared. The data stays in
        the user's own project and on disk."""
        confirm = QMessageBox.question(
            self, tr("Disconnect server"),
            tr("Stop syncing with your own Supabase server and use the built-in one "
               "again?\n\nYour words stay in your own project and on this device. The "
               "server details are remembered so you can switch back anytime. You'll be "
               "local-only until you sign into an account."),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if confirm != QMessageBox.Yes:
            return
        # Remember the server we're leaving, then clear only the active slot.
        cur = _read_env()
        updates = {"SUPABASE_URL": "", "SUPABASE_KEY": ""}
        if cur.get("SUPABASE_URL") and cur.get("SUPABASE_KEY"):
            updates["CUSTOM_SUPABASE_URL"] = cur["SUPABASE_URL"]
            updates["CUSTOM_SUPABASE_KEY"] = cur["SUPABASE_KEY"]
        _write_env(updates)
        try:
            from app.core.supabase_client import get_supabase
            get_supabase().reconfigure()
        except Exception:
            pass
        show_toast(self, tr("Sync"), tr("Disconnected — using the built-in server."), "info")
        # Server-only close: skip the full settings save + app restyle so it's snappy.
        self._server_switch_only = True
        # Close Settings so the main window re-applies sync (back to built-in,
        # local-only) and the tab rebuilds cleanly on next open.
        self.accept()

    def _test_supabase(self):
        # Test exactly what is typed, against a throwaway client — never mutate .env or
        # the live connection (testing must not silently switch servers), and never
        # report success for an empty config (which would fall back to the built-in
        # project and look "connected").
        url = self.supabase_url_edit.text().strip()
        key = self.supabase_key_edit.text().strip()
        if not url or not key:
            QMessageBox.warning(self, "Supabase",
                                tr("Enter your server's URL and anon key first, then test."))
            return
        # Probe on a worker thread so a slow/unreachable server doesn't freeze the UI.
        from app.core.supabase_client import probe_credentials
        self._set_server_busy(True)
        self.test_btn.setText(tr("Testing…"))

        def done(result):
            ok, err = result
            self._set_server_busy(False)
            self.test_btn.setText(tr("Test Connection"))
            if ok:
                QMessageBox.information(self, "Supabase", tr("Connection successful! ✅"))
            elif err:
                QMessageBox.critical(self, "Supabase", tr("Connection test failed:\n{error}").format(error=err))
            else:
                QMessageBox.warning(self, "Supabase",
                                    tr("Could not connect. Check the URL/key and your internet connection."))

        run_in_thread(lambda: probe_credentials(url, key), on_result=done,
                      on_error=lambda e: done((False, str(e))))

    # ----------------------------------------------------------- account

    def _sync_manager(self):
        """Lazily build a SyncManager for account-scoped actions (export/delete).
        It shares the process-wide client + auth, so it sees the current session."""
        if getattr(self, "_sm", None) is None:
            from app.core.sync_manager import SyncManager
            self._sm = SyncManager()
        return self._sm

    def _refresh_account_ui(self):
        # The account UI is built only in built-in mode; in personal own-server mode
        # there is no sign-in, so nothing to refresh.
        if not hasattr(self, "accounts_box"):
            return
        auth = get_auth_manager()
        # Rebuild the remembered-accounts list.
        while self.accounts_box.count():
            item = self.accounts_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        accounts = auth.registry.list_accounts()
        active = auth.current_user_id() if auth.is_logged_in() else None
        if not accounts:
            lbl = QLabel(tr("No accounts yet. Add one to sync your words across devices."))
            lbl.setWordWrap(True)
            lbl.setObjectName("dimLabel")
            self.accounts_box.addWidget(lbl)
        else:
            for acc in accounts:
                self.accounts_box.addWidget(self._account_row(acc, active))
        logged_in = auth.is_logged_in()
        self.local_btn.setVisible(logged_in)
        # Signing in / switching / turning sync off changes the live status, so keep
        # the status line in step without needing to reopen Settings.
        self._update_sync_status()

    def _account_row(self, acc, active_uid):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        uid = acc["uid"]
        email = acc.get("email") or uid
        name = acc.get("name")
        is_active = uid == active_uid
        text = f"{name} ({email})" if name and name != email else email
        if is_active:
            text += "  " + tr("(active)")
        elif acc.get("needs_reauth"):
            text += "  " + tr("(sign in again)")
        label = QLabel(text)
        label.setWordWrap(True)
        if is_active:
            label.setObjectName("accentLabel")
        h.addWidget(label, 1)
        if acc.get("needs_reauth") and not is_active:
            signin = QPushButton(tr("Sign in"))
            signin.clicked.connect(self._open_account_dialog)
            h.addWidget(signin)
        elif not is_active:
            switch = QPushButton(tr("Switch"))
            switch.clicked.connect(lambda _=False, u=uid: self._switch_account(u))
            h.addWidget(switch)
        if is_active:
            # Per-account actions live in a compact ⋮ menu on the active account, so
            # they're discoverable but never crowd the row.
            from app.ui import icons, theme
            more = QPushButton()
            more.setIcon(icons.icon("more", theme.current_colors()["text"], 16))
            more.setToolTip(tr("Account actions"))
            menu = QMenu(more)
            self._account_menu_item(menu, tr("Sync this device's data to my account…"),
                                    self._add_local_to_account)
            self._account_menu_item(menu, tr("Export my data…"), self._export_account_data)
            menu.addSeparator()
            self._account_menu_item(menu, tr("Delete account…"), self._delete_account,
                                    danger=True)
            more.setMenu(menu)
            h.addWidget(more)
        remove = QPushButton(tr("Remove"))
        remove.clicked.connect(lambda _=False, u=uid, e=email: self._forget_account(u, e))
        h.addWidget(remove)
        return row

    def _account_menu_item(self, menu, text, slot, danger=False):
        """A menu row that supports a danger (red) colour — QMenu actions can't be
        recoloured directly, so destructive items use a flat, left-aligned button with
        a consistent menu-item look."""
        from app.ui import theme
        colors = theme.current_colors()
        btn = QPushButton(text)
        btn.setFlat(True)
        btn.setCursor(Qt.PointingHandCursor)
        color = colors["danger"] if danger else colors["text"]
        btn.setStyleSheet(
            f"QPushButton{{border:none;text-align:left;padding:6px 14px;color:{color};}}"
            f"QPushButton:hover{{background:{colors['surface_alt']};}}")
        btn.clicked.connect(lambda: (menu.hide(), slot()))
        action = QWidgetAction(menu)
        action.setDefaultWidget(btn)
        menu.addAction(action)

    def _switch_account(self, uid):
        mw = self.parent()
        if mw is not None and hasattr(mw, "switch_active_account"):
            # Explicit account switch — offer to add local-only items afterwards.
            mw.switch_active_account(uid, offer_contribution=True)
        self._refresh_account_ui()

    def _add_local_to_account(self):
        """Manually offer to add local-only words/texts to the signed-in account
        (the non-destructive contribute flow), even if it was opted out / already
        shown this session."""
        auth = get_auth_manager()
        uid = auth.current_user_id() if auth.is_logged_in() else None
        if not uid:
            return
        mw = self.parent()
        if mw is not None and hasattr(mw, "_maybe_offer_local_contribution"):
            mw._maybe_offer_local_contribution(uid, force=True)

    def _use_local_only(self):
        mw = self.parent()
        if mw is not None and hasattr(mw, "switch_active_account"):
            mw.switch_active_account(None)
        self._refresh_account_ui()
        show_toast(self, tr("Account"), tr("Cloud sync turned off — this device only."), "info")

    def _forget_account(self, uid, email):
        confirm = QMessageBox.question(
            self, tr("Remove account"),
            tr("Remove {email} from this device? You can add it again anytime — your "
               "words stay in the cloud, and the local copy remains on disk. Your "
               "cloud data is not deleted.").format(email=email),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if confirm != QMessageBox.Yes:
            return
        auth = get_auth_manager()
        was_active = uid == auth.current_user_id()
        auth.forget_account(uid)
        if was_active:
            mw = self.parent()
            if mw is not None and hasattr(mw, "switch_active_account"):
                mw.switch_active_account(None)
        self._refresh_account_ui()
        show_toast(self, tr("Account"),
                   tr("Removed {email} from this device.").format(email=email), "success")

    def _open_account_dialog(self):
        # Accounts exist only on the built-in central project (always live), so just
        # open the sign-in dialog. The advanced "own server" fields configure a
        # separate, anonymous personal backend and are intentionally NOT applied
        # here — that switch happens on Save / Test Connection.
        dlg = AccountDialog(self, auth=get_auth_manager())
        dlg.authenticated.connect(self._refresh_account_ui)
        self._account_changed = False
        dlg.authenticated.connect(lambda: setattr(self, "_account_changed", True))
        dlg.exec()
        self._refresh_account_ui()
        # A successful sign-in: hand off to the main window to switch to this
        # account's local DB (with first-time adoption prompt) and sync. Done
        # after the dialog closes so any adoption prompt isn't stacked on it.
        if self._account_changed:
            mw = self.parent()
            if mw is not None and hasattr(mw, "switch_active_account"):
                # Explicit sign-in — offer to add local-only items afterwards.
                mw.switch_active_account(get_auth_manager().current_user_id(),
                                         offer_contribution=True)

    def _export_account_data(self):
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export my data…"), "lingueez-account-data.json",
            tr("JSON files (*.json)"))
        if not path:
            return

        def done(result):
            ok, err = result
            show_toast(self, tr("Account"),
                       tr("Your data was exported.") if ok else (err or tr("Export failed.")),
                       "success" if ok else "error", 6000)

        run_in_thread(self._sync_manager().export_user_data, path, on_result=done,
                      on_error=lambda e: show_toast(self, tr("Account"), str(e), "error", 6000))

    def _delete_account(self):
        confirm = QMessageBox.warning(
            self, tr("Delete account"),
            tr("This permanently deletes your account and ALL of your synced words, "
               "texts and tags from the cloud. Your local copy is archived to the "
               "backups folder. This cannot be undone.\n\nDelete your account?"),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if confirm != QMessageBox.Yes:
            return

        def done(result):
            ok, err = result
            if ok:
                # delete_account already archived + removed the local file and forgot
                # the account; just refresh and drop the UI to local-only.
                self._refresh_account_ui()
                mw = self.parent()
                if mw is not None and hasattr(mw, "switch_active_account"):
                    mw.switch_active_account(None)
                show_toast(self, tr("Account"), tr("Account deleted."), "success", 6000)
            else:
                show_toast(self, tr("Account"), err or tr("Could not delete the account."),
                           "error", 7000)

        run_in_thread(self._sync_manager().delete_account, on_result=done,
                      on_error=lambda e: show_toast(self, tr("Account"), str(e), "error", 6000))

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
        self.deepl_usage_label.setText(tr("Checking…"))

        def done(result):
            count, limit = result
            self.deepl_usage_btn.setEnabled(True)
            if limit > 0:
                percent = count / limit * 100
                self.deepl_usage_bar.setValue(min(1000, round(percent * 10)))
                self.deepl_usage_bar.setFormat(f"{percent:.1f}%")
                self.deepl_usage_bar.setVisible(True)
                self.deepl_usage_label.setText(
                    tr("{count} / {limit} characters this period").format(count=f"{count:,}", limit=f"{limit:,}"))
            else:
                self.deepl_usage_label.setText(tr("{count} characters used").format(count=f"{count:,}"))

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
                data = widget.currentData()
                updated[key] = data if data is not None else widget.currentText()

        seq = self.hotkey_edit.keySequence().toString(QKeySequence.PortableText)
        updated["hotkey"] = seq.split(", ")[0]  # first chord only

        updated["ai_provider"] = self.ai_provider_combo.currentData()
        updated["translation_provider"] = self.translation_provider_combo.currentData()
        updated["language"] = self.language_combo.currentData()
        # An explicit pick is final: never let first-run OS detection override it.
        updated["language_configured"] = "True"

        save_settings(updated)

        env_updates = {}
        if self.openai_key_edit.text().strip() != self.env.get("OPENAI_API_KEY", ""):
            env_updates["OPENAI_API_KEY"] = self.openai_key_edit.text().strip()
        if self.gemini_key_edit.text().strip() != self.env.get("GOOGLE_API_KEY", ""):
            env_updates["GOOGLE_API_KEY"] = self.gemini_key_edit.text().strip()
        # Supabase URL/key fields only exist in advanced (bring-your-own) mode. Plain OK
        # just remembers the typed creds in the saved slot (CUSTOM_*) — it does NOT
        # activate a server (that's the explicit "Use this server" button). The only
        # exception: editing the creds while already on the custom server updates the
        # active slot too, so the change takes effect.
        if getattr(self, "supabase_url_edit", None) is not None:
            from app.core.supabase_client import is_custom_server
            url = self.supabase_url_edit.text().strip()
            key = self.supabase_key_edit.text().strip()
            if url and key:
                env_updates["CUSTOM_SUPABASE_URL"] = url
                env_updates["CUSTOM_SUPABASE_KEY"] = key
                if is_custom_server():
                    env_updates["SUPABASE_URL"] = url
                    env_updates["SUPABASE_KEY"] = key
        if env_updates:
            _write_env(env_updates)
            if "OPENAI_API_KEY" in env_updates or "GOOGLE_API_KEY" in env_updates:
                from app.core import ai
                ai.reset_clients()

        try:
            set_autostart(self.autostart_check.isChecked())
        except Exception as exc:
            QMessageBox.warning(self, tr("Autostart"), tr("Could not update autostart entry:\n{error}").format(error=exc))

        from app.core.audio import google_cloud_tts_problem
        problem = google_cloud_tts_problem()
        if problem:
            QMessageBox.warning(self, tr("Google Cloud TTS"),
                                tr("Google Cloud TTS is selected but {problem}\n\n"
                                   "Audio will fall back to gTTS until this is fixed.").format(problem=problem))

        language_changed = updated["language"] != self._initial_language
        self.accept()
        if language_changed:
            self._offer_restart()

    def _offer_restart(self):
        """The language only fully applies on a fresh start (some UI strings are
        resolved at import time), so offer to relaunch the app now."""
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QApplication
        reply = QMessageBox.question(
            self.parent() or self, tr("Interface language"),
            tr("The interface language has changed. Restart now to apply it?"),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply != QMessageBox.Yes:
            return
        # --relaunch tells the new instance to wait for our single-instance lock
        # to free up instead of bailing out with "already running". For a frozen
        # build sys.executable is the app itself; in dev it's the interpreter, so
        # the script path has to be passed along as the first argument.
        args = [a for a in sys.argv[1:] if a != "--relaunch"] + ["--relaunch"]
        if not getattr(sys, "frozen", False):
            args = [sys.argv[0]] + args
        QProcess.startDetached(sys.executable, args)
        QApplication.quit()
