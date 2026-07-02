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

"""First-launch guided tour — a dimmed spotlight overlay that walks new users
through the core features (navigation, search, adding words, read-aloud and
sync/settings).

Two pieces:
  * ``SpotlightOverlay`` — a translucent child widget covering the whole window.
    It paints a dim everywhere except a rounded "hole" punched over the current
    target widget, and shows a callout bubble beside it.
  * ``TourController`` — owns the ordered steps, the overlay lifecycle and the
    ``tour_completed`` settings flag (mirrors the ``language_configured`` flow).
"""
from PySide6.QtCore import (QEasingCurve, QObject, QPoint, QPropertyAnimation,
                            QRect, QRectF, Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout,
                               QLabel, QPushButton, QVBoxLayout, QWidget)

from app.config import get_bool, save_settings
from app.i18n import tr

# Padding around the highlighted widget, the hole corner radius and the dim alpha.
_PAD = 6
_RADIUS = 8
_DIM_ALPHA = 150
_BUBBLE_WIDTH = 380
_BUBBLE_MARGIN = 18  # inner padding; body wrap width = width - 2*margin
_GAP = 14  # space between the hole and the bubble


class SpotlightOverlay(QWidget):
    """Full-window translucent overlay with a single highlighted cutout."""

    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()

    def __init__(self, parent, colors):
        super().__init__(parent)
        self.colors = colors
        self._hole = QRect()
        # Translucent so the unpainted hole shows the real widget underneath and
        # the dim fill composits over the rest of the live UI.
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(parent.rect())

        self._bubble = self._build_bubble()
        self.raise_()

    # ------------------------------------------------------------------ bubble
    def _build_bubble(self):
        c = self.colors
        bubble = QFrame(self)
        bubble.setObjectName("TourBubble")
        bubble.setFixedWidth(_BUBBLE_WIDTH)
        # Scope the rule to #TourBubble so it doesn't cascade onto the child
        # labels (QLabel subclasses QFrame and would otherwise inherit the border).
        bubble.setStyleSheet(
            f"QFrame#TourBubble {{ background: {c['surface_alt']};"
            f" border: 1px solid {c['border']}; border-radius: 12px; }}"
            f"#TourBubble QLabel {{ border: none; background: transparent; }}")
        lay = QVBoxLayout(bubble)
        lay.setContentsMargins(_BUBBLE_MARGIN, 16, _BUBBLE_MARGIN, 14)
        lay.setSpacing(6)

        inner = _BUBBLE_WIDTH - 2 * _BUBBLE_MARGIN
        self._title = QLabel(bubble)
        self._title.setStyleSheet(
            f"color: {c['text']}; font-size: 12pt; font-weight: 600;")
        self._title.setWordWrap(True)
        self._title.setFixedWidth(inner)
        lay.addWidget(self._title)

        self._body = QLabel(bubble)
        self._body.setWordWrap(True)
        self._body.setFixedWidth(inner)  # fixes wrapped height computation
        self._body.setStyleSheet(f"color: {c['text_dim']}; font-size: 10pt;")
        lay.addWidget(self._body)

        lay.addSpacing(4)
        footer = QHBoxLayout()
        footer.setSpacing(6)
        self._counter = QLabel(bubble)
        self._counter.setStyleSheet(f"color: {c['text_dim']}; font-size: 9pt;")
        footer.addWidget(self._counter)
        footer.addStretch(1)

        self._skip_btn = QPushButton(tr("Skip"), bubble)
        self._skip_btn.setCursor(Qt.PointingHandCursor)
        self._skip_btn.setStyleSheet(
            f"QPushButton {{ color: {c['text_dim']}; border: none; background: transparent;"
            f" padding: 6px 8px; }} QPushButton:hover {{ color: {c['text']}; }}")
        self._skip_btn.clicked.connect(self.skip_clicked)
        footer.addWidget(self._skip_btn)

        self._back_btn = QPushButton(tr("Back"), bubble)
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.setStyleSheet(
            f"QPushButton {{ color: {c['text']}; border: 1px solid {c['border']};"
            f" border-radius: 6px; padding: 6px 12px; background: transparent; }}"
            f"QPushButton:hover {{ background: {c['selection']}; }}")
        self._back_btn.clicked.connect(self.back_clicked)
        footer.addWidget(self._back_btn)

        self._next_btn = QPushButton(tr("Next"), bubble)
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.setStyleSheet(
            f"QPushButton {{ color: white; border: none; border-radius: 6px;"
            f" padding: 6px 14px; background: {c['accent']}; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {c['selection']}; }}")
        self._next_btn.clicked.connect(self.next_clicked)
        footer.addWidget(self._next_btn)

        lay.addLayout(footer)
        # Buttons keep their natural width so localized labels never clip.
        for b in (self._skip_btn, self._back_btn, self._next_btn):
            b.setMinimumWidth(b.sizeHint().width())
        return bubble

    # -------------------------------------------------------------- public API
    def set_step(self, hole, title, body, index, total):
        """Show *title*/*body* highlighting *hole* (a QRect in overlay coords)."""
        self._title.setText(title)
        self._body.setText(body)
        self._counter.setText(tr("Step {n} of {total}").format(n=index + 1, total=total))
        self._back_btn.setVisible(index > 0)
        self._next_btn.setText(tr("Done") if index == total - 1 else tr("Next"))
        # Pin each wrapped label to its exact height for the fixed width, so the
        # bubble never under-sizes (adjustSize alone misjudges wrapped QLabels).
        inner = _BUBBLE_WIDTH - 2 * _BUBBLE_MARGIN
        for lbl in (self._title, self._body):
            lbl.setFixedHeight(lbl.heightForWidth(inner))
        self.set_hole(hole)

    def set_hole(self, hole):
        """Update only the highlighted rectangle (used on resize)."""
        self._hole = hole if hole is not None else QRect()
        self._reposition_bubble()
        self.update()

    def _reposition_bubble(self):
        self._bubble.adjustSize()
        bw, bh = self._bubble.width(), self._bubble.height()
        margin = 16
        if self._hole.isNull():
            # No anchor — centre the bubble.
            x = (self.width() - bw) // 2
            y = (self.height() - bh) // 2
        else:
            # Prefer below the hole; flip above if it would overflow.
            x = self._hole.center().x() - bw // 2
            y = self._hole.bottom() + _GAP
            if y + bh > self.height() - margin:
                y = self._hole.top() - _GAP - bh
            if y < margin:  # doesn't fit above or below — place to the right
                y = self._hole.center().y() - bh // 2
                x = self._hole.right() + _GAP
        x = max(margin, min(x, self.width() - bw - margin))
        y = max(margin, min(y, self.height() - bh - margin))
        self._bubble.move(x, y)

    # ---------------------------------------------------------------- painting
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRect(QRectF(self.rect()))
        if not self._hole.isNull():
            hole = QPainterPath()
            hole.addRoundedRect(QRectF(self._hole), _RADIUS, _RADIUS)
            path = path.subtracted(hole)
        painter.fillPath(path, QColor(0, 0, 0, _DIM_ALPHA))
        if not self._hole.isNull():
            painter.setPen(QPen(QColor(self.colors["accent"]), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(self._hole), _RADIUS, _RADIUS)

    # ------------------------------------------------------------------ events
    def mousePressEvent(self, event):
        # Swallow clicks on the dim so the app underneath stays inert during the
        # tour; bubble buttons handle their own clicks.
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.skip_clicked.emit()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Right):
            self.next_clicked.emit()
        elif event.key() == Qt.Key_Left:
            self.back_clicked.emit()
        else:
            super().keyPressEvent(event)


