"""Texts page: master-detail browser/reader embedded in the main window.

Left: filterable, sortable list of saved texts rendered as cards.
Right: reader card with inline title editing, a controllable read-aloud
player (pause / sentence skips / click-to-seek / word highlighting),
save and delete.
"""
import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QFontMetrics, QPainter, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPushButton, QSplitter, QStackedLayout, QStyle,
    QStyledItemDelegate, QTextEdit, QVBoxLayout, QWidget,
)

from app.config import load_settings, save_settings
from app.core.audio import lang_codes, speak_word
from app.core.backup_management import backup_database
from app.core.translator import DEEPL_LANGUAGE_CODES, translate
from app.ui import icons
from app.ui.animations import fade_swap
from app.ui.dialogs.add_text import AddTextDialog
from app.ui.dialogs.base import ask_item
from app.ui.dialogs.definition import markup_to_html
from app.ui.reader import ReaderPlayer, ReaderToolbar, _sentence_spans
from app.ui.toast import show_toast
from app.ui.widgets import ElidedLabel
from app.ui.word_popup import WordPopup
from app.ui.workers import run_in_thread

META_ROLE = Qt.UserRole + 1
SNIPPET_ROLE = Qt.UserRole + 2

SORT_NEWEST = "Newest first"
SORT_OLDEST = "Oldest first"
SORT_TITLE = "Title A–Z"
ALL_LANGUAGES = "All languages"
ALL_LEVELS = "All levels"
ALL_TOPICS = "All topics"
CEFR_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


class TextCardDelegate(QStyledItemDelegate):
    """Paints each text as a card: title, meta line and a one-line snippet."""

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._c = colors

    def set_colors(self, colors):
        self._c = colors

    def _fonts(self, base):
        title = QFont(base)
        title.setWeight(QFont.DemiBold)
        small = QFont(base)
        small.setPointSizeF(max(7.0, base.pointSizeF() - 1))
        return title, small

    def sizeHint(self, option, index):
        title_font, small_font = self._fonts(option.font)
        height = (10 + QFontMetrics(title_font).height() + 3
                  + 2 * QFontMetrics(small_font).height() + 2 + 10 + 4)
        return QSize(option.rect.width(), height)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(4, 2, -4, -2)
        if option.state & QStyle.State_Selected:
            painter.setBrush(QColor(self._c["selection"]))
        elif option.state & QStyle.State_MouseOver:
            painter.setBrush(QColor(self._c["surface_alt"]))
        else:
            painter.setBrush(Qt.NoBrush)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 8, 8)

        title_font, small_font = self._fonts(option.font)
        x = rect.x() + 12
        width = rect.width() - 24
        y = rect.y() + 10

        painter.setFont(title_font)
        fm = painter.fontMetrics()
        painter.setPen(QColor(self._c["text"]))
        painter.drawText(x, y + fm.ascent(),
                         fm.elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, width))
        y += fm.height() + 3

        painter.setFont(small_font)
        fm = painter.fontMetrics()
        painter.setPen(QColor(self._c["text_dim"]))
        painter.drawText(x, y + fm.ascent(),
                         fm.elidedText(index.data(META_ROLE) or "", Qt.ElideRight, width))
        y += fm.height() + 2
        painter.drawText(x, y + fm.ascent(),
                         fm.elidedText(index.data(SNIPPET_ROLE) or "", Qt.ElideRight, width))
        painter.restore()


