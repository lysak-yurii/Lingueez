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

"""Definition viewer/editor with AI generation.

Renders the stored '***' / '**' / '*' markup as rich text, supports
editing the raw definition, switching between Definition (Word1) and
Definition2 (Word2), and generating missing definitions via the
configured AI provider (ChatGPT or Gemini).
"""
import html
import logging
import re

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QShortcut, QTextBlockFormat, QTextCharFormat,
    QTextCursor, QTextListFormat,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextEdit,
    QVBoxLayout, QWidget,
)

from app.core import ai
from app.i18n import lang_label, tr
from app.ui import icons
from app.ui.dialogs.base import FramelessDialog
from app.ui.workers import run_in_thread


# ── shared markup model ───────────────────────────────────────────────────
# The stored format is a tiny markup: ``***heading***``, ``**bold**``,
# ``*italic*`` and ``- `` bullet lines. Both the read-only HTML view and the
# WYSIWYG editor build on one tokenizer so they can never drift apart.

def _inline_runs(text):
    """Split inline markup into ``(text, bold, italic)`` runs. Plain segments
    keep their newlines (soft breaks) for the paragraph builder to handle."""
    runs = []
    pos = 0
    for m in re.finditer(r'\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*(.+?)\*', text, flags=re.S):
        if m.start() > pos:
            runs.append((text[pos:m.start()], False, False))
        if m.group(1) is not None:        # ***x*** (inline) → bold
            runs.append((m.group(1), True, False))
        elif m.group(2) is not None:      # **x** → bold
            runs.append((m.group(2), True, False))
        else:                             # *x* → italic
            runs.append((m.group(3), False, True))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], False, False))
    return runs or [("", False, False)]


_LIST_RE = re.compile(r'^\s*-\s+')
_HEADING_RE = re.compile(r'\*\*\*(.+?)\*\*\*', flags=re.S)


def parse_markup(text):
    """Tokenize the stored markup into blocks: ``("heading", runs)``,
    ``("list", [item_runs, …])`` or ``("para", runs)``.

    Line-oriented so a ``***heading***`` line or a run of ``- `` bullets is
    recognized even when not separated by a blank line from its neighbours
    (the AI often writes a heading directly above its list)."""
    if not text:
        return []
    lines = str(text).replace('\r\n', '\n').split('\n')
    blocks, para = [], []

    def flush_para():
        joined = "\n".join(para).strip()
        if joined:
            blocks.append(("para", _inline_runs(joined)))
        para.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        heading = _HEADING_RE.fullmatch(stripped)
        if not stripped:
            flush_para()
            i += 1
        elif heading:
            flush_para()
            blocks.append(("heading", _inline_runs(heading.group(1).strip())))
            i += 1
        elif _LIST_RE.match(line):
            flush_para()
            items = []
            while i < n and _LIST_RE.match(lines[i]):
                items.append(_inline_runs(_LIST_RE.sub('', lines[i]).strip()))
                i += 1
            blocks.append(("list", items))
        else:
            para.append(line)
            i += 1
    flush_para()
    return blocks


def _runs_to_html(runs):
    parts = []
    for text, bold, italic in runs:
        esc = html.escape(text).replace("\n", "<br>")
        if bold:
            esc = f"<b>{esc}</b>"
        if italic:
            esc = f"<i>{esc}</i>"
        parts.append(esc)
    return "".join(parts)


def markup_to_html(text):
    """Render the stored markup as clean block HTML — ``<h3>`` headings,
    ``<ul>`` lists and ``<p>`` paragraphs — so spacing stays even."""
    out = []
    for kind, payload in parse_markup(text):
        if kind == "heading":
            out.append(f"<h3>{_runs_to_html(payload)}</h3>")
        elif kind == "list":
            items = "".join(f"<li>{_runs_to_html(it)}</li>" for it in payload)
            out.append(f"<ul>{items}</ul>")
        else:
            out.append(f"<p>{_runs_to_html(payload)}</p>")
    return "".join(out)


class _DefinitionEditor(QTextEdit):
    """Editor that pastes as plain text, so web content can't inject fonts or
    structures the markup can't represent (keeps the round-trip lossless)."""

    def insertFromMimeData(self, source):
        self.insertPlainText(source.text())