# Maps MainWindow.stack indices (PAGE_WORDS/FLASHCARDS/TEXTS/STATS = 0/1/2/3)
# to tour keys. Kept local so this module needn't import main_window.
# The Flashcards page (1) has no tour yet.
_PAGE_TO_TOUR = {0: "words", 2: "texts", 3: "stats"}


class TourController(QObject):
    """Drives the spotlight overlay through the per-page onboarding tours."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.overlay = None
        self.index = 0
        self._key = "words"
        self._auto_selected = False
        self._demo = set()        # page keys we injected demo data for this run
        self._saved_df = None     # real words DataFrame, restored after the tour
        self._saved_text = None   # real current text record, restored after the tour
        # Registry of named tours: each is an ordered list of resolver methods
        # returning (widget, title, body); a resolver may perform a small
        # side-effect (e.g. select a row) to reveal a contextual target.
        self._tours = {
            "words": [self._step_nav, self._step_search, self._step_add,
                      self._step_read, self._step_generate, self._step_sync],
            "texts": [self._step_texts_add, self._step_texts_list,
                      self._step_texts_read, self._step_texts_translate,
                      self._step_texts_modes],
            "stats": [self._step_stats_overview, self._step_stats_added,
                      self._step_stats_reviews],
        }

    @property
    def _steps(self):
        return self._tours[self._key]

    @property
    def total(self):
        return len(self._steps)

    # ----------------------------------------------------------- seen flags
    def _seen(self, key):
        if get_bool(self.win.settings, f"tour_{key}_seen", False):
            return True
        # Migration: the legacy single flag counts as the Words tour being seen.
        return key == "words" and get_bool(self.win.settings, "tour_completed", False)

    def _mark_seen(self, key):
        self.win.settings[f"tour_{key}_seen"] = "True"
        if key == "words":
            self.win.settings["tour_completed"] = "True"  # keep legacy flag in sync
        save_settings(self.win.settings)

    # ----------------------------------------------------------- entry points
    def maybe_start_on_launch(self):
        if self._seen("words"):
            return
        # Let the window finish its first paint/layout before overlaying it.
        QTimer.singleShot(600, lambda: self.start("words", on_launch=True))

    def maybe_start_for_page(self, index):
        """Fire a page's tour the first time the user opens that tab."""
        if self.overlay is not None:
            return
        key = _PAGE_TO_TOUR.get(index)
        if key and not self._seen(key):
            # Delay so the page-switch animation settles before we measure widgets.
            QTimer.singleShot(450, lambda: self.start(key))

    def start_current(self):
        """Replay the tour for whichever tab is currently active (menu action)."""
        self.start(_PAGE_TO_TOUR.get(self.win.stack.currentIndex(), "words"))

    def start(self, key="words", on_launch=False):
        # A minimized/tray launch has no visible window to highlight; leave the
        # flag unset so the tour appears on the next normal launch instead.
        if on_launch and not self.win.isVisible():
            return
        if self.overlay is not None:
            return
        self._key = key if key in self._tours else "words"
        self.index = 0
        self._auto_selected = False
        self._enter_demo(self._key)
        self.overlay = SpotlightOverlay(self.win, self.win.colors)
        self.overlay.next_clicked.connect(self._next)
        self.overlay.back_clicked.connect(self._back)
        self.overlay.skip_clicked.connect(self._finish)
        self.overlay.show()
        self.overlay.setFocus(Qt.OtherFocusReason)
        self._fade(self.overlay, 0.0, 1.0)
        self._show_step(0)

    # --------------------------------------------------------------- stepping
    def _show_step(self, i):
        self.index = i
        widget, title, body = self._steps[i]()
        self.overlay.set_step(self._rect_for(widget), title, body, i, self.total)

    def _next(self):
        if self.index >= self.total - 1:
            self._finish()
        else:
            self._show_step(self.index + 1)

    def _back(self):
        if self.index > 0:
            self._show_step(self.index - 1)

    def _finish(self):
        if self._auto_selected:
            self.win.table.clearSelection()
            self._auto_selected = False
        self._exit_demo()
        self._mark_seen(self._key)
        if self.overlay is not None:
            overlay = self.overlay
            self.overlay = None
            anim = self._fade(overlay, 1.0, 0.0)
            anim.finished.connect(overlay.deleteLater)

    def relayout(self):
        """Keep the cutout aligned when the window is resized."""
        if self.overlay is None:
            return
        self.overlay.setGeometry(self.win.rect())
        widget, _title, _body = self._steps[self.index]()
        self.overlay.set_hole(self._rect_for(widget))

    # ------------------------------------------------------------- geometry
    def _rect_for(self, target):
        """A padded rect covering *target*, which may be one widget or a list of
        adjacent widgets to highlight together (union of the visible ones)."""
        widgets = target if isinstance(target, (list, tuple)) else [target]
        rect = QRect()
        for w in widgets:
            if w is None or not w.isVisible():
                continue
            r = QRect(w.mapTo(self.win, QPoint(0, 0)), w.size())
            rect = r if rect.isNull() else rect.united(r)
        if rect.isNull():
            return None
        return rect.adjusted(-_PAD, -_PAD, _PAD, _PAD)

    # ----------------------------------------------------------------- steps
    def _step_nav(self):
        return ([self.win.nav_words, self.win.nav_texts, self.win.nav_stats],
                tr("Your library"),
                tr("Switch between your Words, Texts and Statistics from this sidebar."))

    def _step_search(self):
        return (self.win.search_box, tr("Find anything"),
                tr("Search across your words, translations and tags as you type."))

    def _step_add(self):
        return (self.win.add_button, tr("Add a word"),
                tr("Add a new word here — its translation can be fetched automatically."))

    def _step_read(self):
        w = self.win
        sm = w.table.selectionModel()
        if sm is not None and not sm.hasSelection() and w.model.rowCount() > 0:
            w.table.selectRow(0)
            self._auto_selected = True
        if w.read_button.isVisible():
            target = w.read_button
        elif w.action_bar.isVisible():
            target = w.action_bar
        else:
            target = w.table
        return (target, tr("Listen and learn"),
                tr("Select words and press Read to hear them aloud. Repeated "
                   "listening promotes each word from New to Reviewing, Learning "
                   "and finally Mastered."))

    def _step_generate(self):
        w = self.win
        sm = w.table.selectionModel()
        if sm is not None and not sm.hasSelection() and w.model.rowCount() > 0:
            w.table.selectRow(0)
            self._auto_selected = True
        if w.generate_text_btn.isVisible():
            target = w.generate_text_btn
        elif w.action_bar.isVisible():
            target = w.action_bar
        else:
            target = w.table
        return (target, tr("Generate a text"),
                tr("Turn selected words into a short AI-written story — "
                   "your vocabulary in context."))

    def _step_sync(self):
        w = self.win
        if getattr(w, "sync_enabled", False) and w.sync_button.isVisible():
            return (w.sync_button, tr("Cloud sync"),
                    tr("Your vocabulary stays in sync across devices. Click for "
                       "status or to sync right now."))
        return (w.nav_settings, tr("Settings"),
                tr("Enable cloud sync, switch language, change appearance and "
                   "more from Settings."))

    # ------------------------------------------------------------ texts steps
    def _ensure_text_open(self):
        """Open the first text so the reader controls become visible."""
        tp = self.win.texts_page
        if tp.current is None and tp.listing.count() > 0:
            tp.listing.setCurrentRow(0)  # _on_row_changed loads it; non-destructive

    def _tt(self, primary):
        """A visible target: the reader control if shown, else the empty state,
        else the list — so the Texts tour works before any text is opened."""
        tp = self.win.texts_page
        for w in (primary, tp.empty_title, tp.listing):
            if w is not None and w.isVisible():
                return w
        return tp.listing

    def _step_texts_add(self):
        return (self.win.texts_page.new_text_btn, tr("Add texts"),
                tr("Write or paste a text, fetch one from the Internet "
                   "(AI / Wikipedia / URL / RSS), or import .txt files."))

    def _step_texts_list(self):
        return (self.win.texts_page._left_panel, tr("Your texts"),
                tr("Browse your saved texts and filter them by language, "
                   "level or topic."))

    def _step_texts_read(self):
        self._ensure_text_open()
        tp = self.win.texts_page
        return (self._tt(tp.tts_btn), tr("Read aloud"),
                tr("Listen to any text aloud — and click a word while reading "
                   "to see its translation or add it to your vocabulary."))

    def _step_texts_translate(self):
        tp = self.win.texts_page
        return (self._tt(tp.translate_btn), tr("Translate"),
                tr("Show a parallel translation side-by-side; pick the language "
                   "with the arrow beside it."))

    def _step_texts_modes(self):
        tp = self.win.texts_page
        btns = [tp.focus_btn, tp.paper_btn, tp.edit_btn]
        visible = [b for b in btns if b.isVisible()]
        target = visible if visible else self._tt(tp.focus_btn)
        return (target, tr("Reading modes"),
                tr("Focus mode hides the list, Paper mode changes the "
                   "background, and Edit lets you tweak the text."))

    # ------------------------------------------------------------ stats steps
    def _scroll_stats_to(self, widget):
        """Scroll the stats dashboard so *widget* is in view before highlighting."""
        scroll = getattr(self.win.stats_page, "scroll", None)
        if scroll is not None and widget is not None:
            scroll.ensureWidgetVisible(widget, 40, 40)

    def _step_stats_overview(self):
        w = self.win.stats_page.overview
        self._scroll_stats_to(w)
        return (w, tr("Overview"),
                tr("Your vocabulary at a glance — totals, mastered words, "
                   "languages and your current streak."))

    def _step_stats_added(self):
        w = self.win.stats_page.area
        self._scroll_stats_to(w)
        return (w, tr("Words added over time"),
                tr("See how your vocabulary has grown over time."))

    def _step_stats_reviews(self):
        w = self.win.stats_page.rev_area
        self._scroll_stats_to(w)
        return (w, tr("Reviews over time"),
                tr("Track how much you've reviewed over time."))

    # ------------------------------------------------------- demo data
    def _enter_demo(self, key):
        """On a fresh/empty page, show illustrative in-memory data so the tour
        actually demonstrates the feature. Restored verbatim by _exit_demo();
        nothing is written to the database."""
        win = self.win
        if key == "words":
            df = win.df
            if df is None or getattr(df, "empty", True):
                self._saved_df = win.df
                win.df = _demo_words_df()
                win.refresh_display()
                self._demo.add("words")
        elif key == "texts":
            tp = win.texts_page
            if tp.listing.count() == 0:
                self._saved_text = tp.current
                tp.texts = _demo_texts()
                tp._refresh_list()  # builds the list and opens the first text
                self._demo.add("texts")
        elif key == "stats":
            df = win.df
            if df is None or getattr(df, "empty", True):
                demo = _demo_stats()
                # _refresh_area/_refresh_review_area resample self._stats, so set it
                # too — not just push values through _apply.
                win.stats_page._stats = demo
                win.stats_page._apply(demo)
                self._demo.add("stats")

    def _exit_demo(self):
        win = self.win
        if "words" in self._demo:
            win.df = self._saved_df
            self._saved_df = None
            win.refresh_display()
        if "texts" in self._demo:
            tp = win.texts_page
            tp.current = self._saved_text
            self._saved_text = None
            tp.load_texts()  # re-reads the real (empty) DB → clears list + empty state
        if "stats" in self._demo:
            win._refresh_stats()
        self._demo.clear()

    # ------------------------------------------------------------------ fade
    @staticmethod
    def _fade(widget, start, end):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(220)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.start()
        widget._tour_fade_anim = anim  # keep a reference alive
        return anim