class TextsPage(QWidget):
    """Embedded replacement for the old Texts popup dialog."""

    counts_changed = Signal(int, int)  # (shown, total)
    tts_started = Signal()  # lets the main window stop its word player
    add_word_requested = Signal(str, str)  # (word, language)
    vocab_changed = Signal()  # popup saved a word — main window reloads

    def __init__(self, db_adapter, colors, parent=None):
        super().__init__(parent)
        self.db_adapter = db_adapter
        self._colors = colors
        self.texts = []        # everything fetched from the database
        self.filtered = []     # what the list currently shows
        self.current = None    # text dict loaded in the reader
        self.search_query = ""
        self.is_reading = False
        self.translation_visible = False
        self._trans_request = 0          # stale-result guard, as in WordPopup
        self._translation_cache = {}     # (text_id, target) -> (source, translation)
        self._saved_splitter_sizes = None
        self._syncing_scroll = False
        self._loading = False  # populating editors programmatically
        self._dirty = False
        self._loaded_once = False
        self._themed = []      # (button, icon name, color key, size)
        self._sentence_range = None  # highlight ranges (absolute char offsets)
        self._word_range = None
        self._hover_range = None
        self._popup_range = None     # word the translation popup belongs to
        self._press_pos = None       # word-click gesture tracking
        self._pending_click = None   # viewport pos of a click awaiting the
        self._click_timer = QTimer(self)  # double-click window while reading
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(QApplication.doubleClickInterval())
        self._click_timer.timeout.connect(self._on_deferred_click)

        self._build_ui()

        self.reader = ReaderPlayer(self)
        self.reader.state_changed.connect(self.reader_bar.set_state)
        self.reader.progress_changed.connect(self.reader_bar.set_progress)
        self.reader.sentence_changed.connect(self._on_sentence_changed)
        self.reader.word_changed.connect(self._on_word_changed)
        self.reader.finished.connect(self._reading_finished)
        self.reader.error.connect(self._on_reader_error)
        self.reader_bar.prev_clicked.connect(self.reader.prev_sentence)
        self.reader_bar.toggle_clicked.connect(self.reader.toggle_pause)
        self.reader_bar.next_clicked.connect(self.reader.next_sentence)
        self.reader_bar.stop_clicked.connect(self.stop_reading)
        self.reader_bar.rate_changed.connect(self.reader.set_rate)

        self.word_popup = WordPopup(self._colors, self.db_adapter, parent=self)
        self.word_popup.word_saved.connect(self.vocab_changed.emit)
        self.word_popup.closed.connect(self._on_popup_closed)

    # ------------------------------------------------------------------ UI

    def _icon_button(self, name, color_key, tooltip, slot, size=18):
        btn = QPushButton(objectName="iconButton")
        btn.setIcon(icons.icon(name, self._colors[color_key], size))
        btn.setIconSize(QSize(size, size))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        self._themed.append((btn, name, color_key, size))
        return btn

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 8)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setChildrenCollapsible(False)
        self._splitter = splitter

        # ---------- left: filters + list ----------
        left = QWidget()
        self._left_panel = left
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.addWidget(self._icon_button(
            "plus", "text", "New text (write or paste)",
            lambda: self._open_add_dialog(0)))
        toolbar.addWidget(self._icon_button(
            "globe", "text", "Get text from the Internet (AI / Wikipedia / URL / RSS)",
            lambda: self._open_add_dialog(1)))
        toolbar.addWidget(self._icon_button(
            "download", "text", "Import .txt file(s)", self._import_txt_files))
        toolbar.addStretch(1)
        ll.addLayout(toolbar)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.lang_filter = QComboBox()
        self.lang_filter.setMinimumWidth(120)
        self.lang_filter.addItem(ALL_LANGUAGES)
        self.lang_filter.currentTextChanged.connect(self._refresh_list)
        filter_row.addWidget(self.lang_filter, 1)
        self.sort_combo = QComboBox()
        self.sort_combo.addItems([SORT_NEWEST, SORT_OLDEST, SORT_TITLE])
        self.sort_combo.currentTextChanged.connect(self._refresh_list)
        filter_row.addWidget(self.sort_combo)
        ll.addLayout(filter_row)

        filter_row2 = QHBoxLayout()
        filter_row2.setSpacing(8)
        self.level_filter = QComboBox()
        self.level_filter.addItem(ALL_LEVELS)
        self.level_filter.currentTextChanged.connect(self._refresh_list)
        filter_row2.addWidget(self.level_filter, 1)
        self.topic_filter = QComboBox()
        self.topic_filter.addItem(ALL_TOPICS)
        self.topic_filter.currentTextChanged.connect(self._refresh_list)
        filter_row2.addWidget(self.topic_filter, 1)
        ll.addLayout(filter_row2)

        self.listing = QListWidget(objectName="TextsList")
        self.listing.setMouseTracking(True)
        self.listing.viewport().setAttribute(Qt.WA_Hover, True)
        self.listing.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.listing.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._delegate = TextCardDelegate(self._colors, self.listing)
        self.listing.setItemDelegate(self._delegate)
        self.listing.currentRowChanged.connect(self._on_row_changed)
        ll.addWidget(self.listing, 1)
        splitter.addWidget(left)

        # ---------- right: empty state / reader card ----------
        right = QWidget()
        self.reader_stack = QStackedLayout(right)

        empty = QWidget()
        ev = QVBoxLayout(empty)
        ev.addStretch(1)
        self.empty_icon = QLabel(alignment=Qt.AlignCenter)
        self.empty_icon.setPixmap(icons.pixmap("file-text", self._colors["text_dim"], 44))
        ev.addWidget(self.empty_icon)
        self.empty_title = QLabel("No texts yet", objectName="EmptyTitle",
                                  alignment=Qt.AlignCenter)
        ev.addWidget(self.empty_title)
        self.empty_sub = QLabel("", objectName="dimLabel", alignment=Qt.AlignCenter)
        self.empty_sub.setWordWrap(True)
        ev.addWidget(self.empty_sub)
        ev.addStretch(2)
        self.reader_stack.addWidget(empty)

        card = QFrame(objectName="ReaderCard")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(18, 12, 18, 12)
        cv.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(4)
        self.title_edit = QLineEdit(objectName="ReaderTitle")
        self.title_edit.setPlaceholderText("Title")
        self.title_edit.textEdited.connect(self._mark_dirty)
        top.addWidget(self.title_edit, 1)
        self.tts_btn = self._icon_button("volume", "text", "Read aloud", self.toggle_reading)
        top.addWidget(self.tts_btn)
        self.translate_btn = self._icon_button(
            "translate", "text", "Translate text", self.toggle_translation)
        top.addWidget(self.translate_btn)
        self.trans_lang_btn = self._icon_button(
            "chevron-down", "text", "Translation language",
            self._pick_translation_language, size=12)
        top.addWidget(self.trans_lang_btn)
        self.edit_btn = self._icon_button("edit", "text", "Edit text", self._on_edit_toggled)
        self.edit_btn.setCheckable(True)
        top.addWidget(self.edit_btn)
        delete_btn = self._icon_button("trash", "danger", "Delete text", self.delete_current)
        top.addWidget(delete_btn)
        cv.addLayout(top)

        meta = QHBoxLayout()
        meta.setSpacing(10)
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        self.language_combo.addItems(sorted(lang_codes.keys()))
        self.language_combo.editTextChanged.connect(self._mark_dirty)
        meta.addWidget(self.language_combo)
        self.level_combo = QComboBox()
        self.level_combo.addItems([""] + CEFR_LEVELS)
        self.level_combo.setToolTip("Level")
        self.level_combo.currentTextChanged.connect(self._mark_dirty)
        meta.addWidget(self.level_combo)
        self.topic_edit = QLineEdit()
        self.topic_edit.setPlaceholderText("Topic")
        self.topic_edit.setMaximumWidth(160)
        self.topic_edit.textEdited.connect(self._mark_dirty)
        meta.addWidget(self.topic_edit)
        self.created_label = QLabel("", objectName="dimLabel")
        meta.addWidget(self.created_label)
        meta.addStretch(1)
        cv.addLayout(meta)

        self.words_line = ElidedLabel()
        self.words_line.setObjectName("dimLabel")
        cv.addWidget(self.words_line)

        self.reader_bar = ReaderToolbar(self._colors)
        self.reader_bar.setVisible(False)
        cv.addWidget(self.reader_bar)

        self.body = QTextEdit(objectName="ReaderBody")
        self.body.setReadOnly(True)  # reading-first; editing via the pencil toggle
        self.body.textChanged.connect(self._mark_dirty)
        self.body.viewport().installEventFilter(self)
        self.body.viewport().setMouseTracking(True)
        self.body.viewport().setCursor(Qt.ArrowCursor)
        self.body.setContextMenuPolicy(Qt.CustomContextMenu)
        self.body.customContextMenuRequested.connect(self._body_context_menu)

        # original | translation, side by side while translation mode is on
        self.body_split = QSplitter(Qt.Horizontal)
        self.body_split.setHandleWidth(8)
        self.body_split.setChildrenCollapsible(False)
        self.body_split.addWidget(self.body)
        self.trans_body = QTextEdit(objectName="ReaderBody")
        self.trans_body.setReadOnly(True)
        self.trans_body.viewport().setCursor(Qt.ArrowCursor)
        self.trans_body.setVisible(False)
        self.body_split.addWidget(self.trans_body)
        self.body_split.setStretchFactor(0, 1)
        self.body_split.setStretchFactor(1, 1)
        self.body.verticalScrollBar().valueChanged.connect(
            lambda v: self._sync_scroll(self.body, self.trans_body))
        self.trans_body.verticalScrollBar().valueChanged.connect(
            lambda v: self._sync_scroll(self.trans_body, self.body))
        cv.addWidget(self.body_split, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(4)
        self.prev_btn = self._icon_button("chevron-left", "text", "Previous text",
                                          lambda: self._select_relative(-1))
        bottom.addWidget(self.prev_btn)
        self.page_label = QLabel("", objectName="dimLabel")
        bottom.addWidget(self.page_label)
        self.next_btn = self._icon_button("chevron-right", "text", "Next text",
                                          lambda: self._select_relative(1))
        bottom.addWidget(self.next_btn)
        bottom.addStretch(1)
        self.save_btn = QPushButton("Save Changes", objectName="primaryButton")
        self.save_btn.setVisible(False)  # appears only with unsaved changes
        self.save_btn.clicked.connect(self.save_current)
        bottom.addWidget(self.save_btn)
        cv.addLayout(bottom)

        self.reader_card = card
        self.reader_stack.addWidget(card)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([300, 620])
        root.addWidget(splitter)

    def refresh_theme(self, colors):
        """Re-tint icons and delegate colors after a theme change."""
        self._colors = colors
        self._delegate.set_colors(colors)
        self.listing.viewport().update()
        for btn, name, color_key, size in self._themed:
            btn.setIcon(icons.icon(name, colors[color_key], size))
        if self.is_reading:
            self.tts_btn.setIcon(icons.icon("stop", colors["danger"], 18))
        if self.edit_btn.isChecked():
            self.edit_btn.setIcon(icons.icon("edit", colors["accent_text"], 18))
        if self.translation_visible:
            self.translate_btn.setIcon(icons.icon("translate", colors["accent_text"], 18))
        self.empty_icon.setPixmap(icons.pixmap("file-text", colors["text_dim"], 44))
        self.reader_bar.refresh_theme(colors)
        self.word_popup.refresh_theme(colors)
        self._apply_highlight()

    # ---------------------------------------------------------------- data

    def set_search(self, query):
        self.search_query = query or ""
        if self._loaded_once:
            self._refresh_list()

    def load_texts(self, preserve_id=None):
        """(Re)fetch texts from the database and rebuild the list."""
        if preserve_id is None and self.current is not None:
            preserve_id = self.current.get("ID")
        try:
            self.texts = self.db_adapter.get_texts() or []
        except Exception as exc:
            logging.error(f"Failed to load texts: {exc}")
            self.texts = []
        self._loaded_once = True

        self._repopulate_filter(self.lang_filter, ALL_LANGUAGES, 'Language')
        self._repopulate_filter(self.level_filter, ALL_LEVELS, 'Level',
                                sort_key=lambda v: (CEFR_LEVELS.index(v)
                                                    if v in CEFR_LEVELS else len(CEFR_LEVELS), v))
        self._repopulate_filter(self.topic_filter, ALL_TOPICS, 'Category')

        self._refresh_list(preserve_id=preserve_id)

    def _repopulate_filter(self, combo, all_label, field, sort_key=None):
        """Rebuild a filter combo from distinct values, keeping the selection."""
        values = sorted({str(t.get(field) or '').strip()
                         for t in self.texts if str(t.get(field) or '').strip()},
                        key=sort_key)
        selected = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label)
        combo.addItems(values)
        if selected and combo.findText(selected) >= 0:
            combo.setCurrentText(selected)
        combo.blockSignals(False)

    def _refresh_list(self, *_, preserve_id=None):
        if preserve_id is None and self.current is not None:
            preserve_id = self.current.get("ID")

        query = self.search_query.strip().lower()
        language = self.lang_filter.currentText()
        level = self.level_filter.currentText()
        topic = self.topic_filter.currentText()
        items = list(self.texts)
        if language and language != ALL_LANGUAGES:
            items = [t for t in items if str(t.get('Language') or '').strip() == language]
        if level and level != ALL_LEVELS:
            items = [t for t in items if str(t.get('Level') or '').strip() == level]
        if topic and topic != ALL_TOPICS:
            items = [t for t in items if str(t.get('Category') or '').strip() == topic]
        if query:
            items = [t for t in items if query in str(t.get('Title') or '').lower()
                     or query in str(t.get('Text') or '').lower()
                     or query in str(t.get('Words') or '').lower()]

        sort = self.sort_combo.currentText()
        if sort == SORT_TITLE:
            items.sort(key=lambda t: str(t.get('Title') or '').lower())
        else:
            items.sort(key=lambda t: str(t.get('created_at') or ''),
                       reverse=(sort != SORT_OLDEST))
        self.filtered = items

        for combo, all_label, value in ((self.lang_filter, ALL_LANGUAGES, language),
                                        (self.level_filter, ALL_LEVELS, level),
                                        (self.topic_filter, ALL_TOPICS, topic)):
            combo.setProperty("filterActive", value not in (all_label, ""))
            combo.style().unpolish(combo)
            combo.style().polish(combo)

        self.listing.blockSignals(True)
        self.listing.clear()
        target_row = 0
        for row, text in enumerate(self.filtered):
            title = str(text.get('Title') or '').strip() or "(untitled)"
            lang = str(text.get('Language') or '').strip()
            text_level = str(text.get('Level') or '').strip()
            text_topic = str(text.get('Category') or '').strip()
            date = str(text.get('created_at') or '')[:10]
            item = QListWidgetItem(title)
            item.setData(META_ROLE,
                         " · ".join(p for p in (lang, text_level, text_topic, date) if p))
            item.setData(SNIPPET_ROLE, " ".join(str(text.get('Text') or '').split())[:200])
            self.listing.addItem(item)
            if preserve_id is not None and text.get('ID') == preserve_id:
                target_row = row
        self.listing.blockSignals(False)

        self.counts_changed.emit(len(self.filtered), len(self.texts))

        if not self.filtered:
            self.current = None
            self._set_dirty(False)
            self.close_translation()
            if self.texts:
                self.empty_title.setText("No matching texts")
                self.empty_sub.setText("Try a different search or language filter.")
            else:
                self.empty_title.setText("No texts yet")
                self.empty_sub.setText(
                    "Click “+” to write or paste a text, the globe to fetch one\n"
                    "from the Internet, or select words in the Words view and\n"
                    'use the "Text" action to generate a study text.')
            self.reader_stack.setCurrentIndex(0)
            return

        # setCurrentRow with the same row index would not re-emit, so force
        # a refresh of the reader for the (possibly different) record
        self.listing.setCurrentRow(target_row)
        if self.listing.currentRow() == target_row:
            self._on_row_changed(target_row)

    # ----------------------------------------------------------- new texts

    def _open_add_dialog(self, initial_tab):
        dialog = AddTextDialog(self.window(), self.db_adapter,
                               initial_tab=initial_tab)
        dialog.text_saved.connect(
            lambda text_id: self.load_texts(preserve_id=text_id))
        dialog.show()

    def _import_txt_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import text files", "",
            "Text files (*.txt);;All files (*)")
        if not paths:
            return
        default = self.lang_filter.currentText()
        if default in (ALL_LANGUAGES, ""):
            default = load_settings().get("addtext_language") or "English"
        languages = sorted(lang_codes.keys())
        language, ok = ask_item(
            self, "Import text files", "Language of the imported text(s):",
            languages, languages.index(default) if default in languages else 0,
            True)
        if not ok or not language.strip():
            return
        language = language.strip()

        def work():
            last_id, count, errors = None, 0, []
            for path in paths:
                try:
                    text = self._read_text_file(path)
                except OSError as exc:
                    errors.append(f"{Path(path).name}: {exc}")
                    continue
                result = self.db_adapter.insert_text({
                    'RowNumber': None,
                    'Title': Path(path).stem,
                    'Text': text.strip(),
                    'Words': None,
                    'Language': language,
                    'Level': None,
                    'Category': None,
                })
                if result:
                    last_id, count = result['ID'], count + 1
                else:
                    errors.append(f"{Path(path).name}: insert failed")
            return last_id, count, errors

        def done(result):
            last_id, count, errors = result
            if count:
                backup_database()
                show_toast(self.window(), "Texts",
                           f"Imported {count} text(s).", "success")
                self.load_texts(preserve_id=last_id)
            if errors:
                QMessageBox.warning(self, "Import",
                                    "Some files could not be imported:\n"
                                    + "\n".join(errors))

        run_in_thread(work, on_result=done,
                      on_error=lambda msg: QMessageBox.critical(
                          self, "Import", f"Import failed:\n{msg}"))

    @staticmethod
    def _read_text_file(path):
        # utf-8-sig also reads plain utf-8 and strips a BOM if present
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                return Path(path).read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        return Path(path).read_text(encoding="utf-8", errors="replace")

    # -------------------------------------------------------------- reader

    def _on_row_changed(self, row):
        if row < 0 or row >= len(self.filtered):
            return
        text = self.filtered[row]
        if self._dirty and self.current is not None and text is not self.current:
            self._maybe_save_pending()
        self._show_text(text, row)

    def _show_text(self, text, row):
        if text is not self.current:
            self.stop_reading()
            self._set_edit_mode(False)
        if self.reader_stack.currentIndex() == 1 and self.current is not None \
                and text is not self.current:
            fade_swap(self.reader_card)
        self.current = text

        self._loading = True
        self.title_edit.setText(str(text.get('Title') or ""))
        self.language_combo.setCurrentText(str(text.get('Language') or "English"))
        level = str(text.get('Level') or "").strip()
        self.level_combo.setCurrentText(level if level in CEFR_LEVELS else "")
        self.topic_edit.setText(str(text.get('Category') or "").strip())
        created = str(text.get('created_at') or "")[:16].replace("T", " ")
        self.created_label.setText(f"Created {created}" if created else "")
        words = str(text.get('Words') or "").strip()
        self.words_line.set_full_text(f"From words: {words}" if words else "")
        self.words_line.setVisible(bool(words))
        self.body.setHtml(markup_to_html(str(text.get('Text') or "")))
        self._loading = False
        self._set_dirty(False)

        self.page_label.setText(f"{row + 1} / {len(self.filtered)}")
        self.prev_btn.setEnabled(row > 0)
        self.next_btn.setEnabled(row < len(self.filtered) - 1)
        self.reader_stack.setCurrentIndex(1)
        if self.translation_visible:
            self._translate_current()

    def _select_relative(self, delta):
        row = self.listing.currentRow() + delta
        if 0 <= row < self.listing.count():
            self.listing.setCurrentRow(row)

    # --------------------------------------------------------------- edits

    def _on_edit_toggled(self, checked):
        if checked:
            self.stop_reading()
            self.close_translation()  # edits would make the translation stale
        self._set_edit_mode(checked)

    def _set_edit_mode(self, editing):
        self.edit_btn.setChecked(editing)
        self.edit_btn.setToolTip("Done editing" if editing else "Edit text")
        self.edit_btn.setIcon(icons.icon(
            "edit", self._colors["accent_text" if editing else "text"], 18))
        self.body.setReadOnly(not editing)
        self.body.viewport().setCursor(
            Qt.IBeamCursor if editing else Qt.ArrowCursor)
        self._set_hover(None)
        self._click_timer.stop()
        self._pending_click = None
        if editing:
            self.body.setFocus()

    def _mark_dirty(self, *_):
        if not self._loading and self.current is not None:
            self._set_dirty(True)

    def _set_dirty(self, dirty):
        self._dirty = dirty
        self.save_btn.setVisible(dirty and self.current is not None)

    def _editor_data(self):
        return {
            'Title': self.title_edit.text().strip(),
            'Language': self.language_combo.currentText().strip(),
            'Level': self.level_combo.currentText().strip(),
            'Category': self.topic_edit.text().strip(),
            'Text': self.body.toPlainText().strip(),
        }

    def _write_text(self, text, data):
        try:
            self.db_adapter.update_text(int(text['ID']), data)
            backup_database()
            text.update(data)
            return True
        except Exception as exc:
            logging.error(f"Failed to save text: {exc}")
            QMessageBox.critical(self, "Error", f"Failed to save text:\n{exc}")
            return False

    def _maybe_save_pending(self):
        """Offer to keep edits when navigating away from a modified text."""
        data = self._editor_data()
        previous = self.current
        self._set_dirty(False)
        title = str(previous.get('Title') or '(untitled)')
        if QMessageBox.question(
                self, "Unsaved changes", f"Save changes to '{title}'?",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._write_text(previous, data)

    def save_current(self):
        if self.current is None:
            return
        if self._write_text(self.current, self._editor_data()):
            self._set_dirty(False)
            show_toast(self.window(), "Texts", "Changes saved.", "success")
            self.load_texts(preserve_id=self.current.get('ID'))

    def delete_current(self):
        if self.current is None:
            return
        self.stop_reading()
        title = str(self.current.get('Title') or "(untitled)")
        if QMessageBox.question(self, "Delete Text", f"Delete '{title}'?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        row = self.listing.currentRow()
        neighbor = None
        if 0 <= row < len(self.filtered) - 1:
            neighbor = self.filtered[row + 1].get('ID')
        elif row > 0:
            neighbor = self.filtered[row - 1].get('ID')
        try:
            self.db_adapter.delete_text(int(self.current['ID']))
            backup_database()
        except Exception as exc:
            logging.error(f"Failed to delete text: {exc}")
            QMessageBox.critical(self, "Error", f"Failed to delete text:\n{exc}")
            return
        self.current = None
        self._set_dirty(False)
        show_toast(self.window(), "Texts", f"'{title}' moved to bin.", "success")
        self.load_texts(preserve_id=neighbor)

    # ------------------------------------------------------------- reading

    def toggle_reading(self):
        if self.is_reading:
            self.stop_reading()
        else:
            self.start_reading()

    def start_reading(self, start_char=0):
        """Begin a read-aloud session, optionally from a character offset."""
        if self.current is None:
            return
        language = self.language_combo.currentText()
        if language not in lang_codes:
            show_toast(self.window(), "Reader",
                       f"Unsupported language: {language}", "warning")
            return
        self._set_edit_mode(False)
        self.tts_started.emit()  # the main window stops its word player
        # toPlainText() unstripped: reader offsets must match the document
        if not self.reader.start(self.body.toPlainText(), language,
                                 start_char=start_char):
            return
        self.is_reading = True
        self.reader_bar.reset()
        self.reader_bar.setVisible(True)
        self.tts_btn.setIcon(icons.icon("stop", self._colors["danger"], 18))
        self.tts_btn.setToolTip("Stop reading")

    def stop_reading(self):
        if self.is_reading:
            self.reader.stop()  # emits finished -> _reading_finished

    def _reading_finished(self):
        if not self.is_reading:
            return
        self.is_reading = False
        self._click_timer.stop()
        self._pending_click = None
        self.reader_bar.setVisible(False)
        self._sentence_range = None
        self._word_range = None
        self._apply_highlight()
        self.tts_btn.setIcon(icons.icon("volume", self._colors["text"], 18))
        self.tts_btn.setToolTip("Read aloud")

    def _on_reader_error(self, message):
        show_toast(self.window(), "Reader", message, "warning")

    # --------------------------------------------------------- translation

    def toggle_translation(self):
        if self.translation_visible:
            self.close_translation()
        else:
            self._open_translation()

    def _open_translation(self):
        """Side-by-side mode: list collapses, translation pane opens."""
        if self.current is None or self.translation_visible:
            return
        self._set_edit_mode(False)
        self.translation_visible = True
        self._saved_splitter_sizes = self._splitter.sizes()
        self._left_panel.setVisible(False)
        self.trans_body.setVisible(True)
        total = max(2, sum(self.body_split.sizes()))
        self.body_split.setSizes([total // 2, total - total // 2])
        self.translate_btn.setIcon(
            icons.icon("translate", self._colors["accent_text"], 18))
        self.translate_btn.setToolTip("Hide translation")
        self._translate_current()

    def close_translation(self):
        if not self.translation_visible:
            return
        self.translation_visible = False
        self._trans_request += 1  # orphan any in-flight worker result
        self.trans_body.setVisible(False)
        self._left_panel.setVisible(True)
        if self._saved_splitter_sizes:
            self._splitter.setSizes(self._saved_splitter_sizes)
        self.translate_btn.setIcon(icons.icon("translate", self._colors["text"], 18))
        self.translate_btn.setToolTip("Translate text")

    def _translate_target(self):
        # shared with the word popup, so both translate to the same language
        target = str(load_settings().get("reader_translate_target", "English"))
        return target if target in DEEPL_LANGUAGE_CODES else "English"

    def _pick_translation_language(self):
        menu = QMenu(self)
        current = self._translate_target()
        for name in sorted(DEEPL_LANGUAGE_CODES):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == current)
        chosen = menu.exec(self.trans_lang_btn.mapToGlobal(
            QPoint(0, self.trans_lang_btn.height())))
        if chosen and chosen.text() != current:
            settings = load_settings()
            settings["reader_translate_target"] = chosen.text()
            save_settings(settings)
            if self.translation_visible:
                self._translate_current()

    def _set_translation_text(self, text, dim=False, danger=False):
        color = self._colors["danger"] if danger else (
            self._colors["text_dim"] if dim else None)
        self.trans_body.setStyleSheet(f"color: {color};" if color else "")
        self.trans_body.setPlainText(text)

    def _translate_current(self):
        if self.current is None:
            return
        self._trans_request += 1
        request = self._trans_request
        text = self.body.toPlainText().strip()
        if not text:
            self._set_translation_text("")
            return
        target = self._translate_target()
        source = self.language_combo.currentText()
        source = source if source in DEEPL_LANGUAGE_CODES else None
        if source == target:
            source = None  # let DeepL detect; avoids same-language no-ops
        key = (self.current.get('ID'), target)
        cached = self._translation_cache.get(key)
        if cached and cached[0] == text:
            self._set_translation_text(cached[1])
            return
        self._set_translation_text("Translating…", dim=True)

        def work():
            translation, _detected = translate(text, target, source)
            return translation

        def done(translation):
            if request != self._trans_request:
                return
            self._translation_cache[key] = (text, translation)
            self._set_translation_text(translation)

        def fail(message):
            if request != self._trans_request:
                return
            logging.warning(f"Text translation failed: {message}")
            self._set_translation_text(message, danger=True)
            show_toast(self.window(), "Translation", message, "warning")

        run_in_thread(work, on_result=done, on_error=fail)

    def _sync_scroll(self, src, dst):
        """Keep the two panes roughly aligned by scroll proportion."""
        if self._syncing_scroll or not dst.isVisible():
            return
        sbar, dbar = src.verticalScrollBar(), dst.verticalScrollBar()
        if sbar.maximum() <= 0 or dbar.maximum() <= 0:
            return
        self._syncing_scroll = True
        dbar.setValue(round(sbar.value() / sbar.maximum() * dbar.maximum()))
        self._syncing_scroll = False

    # -------------------------------------------------------- highlighting

    def _on_sentence_changed(self, start, end):
        self._sentence_range = (start, end)
        self._word_range = None
        self._apply_highlight()

    def _on_word_changed(self, start, end):
        if start < 0:
            self._sentence_range = None
            self._word_range = None
            self._apply_highlight()
            return
        self._word_range = (start, end)
        self._apply_highlight()
        self._scroll_to_word()

    def _selection(self, start, end, background):
        selection = QTextEdit.ExtraSelection()
        cursor = QTextCursor(self.body.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        selection.cursor = cursor
        selection.format.setBackground(QColor(background))
        return selection

    def _tint(self, alpha):
        color = QColor(self._colors["accent"])
        color.setAlpha(alpha)
        return color

    def _apply_highlight(self):
        # Translucent accent tints all the way down: the text keeps its
        # normal color and the layers stay calm but distinguishable.
        selections = []
        if self._sentence_range:
            selections.append(self._selection(*self._sentence_range, self._tint(22)))
        if self._hover_range and self._hover_range not in (
                self._word_range, self._popup_range):
            selections.append(self._selection(*self._hover_range, self._tint(55)))
        if self._popup_range and self._popup_range != self._word_range:
            selections.append(self._selection(*self._popup_range, self._tint(55)))
        if self._word_range:
            selections.append(self._selection(*self._word_range, self._tint(95)))
        self.body.setExtraSelections(selections)

    def _scroll_to_word(self):
        """Keep the highlighted word visible without moving the caret."""
        if not self._word_range:
            return
        cursor = QTextCursor(self.body.document())
        cursor.setPosition(self._word_range[0])
        rect = self.body.cursorRect(cursor)
        viewport_height = self.body.viewport().height()
        if rect.top() < 0 or rect.bottom() > viewport_height:
            bar = self.body.verticalScrollBar()
            bar.setValue(bar.value() + rect.center().y() - viewport_height // 2)

    # --------------------------------------------------- word interactions

    def eventFilter(self, obj, event):
        # Read-mode mouse behavior: a plain click on a word pronounces it
        # and opens the translation popup. While reading, that click is held
        # back for one double-click interval so a double click can seek
        # playback to the word instead. Drag-selection (for copying) still
        # works because a release after a drag or with an active selection
        # is left alone. The hovered word gets a subtle highlight.
        if obj is self.body.viewport():
            etype = event.type()
            if etype == QEvent.MouseMove:
                if self.body.isReadOnly():
                    self._set_hover(self._word_at_point(event.position().toPoint()))
            elif etype == QEvent.Leave:
                self._set_hover(None)
            elif self.body.isReadOnly() and etype == QEvent.MouseButtonPress \
                    and event.button() == Qt.LeftButton:
                self._press_pos = event.position().toPoint()
            elif self.body.isReadOnly() and etype == QEvent.MouseButtonDblClick \
                    and event.button() == Qt.LeftButton and self.is_reading:
                self._click_timer.stop()
                self._pending_click = None
                self.reader.seek_to_char(self.body.cursorForPosition(
                    event.position().toPoint()).position())
                return True  # also keeps QTextEdit from selecting the word
            elif self.body.isReadOnly() and etype == QEvent.MouseButtonRelease \
                    and event.button() == Qt.LeftButton:
                press, self._press_pos = self._press_pos, None
                pos = event.position().toPoint()
                if press is not None and (pos - press).manhattanLength() < 8 \
                        and not self.body.textCursor().hasSelection():
                    if self.is_reading:
                        # wait out the double-click window: a second click
                        # means "jump here", not "translate this"
                        self._pending_click = pos
                        self._click_timer.start()
                        return True
                    if self._popup_word_at(pos):
                        return True
        return super().eventFilter(obj, event)

    def _on_deferred_click(self):
        pos, self._pending_click = self._pending_click, None
        if pos is not None and self.body.isReadOnly():
            self._popup_word_at(pos)

    def _popup_word_at(self, pos):
        """Pronounce the word at *pos* and open its translation popup."""
        word_range = self._word_at_point(pos)
        if not word_range:
            return False
        start, end = word_range
        word = self.body.toPlainText()[start:end]
        self._pronounce(word, self.language_combo.currentText())
        self._show_word_popup(word, start, end)
        return True

    def _word_at_point(self, pos):
        """(start, end) of the word under the mouse, or None.

        ExactHit: only react when the pointer is really over text, not in
        the empty area where cursorForPosition snaps to the nearest word.
        """
        layout = self.body.document().documentLayout()
        doc_point = QPointF(pos.x() + self.body.horizontalScrollBar().value(),
                            pos.y() + self.body.verticalScrollBar().value())
        if layout.hitTest(doc_point, Qt.ExactHit) < 0:
            return None
        cursor = self.body.cursorForPosition(pos)
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText()
        if word and any(ch.isalpha() for ch in word):
            return (cursor.selectionStart(), cursor.selectionEnd())
        return None

    def _show_word_popup(self, word, start, end):
        """Anchor the translation popover above the clicked word."""
        text = self.body.toPlainText()
        sentence = next((text[a:b].strip() for a, b in _sentence_spans(text)
                         if a <= start < b), "")
        cursor = QTextCursor(self.body.document())
        cursor.setPosition(start)
        rect = self.body.cursorRect(cursor)
        cursor.setPosition(end)
        rect = rect.united(self.body.cursorRect(cursor))
        anchor = QRect(self.body.viewport().mapToGlobal(rect.topLeft()),
                       rect.size())
        self._popup_range = (start, end)  # keep the word marked while open
        self._apply_highlight()
        self.word_popup.show_for(word, self.language_combo.currentText(),
                                 sentence, anchor)

    def _on_popup_closed(self):
        if self._popup_range:
            self._popup_range = None
            self._apply_highlight()

    def _set_hover(self, word_range):
        if word_range != self._hover_range:
            self._hover_range = word_range
            self._apply_highlight()
        if not self.body.isReadOnly():
            shape = Qt.IBeamCursor
        elif word_range:
            shape = Qt.PointingHandCursor  # click translates; dbl-click seeks
        else:
            shape = Qt.ArrowCursor
        self.body.viewport().setCursor(shape)

    def _body_context_menu(self, pos):
        menu = self.body.createStandardContextMenu(pos)
        cursor = self.body.cursorForPosition(pos)
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText().strip()
        language = self.language_combo.currentText()
        if word and any(ch.isalpha() for ch in word):
            display = word if len(word) <= 24 else word[:21] + "…"
            start_char = cursor.selectionStart()
            pronounce = QAction(f"Pronounce “{display}”", menu)
            pronounce.triggered.connect(lambda: self._pronounce(word, language))
            add = QAction(f"Add “{display}” to vocabulary", menu)
            add.triggered.connect(
                lambda: self.add_word_requested.emit(word, language))
            read_here = QAction("Read from here", menu)
            read_here.triggered.connect(lambda: self._read_from(start_char))
            separator = QAction(menu)
            separator.setSeparator(True)
            first = menu.actions()[0] if menu.actions() else None
            menu.insertActions(first, [pronounce, add, read_here, separator])
        menu.exec(self.body.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _pronounce(self, word, language):
        if language not in lang_codes:
            language = "English"
        self.reader.pause()  # don't talk over the reading voice
        run_in_thread(speak_word, word, language,
                      on_error=lambda msg: show_toast(
                          self.window(), "Reader", msg, "warning"))

    def _read_from(self, start_char):
        if self.is_reading:
            self.reader.seek_to_char(start_char)
        else:
            self.start_reading(start_char=start_char)
