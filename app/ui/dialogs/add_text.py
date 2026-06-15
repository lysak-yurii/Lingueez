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

"""Add Text dialog: write/paste, AI-generate, Wikipedia, URL and RSS sources.

Every source tab fills the same shared editor below the tabs (title, language,
level, topic, preview); one Save button persists to the texts table. The
dialog stays open after saving so several texts can be collected in a row.
"""
import logging

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSizePolicy, QTabWidget, QTextEdit,
    QToolTip, QVBoxLayout, QWidget,
)

from app.config import load_settings, save_settings
from app.core import ai, text_sources
from app.core.audio import lang_codes
from app.core.backup_management import backup_database
from app.i18n import fill_lang_combo, get_lang, set_lang, tr
from app.ui import icons
from app.ui.animations import fade_swap
from app.ui.dialogs.base import FramelessDialog, ask_text
from app.ui.toast import show_toast
from app.ui.workers import run_in_thread

CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
TOPIC_CHIPS = [tr("Travel"), tr("Food"), tr("Daily routine"), tr("A short story"),
               tr("News"), tr("Dialogue at a café")]

TAB_WRITE, TAB_AI, TAB_WIKI, TAB_URL, TAB_RSS = range(5)


def _lengths():
    return [(tr("Short (~100 words)"), 100),
            (tr("Medium (~250 words)"), 250),
            (tr("Long (~500 words)"), 500)]


def _tab_hints():
    return {
        TAB_WRITE: tr("Type or paste a text into the editor below, give it a title, set the language — then save."),
        TAB_AI: tr("Generates a text with AI using the Language, Level and Topic fields below. Pick a topic chip or type your own."),
        TAB_WIKI: tr('Searches Wikipedia in the selected language. Click a result to load the article; use "Adapt to my level" to simplify it.'),
        TAB_URL: tr("Extracts the readable article text from any web page. Pages behind logins or built purely with JavaScript may not work."),
        TAB_RSS: tr('News feeds for the selected language. Load a feed, then double-click an entry to fetch its full text. Add your own feeds with "Add feed…".'),
    }


def _preview_placeholder():
    return tr("Type or paste your text here, or fetch one with the tabs above…")
LIST_MIN_HEIGHT = 180