# --------------------------------------------------------------- demo builders
# Heavy imports stay inside the builders so they only load when a tour actually
# needs demo data (and after set_language() has run).

def _demo_words_df():
    """~20 illustrative rows shaped exactly like the real words DataFrame, so the
    table looks full. Status and Language names are canonical English so the pills
    and language labels localize; the word pairs stay as concrete examples.
    Negative IDs mark them as fake."""
    import pandas as pd
    from datetime import date, timedelta
    from app.ui.word_model import EMPTY_DF_COLUMNS
    base = date(2026, 6, 17)
    # (Status, Language1, Word1, Language2, Word2, favorite) — pairs go both ways,
    # German appears once, no Russian, Ukrainian a bit more often.
    data = [
        ("Mastered", "English", "house", "Spanish", "casa", True),
        ("Learning", "Ukrainian", "вода", "English", "water", False),
        ("Reviewing", "Spanish", "libro", "English", "book", False),
        ("New", "English", "friend", "Italian", "amico", False),
        ("Mastered", "Ukrainian", "собака", "Polish", "pies", True),
        ("Learning", "French", "chat", "English", "cat", False),
        ("Reviewing", "English", "sun", "Ukrainian", "сонце", False),
        ("New", "Italian", "luna", "English", "moon", False),
        ("Learning", "Ukrainian", "хліб", "French", "pain", False),
        ("New", "Polish", "mleko", "English", "milk", False),
        ("Reviewing", "English", "apple", "Ukrainian", "яблуко", True),
        ("Mastered", "French", "ville", "English", "city", False),
        ("New", "Portuguese", "estrada", "English", "road", False),
        ("Learning", "English", "tree", "Ukrainian", "дерево", False),
        ("Reviewing", "Spanish", "río", "Ukrainian", "річка", False),
        ("New", "English", "school", "Italian", "scuola", False),
        ("Mastered", "Ukrainian", "музика", "English", "music", False),
        ("New", "Dutch", "raam", "English", "window", False),
        ("Learning", "French", "fleur", "Ukrainian", "квітка", False),
        ("Reviewing", "German", "Berg", "English", "mountain", False),
    ]
    rows = []
    for i, (status, lang1, w1, lang2, w2, fav) in enumerate(data):
        created = (base - timedelta(days=i)).isoformat() + "T10:00:00"
        rows.append([-(i + 1), status, lang1, w1, lang2, w2, "", created, None, fav])
    return pd.DataFrame(rows, columns=EMPTY_DF_COLUMNS)