class DefinitionDialog(FramelessDialog):
    definition_changed = Signal()

    def __init__(self, parent, record, db_adapter):
        super().__init__(parent, title=tr("Definition — {word}").format(word=record.get('Word1', '')))
        self.record = record
        self.db_adapter = db_adapter
        self.word_id = record["ID"]
        self.current_field = 'Word1'   # which word's definition is shown
        self._pick_initial_field = True  # on first load, open the side that has a definition
        self.editing = False
        self.ai_label = ai.provider_label()

        self.setMinimumSize(620, 480)
        self.setAttribute(Qt.WA_DeleteOnClose)

        layout = self.content_layout
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # Header: the word being defined, led by a small book glyph.
        head = QHBoxLayout()
        head.setSpacing(10)
        glyph = QLabel()
        glyph.setPixmap(icons.icon("book-open", self.colors["text_dim"], 20).pixmap(QSize(20, 20)))
        head.addWidget(glyph, 0, Qt.AlignVCenter)
        self.header_label = QLabel(objectName="AppTitle")
        head.addWidget(self.header_label, 0, Qt.AlignVCenter)
        head.addStretch(1)
        layout.addLayout(head)

        # Segmented toggle: pick which side's definition to show — the word's or
        # its translation's. Replaces the old "Show … definition" text button.
        # It shares its slot with the formatting toolbar (shown while editing).
        self.chips_row = QWidget()
        toggle = QHBoxLayout(self.chips_row)
        toggle.setContentsMargins(0, 0, 0, 0)
        toggle.setSpacing(6)
        self.lang_chips = []
        for field, lang_key in (('Word1', 'Language1'), ('Word2', 'Language2')):
            chip = QPushButton(lang_label(self.record.get(lang_key, '')), objectName="chipButton")
            chip.setCheckable(True)
            chip.setAutoExclusive(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _checked, f=field: self._select_field(f))
            toggle.addWidget(chip)
            self.lang_chips.append((chip, field))
        toggle.addStretch(1)
        layout.addWidget(self.chips_row)

        self.format_row = self._build_format_toolbar()
        self.format_row.hide()
        layout.addWidget(self.format_row)

        # Definition body inside a soft card: a rich-text view that doubles as the
        # WYSIWYG editor, with a centered empty state when nothing is stored.
        card = QFrame(objectName="DefinitionCard")
        card.setStyleSheet(
            f"#DefinitionCard{{background:{self.colors['surface']};"
            f" border:1px solid {self.colors['border']}; border-radius:10px;}}")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(4, 4, 4, 4)
        card_lay.setSpacing(0)
        self.text = _DefinitionEditor()
        self.text.setReadOnly(True)
        self.text.setStyleSheet("QTextEdit{background:transparent; border:none; padding:8px 10px;}")
        # Themed, tight block spacing for the rendered definition (accent
        # headings; modest paragraph gaps instead of blank-line pile-up).
        self.text.document().setDefaultStyleSheet(
            f"h3 {{ color:{self.colors['accent_text']}; margin-top:14px; margin-bottom:2px; }}"
            "p { margin-top:0px; margin-bottom:8px; }"
            "li { margin-bottom:3px; }")
        self.text.cursorPositionChanged.connect(self._sync_toolbar)
        self.text.currentCharFormatChanged.connect(lambda _f: self._sync_toolbar())
        for seq, btn, handler in (
                (QKeySequence.Bold, lambda: self.fmt_bold, self._toggle_bold),
                (QKeySequence.Italic, lambda: self.fmt_italic, self._toggle_italic)):
            sc = QShortcut(seq, self.text)
            sc.setContext(Qt.WidgetShortcut)
            sc.activated.connect(lambda b=btn, h=handler: self._shortcut(b(), h))
        card_lay.addWidget(self.text, 1)
        self.empty_widget = self._build_empty_state()
        card_lay.addWidget(self.empty_widget, 1)
        layout.addWidget(card, 1)

        # Footer: the AI action on the left; edit/save/cancel + close on the right.
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.generate_btn = QPushButton(tr("Generate with AI"), objectName="tonalButton")
        self.generate_btn.setIcon(icons.icon("sparkles", self.colors["accent_text"], 15))
        self.generate_btn.setIconSize(QSize(15, 15))
        self.generate_btn.setCursor(Qt.PointingHandCursor)
        self.generate_btn.clicked.connect(self.generate_definition)
        buttons.addWidget(self.generate_btn)
        buttons.addStretch(1)

        self.edit_btn = QPushButton(tr("Edit"))
        self.edit_btn.setIcon(icons.icon("edit", self.colors["text_dim"], 15))
        self.edit_btn.setIconSize(QSize(15, 15))
        self.edit_btn.setCursor(Qt.PointingHandCursor)
        self.edit_btn.clicked.connect(self.toggle_edit)
        buttons.addWidget(self.edit_btn)

        self.cancel_btn = QPushButton(tr("Cancel"))
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancel_edit)
        self.cancel_btn.hide()
        buttons.addWidget(self.cancel_btn)

        self.save_btn = QPushButton(tr("Save"), objectName="primaryButton")
        self.save_btn.setIcon(icons.icon("check", "#ffffff", 15))
        self.save_btn.setIconSize(QSize(15, 15))
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(self.save_definition)
        self.save_btn.hide()
        buttons.addWidget(self.save_btn)

        self.close_btn = QPushButton(tr("Close"))
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)
        buttons.addWidget(self.close_btn)
        layout.addLayout(buttons)

        self.reload_word()

    def _build_empty_state(self):
        """Centered placeholder shown in the card when no definition is stored."""
        wrap = QWidget()
        col = QVBoxLayout(wrap)
        col.setContentsMargins(20, 20, 20, 20)
        col.setSpacing(8)
        col.addStretch(1)
        glyph = QLabel()
        glyph.setPixmap(icons.icon("file-text", self.colors["text_dim"], 34).pixmap(QSize(34, 34)))
        glyph.setAlignment(Qt.AlignCenter)
        col.addWidget(glyph)
        title = QLabel(tr("No definition yet"), objectName="EmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        col.addWidget(title)
        self.empty_hint = QLabel(objectName="dimLabel")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        self.empty_hint.setWordWrap(True)
        col.addWidget(self.empty_hint)
        col.addStretch(1)
        return wrap

    def _show_body(self, empty):
        """Swap between the editor/viewer and the empty placeholder. The editor
        always wins while editing, regardless of content."""
        show_empty = empty and not self.editing
        self.text.setVisible(not show_empty)
        self.empty_widget.setVisible(show_empty)

    # --------------------------------------------------------- formatting

    def _build_format_toolbar(self):
        """Bold / Italic / Heading / List toggles, shown in place of the chips
        while editing."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.fmt_bold = self._fmt_button("bold", tr("Bold"), self._toggle_bold)
        self.fmt_italic = self._fmt_button("italic", tr("Italic"), self._toggle_italic)
        self.fmt_heading = self._fmt_button("type", tr("Heading"), self._toggle_heading)
        self.fmt_list = self._fmt_button("list", tr("List"), self._toggle_list)
        for btn in (self.fmt_bold, self.fmt_italic, self.fmt_heading, self.fmt_list):
            h.addWidget(btn)
        h.addStretch(1)
        return row

    def _fmt_button(self, icon_name, tooltip, slot):
        btn = QPushButton(objectName="iconButton")
        btn.setCheckable(True)
        btn.setIcon(icons.icon(icon_name, self.colors["text_dim"], 16))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(slot)
        return btn

    def _shortcut(self, btn, handler):
        if not self.editing:
            return
        btn.toggle()           # mirror the button so its checked state drives the handler
        handler()

    def _heading_char_format(self, on):
        fmt = QTextCharFormat()
        size = self.text.document().defaultFont().pointSizeF()
        if on:
            fmt.setFontWeight(QFont.Bold)
            fmt.setForeground(QColor(self.colors["accent_text"]))
            if size > 0:
                fmt.setFontPointSize(size + 3)
        else:
            fmt.setFontWeight(QFont.Normal)
            fmt.setForeground(QColor(self.colors["text"]))
            if size > 0:
                fmt.setFontPointSize(size)
        return fmt

    def _selected_blocks(self):
        doc = self.text.document()
        cur = self.text.textCursor()
        start, end = sorted((cur.selectionStart(), cur.selectionEnd()))
        block = doc.findBlock(start)
        blocks = []
        while block.isValid():
            blocks.append(block)
            if block.position() + block.length() - 1 >= end:
                break
            block = block.next()
        return blocks

    def _merge_char(self, fmt):
        cur = self.text.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.WordUnderCursor)
        cur.mergeCharFormat(fmt)
        self.text.mergeCurrentCharFormat(fmt)
        self.text.setFocus()

    def _toggle_bold(self):
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Bold if self.fmt_bold.isChecked() else QFont.Normal)
        self._merge_char(fmt)

    def _toggle_italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(self.fmt_italic.isChecked())
        self._merge_char(fmt)

    def _toggle_heading(self):
        on = self.fmt_heading.isChecked()
        cur = self.text.textCursor()
        cur.beginEditBlock()
        for block in self._selected_blocks():
            bcur = QTextCursor(block)
            bf = bcur.blockFormat()
            bf.setHeadingLevel(3 if on else 0)
            bcur.setBlockFormat(bf)
            bcur.setPosition(block.position())
            bcur.setPosition(block.position() + block.length() - 1, QTextCursor.KeepAnchor)
            bcur.setCharFormat(self._heading_char_format(on))
        cur.endEditBlock()
        self.text.setFocus()

    def _toggle_list(self):
        cur = self.text.textCursor()
        if self.fmt_list.isChecked():
            fmt = QTextListFormat()
            fmt.setStyle(QTextListFormat.ListDisc)
            cur.createList(fmt)
        else:
            cur.beginEditBlock()
            for block in self._selected_blocks():
                lst = block.textList()
                if lst is not None:
                    lst.remove(block)
                    bf = block.blockFormat()
                    bf.setObjectIndex(-1)
                    bf.setIndent(0)
                    QTextCursor(block).setBlockFormat(bf)
            cur.endEditBlock()
        self.text.setFocus()

    def _sync_toolbar(self):
        """Reflect the format under the cursor in the toolbar's checked states."""
        if not self.editing:
            return
        cf = self.text.currentCharFormat()
        block = self.text.textCursor().block()
        is_heading = block.blockFormat().headingLevel() >= 1
        for btn, value in (
                (self.fmt_bold, cf.fontWeight() >= QFont.Bold and not is_heading),
                (self.fmt_italic, cf.fontItalic()),
                (self.fmt_heading, is_heading),
                (self.fmt_list, block.textList() is not None)):
            btn.blockSignals(True)
            btn.setChecked(bool(value))
            btn.blockSignals(False)

    # ------------------------------------------------- markup <-> document

    def _load_markup_into_editor(self, markup):
        """Build the editor document from stored markup so the user edits
        formatted text — headings, bold/italic runs and bullet lists — never the
        raw asterisks."""
        doc = self.text.document()
        doc.blockSignals(True)
        doc.clear()
        cursor = QTextCursor(doc)
        first = True
        for kind, payload in parse_markup(markup):
            if not first:
                cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
            first = False
            if kind == "heading":
                bf = QTextBlockFormat()
                bf.setHeadingLevel(3)
                cursor.setBlockFormat(bf)
                self._insert_runs(cursor, payload, heading=True)
            elif kind == "list":
                lst_fmt = QTextListFormat()
                lst_fmt.setStyle(QTextListFormat.ListDisc)
                lst = None
                for i, item in enumerate(payload):
                    if i == 0:
                        lst = cursor.createList(lst_fmt)
                    else:
                        cursor.insertBlock()
                        lst.add(cursor.block())
                    self._insert_runs(cursor, item, heading=False)
            else:
                cursor.setBlockFormat(QTextBlockFormat())
                self._insert_runs(cursor, payload, heading=False)
        doc.blockSignals(False)
        self.text.moveCursor(QTextCursor.Start)

    def _insert_runs(self, cursor, runs, heading):
        base = self._heading_char_format(True) if heading else None
        for text, bold, italic in runs:
            fmt = QTextCharFormat(base) if base is not None else QTextCharFormat()
            if bold and not heading:
                fmt.setFontWeight(QFont.Bold)
            if italic:
                fmt.setFontItalic(True)
            # Soft line breaks within a block use the Unicode line separator.
            cursor.insertText(text.replace("\n", "\u2028"), fmt)

    def _editor_to_markup(self):
        """Serialize the edited document back to the stored ``***/**/*`` + ``- ``
        markup so AI regenerate, export and sync keep working unchanged."""
        doc = self.text.document()
        out, pending_list = [], []
        block = doc.begin()
        while block.isValid():
            runs = self._block_runs(block)
            if block.textList() is not None:
                pending_list.append("- " + self._runs_to_markup(runs))
            else:
                if pending_list:
                    out.append("\n".join(pending_list))
                    pending_list = []
                if block.blockFormat().headingLevel() >= 1:
                    plain = "".join(t for t, _b, _i in runs).strip()
                    if plain:
                        out.append(f"***{plain}***")
                else:
                    line = self._runs_to_markup(runs)
                    if line.strip():
                        out.append(line)
            block = block.next()
        if pending_list:
            out.append("\n".join(pending_list))
        return "\n\n".join(out).strip()

    @staticmethod
    def _block_runs(block):
        runs = []
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                cf = frag.charFormat()
                text = frag.text().replace("\u2028", "\n").replace("\u2029", "\n")
                runs.append((text, cf.fontWeight() >= QFont.Bold, cf.fontItalic()))
            it += 1
        return runs

    @staticmethod
    def _runs_to_markup(runs):
        merged = []
        for text, bold, italic in runs:
            if not text:
                continue
            if merged and merged[-1][1] == bold and merged[-1][2] == italic:
                merged[-1] = (merged[-1][0] + text, bold, italic)
            else:
                merged.append([text, bold, italic])
        parts = []
        for text, bold, italic in merged:
            marker = "**" if bold else ("*" if italic else "")
            if marker and text.strip():
                lead = text[:len(text) - len(text.lstrip())]
                trail = text[len(text.rstrip()):]
                parts.append(f"{lead}{marker}{text.strip()}{marker}{trail}")
            else:
                parts.append(text)
        return "".join(parts)

    # ------------------------------------------------------------------

    def reload_word(self):
        word = self.db_adapter.get_word(self.word_id) or self.record
        self.word = word
        if self._pick_initial_field:
            self._pick_initial_field = False
            has_def1 = bool(str(word.get('Definition') or "").strip())
            has_def2 = bool(str(word.get('Definition2') or "").strip())
            if not has_def1 and has_def2:
                self.current_field = 'Word2'
        self.refresh_view()

    def _definition_column(self):
        return 'Definition' if self.current_field == 'Word1' else 'Definition2'

    def _displayed_word(self):
        return self.word.get(self.current_field) or ""

    def refresh_view(self):
        definition = str(self.word.get(self._definition_column()) or "").strip()
        self.header_label.setText(str(self._displayed_word()))
        for chip, field in self.lang_chips:
            chip.setChecked(field == self.current_field)
        if definition:
            self.text.setHtml(markup_to_html(definition))
            self.generate_btn.setText(tr("Regenerate with AI"))
            self._show_body(empty=False)
        else:
            self.generate_btn.setText(tr("Generate with AI"))
            self.empty_hint.setText(tr("Generate one with AI, or write your own with Edit."))
            self._show_body(empty=True)

    def _select_field(self, field):
        if self.editing or field == self.current_field:
            return
        self.current_field = field
        self.refresh_view()

    # ------------------------------------------------------------ editing

    def toggle_edit(self):
        self.editing = True
        raw = self.word.get(self._definition_column()) or ""
        self.text.setReadOnly(False)
        self._load_markup_into_editor(str(raw))
        self._show_body(empty=False)
        self.chips_row.hide()
        self.format_row.show()
        self.edit_btn.hide()
        self.close_btn.hide()
        self.save_btn.show()
        self.cancel_btn.show()
        self.generate_btn.setEnabled(False)
        self._sync_toolbar()
        self.text.setFocus()

    def cancel_edit(self):
        self.editing = False
        self.text.setReadOnly(True)
        self.format_row.hide()
        self.chips_row.show()
        self.edit_btn.show()
        self.close_btn.show()
        self.save_btn.hide()
        self.cancel_btn.hide()
        self.generate_btn.setEnabled(True)
        self.refresh_view()

    def save_definition(self):
        new_text = self._editor_to_markup()
        try:
            self.db_adapter.update_word(self.word_id, {self._definition_column(): new_text})
            self.definition_changed.emit()
        except Exception as exc:
            logging.error(f"Error saving definition: {exc}")
            QMessageBox.critical(self, tr("Error"), tr("Failed to save definition:\n{error}").format(error=exc))
            return
        self.cancel_edit()
        self.reload_word()

    # --------------------------------------------------------------- gpt

    def generate_definition(self):
        word = self._displayed_word()
        if not str(word).strip():
            QMessageBox.warning(self, tr("No word"), tr("There is no word to define."))
            return
        if not ai.has_api_key():
            QMessageBox.warning(self, tr("API key missing"),
                                tr("Set your {ai} API key in Settings → Translation & AI → AI first.").format(ai=self.ai_label))
            return
        lang1 = self.word.get('Language1') or "English"
        lang2 = self.word.get('Language2') or "English"
        if self.current_field == 'Word2':
            lang1, lang2 = lang2, lang1

        self.generate_btn.setEnabled(False)
        self._show_body(empty=False)
        self.text.setHtml(f"<p><i>{tr('Generating definition…')}</i></p>")

        field = self.current_field

        def work():
            return ai.update_definition_in_db(str(word), lang1, lang2, field, self.word_id)

        def done(result):
            ok, message = result
            if ok:
                self.definition_changed.emit()
                self.reload_word()
            else:
                self.refresh_view()
                QMessageBox.warning(self, self.ai_label, message)

        run_in_thread(work, on_result=done,
                      on_error=lambda e: (self.refresh_view(),
                                          QMessageBox.critical(self, self.ai_label, e)),
                      on_finished=lambda: self.generate_btn.setEnabled(True))