class AddTextDialog(FramelessDialog):
    """Collect a text from any source and save it to the texts table."""

    text_saved = Signal(int)  # ID of the newly inserted text

    def __init__(self, parent, db_adapter, initial_tab=TAB_WRITE):
        super().__init__(parent, tr("Add Text"))
        self.db_adapter = db_adapter
        self.setMinimumSize(680, 560)

        settings = load_settings()

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_write_tab(), tr("Write"))
        self.tabs.addTab(self._build_ai_tab(), tr("AI Generate"))
        self.tabs.addTab(self._build_wiki_tab(), tr("Wikipedia"))
        self.tabs.addTab(self._build_url_tab(), tr("From URL"))
        self.tabs.addTab(self._build_rss_tab(), tr("RSS"))
        self.tabs.currentChanged.connect(self._tab_changed)

        self.help_btn = QPushButton(objectName="iconButton")
        self.help_btn.setIcon(icons.icon("help-circle",
                                         self.colors["text_dim"], 16))
        self.help_btn.setIconSize(QSize(16, 16))
        self.help_btn.setCursor(Qt.PointingHandCursor)
        self.help_btn.setFocusPolicy(Qt.NoFocus)
        self.help_btn.clicked.connect(self._show_help)
        self.tabs.setCornerWidget(self.help_btn, Qt.TopRightCorner)
        self.content_layout.addWidget(self.tabs)

        # ---- shared metadata + editor ----
        meta = QHBoxLayout()
        meta.setSpacing(10)
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        fill_lang_combo(self.language_combo, sorted(lang_codes.keys()))
        set_lang(self.language_combo, settings.get("addtext_language") or "English")
        self.language_combo.currentTextChanged.connect(self._language_changed)
        meta.addWidget(QLabel(tr("Language:")))
        meta.addWidget(self.language_combo, 1)
        self.level_combo = QComboBox()
        self.level_combo.addItems([""] + CEFR_LEVELS)
        self.level_combo.setCurrentText(settings.get("addtext_level") or "")
        meta.addWidget(QLabel(tr("Level:")))
        meta.addWidget(self.level_combo)
        self.topic_edit = QLineEdit()
        self.topic_edit.setPlaceholderText(tr("Topic…"))
        meta.addWidget(QLabel(tr("Topic:")))
        meta.addWidget(self.topic_edit, 1)
        self.content_layout.addLayout(meta)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(tr("Title…"))
        self.content_layout.addWidget(self.title_edit)

        self.preview = QTextEdit()
        self.preview.setPlaceholderText(_preview_placeholder())
        self.preview.textChanged.connect(self._preview_changed)
        self.content_layout.addWidget(self.preview, 1)

        self._loading_base = ""
        self._loading_dots = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(350)
        self._loading_timer.timeout.connect(self._tick_loading)

        self.status_label = QLabel("", objectName="dimLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)  # only takes a row when needed
        self.content_layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.adapt_btn = QPushButton(tr("Adapt to my level"))
        self.adapt_btn.setIcon(icons.icon("sparkles",
                                          self.colors["text"], 16))
        self.adapt_btn.setToolTip(
            tr("Rewrite the text below for the selected CEFR level with {ai}").format(
                ai=ai.provider_label()))
        self.adapt_btn.clicked.connect(self._adapt)
        self.adapt_btn.setEnabled(False)
        buttons.addWidget(self.adapt_btn)
        buttons.addStretch(1)
        self.save_btn = QPushButton(tr("Save to Texts"), objectName="primaryButton")
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setEnabled(False)
        buttons.addWidget(self.save_btn)
        close_btn = QPushButton(tr("Close"))
        close_btn.clicked.connect(self.close)
        buttons.addWidget(close_btn)
        self.content_layout.addLayout(buttons)

        self.tabs.setCurrentIndex(initial_tab)
        self._tab_changed(initial_tab)
        self._reload_feeds()

    # ----------------------------------------------------------------- tabs

    @staticmethod
    def _tab_page():
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        return tab, layout

    def _build_write_tab(self):
        # the editor placeholder says it all; details live in the "?" hint
        tab, _layout = self._tab_page()
        return tab

    def _build_ai_tab(self):
        tab, layout = self._tab_page()

        chips = QHBoxLayout()
        chips.setSpacing(6)
        chips.addWidget(QLabel(tr("Ideas:"), objectName="dimLabel"))
        for chip in TOPIC_CHIPS:
            btn = QPushButton(chip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, c=chip: self.topic_edit.setText(c))
            chips.addWidget(btn)
        chips.addStretch(1)
        layout.addLayout(chips)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.length_combo = QComboBox()
        for label, _words in _lengths():
            self.length_combo.addItem(label)
        self.length_combo.setCurrentIndex(1)
        row.addWidget(QLabel(tr("Length:")))
        row.addWidget(self.length_combo)
        row.addStretch(1)
        self.generate_btn = QPushButton(
            tr("Generate with AI"),
            objectName="primaryButton")
        self.generate_btn.setIcon(icons.icon("sparkles",
                                             self.colors["accent_text"], 16))
        self.generate_btn.clicked.connect(self._generate)
        row.addWidget(self.generate_btn)
        layout.addLayout(row)
        return tab

    def _build_wiki_tab(self):
        tab, layout = self._tab_page()
        row = QHBoxLayout()
        row.setSpacing(8)
        self.wiki_query = QLineEdit()
        self.wiki_query.setPlaceholderText(
            tr("Search Wikipedia (in the selected language)…"))
        self.wiki_query.returnPressed.connect(self._wiki_search)
        row.addWidget(self.wiki_query, 1)
        self.wiki_search_btn = QPushButton(tr("Search"))
        self.wiki_search_btn.clicked.connect(self._wiki_search)
        row.addWidget(self.wiki_search_btn)
        layout.addLayout(row)
        self.wiki_results = QListWidget()
        self.wiki_results.setMinimumHeight(LIST_MIN_HEIGHT)
        self.wiki_results.setVisible(False)  # shown once there are results
        self.wiki_results.itemActivated.connect(self._wiki_load)
        self.wiki_results.itemClicked.connect(self._wiki_load)
        layout.addWidget(self.wiki_results, 1)
        self.wiki_toggle = QPushButton()
        self.wiki_toggle.setVisible(False)
        self.wiki_toggle.setCursor(Qt.PointingHandCursor)
        self.wiki_toggle.clicked.connect(
            lambda: self._expand_list(self.wiki_results, self.wiki_toggle))
        layout.addWidget(self.wiki_toggle)
        return tab

    def _build_url_tab(self):
        tab, layout = self._tab_page()
        row = QHBoxLayout()
        row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://example.com/article…")
        self.url_edit.returnPressed.connect(self._fetch_url)
        row.addWidget(self.url_edit, 1)
        self.url_fetch_btn = QPushButton(tr("Fetch"))
        self.url_fetch_btn.clicked.connect(self._fetch_url)
        row.addWidget(self.url_fetch_btn)
        layout.addLayout(row)
        return tab

    def _build_rss_tab(self):
        tab, layout = self._tab_page()
        row = QHBoxLayout()
        row.setSpacing(8)
        self.feed_combo = QComboBox()
        self.feed_combo.currentIndexChanged.connect(self._update_remove_btn)
        row.addWidget(self.feed_combo, 1)
        self.feed_refresh_btn = QPushButton(tr("Load entries"))
        self.feed_refresh_btn.clicked.connect(self._load_feed)
        row.addWidget(self.feed_refresh_btn)
        add_btn = QPushButton(tr("Add feed…"))
        add_btn.clicked.connect(self._add_feed)
        row.addWidget(add_btn)
        self.feed_remove_btn = QPushButton(tr("Remove"))
        self.feed_remove_btn.clicked.connect(self._remove_feed)
        row.addWidget(self.feed_remove_btn)
        layout.addLayout(row)
        self.feed_entries = QListWidget()
        self.feed_entries.setMinimumHeight(LIST_MIN_HEIGHT)
        self.feed_entries.setVisible(False)  # shown once entries are loaded
        self.feed_entries.setToolTip(
            tr("Double-click an entry to load its full text."))
        self.feed_entries.itemActivated.connect(self._load_entry)
        layout.addWidget(self.feed_entries, 1)
        self.feed_toggle = QPushButton()
        self.feed_toggle.setVisible(False)
        self.feed_toggle.setCursor(Qt.PointingHandCursor)
        self.feed_toggle.clicked.connect(
            lambda: self._expand_list(self.feed_entries, self.feed_toggle))
        layout.addWidget(self.feed_toggle)
        return tab

    # ------------------------------------------------------------ plumbing

    def _tab_changed(self, index):
        self.help_btn.setToolTip(_tab_hints().get(index, ""))
        self._fit_tab_height(index)

    def _show_help(self):
        QToolTip.showText(QCursor.pos(),
                          _tab_hints().get(self.tabs.currentIndex(), ""),
                          self.help_btn)

    def _set_status(self, text):
        """Inline feedback line; takes no space while there is nothing to say."""
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _fit_tab_height(self, index):
        """Size the tab widget to the current tab only, not the tallest one.

        QTabWidget's sizeHint follows its tallest page (the Wikipedia/RSS
        lists), which leaves the short tabs (Write, AI, From URL) padded with
        dead space. Cap the height at the current page's hint instead, so the
        editor below gets the room; the list tabs stay uncapped.
        """
        for i in range(self.tabs.count()):
            vertical = (QSizePolicy.Preferred if i == index
                        else QSizePolicy.Ignored)
            self.tabs.widget(i).setSizePolicy(QSizePolicy.Preferred, vertical)
        has_list = ((index == TAB_WIKI and self.wiki_results.isVisible())
                    or (index == TAB_RSS and self.feed_entries.isVisible()))
        if has_list:
            self.tabs.setMaximumHeight(16777215)  # QWIDGETSIZE_MAX
        else:
            bar = self.tabs.tabBar().sizeHint().height()
            page = self.tabs.widget(index)
            if page.findChildren(QWidget):
                self.tabs.setMaximumHeight(page.sizeHint().height() + bar + 14)
            else:  # empty page (Write): no content, so no pane either
                self.tabs.setMaximumHeight(bar + 2)
        self.tabs.updateGeometry()

    def _run_async(self, button, work, on_result, loading=None, on_fail=None):
        """Run *work* in a thread; status + button state handled here.

        *loading* puts an animated "Fetching…" placeholder into the (cleared)
        editor while the work runs; *on_fail* runs after an error, e.g. to
        re-expand a collapsed result list.
        """
        button.setEnabled(False)
        self._set_status(tr("Working…"))
        if loading:
            self._start_loading(loading)

        def done(result):
            if not self.isVisible():
                return
            if loading:
                self._stop_loading()
            self._set_status("")
            on_result(result)

        def fail(message):
            if not self.isVisible():
                return
            if loading:
                self._stop_loading()
            self._set_status(str(message))
            if on_fail:
                on_fail()

        run_in_thread(work, on_result=done, on_error=fail,
                      on_finished=lambda: button.setEnabled(True))

    def _start_loading(self, message):
        self._loading_base = message
        self._loading_dots = 0
        self.preview.clear()
        self.preview.setPlaceholderText(message)
        self._loading_timer.start()

    def _tick_loading(self):
        self._loading_dots = (self._loading_dots + 1) % 4
        self.preview.setPlaceholderText(
            self._loading_base + "." * self._loading_dots)

    def _stop_loading(self):
        self._loading_timer.stop()
        self.preview.setPlaceholderText(_preview_placeholder())

    def _set_text(self, title, text):
        if title:
            self.title_edit.setText(title)
        self.preview.setPlainText(text or "")

    def _collapse_list(self, list_widget, toggle):
        """Swap a filled result list for its toggle, crossfading the change.

        Like the dashboard page switch: the new layout is applied instantly
        (one relayout, nothing slides around) under a fading snapshot of the
        old state.
        """
        count = list_widget.count()
        if not count or not list_widget.isVisible():
            return
        fade_swap(self)
        toggle.setText(tr("Show the {count} result(s) again").format(count=count))
        toggle.setVisible(True)
        list_widget.setVisible(False)
        self._fit_tab_height(self.tabs.currentIndex())

    def _expand_list(self, list_widget, toggle):
        """Bring a result list back (after a search, a failure, or the toggle)."""
        if list_widget.isVisible() and not toggle.isVisible():
            self._fit_tab_height(self.tabs.currentIndex())
            return
        fade_swap(self)
        toggle.setVisible(False)
        list_widget.setVisible(True)
        self._fit_tab_height(self.tabs.currentIndex())

    def _preview_changed(self):
        has_text = bool(self.preview.toPlainText().strip())
        self.save_btn.setEnabled(has_text)
        self.adapt_btn.setEnabled(has_text)

    def _language_changed(self, *_):
        self._reload_feeds()

    # ----------------------------------------------------------------- AI

    def _require_api_key(self):
        if ai.has_api_key():
            return True
        self._set_status(
            tr("{ai} API key is not set. Configure it in Settings → APIs → AI.").format(
                ai=ai.provider_label()))
        return False

    def _generate(self):
        if not self._require_api_key():
            return
        language = get_lang(self.language_combo).strip() or "English"
        level = self.level_combo.currentText().strip() or "B1"
        topic = self.topic_edit.text().strip() or "anything interesting"
        length = _lengths()[self.length_combo.currentIndex()][1]
        self._run_async(self.generate_btn,
                        lambda: ai.generate_topic_text(language, level,
                                                       topic, length),
                        lambda result: self._set_text(*result),
                        loading=tr("Generating with {ai}…").format(ai=ai.provider_label()))

    def _adapt(self):
        if not self._require_api_key():
            return
        text = self.preview.toPlainText().strip()
        if not text:
            return
        language = get_lang(self.language_combo).strip() or "English"
        level = self.level_combo.currentText().strip() or "B1"
        self._run_async(self.adapt_btn,
                        lambda: ai.adapt_text_to_level(text, language, level),
                        lambda result: self._set_text(*result))

    # ------------------------------------------------------------ Wikipedia

    def _wiki_search(self):
        query = self.wiki_query.text().strip()
        if not query:
            return
        language = get_lang(self.language_combo).strip() or "English"

        def show(results):
            self.wiki_results.clear()
            for result in results:
                label = result["title"]
                if result["description"]:
                    label += f" — {result['description']}"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, result)
                item.setToolTip(result["excerpt"])
                self.wiki_results.addItem(item)
            self._expand_list(self.wiki_results, self.wiki_toggle)

        self._run_async(self.wiki_search_btn,
                        lambda: text_sources.wikipedia_search(query, language),
                        show)

    def _wiki_load(self, item):
        result = item.data(Qt.UserRole)
        if not result:
            return
        language = get_lang(self.language_combo).strip() or "English"
        if not self.topic_edit.text().strip():
            self.topic_edit.setText(self.wiki_query.text().strip())

        # free the editor right away; bring the list back if the fetch fails
        self._collapse_list(self.wiki_results, self.wiki_toggle)
        self._run_async(self.wiki_search_btn,
                        lambda: text_sources.wikipedia_fetch(result["title"],
                                                             language),
                        lambda r: self._set_text(*r),
                        loading=tr('Fetching "{title}"…').format(title=result['title']),
                        on_fail=lambda: self._expand_list(self.wiki_results,
                                                          self.wiki_toggle))

    # ------------------------------------------------------------------ URL

    def _fetch_url(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        self._run_async(self.url_fetch_btn,
                        lambda: text_sources.extract_url(url),
                        lambda result: self._set_text(*result))

    # ------------------------------------------------------------------ RSS

    def _reload_feeds(self):
        language = get_lang(self.language_combo).strip()
        self.feed_combo.clear()
        for feed in text_sources.feeds_for_language(language):
            suffix = "  " + tr("(yours)") if feed.get("user") else ""
            self.feed_combo.addItem(feed["name"] + suffix, feed)
        self.feed_refresh_btn.setEnabled(self.feed_combo.count() > 0)
        self._update_remove_btn()

    def _update_remove_btn(self, *_):
        feed = self.feed_combo.currentData()
        self.feed_remove_btn.setEnabled(bool(feed and feed.get("user")))

    def _load_feed(self):
        feed = self.feed_combo.currentData()
        if not feed:
            return

        def show(entries):
            self.feed_entries.clear()
            for entry in entries:
                label = entry["title"] or "(untitled)"
                if entry["published"]:
                    label += f"  ·  {entry['published']}"
                item = QListWidgetItem(label)
                item.setData(Qt.UserRole, entry)
                self.feed_entries.addItem(item)
            self._expand_list(self.feed_entries, self.feed_toggle)

        self._run_async(self.feed_refresh_btn,
                        lambda: text_sources.fetch_feed(feed["url"]), show)

    def _load_entry(self, item):
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        self._collapse_list(self.feed_entries, self.feed_toggle)
        self._run_async(self.feed_refresh_btn,
                        lambda: text_sources.fetch_feed_entry(entry),
                        lambda result: self._set_text(*result),
                        loading=tr("Fetching the full text…"),
                        on_fail=lambda: self._expand_list(self.feed_entries,
                                                          self.feed_toggle))

    def _add_feed(self):
        name, ok = ask_text(self, tr("Add feed"), tr("Feed name:"))
        if not ok or not name.strip():
            return
        url, ok = ask_text(self, tr("Add feed"), tr("Feed URL:"))
        if not ok or not url.strip():
            return
        feeds = text_sources.user_feeds()
        feeds.append({"name": name.strip(), "url": url.strip(),
                      "language": get_lang(self.language_combo).strip()})
        text_sources.save_user_feeds(feeds)
        self._reload_feeds()
        self.feed_combo.setCurrentIndex(self.feed_combo.count() - 1)

    def _remove_feed(self):
        feed = self.feed_combo.currentData()
        if not feed or not feed.get("user"):
            return
        feeds = [f for f in text_sources.user_feeds()
                 if f.get("url") != feed.get("url")]
        text_sources.save_user_feeds(feeds)
        self._reload_feeds()

    # ----------------------------------------------------------------- save

    def _save(self):
        text = self.preview.toPlainText().strip()
        if not text:
            return
        title = self.title_edit.text().strip()
        language = get_lang(self.language_combo).strip()
        level = self.level_combo.currentText().strip()
        topic = self.topic_edit.text().strip()
        try:
            result = self.db_adapter.insert_text({
                'RowNumber': None,
                'Title': title,
                'Text': text,
                'Words': None,
                'Language': language,
                'Level': level,
                'Category': topic,
            })
            if not result:
                self._set_status(tr("Failed to save the text."))
                return
            backup_database()
        except Exception as exc:
            logging.error(f"Failed to save text: {exc}")
            self._set_status(tr("Failed to save the text: {error}").format(error=exc))
            return

        settings = load_settings()
        settings["addtext_language"] = language
        settings["addtext_level"] = level
        save_settings(settings)

        self.text_saved.emit(int(result['ID']))
        show_toast(self, tr("Texts"),
                   tr("'{title}' saved.").format(title=title or tr("(untitled)")),
                   "success")
        self.title_edit.clear()
        self.preview.clear()
        self._set_status("")  # the toast is confirmation enough