def _demo_text():
    """The full sample text rendered in the reader (never persisted)."""
    return {
        "ID": -1,
        "Title": tr("Sample: A walk in the city"),
        "Text": tr(
            "The morning was bright and the streets were quiet. A young woman "
            "walked slowly along the old road, looking at the tall houses and the "
            "small shops that were just opening. She stopped to buy some fresh "
            "bread and a cup of coffee, then crossed the square toward the park. "
            "Children were playing near the river while their parents talked on the "
            "benches nearby. She sat down under a large tree, opened her book, and "
            "began to read. The story was about a traveller who crossed the "
            "mountains in search of an old friend he had not seen for many years. "
            "After a while she looked up, watching the boats drift slowly down the "
            "river and the birds circle high above the rooftops. A street musician "
            "began to play somewhere nearby, and the soft notes followed her "
            "thoughts. It was a calm and happy morning, the kind she liked best."),
        "Language": "English",
        "Level": "A2",
        "Category": tr("Demo"),
        "Words": None,
        "created_at": "2026-06-17T09:00:00",
    }


def _demo_texts():
    """The full sample text plus a few title-only stubs so the list looks full.
    The full text has the newest date so it sorts to the top and opens first."""
    records = [_demo_text()]
    stubs = [
        ("My first story", "Once upon a time, in a small village by the sea, "
         "there lived a curious young fox.", "B1", "2026-06-15T09:00:00"),
        ("A news article", "Researchers have found a new way to study how "
         "languages change and grow over the centuries.", "B2", "2026-06-12T09:00:00"),
        ("A short poem", "The wind walks softly through the autumn trees, "
         "carrying old and half-forgotten songs.", "A2", "2026-06-09T09:00:00"),
        ("Travel notes", "Day one: we arrived in the city late at night, and the "
         "streets were still full of warm light.", "A1", "2026-06-05T09:00:00"),
    ]
    for i, (title, snippet, level, created) in enumerate(stubs):
        records.append({
            "ID": -(i + 2), "Title": tr(title), "Text": tr(snippet),
            "Language": "English", "Level": level, "Category": tr("Demo"),
            "Words": None, "created_at": created,
        })
    return records


def _demo_stats():
    """A coherent set of fake numbers to populate the dashboard during the tour."""
    from collections import OrderedDict
    from datetime import date, timedelta
    from app.core.stats import DashboardStats
    today = date(2026, 6, 17)
    daily = [(today - timedelta(days=i), (i * 7 + 3) % 6) for i in range(30, -1, -1)]
    reviews_daily = [(today - timedelta(days=i), (i * 5 + 2) % 5) for i in range(30, -1, -1)]
    return DashboardStats(
        total_words=48,
        favorites=9,
        language_count=4,
        definitions_filled=21,
        definitions_total=48,
        status_counts=OrderedDict([("New", 14), ("Reviewing", 12),
                                   ("Learning", 13), ("Mastered", 9)]),
        added_today=3,
        added_this_week=17,
        added_this_month=41,
        current_streak=6,
        longest_streak=12,
        busiest_day_count=8,
        top_language_pairs=[("English → Spanish", 18), ("English → French", 15),
                            ("English → German", 9)],
        top_tags=[("travel", 11), ("food", 8), ("verbs", 6)],
        daily=daily,
        has_dates=True,
        reviews_total=164,
        reviews_today=5,
        reviews_this_week=39,
        review_streak=4,
        reviews_daily=reviews_daily,
        most_reviewed=[("house", 14), ("water", 11), ("book", 7)],
        has_reviews=True,
    )
