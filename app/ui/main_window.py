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

"""Main application window."""
import json
import logging
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta

from PySide6.QtCore import QElapsedTimer, QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QIcon,
    QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QStatusBar,
    QTableView, QVBoxLayout, QWidget, QWidgetAction, QCheckBox,
    QAbstractItemView,
)

from app.config import get_bool, get_float, get_int, load_settings, save_settings
from app.core import db as dbq
from app.i18n import fill_lang_combo, tr
from app.core import progression
from app.core import exporters
from app.core import translator
from app.core.audio import stop_playback
from app.core.backup_management import backup_database
from app.core.database_adapter import DatabaseAdapter
from app.core.shell_utils import suggest_filename
from app.core.sync_manager import SyncManager
from app.core.data_management import open_words_from_excel
from app.ui import icons, theme
from app.ui.animations import AnimatedStackedWidget, crossfade_during, fade_swap
from app.ui.mini_player import MiniPlayer
from app.ui.player import PlaybackSettingsPopup, PlayerBar, WordPlayer
from app.ui.texts_page import TextsPage
from app.ui.stats_page import StatsPage
from app.ui.toast import show_toast
from app.ui.word_model import (
    COL_ID, COL_CREATED, COL_LANG1, COL_LANG2, COL_SOURCE, COL_STATUS,
    COL_WORD1, COL_WORD2, HEADERS, WordFilter, WordTableModel, words_to_dataframe,
)
from app.ui.workers import run_in_thread
from app.version import APP_NAME, APP_VERSION, BUILD_NUMBER

GEOMETRY_FILE = "window_geometry.json"
PREDEFINED_STATUSES = ["New", "To Learn", "Learning", "Mastered", "Ignored"]
DEFAULT_HOTKEY = "Ctrl+Shift+V"
PAGE_WORDS, PAGE_TEXTS, PAGE_STATS = 0, 1, 2


def _tray_icon_path() -> str:
    """Resolve the tray-icon asset for the current OS.

    Windows gets its own variant (the notification area renders differently from
    Linux desktop panels); everything else shares tray_icon.png. Falls back to the
    shared icon if the per-OS file is missing so the tray is never left blank by a
    packaging slip.
    """
    icons_dir = os.path.join("assets", "icons")
    if sys.platform == "win32":
        win_icon = os.path.join(icons_dir, "tray_icon_win.png")
        if os.path.exists(win_icon):
            return win_icon
    return os.path.join(icons_dir, "tray_icon.png")


def _hotkey_to_pynput(seq):
    """Qt portable shortcut ("Ctrl+Shift+V") -> pynput ("<ctrl>+<shift>+v")."""
    mapped = []
    for part in (p.strip().lower() for p in seq.split("+") if p.strip()):
        if part in ("meta", "super", "win"):
            mapped.append("<cmd>")
        elif len(part) == 1:
            mapped.append(part)
        else:
            mapped.append(f"<{part}>")  # ctrl, shift, alt, f1, space, …
    return "+".join(mapped)


def _hotkey_to_keyboard(seq):
    """Qt portable shortcut -> 'keyboard' module format (Windows)."""
    return "+".join(
        "windows" if p in ("meta", "super", "cmd") else p
        for p in (s.strip().lower() for s in seq.split("+") if s.strip())
    )

# sync status -> (icon name, color key)
SYNC_ICONS = {
    "idle": ("cloud", "text_dim"),
    "syncing": ("sync", "accent"),
    "success": ("check", "success"),
    "error": ("alert", "danger"),
}


def save_geometry(window, window_id, filename=GEOMETRY_FILE):
    data = {}
    if os.path.exists(filename):
        try:
            with open(filename) as fh:
                data = json.load(fh)
        except Exception:
            data = {}
    geo = window.geometry()
    data[window_id] = {"geometry": [geo.x(), geo.y(), geo.width(), geo.height()]}
    with open(filename, "w") as fh:
        json.dump(data, fh)


def load_geometry(window, window_id, default_size=(1100, 680), filename=GEOMETRY_FILE):
    if os.path.exists(filename):
        try:
            with open(filename) as fh:
                data = json.load(fh)
            entry = data.get(window_id, {}).get("geometry")
            if isinstance(entry, list) and len(entry) == 4:
                window.setGeometry(*entry)
                return
        except Exception:
            pass
    window.resize(*default_size)


class _HeaderFilterCombo(QComboBox):
    """Filter combo embedded in the table header; ignores wheel events so
    scrolling over the header doesn't change filters accidentally."""

    def wheelEvent(self, event):
        event.ignore()


class WordTableView(QTableView):
    """QTableView where a plain left click on the only selected row
    deselects it again (no Ctrl needed)."""

    # Set by MainWindow; called to step / reset the table density on Ctrl+zoom.
    density_step = None
    density_reset = None

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier and self.density_step:
            self.density_step(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            key = event.key()
            if key in (Qt.Key_Plus, Qt.Key_Equal) and self.density_step:
                self.density_step(1)
                event.accept()
                return
            if key in (Qt.Key_Minus, Qt.Key_Underscore) and self.density_step:
                self.density_step(-1)
                event.accept()
                return
            if key == Qt.Key_0 and self.density_reset:
                self.density_reset()
                event.accept()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.modifiers() == Qt.NoModifier:
            index = self.indexAt(event.position().toPoint())
            sel = self.selectionModel()
            if index.isValid() and sel is not None:
                rows = sel.selectedRows()
                if len(rows) == 1 and rows[0].row() == index.row():
                    self.clearSelection()
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        # The first press of a double-click may have just deselected the
        # row; restore it so doubleClicked handlers see a selection.
        index = self.indexAt(event.position().toPoint())
        if index.isValid() and not self.selectionModel().selectedRows():
            self.selectRow(index.row())
        super().mouseDoubleClickEvent(event)


class MainWindow(QMainWindow):
    sync_status_changed = Signal(str, str)
    hotkey_pressed = Signal()
    reload_requested = Signal()
    translation_fallback = Signal(str)

    def __init__(self, settings, start_hidden=False):
        super().__init__()
        self.settings = settings
        self.colors = theme.current_colors()
        self._themed_icons = []  # (target, name, color_key, size) for re-tinting
        self._pending_update = None  # UpdateInfo once a newer release is known

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon("assets/icons/icon.png"))
        # Client-side decorations: window controls live in the app's top bar
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        load_geometry(self, "main_window")

        # --- backend ---
        self.sync_enabled = get_bool(settings, "enable_sync", False)
        # Optimistic until the first sync result confirms reachability; the Bin
        # button tracks this so it hides when the cloud is unreachable.
        self._cloud_connected = self.sync_enabled
        self.db_adapter = DatabaseAdapter(use_cloud=self.sync_enabled)
        self.sync_manager = SyncManager()

        self.word_filter = WordFilter()
        self.df = None
        self.is_reading_active = False
        self.word_player = WordPlayer(self)
        self._mini_positioned = False  # mini player placed on first show
        self._playing_records = []
        self.show_source = False
        self.show_created = False
        self._quitting = False
        self._open_dialogs = {}
        self._tts_fallback_warned = False
        self._page_search = {PAGE_WORDS: "", PAGE_TEXTS: "", PAGE_STATS: ""}
        self._footer_counts = {PAGE_WORDS: tr("No data"), PAGE_TEXTS: tr("No texts yet"),
                               PAGE_STATS: tr("Statistics")}
        self._words_subtitle = tr("Vocabulary")
        self._file_view = False  # viewing an opened Excel file (read-only preview)

        self._build_ui()
        self._build_tray()
        self._setup_shortcuts()
        self._setup_global_hotkey()
        # size hints are only reliable once widgets are polished
        QTimer.singleShot(0, self._lock_filter_row_height)

        # Follow the desktop light/dark preference live (and recover from a
        # login-time launch where the color-scheme portal wasn't ready yet).
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._on_system_color_scheme_changed)

        self.sync_status_changed.connect(self._update_sync_status_ui)
        self._sync_running = False
        self.sync_popover = None  # built lazily on first cloud-icon click
        self.hotkey_pressed.connect(self.open_add_word_and_translate)
        self.reload_requested.connect(self.load_data)
        # Surface DeepL->Google fallbacks (raised on worker threads) as a toast.
        self.translation_fallback.connect(
            lambda msg: show_toast(self, tr("Translation"), msg, "info"))
        translator.set_fallback_listener(self.translation_fallback.emit)
        self.word_player.index_changed.connect(self._on_player_index)
        self.word_player.part_changed.connect(self._on_player_part)
        self.word_player.state_changed.connect(self._on_player_state)
        self.word_player.word_completed.connect(self._on_word_completed)
        self.word_player.finished.connect(self._on_player_finished)
        self.word_player.synthesis_warning.connect(
            lambda msg: show_toast(self, tr("Read aloud"), msg, "warning", 6000))
        # playback-driven status progression (settings snapshot per session)
        self._promote_on_play = True
        self._thresholds = progression.normalize_thresholds()
        self._session_status = {}  # word_id -> status, updated as we promote
        # the texts reader uses the same audio output — one player at a time
        self.texts_page.tts_started.connect(self.word_player.stop)
        # mirror the texts reader into the mini player (running-line mode)
        self._mini_text_start = 0
        self.texts_page.reader.sentence_changed.connect(self._on_reader_sentence)
        self.texts_page.reader.word_changed.connect(self._on_reader_word)
        self.texts_page.reader.state_changed.connect(
            lambda state: self.mini_player.set_paused(state == "paused"))
        self.texts_page.reader.finished.connect(self._sync_mini_player)
        self.texts_page.add_word_requested.connect(self._on_text_word_add)
        self.texts_page.vocab_changed.connect(self._after_db_change)

        self.load_data()

        if self.sync_enabled and self.sync_manager.is_sync_enabled():
            run_in_thread(self._run_startup_sync)
        elif self.sync_enabled:
            self._update_sync_status_ui("error", tr("Sync enabled but not connected. Check settings."))

        # Prune expired entries from the local Bin (trash) once per launch.
        try:
            self.db_adapter.purge_old_binned_items(
                get_int(settings, "cleanup_grace_period_days", 30))
        except Exception as exc:
            logging.warning(f"Bin purge failed: {exc}")

        # Enable autostart by default on the very first run (once only, so a
        # later opt-out in Settings sticks), then repair the entry if a prior
        # build's executable path drifted (e.g. a renamed AppImage on update).
        self._maybe_enable_autostart_default()
        if getattr(sys, "frozen", False):
            try:
                from app.system.autostart import sync_autostart_path
                sync_autostart_path()
            except Exception as exc:
                logging.warning(f"Autostart path sync failed: {exc}")

        # Check GitHub for a newer release (throttled, non-blocking).
        QTimer.singleShot(3000, self._maybe_check_for_updates)

    # ------------------------------------------------------------------ UI

    def _apply_table_density(self):
        from app.ui.theme import TABLE_DENSITY, TABLE_DENSITY_DEFAULT
        d = TABLE_DENSITY.get(
            self.settings.get("table_density", TABLE_DENSITY_DEFAULT),
            TABLE_DENSITY[TABLE_DENSITY_DEFAULT],
        )
        widget_scaling = get_float(self.settings, "widget_scaling", 1.0)
        base_font = max(8, round(10 * widget_scaling))
        font_pt = max(7, round(base_font * d["scale"]))
        row_px = round(font_pt * d["row_ratio"])
        # widget-level stylesheet: wins over the app stylesheet regardless of
        # polish order (setFont() is overridden by the app QSS at startup)
        self.table.setStyleSheet(f"QTableView {{ font-size: {font_pt}pt; }}")
        self.table.verticalHeader().setDefaultSectionSize(row_px)

    def _step_table_density(self, delta):
        """Move the table density one level up/down (Ctrl+scroll / Ctrl +/-)."""
        from app.ui.theme import TABLE_DENSITY, TABLE_DENSITY_DEFAULT
        order = list(TABLE_DENSITY.keys())
        current = self.settings.get("table_density", TABLE_DENSITY_DEFAULT)
        idx = order.index(current) if current in order else order.index(TABLE_DENSITY_DEFAULT)
        new_idx = max(0, min(len(order) - 1, idx + delta))
        if order[new_idx] == current:
            return
        self.settings["table_density"] = order[new_idx]
        save_settings(self.settings)
        self._apply_table_density()

    def _reset_table_density(self):
        """Restore the default table density (Ctrl+0)."""
        from app.ui.theme import TABLE_DENSITY_DEFAULT
        if self.settings.get("table_density") == TABLE_DENSITY_DEFAULT:
            return
        self.settings["table_density"] = TABLE_DENSITY_DEFAULT
        save_settings(self.settings)
        self._apply_table_density()

    def _icon(self, name, color_key="text", size=20):
        return icons.icon(name, self.colors[color_key], size)

    def _set_icon(self, target, name, color_key="text", size=20):
        """Set a themed icon and remember it so theme switches re-tint it.
        Setting an icon on an already-registered target replaces its entry
        (nav buttons are re-tinted on every page switch)."""
        target.setIcon(self._icon(name, color_key, size))
        for i, entry in enumerate(self._themed_icons):
            if entry[0] is target:
                self._themed_icons[i] = (target, name, color_key, size)
                return
        self._themed_icons.append((target, name, color_key, size))

    def _refresh_icons(self):
        """Re-tint all registered icons after a theme change."""
        for target, name, color_key, size in self._themed_icons:
            target.setIcon(self._icon(name, color_key, size))
        self._on_favorites_toggled(self.favorites_btn.isChecked())
        if self.is_reading_active:
            self.read_button.setIcon(self._icon("stop", "danger", 17))
        self.texts_page.refresh_theme(self.colors)
        self.stats_page.refresh_theme(self.colors)
        self.player_bar.refresh_theme(self.colors)
        self.mini_player.refresh_theme(self.colors)
        self.window_controls.set_colors(self.colors)
        if self.sync_popover is not None:
            self.sync_popover.refresh_theme(self.colors)
        self._rebuild_app_menu()
        self._apply_menu_button_icon()

    def _rebuild_app_menu(self):
        """Swap in a freshly built app menu (theme change / update-state change)."""
        old_menu = self.app_menu
        self.app_menu = self._build_app_menu()
        old_menu.deleteLater()

    def _set_pending_update(self, info):
        """Record/clear the pending update and refresh the menu + ☰ badge."""
        self._pending_update = info
        self._rebuild_app_menu()
        self._apply_menu_button_icon()

    def _apply_menu_button_icon(self):
        """The ☰ icon, with a small accent dot when an update is pending."""
        if getattr(self, "_pending_update", None):
            self.menu_btn.setIcon(self._badged_icon("menu", "text_dim"))
        else:
            self.menu_btn.setIcon(self._icon("menu", "text_dim"))

    def _badged_icon(self, name, color_key, size=20):
        """A themed icon with a small accent 'notification' dot in the top-right."""
        from PySide6.QtGui import QColor, QPainter, QPixmap
        # icons.pixmap() returns a shared, cached pixmap — copy before painting.
        pm = QPixmap(icons.pixmap(name, self.colors[color_key], size))
        dpr = pm.devicePixelRatio()
        d = int(7 * dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.colors["accent"]))
        p.drawEllipse(pm.width() - d, 0, d, d)
        p.end()
        return QIcon(pm)

    def _build_update_menu_item(self, menu, info):
        """A QWidgetAction styled to stand out (accent, semibold) so an available
        update reads as a call-to-action rather than a regular menu entry."""
        action = QWidgetAction(menu)
        button = QPushButton(self._icon("download", "accent"),
                             tr("Update available — v{version}").format(version=info.version))
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(
            f"QPushButton {{ text-align: left; padding: 7px 12px; border: none;"
            f" background: transparent; color: {self.colors['accent']}; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {self.colors['selection']}; }}")
        button.clicked.connect(lambda: (menu.close(), self._show_update_dialog(info)))
        action.setDefaultWidget(button)
        return action

    def _build_app_menu(self):
        """Hamburger menu with file operations and view options."""
        menu = QMenu(self)
        menu.addAction(self._icon("upload"), tr("Open Excel Table…"), self.open_table_action)
        menu.addAction(self._icon("upload"), tr("Import Excel to Database…"), self.import_excel)
        menu.addAction(self._icon("download"), tr("Save Import Template…"), self.save_import_template)
        menu.addSeparator()
        export_menu = menu.addMenu(self._icon("download"), tr("Export"))
        export_menu.addAction(tr("PDF…"), self.export_pdf)
        export_menu.addAction(tr("Excel / CSV…"), self.export_excel)
        export_menu.addAction(tr("TXT…"), self.export_txt)
        export_menu.addAction(tr("Audio (MP3)…"), self.save_audio_action)
        menu.addAction(self._icon("archive"), tr("Backups…"), self.open_backups)
        menu.addSeparator()
        # carry the checked states over when the menu is rebuilt (theme change)
        show_source = getattr(self, "action_show_source", None)
        show_created = getattr(self, "action_show_created", None)
        self.action_show_source = QAction(tr("Show Source column"), self, checkable=True)
        self.action_show_source.setChecked(show_source.isChecked() if show_source else False)
        self.action_show_source.toggled.connect(self.toggle_source_column)
        menu.addAction(self.action_show_source)
        self.action_show_created = QAction(tr("Show Created At column"), self, checkable=True)
        self.action_show_created.setChecked(show_created.isChecked() if show_created else False)
        self.action_show_created.toggled.connect(self.toggle_created_column)
        menu.addAction(self.action_show_created)
        menu.addAction(self._icon("rows"), tr("Max words…"), self.prompt_row_limit)
        menu.addSeparator()
        menu.addAction(self._icon("list"), tr("View Log"), self.open_log_window)
        if getattr(self, "_pending_update", None):
            menu.addAction(self._build_update_menu_item(menu, self._pending_update))
        menu.addAction(tr("About"), self.show_about)
        menu.addAction(self._icon("x"), tr("Quit"), self.quit_app)
        return menu

    def _build_ui(self):
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------- sidebar ----------
        sidebar = QWidget(objectName="Sidebar")
        sidebar.setFixedWidth(58)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(0, 10, 0, 10)
        sb.setSpacing(2)

        self.menu_btn = QPushButton()
        self._apply_menu_button_icon()
        self.menu_btn.setIconSize(QSize(20, 20))
        self.menu_btn.setToolTip(tr("Menu"))
        self.menu_btn.setCursor(Qt.PointingHandCursor)
        self.app_menu = self._build_app_menu()
        self.menu_btn.clicked.connect(lambda: self.app_menu.exec(
            self.menu_btn.mapToGlobal(QPoint(self.menu_btn.width(), 0))))
        sb.addWidget(self.menu_btn)
        sb.addSpacing(12)

        def nav_button(icon_name, tooltip, slot, checkable=False, checked=False):
            btn = QPushButton()
            self._set_icon(btn, icon_name, "text" if checked else "text_dim")
            btn.setIconSize(QSize(21, 21))
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.PointingHandCursor)
            if checkable or checked:
                btn.setCheckable(True)
                btn.setChecked(checked)
            btn.clicked.connect(slot)
            sb.addWidget(btn)
            return btn

        self.nav_words = nav_button("book-open", tr("Words"),
                                    lambda: self.switch_page(PAGE_WORDS),
                                    checkable=True, checked=True)
        self.nav_texts = nav_button("file-text", tr("Texts"),
                                    lambda: self.switch_page(PAGE_TEXTS),
                                    checkable=True)
        self.nav_stats = nav_button("bar-chart", tr("Statistics"),
                                    lambda: self.switch_page(PAGE_STATS),
                                    checkable=True)
        self.nav_bin = nav_button("trash", tr("Bin (deleted items)"), self.open_bin)
        self.nav_bin.setVisible(self.sync_enabled)
        sb.addStretch(1)
        nav_button("sliders", tr("Settings"), self.open_settings)

        outer.addWidget(sidebar)

        # ---------- content column ----------
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---------- top bar (draggable, holds window controls) ----------
        from app.ui.titlebar import DragArea, FramelessResizer, WindowControls

        header = DragArea(objectName="TopBar")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 8, 6, 8)
        h.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel(APP_NAME, objectName="AppTitle")
        title_box.addWidget(title)

        subtitle_row = QHBoxLayout()
        subtitle_row.setSpacing(6)
        self.source_label = QLabel(tr("Vocabulary"), objectName="SubTitle")
        subtitle_row.addWidget(self.source_label)
        self.close_file_btn = QPushButton(objectName="iconButton")
        self._set_icon(self.close_file_btn, "x", "text_dim", 14)
        self.close_file_btn.setIconSize(QSize(13, 13))
        self.close_file_btn.setFixedSize(20, 20)
        self.close_file_btn.setCursor(Qt.PointingHandCursor)
        self.close_file_btn.setToolTip(tr("Close file and return to your vocabulary"))
        self.close_file_btn.clicked.connect(self.load_data)
        self.close_file_btn.hide()
        subtitle_row.addWidget(self.close_file_btn)
        subtitle_row.addStretch(1)
        title_box.addLayout(subtitle_row)
        h.addLayout(title_box)

        h.addStretch(1)

        self.search_box = QLineEdit(objectName="SearchBox")
        self.search_box.setPlaceholderText(tr("Search words, translations or tags…"))
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setMinimumWidth(320)
        self.search_box.setMaximumWidth(560)
        self.search_box.textChanged.connect(self.on_search_changed)
        search_icon_action = self.search_box.addAction(
            self._icon("search", "text_dim", 16), QLineEdit.LeadingPosition)
        self._themed_icons.append((search_icon_action, "search", "text_dim", 16))
        h.addWidget(self.search_box, 2, Qt.AlignVCenter)

        self.search_scope_btn = QPushButton(objectName="iconButton")
        self._set_icon(self.search_scope_btn, "filter", "text_dim")
        self.search_scope_btn.setIconSize(QSize(18, 18))
        self.search_scope_btn.setToolTip(tr("Search scope"))
        self.search_scope_btn.setCursor(Qt.PointingHandCursor)
        self.search_scope_btn.clicked.connect(self.show_search_scope_menu)
        h.addWidget(self.search_scope_btn, 0, Qt.AlignVCenter)

        self.add_button = QPushButton(objectName="iconButton")
        self._set_icon(self.add_button, "plus", "text_dim")
        self.add_button.setIconSize(QSize(19, 19))
        self.add_button.setToolTip(tr("Add word"))
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.clicked.connect(self.open_add_word)
        h.addWidget(self.add_button, 0, Qt.AlignVCenter)

        # the search box and its content actions stay grouped and centred; the
        # global sync button sits with the window controls at the far right
        h.addStretch(1)

        # Always build the sync button so it can be shown/hidden live when sync
        # is toggled in Settings; visibility tracks self.sync_enabled.
        self.sync_button = QPushButton(objectName="iconButton")
        self._set_icon(self.sync_button, "cloud", "text_dim")
        self.sync_button.setIconSize(QSize(19, 19))
        self.sync_button.setToolTip(tr("Cloud sync: idle"))
        self.sync_button.setCursor(Qt.PointingHandCursor)
        self.sync_button.clicked.connect(self.show_sync_info)
        self.sync_button.setVisible(self.sync_enabled)
        h.addWidget(self.sync_button, 0, Qt.AlignVCenter)

        h.addSpacing(8)
        self.window_controls = WindowControls(self, self.colors)
        h.addWidget(self.window_controls, 0, Qt.AlignVCenter)

        self._frameless_resizer = FramelessResizer(self)
        QApplication.instance().installEventFilter(self._frameless_resizer)

        root.addWidget(header)

        # ---------- filter row ----------
        filters = QWidget()
        f = QHBoxLayout(filters)
        f.setContentsMargins(16, 12, 16, 6)
        f.setSpacing(8)

        self.tag_combo = QComboBox()
        self.tag_combo.setMinimumWidth(125)
        self.tag_combo.currentTextChanged.connect(self.on_filters_changed)
        f.addWidget(self.tag_combo)

        # icon-only stand-in for the tag combo while the player is shown
        self.tag_icon_btn = QPushButton(objectName="chipButton")
        self._set_icon(self.tag_icon_btn, "tag", "text_dim", 16)
        self.tag_icon_btn.setIconSize(QSize(16, 16))
        self.tag_icon_btn.setToolTip(tr("Filter by tag"))
        self.tag_icon_btn.setCursor(Qt.PointingHandCursor)
        self.tag_icon_btn.clicked.connect(self._show_tag_menu)
        self.tag_icon_btn.setVisible(False)
        f.addWidget(self.tag_icon_btn)

        self.favorites_btn = QPushButton(tr(" Favorites"), objectName="chipButton")
        self.favorites_btn.setIcon(self._icon("star", "text_dim", 16))  # re-tinted via _on_favorites_toggled
        self.favorites_btn.setCheckable(True)
        self.favorites_btn.setCursor(Qt.PointingHandCursor)
        self.favorites_btn.toggled.connect(self._on_favorites_toggled)
        f.addWidget(self.favorites_btn)

        f.addStretch(1)
        self.filter_row = filters

        # ---------- contextual actions (right side of the filter row) ----------
        # The row is fixed-height and wide enough for chips + actions even at
        # the minimum window width, so showing the bar never moves anything.
        self.action_bar = QWidget(objectName="ActionBar")
        ab = QHBoxLayout(self.action_bar)
        ab.setContentsMargins(10, 0, 10, 0)
        ab.setSpacing(2)

        self.selection_label = QLabel("")
        ab.addWidget(self.selection_label)
        ab.addSpacing(8)

        def action_button(icon_name, text, tip, slot):
            btn = QPushButton()
            self._set_icon(btn, icon_name, "text", 17)
            btn.setIconSize(QSize(17, 17))
            btn.setToolTip(f"{text} — {tip}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            ab.addWidget(btn)
            return btn

        action_button("book", tr("Definition"), tr("View definition (double-click)"), self.view_definition)
        self.read_button = action_button("volume", tr("Read"), tr("Read selected words aloud"),
                                         self.read_words_action)
        action_button("star", tr("Favorite"), tr("Toggle favorite"), self.toggle_favorite)
        action_button("tag", tr("Tags"), tr("Add / remove tags"), self.open_tags)
        action_button("edit", tr("Edit"), tr("Edit word"), self.edit_row)
        action_button("copy", tr("Copy"), tr("Copy words"), self.show_copy_menu)
        action_button("sparkles", tr("Text"), tr("Generate text from selection"),
                      self.generate_text_action)
        ab.addSpacing(6)
        delete_btn = QPushButton()
        self._set_icon(delete_btn, "trash", "danger", 17)
        delete_btn.setIconSize(QSize(17, 17))
        delete_btn.setToolTip(tr("Delete selected (Del)"))
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.clicked.connect(self.delete_rows)
        ab.addWidget(delete_btn)

        # ---------- playback bar (appears left of the actions while reading) ----------
        self.player_bar = PlayerBar(self.colors)
        self.player_bar.setVisible(False)
        self.player_bar.prev_clicked.connect(self.word_player.prev)
        self.player_bar.toggle_clicked.connect(self.word_player.toggle_pause)
        self.player_bar.next_clicked.connect(self.word_player.next)
        self.player_bar.config_clicked.connect(self._open_playback_settings)
        self.player_bar.stop_clicked.connect(self.word_player.stop)
        self._playback_popup = None

        # ---------- floating mini player (shown while hidden/minimized) ----------
        self.mini_player = MiniPlayer(self.colors)
        self.mini_player.prev_clicked.connect(self._mini_prev)
        self.mini_player.toggle_clicked.connect(self._mini_toggle)
        self.mini_player.next_clicked.connect(self._mini_next)
        self.mini_player.restore_requested.connect(self.show_window)
        self.mini_player.moved.connect(
            lambda: save_geometry(self.mini_player, "mini_player"))

        self.action_bar.setVisible(False)
        f.addWidget(self.player_bar)
        f.addSpacing(8)
        f.addWidget(self.action_bar)
        self._lock_filter_row_height()

        # ---------- pages (words / texts) ----------
        self.stack = AnimatedStackedWidget()
        words_page = QWidget()
        wp = QVBoxLayout(words_page)
        wp.setContentsMargins(0, 0, 0, 0)
        wp.setSpacing(0)
        wp.addWidget(filters)

        # ---------- table ----------
        table_wrap = QWidget()
        tw = QVBoxLayout(table_wrap)
        tw.setContentsMargins(16, 0, 16, 8)

        self.model = WordTableModel(self.colors, self)
        self.table = WordTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.density_step = self._step_table_density
        self.table.density_reset = self._reset_table_density
        self._apply_table_density()
        self.table.setWordWrap(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.doubleClicked.connect(lambda _: self.view_definition())

        self.table.setMouseTracking(True)
        from app.ui.delegates import RowTintDelegate, StatusPillDelegate
        self._row_delegate = RowTintDelegate(self.table)
        self.table.setItemDelegate(self._row_delegate)
        self._status_delegate = StatusPillDelegate(self.table)
        self.table.setItemDelegateForColumn(COL_STATUS, self._status_delegate)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        table_header = self.table.horizontalHeader()
        table_header.setSectionResizeMode(QHeaderView.Interactive)
        table_header.setStretchLastSection(False)
        self.table.setColumnHidden(COL_ID, True)
        # column widths must fit the header labels at the active font
        # scaling; QSS renders header sections at font-weight 600
        table_header.ensurePolished()
        header_font = QFont(table_header.font())
        header_font.setWeight(QFont.DemiBold)
        hfm = QFontMetrics(header_font)

        def header_width(col, minimum):
            # +56: room for the embedded filter combo's chevron + paddings
            return max(minimum, hfm.horizontalAdvance(HEADERS[col]) + 56)

        # Minimum width per meta column (must clear the embedded filter combo);
        # _fit_meta_columns() grows these to fit localized cell content at start.
        self._meta_col_min = {COL_STATUS: header_width(COL_STATUS, 116),
                              COL_LANG1: header_width(COL_LANG1, 110),
                              COL_LANG2: header_width(COL_LANG2, 110)}
        self._meta_fitted = False
        self.table.setColumnWidth(1, 46)
        for col, width in self._meta_col_min.items():
            self.table.setColumnWidth(col, width)
        self.table.setColumnHidden(COL_SOURCE, True)
        self.table.setColumnHidden(COL_CREATED, True)

        # ---------- filter combos embedded in the header sections ----------
        self._header_filters = {}
        for col, placeholder in ((COL_STATUS, tr("Status")), (COL_LANG1, tr("Language")),
                                 (COL_LANG2, tr("Translation"))):
            combo = _HeaderFilterCombo(table_header)
            combo.setObjectName("headerFilter")
            combo.setCursor(Qt.PointingHandCursor)
            combo.addItem(placeholder)
            combo.view().setMinimumWidth(170)
            combo.currentTextChanged.connect(self.on_filters_changed)
            self.model.set_header_text(col, "")
            self._header_filters[col] = combo
        self.status_combo = self._header_filters[COL_STATUS]
        self.lang1_combo = self._header_filters[COL_LANG1]
        self.lang2_combo = self._header_filters[COL_LANG2]

        table_header.sectionResized.connect(self._position_header_filters)
        table_header.geometriesChanged.connect(self._position_header_filters)
        self.table.horizontalScrollBar().valueChanged.connect(self._position_header_filters)
        QTimer.singleShot(0, self._position_header_filters)

        # Refitting the word columns repaints the whole viewport, which is
        # too slow to do on every resize tick — throttle to ~12 fps and
        # always run once more after the drag stops.
        self._col_fit_timer = QTimer(self)
        self._col_fit_timer.setSingleShot(True)
        self._col_fit_timer.setInterval(80)
        self._col_fit_timer.timeout.connect(self._fit_word_columns)
        self._col_fit_elapsed = QElapsedTimer()
        self._col_fit_elapsed.start()
        QTimer.singleShot(0, self._fit_word_columns)

        tw.addWidget(self.table)
        wp.addWidget(table_wrap, 1)

        self.texts_page = TextsPage(self.db_adapter, self.colors)
        self.texts_page.counts_changed.connect(self._on_texts_counts)

        self.stats_page = StatsPage(self.db_adapter, self.colors)

        self.stack.addWidget(words_page)
        self.stack.addWidget(self.texts_page)
        self.stack.addWidget(self.stats_page)
        root.addWidget(self.stack, 1)

        # ---------- footer ----------
        footer = QWidget(objectName="Footer")
        fo = QHBoxLayout(footer)
        fo.setContentsMargins(16, 6, 16, 6)
        self.words_label = QLabel(tr("No data"))
        self.words_label.setObjectName("dimLabel")
        fo.addWidget(self.words_label)
        fo.addStretch(1)
        self.status_message = QLabel("")
        self.status_message.setObjectName("dimLabel")
        fo.addWidget(self.status_message)
        version_label = QLabel(f"v{APP_VERSION}")
        version_label.setObjectName("dimLabel")
        fo.addWidget(version_label)
        root.addWidget(footer)

        outer.addWidget(content, 1)
        self.setCentralWidget(central)

    def _position_header_filters(self, *_):
        header = self.table.horizontalHeader()
        needed = max(c.sizeHint().height() for c in self._header_filters.values()) + 4
        if header.minimumHeight() < needed:
            header.setMinimumHeight(needed)
        for col, combo in self._header_filters.items():
            if self.table.isColumnHidden(col):
                combo.hide()
                continue
            x = header.sectionViewportPosition(col)
            w = header.sectionSize(col)
            ch = combo.sizeHint().height()
            combo.setGeometry(x + 2, (header.height() - ch) // 2, w - 4, ch)
            combo.show()

    def showEvent(self, event):
        super().showEvent(event)
        # When launched with --minimized the window is shown for the first time
        # from the tray. At construction the table viewport had no real width,
        # so the word columns were sized to a stale/narrow viewport, leaving
        # empty space on the right. Refit once the real geometry is in place.
        if hasattr(self, '_col_fit_timer'):
            QTimer.singleShot(0, self._fit_word_columns)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, '_col_fit_timer'):
            return
        if self._col_fit_elapsed.elapsed() >= self._col_fit_timer.interval():
            self._fit_word_columns()
        else:
            self._col_fit_timer.start()

    def _fit_meta_columns(self):
        """Size the Status / Language / Translation columns to their content
        once, so localized labels (e.g. 'Переглянуто', 'Англійська') aren't
        clipped on launch. Columns stay user-resizable (Interactive) afterwards."""
        for col in (COL_STATUS, COL_LANG1, COL_LANG2):
            if self.table.isColumnHidden(col):
                continue
            self.table.resizeColumnToContents(col)        # uses delegate sizeHint
            width = max(self.table.columnWidth(col), self._meta_col_min[col])
            self.table.setColumnWidth(col, min(width, 320))  # cap runaway widths
        self._fit_word_columns()

    def _fit_word_columns(self):
        self._col_fit_timer.stop()
        self._col_fit_elapsed.restart()
        header = self.table.horizontalHeader()
        viewport_w = self.table.viewport().width()
        fixed = sum(
            self.table.columnWidth(c)
            for c in range(header.count())
            if c not in (COL_WORD1, COL_WORD2) and not self.table.isColumnHidden(c)
        )
        available = max(100, viewport_w - fixed)
        self.table.setColumnWidth(COL_WORD1, available // 2)
        self.table.setColumnWidth(COL_WORD2, available - available // 2)

    def _lock_filter_row_height(self):
        """Fix the filter row height so swapping chips/actions can't shift
        the table; recompute after theme/scaling changes."""
        chips = (self.tag_combo, self.favorites_btn)
        row_h = max(w.sizeHint().height() for w in chips)
        self.action_bar.setMaximumHeight(row_h)
        self.player_bar.setMaximumHeight(row_h)
        margins = self.filter_row.layout().contentsMargins()
        self.filter_row.setFixedHeight(row_h + margins.top() + margins.bottom())

    def _on_selection_changed(self, *_):
        count = len(self.table.selectionModel().selectedRows())
        self.selection_label.setText(tr("{count} selected").format(count=count))
        # with no selection the bar is only up for playback — "0 selected"
        # would be noise and widens the row at minimum window width
        self.selection_label.setVisible(count > 0)
        # the action bar stays up while reading aloud (it holds the stop button)
        self.action_bar.setVisible(count > 0 or self.is_reading_active)

    def _on_favorites_toggled(self, checked):
        self.favorites_btn.setIcon(
            self._icon("star-filled" if checked else "star",
                       "warning" if checked else "text_dim", 16))
        self.on_filters_changed()

    def _setup_shortcuts(self):
        QShortcut(QKeySequence.SelectAll, self.table, self.table.selectAll)
        QShortcut(QKeySequence.Copy, self.table, self.copy_selected)
        QShortcut(QKeySequence.Delete, self.table, self.delete_rows)
        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self.switch_page(PAGE_WORDS))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self.switch_page(PAGE_TEXTS))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self.switch_page(PAGE_STATS))

    def _hotkey_setting(self):
        return (self.settings.get("hotkey", DEFAULT_HOTKEY) or "").strip()

    def _setup_global_hotkey(self):
        """Global Add-Word hotkey (configurable in Settings → System).

        On Linux the pynput listener runs in a SEPARATE PROCESS — its X11
        record thread can segfault, and in-process that kills the whole
        app. The agent prints a line per hotkey press; we restart it if
        it dies.
        """
        self._hotkey_listener = None
        self._hotkey_proc = None
        self._hotkey_handle = None
        self._active_hotkey = None
        self._apply_global_hotkey()

    def _apply_global_hotkey(self):
        """(Re)register the hotkey from settings; safe to call repeatedly."""
        hotkey = self._hotkey_setting()
        if hotkey == self._active_hotkey:
            return
        self._active_hotkey = hotkey
        self._update_tray_hotkey_label()

        if sys.platform == 'win32':
            try:
                import keyboard
                if self._hotkey_handle is not None:
                    keyboard.remove_hotkey(self._hotkey_handle)
                    self._hotkey_handle = None
                if hotkey:
                    self._hotkey_handle = keyboard.add_hotkey(
                        _hotkey_to_keyboard(hotkey), self.hotkey_pressed.emit)
            except Exception as exc:
                logging.warning(f"Global hotkey unavailable: {exc}")
            return

        self._stop_hotkey_agent()
        if hotkey:
            self._start_hotkey_agent()

    def _update_tray_hotkey_label(self):
        action = getattr(self, "tray_add_action", None)
        if action is not None:
            hotkey = self._hotkey_setting()
            action.setText(f"{tr('Add Word')} ({hotkey})" if hotkey else tr("Add Word"))

    def _stop_hotkey_agent(self):
        proc = self._hotkey_proc
        if proc is None:
            return
        try:
            proc.finished.disconnect(self._on_hotkey_agent_died)
        except Exception:
            pass
        proc.kill()
        proc.waitForFinished(1000)
        proc.deleteLater()
        self._hotkey_proc = None

    def _start_hotkey_agent(self):
        from PySide6.QtCore import QProcess
        agent = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "system", "hotkey_agent.py")
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([agent, _hotkey_to_pynput(self._active_hotkey or DEFAULT_HOTKEY)])
        proc.readyReadStandardOutput.connect(self._on_hotkey_agent_output)
        proc.finished.connect(self._on_hotkey_agent_died)
        proc.start()
        self._hotkey_proc = proc

    def _on_hotkey_agent_output(self):
        if self._hotkey_proc is None:
            return
        data = bytes(self._hotkey_proc.readAllStandardOutput()).decode(errors="replace")
        for line in data.splitlines():
            if line.strip() == "HOTKEY":
                self.hotkey_pressed.emit()

    def _on_hotkey_agent_died(self, *_):
        if self._quitting:
            return
        logging.warning("Hotkey agent exited — restarting in 2s")
        QTimer.singleShot(2000, lambda: not self._quitting and self._start_hotkey_agent())

    def _build_tray(self):
        from PySide6.QtWidgets import QSystemTrayIcon

        # Tray uses a dedicated, per-OS icon so the top-panel/notification-area
        # glyph can differ from the launcher and window icons (which stay on
        # icon.png). See _tray_icon_path() for the Windows/other selection.
        self.tray = QSystemTrayIcon(QIcon(_tray_icon_path()), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction(tr("Show"), self.show_window)
        menu.addSeparator()
        self.tray_add_action = menu.addAction(tr("Add Word"), self.open_add_word_and_translate)
        self._update_tray_hotkey_label()
        menu.addSeparator()
        menu.addAction(tr("Quit"), self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        from PySide6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
                self._sync_mini_player()
            else:
                self.show_window()

    def show_window(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.raise_()
        self.activateWindow()
        self._sync_mini_player()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_mini_player()

    def _sync_mini_player(self):
        """Float the mini player only while a player is active (words table or
        texts reader) AND the window is away (hidden to tray or minimized)."""
        active = self.is_reading_active or self.texts_page.is_reading
        show = active and (not self.isVisible() or self.isMinimized())
        if show:
            if not self._mini_positioned:
                self._mini_positioned = True
                self._restore_mini_geometry()
            self.mini_player.show()
            self.mini_player.raise_()
        else:
            self.mini_player.hide()

    def _restore_mini_geometry(self):
        """Place the mini player at its saved spot, or bottom-right on first run."""
        entry = None
        if os.path.exists(GEOMETRY_FILE):
            try:
                with open(GEOMETRY_FILE) as fh:
                    entry = json.load(fh).get("mini_player", {}).get("geometry")
            except Exception:
                entry = None
        if isinstance(entry, list) and len(entry) == 4:
            self.mini_player.resize(entry[2], self.mini_player.HEIGHT)
            self.mini_player.move(entry[0], entry[1])
        else:
            self.mini_player.place_default()

    def closeEvent(self, event):
        if self._quitting:
            save_geometry(self, "main_window")
            event.accept()
            return
        # Hide to tray instead of closing
        save_geometry(self, "main_window")
        self.hide()
        event.ignore()
        self._sync_mini_player()

    def quit_app(self):
        self._quitting = True
        try:
            self.word_player.stop()
            self.texts_page.stop_reading()
            stop_playback()
        except Exception:
            pass
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        if getattr(self, "_hotkey_proc", None) is not None:
            try:
                self._hotkey_proc.finished.disconnect(self._on_hotkey_agent_died)
                self._hotkey_proc.kill()
            except Exception:
                pass
        save_geometry(self, "main_window")
        if self._mini_positioned:
            save_geometry(self.mini_player, "mini_player")
        self.mini_player.hide()
        self.tray.hide()
        QApplication.quit()

    # -------------------------------------------------------------- pages

    def switch_page(self, index, animate=True):
        """Swap the central view between Words, Texts and Statistics. The top
        bar stays shared but contextual: search applies to the active page,
        while words-only controls (Add Word, search scope) hide elsewhere and
        search itself is disabled on the dashboard."""
        for btn, page, icon in ((self.nav_words, PAGE_WORDS, "book-open"),
                                (self.nav_texts, PAGE_TEXTS, "file-text"),
                                (self.nav_stats, PAGE_STATS, "bar-chart")):
            btn.setChecked(index == page)
            self._set_icon(btn, icon, "text" if index == page else "text_dim")

        current = self.stack.currentIndex()
        if index == current:
            return

        # each page keeps its own search text
        self._page_search[current] = self.search_box.text()
        self.search_box.blockSignals(True)
        self.search_box.setText(self._page_search.get(index, ""))
        self.search_box.setPlaceholderText(
            tr("Search words, translations or tags…") if index == PAGE_WORDS
            else tr("Search texts by title, content or words…") if index == PAGE_TEXTS
            else "")
        self.search_box.blockSignals(False)

        on_words = index == PAGE_WORDS
        on_stats = index == PAGE_STATS
        self.add_button.setVisible(on_words)
        self.search_scope_btn.setVisible(on_words)
        self.search_box.setVisible(not on_stats)
        subtitle = (self._words_subtitle if on_words
                    else tr("Statistics") if on_stats else tr("Texts"))
        self.source_label.setText(subtitle)
        self._update_file_view()

        if index == PAGE_TEXTS:
            self.texts_page.set_search(self.search_box.text())
            self.texts_page.load_texts()
        elif on_stats:
            self._refresh_stats()

        if animate:
            self.stack.set_current_index_animated(index)
        else:
            self.stack.setCurrentIndex(index)
        self.words_label.setText(self._footer_counts[index])

        if on_words:
            # the table can miss resizes while hidden (e.g. the window was
            # maximized on the Texts tab); refit the word columns to the
            # now-current width once the page is shown and laid out
            QTimer.singleShot(0, self._fit_word_columns)

    def _refresh_stats(self):
        """Recompute the dashboard from the in-memory words DataFrame plus tag
        and definition counts. Cheap and exception-guarded inside the page."""
        try:
            tag_counts = dbq.get_tag_usage_counts()
        except Exception:
            tag_counts = {}
        try:
            def_counts = dbq.get_definition_counts()
        except Exception:
            def_counts = None
        try:
            reviews = dbq.get_review_aggregates()
        except Exception:
            reviews = None
        self.stats_page.set_data(self.df, tag_counts, def_counts, reviews)

    def _on_texts_counts(self, shown, total):
        if total == 0:
            text = tr("No texts yet")
        elif shown == total:
            text = tr("Texts: {total}").format(total=total)
        else:
            text = tr("Texts: {shown}/{total}").format(shown=shown, total=total)
        self._footer_counts[PAGE_TEXTS] = text
        if self.stack.currentIndex() == PAGE_TEXTS:
            self.words_label.setText(text)

    # --------------------------------------------------------------- data

    def load_data(self):
        try:
            words = self.db_adapter.get_words()
            self.df = words_to_dataframe(words)
            self.update_filter_combos()
            self.refresh_display()
            self._words_subtitle = tr("Vocabulary")
            self._file_view = False
            self._update_file_view()
            if self.stack.currentIndex() == PAGE_WORDS:
                self.source_label.setText(self._words_subtitle)
            elif self.stack.currentIndex() == PAGE_STATS:
                self._refresh_stats()
            logging.info("Database loaded successfully.")
        except Exception as exc:
            logging.error(f"Database loading failed: {exc}")
            QMessageBox.critical(self, tr("Database Error"), f"{tr('Failed to load the database:')} {exc}")

    def _update_file_view(self):
        """Show the 'close file' affordance only while previewing an opened
        Excel file on the Words page."""
        self.close_file_btn.setVisible(
            self._file_view and self.stack.currentIndex() == PAGE_WORDS)

    def update_filter_combos(self):
        if self.df is None:
            return
        languages = sorted({str(v) for v in set(self.df['Language1']).union(set(self.df['Language2']))
                            if isinstance(v, str) and v})
        statuses = sorted({s for s in set(self.df['Status']) if isinstance(s, str) and s}
                          | set(PREDEFINED_STATUSES))

        # Language filter combos: the queried value stays English (item
        # userData); only the displayed label is localized.
        for combo, default in [
            (self.lang1_combo, tr("Language")),
            (self.lang2_combo, tr("Translation")),
        ]:
            current = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(default)  # placeholder, userData = None
            fill_lang_combo(combo, languages)
            if current:
                i = combo.findData(current)
                if i >= 0:
                    combo.setCurrentIndex(i)
            combo.blockSignals(False)

        current = self.tag_combo.currentText()
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem(tr("All tags"))
        self.tag_combo.addItems(dbq.get_all_tags())
        if current and self.tag_combo.findText(current) >= 0:
            self.tag_combo.setCurrentText(current)
        self.tag_combo.blockSignals(False)

        # Status combo: the stored/queried value stays English (kept as item
        # userData); only the displayed label is localized.
        current_status = self.status_combo.currentData()
        self.status_combo.blockSignals(True)
        self.status_combo.clear()
        self.status_combo.addItem(tr("Status"))  # placeholder, userData = None
        for s in statuses:
            self.status_combo.addItem(tr(s), s)
        if current_status:
            i = self.status_combo.findData(current_status)
            if i >= 0:
                self.status_combo.setCurrentIndex(i)
        self.status_combo.blockSignals(False)

    def on_search_changed(self, text):
        if self.stack.currentIndex() == PAGE_TEXTS:
            self.texts_page.set_search(text)
        else:
            self.refresh_display()

    def on_filters_changed(self, *_):
        self.refresh_display()

    def show_search_scope_menu(self):
        menu = QMenu(self)
        for label, attr in [(tr("Search in Word"), "search_word1"),
                            (tr("Search in Translation"), "search_word2"),
                            (tr("Search in Tags"), "search_tags")]:
            cb = QCheckBox(label)
            cb.setChecked(getattr(self.word_filter, attr))
            cb.toggled.connect(lambda checked, a=attr: (
                setattr(self.word_filter, a, checked), self.refresh_display()))
            wa = QWidgetAction(menu)
            container = QWidget()
            lay = QHBoxLayout(container)
            lay.setContentsMargins(12, 6, 12, 6)
            lay.addWidget(cb)
            wa.setDefaultWidget(container)
            menu.addAction(wa)
        menu.exec(self.search_scope_btn.mapToGlobal(QPoint(0, self.search_scope_btn.height())))

    def refresh_display(self):
        if self.df is None:
            return
        wf = self.word_filter
        wf.lang1 = self.lang1_combo.currentData() if self.lang1_combo.currentIndex() != 0 else None
        wf.lang2 = self.lang2_combo.currentData() if self.lang2_combo.currentIndex() != 0 else None
        wf.status = self.status_combo.currentData() if self.status_combo.currentIndex() != 0 else None
        wf.selected_tag = self.tag_combo.currentText() if self.tag_combo.currentIndex() != 0 else None
        wf.favorites_only = self.favorites_btn.isChecked()
        wf.search_query = self.search_box.text()

        filtered = wf.apply(self.df)
        self.model.set_dataframe(filtered)

        # Fit the meta columns to content once, after the first rows arrive;
        # done here (not at construction) because resizeColumnToContents needs
        # populated rows. User resizing afterwards is preserved.
        if not self._meta_fitted and len(filtered):
            self._meta_fitted = True
            QTimer.singleShot(0, self._fit_meta_columns)

        total = len(self.df)
        words_text = tr("Words: {shown}/{total}").format(shown=len(filtered), total=total)
        if wf.row_limit is not None:
            words_text += " " + tr("(showing first {n})").format(n=wf.row_limit)
        self._footer_counts[PAGE_WORDS] = words_text
        if self.stack.currentIndex() == PAGE_WORDS:
            self.words_label.setText(words_text)

        for combo, active in [(self.lang1_combo, wf.lang1), (self.lang2_combo, wf.lang2),
                              (self.status_combo, wf.status), (self.tag_combo, wf.selected_tag)]:
            combo.setProperty("filterActive", bool(active))
            combo.style().unpolish(combo)
            combo.style().polish(combo)

        # keep the squashed tag icon in sync with the tag filter state
        self._set_icon(self.tag_icon_btn, "tag",
                       "accent" if wf.selected_tag else "text_dim", 16)
        self.tag_icon_btn.setToolTip(
            tr("Filter by tag — {tag}").format(tag=wf.selected_tag) if wf.selected_tag
            else tr("Filter by tag"))


    def toggle_source_column(self, checked):
        self.table.setColumnHidden(COL_SOURCE, not checked)
        if checked:
            self.table.resizeColumnToContents(COL_SOURCE)
        self._fit_word_columns()

    def toggle_created_column(self, checked):
        self.table.setColumnHidden(COL_CREATED, not checked)
        if checked:
            self.table.resizeColumnToContents(COL_CREATED)
        self._fit_word_columns()

    def prompt_row_limit(self):
        from app.ui.dialogs.base import ask_int
        current = self.word_filter.row_limit or 0
        value, ok = ask_int(self, tr("Max Words"),
                            tr("Show only the first N words (0 = show all):"),
                            current, 0, 1000000)
        if ok:
            self.word_filter.row_limit = value if value > 0 else None
            self.refresh_display()

    # ---------------------------------------------------------- selection

    def selected_records(self):
        rows = sorted({ix.row() for ix in self.table.selectionModel().selectedRows()})
        return [self.model.row_record(r) for r in rows]

    def _require_selection(self, action="continue"):
        records = self.selected_records()
        if not records:
            show_toast(self, tr("No selection"), tr("Please select at least one word."), "warning")
        return records

    # ------------------------------------------------------------ actions

    def open_add_word(self, prefill=None, auto_translate=False, language1=None):
        from app.ui.dialogs.add_word import AddWordDialog
        # When the main window is hidden/minimized (hotkey flow), open the
        # dialog without a parent so it doesn't drag the main window onto
        # the screen behind it.
        main_on_screen = self.isVisible() and not self.isMinimized()
        parent = self if main_on_screen else None
        dialog = AddWordDialog(parent, prefill=prefill, auto_translate=auto_translate,
                               language1=language1)
        dialog.word_saved.connect(self._after_db_change)
        if parent is None:
            self._open_dialogs["add_word"] = dialog  # keep it alive
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _on_text_word_add(self, word, language):
        """A word clicked in the texts reader: capture it with translation."""
        self.open_add_word(prefill=word, auto_translate=True, language1=language)

    def open_add_word_and_translate(self):
        clipboard = QGuiApplication.clipboard().text()
        words = clipboard.split()
        truncated = " ".join(words[:100])
        self.open_add_word(prefill=truncated, auto_translate=bool(truncated.strip()))

    def _after_db_change(self):
        self.load_data()

    def edit_row(self):
        records = self._require_selection("edit")
        if not records:
            return
        from app.ui.dialogs.edit_word import EditWordDialog
        record = records[0]
        languages = [self.lang1_combo.itemData(i) for i in range(1, self.lang1_combo.count())]
        statuses = [self.status_combo.itemData(i) for i in range(1, self.status_combo.count())]
        dialog = EditWordDialog(self, record, languages, statuses)
        if dialog.exec():
            updated = dialog.result_data()
            try:
                self._sync_before_db_operation()
                self.db_adapter.update_word(record["ID"], updated)
                backup_database()
                self.load_data()
                show_toast(self, tr("Saved"), tr("'{word}' updated.").format(word=updated.get('Word1', '')), "success")
            except Exception as exc:
                logging.error(f"Error updating row: {exc}")
                QMessageBox.critical(self, tr("Error"), tr("Failed to update: {error}").format(error=exc))

    def delete_rows(self):
        records = self._require_selection("delete")
        if not records:
            return
        names = ", ".join(str(r.get("Word1", "")) for r in records[:8])
        if len(records) > 8:
            names += ", …"
        if QMessageBox.question(
                self, tr("Delete"),
                tr("Delete {count} word(s)?").format(count=len(records)) + f"\n\n{names}",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        errors = 0
        for record in records:
            try:
                self.db_adapter.delete_word(int(record["ID"]))
            except Exception as exc:
                logging.error(f"Error deleting word {record['ID']}: {exc}")
                errors += 1
        backup_database()
        self.load_data()
        if errors:
            show_toast(self, tr("Delete"), tr("Deleted with {n} error(s).").format(n=errors), "warning")
        else:
            show_toast(self, tr("Deleted"), tr("{count} word(s) deleted.").format(count=len(records)), "success")

    def toggle_favorite(self):
        records = self._require_selection("favorite")
        if not records:
            return
        try:
            self._sync_before_db_operation(force=True)
            target = not all(bool(r.get("favorite")) for r in records)
            for record in records:
                self.db_adapter.update_word(int(record["ID"]), {'favorite': target})
            self.load_data()
            if target:
                show_toast(self, tr("Favorites"),
                           tr("{count} word(s) added to favorites.").format(count=len(records)), "success")
            else:
                show_toast(self, tr("Favorites"),
                           tr("{count} word(s) removed from favorites.").format(count=len(records)), "success")
        except Exception as exc:
            logging.error(f"Error toggling favorite: {exc}")

    def open_tags(self):
        records = self._require_selection("tag")
        if not records:
            return
        from app.ui.dialogs.tags import TagDialog
        dialog = TagDialog(self, [int(r["ID"]) for r in records], self.db_adapter)
        dialog.exec()
        self.update_filter_combos()
        self.refresh_display()

    def change_status(self):
        records = self._require_selection("change status")
        if not records:
            return
        from app.ui.dialogs.base import ask_item
        statuses = PREDEFINED_STATUSES
        labels = [tr(s) for s in statuses]
        chosen, ok = ask_item(self, tr("Change Status"), tr("New status:"), labels, 0, False)
        if not ok:
            return
        # map the localized label back to the canonical English status
        status = next((s for s in statuses if tr(s) == chosen), chosen)
        for record in records:
            try:
                self.db_adapter.update_word(int(record["ID"]), {'Status': status})
            except Exception as exc:
                logging.error(f"Error updating status: {exc}")
        backup_database()
        self.load_data()
        show_toast(self, tr("Status"),
                   tr("Status set to '{status}' for {count} word(s).").format(
                       status=tr(status), count=len(records)), "success")

    def view_definition(self):
        records = self._require_selection("view its definition")
        if not records:
            return
        from app.ui.dialogs.definition import DefinitionDialog
        record = records[0]
        key = int(record["ID"])
        existing = self._open_dialogs.get(("def", key))
        try:
            if existing is not None and existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
        except RuntimeError:
            pass  # WA_DeleteOnClose: the C++ widget is already gone
        dialog = DefinitionDialog(self, record, self.db_adapter)
        dialog.definition_changed.connect(self.load_data)
        self._open_dialogs[("def", key)] = dialog
        dialog.show()

    # ------------------------------------------------------------- copy

    def copy_selected(self):
        records = self.selected_records()
        if not records:
            return
        text = "\n".join(f"{r.get('Word1', '')}\t{r.get('Word2', '')}" for r in records)
        QGuiApplication.clipboard().setText(text)
        show_toast(self, tr("Copied"), tr("{count} row(s) copied to clipboard.").format(count=len(records)), "success", 2000)

    def show_copy_menu(self):
        records = self._require_selection("copy")
        if not records:
            return
        menu = QMenu(self)
        menu.addAction(tr("Copy Word(s)"), lambda: self._copy_field(records, 'Word1'))
        menu.addAction(tr("Copy Translation(s)"), lambda: self._copy_field(records, 'Word2'))
        menu.addAction(tr("Copy Both"), self.copy_selected)
        menu.exec(self.cursor().pos())

    def _copy_field(self, records, field):
        QGuiApplication.clipboard().setText("\n".join(str(r.get(field, "")) for r in records))
        show_toast(self, tr("Copied"), tr("{count} item(s) copied to clipboard.").format(count=len(records)), "success", 2000)

    # ----------------------------------------------------------- context

    def show_context_menu(self, pos):
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        menu = QMenu(self)
        menu.addAction(tr("View Definition"), self.view_definition)
        menu.addAction(tr("Edit"), self.edit_row)
        menu.addAction(tr("Delete"), self.delete_rows)
        menu.addSeparator()
        menu.addAction(tr("Copy Word"), lambda: self._copy_field(self.selected_records(), 'Word1'))
        menu.addAction(tr("Copy Translation"), lambda: self._copy_field(self.selected_records(), 'Word2'))
        menu.addSeparator()
        menu.addAction(tr("Toggle Favorite"), self.toggle_favorite)
        menu.addAction(tr("Change Status…"), self.change_status)
        menu.addAction(tr("Add / Remove Tags…"), self.open_tags)
        menu.addSeparator()
        menu.addAction(tr("Read Aloud"), self.read_words_action)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------- audio

    def read_words_action(self):
        if self.is_reading_active:
            self.word_player.stop()
            return

        records = self._require_selection("read aloud")
        if not records:
            return

        if not self._tts_fallback_warned:
            from app.core.audio import google_cloud_tts_problem
            problem = google_cloud_tts_problem()
            if problem:
                self._tts_fallback_warned = True
                show_toast(self, tr("Google Cloud TTS unavailable"),
                           tr("Using gTTS instead — {problem}\nFix it in Settings → Audio.").format(problem=problem),
                           "warning", 8000)

        if len(records) > 200:
            records = records[:200]
            show_toast(self, tr("Selection limit"),
                       tr("Only the first 200 selected words will be read."), "info")

        words = [(r.get('Word1', ''), r.get('Word2', '')) for r in records]
        languages = [(r.get('Language1', ''), r.get('Language2', '')) for r in records]

        self._playing_records = records
        # snapshot progression settings for this session
        self._promote_on_play = get_bool(self.settings, "playback_promote", True)
        self._thresholds = progression.normalize_thresholds(
            get_int(self.settings, "playback_reviewing_listens", 3),
            get_int(self.settings, "playback_learning_listens", 15),
            get_int(self.settings, "playback_mastered_listens", 100))
        self._session_status = {int(r['ID']): r.get('Status') for r in records if r.get('ID') is not None}
        self.texts_page.stop_reading()  # one player at a time
        # the queue is captured; clear the selection first so its highlight
        # doesn't drown out the moving played-row highlight — and so the
        # selection label never coexists with the player in one layout pass
        self.table.clearSelection()
        self._set_playback_ui(True)
        self.player_bar.set_paused(False)
        self.player_bar.set_position(0, len(records), records[0].get('Word1', ''))
        self.mini_player.set_paused(False)
        self.mini_player.set_pair(records[0].get('Word1', ''), records[0].get('Word2', ''))
        self.word_player.play(
            words, languages,
            pause=get_float(self.settings, "playback_pause", 0.5),
            repeats=get_int(self.settings, "playback_repeats", 1))
        self._sync_mini_player()  # may already be hidden to tray

    def _open_playback_settings(self):
        """Open the compact pacing popup anchored under the bar's config button.

        Changes are persisted to settings.cfg and applied live to the running
        session (and used as the snapshot for the next session)."""
        popup = PlaybackSettingsPopup(
            get_float(self.settings, "playback_pause", 0.5),
            get_int(self.settings, "playback_repeats", 1),
            self)

        def on_pause(value):
            self.settings["playback_pause"] = f"{value:g}"
            save_settings(self.settings)
            self.word_player.set_pause(value)

        def on_repeats(value):
            self.settings["playback_repeats"] = str(value)
            save_settings(self.settings)
            self.word_player.set_repeats(value)

        popup.pause_changed.connect(on_pause)
        popup.repeats_changed.connect(on_repeats)
        self._playback_popup = popup  # keep a reference alive
        popup.popup_at(self.player_bar.config_btn)

    def _set_playback_ui(self, active):
        """Show/hide the player bar; the words-only filter chips squash to
        icons while it is visible. The whole row swaps under a crossfade."""
        if self.is_reading_active == active:
            return
        self.is_reading_active = active
        fade_swap(self.filter_row, 200)
        # hide before show: a transient state holding both the wide chips and
        # the player would spike the row's minimum width and, at the smallest
        # window size, force the window to grow permanently
        has_selection = len(self.table.selectionModel().selectedRows()) > 0
        if active:
            self._pre_playback_width = self.width()
            self._playback_grown_width = 0
            self.tag_combo.setVisible(False)
            self.favorites_btn.setText("")
            self.tag_icon_btn.setVisible(True)
            self.player_bar.setVisible(True)
            self.action_bar.setVisible(True)
            # after the layout settles, note whether it force-grew the window
            QTimer.singleShot(0, lambda: setattr(
                self, "_playback_grown_width",
                self.width() if self.width() > self._pre_playback_width else 0))
        else:
            self.player_bar.setVisible(False)
            self.tag_icon_btn.setVisible(False)
            self.favorites_btn.setText(tr(" Favorites"))
            self.tag_combo.setVisible(True)
            self.action_bar.setVisible(has_selection)
            QTimer.singleShot(0, self._restore_pre_playback_width)
        if active:
            self.read_button.setIcon(self._icon("stop", "danger", 17))
            self.read_button.setToolTip(tr("Stop reading"))
        else:
            self.read_button.setIcon(self._icon("volume", "text", 17))
            self.read_button.setToolTip(tr("Read — Read selected words aloud"))

    def _restore_pre_playback_width(self, attempts=10):
        """If showing the player force-grew the window (minimum-size
        enforcement at small widths), shrink back once it is gone. A manual
        resize during playback (width no longer the forced one) is kept."""
        width = getattr(self, "_pre_playback_width", 0)
        grown = getattr(self, "_playback_grown_width", 0)
        if not (width and grown and self.width() == grown and not self.isMaximized()):
            return
        if self.minimumSizeHint().width() > width:
            # the layout hasn't dropped its minimum yet — try again shortly
            if attempts > 0:
                QTimer.singleShot(30, lambda: self._restore_pre_playback_width(attempts - 1))
            return
        self.resize(width, self.height())

    def _show_tag_menu(self):
        """Dropdown stand-in for the squashed tag combo."""
        menu = QMenu(self)
        current = self.tag_combo.currentText()
        for i in range(self.tag_combo.count()):
            label = self.tag_combo.itemText(i)
            action = menu.addAction(label, lambda t=label: self.tag_combo.setCurrentText(t))
            action.setCheckable(True)
            action.setChecked(label == current)
        menu.exec(self.tag_icon_btn.mapToGlobal(QPoint(0, self.tag_icon_btn.height())))

    def _on_player_index(self, i):
        records = self._playing_records
        if not records or i >= len(records):
            return
        record = records[i]
        self.player_bar.set_position(i, len(records), record.get('Word1', ''))
        self.mini_player.set_pair(record.get('Word1', ''), record.get('Word2', ''))
        self.model.set_queued_ids(r.get('ID') for r in records[i + 1:])
        row = self.model.set_playing_id(record.get('ID'))
        if row >= 0:
            self.table.scrollTo(self.model.index(row, COL_WORD1))

    def _on_player_part(self, slot):
        self.mini_player.set_active_part(slot)

    # mini-player transport routes to whichever player is currently active
    def _mini_prev(self):
        if self.texts_page.is_reading:
            self.texts_page.reader.prev_sentence()
        elif self.word_player.active:
            self.word_player.prev()

    def _mini_toggle(self):
        if self.texts_page.is_reading:
            self.texts_page.reader.toggle_pause()
        elif self.word_player.active:
            self.word_player.toggle_pause()

    def _mini_next(self):
        if self.texts_page.is_reading:
            self.texts_page.reader.next_sentence()
        elif self.word_player.active:
            self.word_player.next()

    def _on_reader_sentence(self, start, end):
        self._mini_text_start = start
        self.mini_player.set_line(self.texts_page.plain_text()[start:end])
        self._sync_mini_player()

    def _on_reader_word(self, start, end):
        if start < 0:
            return
        self.mini_player.set_text_word(
            start - self._mini_text_start, end - self._mini_text_start)

    def _on_player_state(self, paused):
        self.player_bar.set_paused(paused)
        self.mini_player.set_paused(paused)

    def _on_word_completed(self, i):
        """A word finished playing in full: record the listen and, if enabled,
        promote its status once enough cumulative listens have accrued."""
        try:
            records = self._playing_records
            if not records or i >= len(records):
                return
            rec = records[i]
            wid = rec.get('ID')
            if wid is None:
                return
            wid = int(wid)
            dbq.log_review(wid, datetime.now().isoformat(timespec='seconds'))

            if self._promote_on_play:
                count = dbq.get_play_count(wid)
                current = self._session_status.get(wid, rec.get('Status'))
                target = progression.next_status(current, count, self._thresholds)
                if target:
                    self.db_adapter.update_word(wid, {'Status': target})
                    self._session_status[wid] = target
                    if self.df is not None:
                        self.df.loc[self.df['ID'] == wid, 'Status'] = target
                    self.model.update_status(wid, target)
                    show_toast(self, tr("Promoted"),
                               f"'{rec.get('Word1', '')}' → {target}", "success", 2500)
        except Exception as exc:
            logging.error(f"Playback status update failed: {exc}")

    def _on_player_finished(self):
        self._set_playback_ui(False)
        self.mini_player.hide()
        self.model.set_playing_id(None)
        self.model.set_queued_ids(())
        if self.stack.currentIndex() == PAGE_STATS:
            self._refresh_stats()

    def save_audio_action(self):
        records = self.selected_records()
        if not records:
            show_toast(self, tr("No selection"), tr("Select words to save as audio."), "warning")
            return
        from app.ui.dialogs.audio_saver import AudioSaverDialog
        words = [(r.get('Word1', ''), r.get('Word2', '')) for r in records]
        languages = [(r.get('Language1', ''), r.get('Language2', '')) for r in records]
        initial_name = suggest_filename(
            "audio", word_count=len(words),
            lang1=self.lang1_combo.currentData(), lang2=self.lang2_combo.currentData(),
            status=self.status_combo.currentData(), extension=".mp3")
        dialog = AudioSaverDialog(self, words, languages, initial_name)
        dialog.exec()

    # --------------------------------------------------------------- gpt

    def generate_text_action(self):
        records = self._require_selection("generate a text from")
        if not records:
            return
        if len(records) > 50:
            records = records[:50]
            show_toast(self, tr("Selection limit"), tr("Only the first 50 words will be used."), "info")
        words = [str(r.get('Word1', '')) for r in records]
        language = records[0].get('Language1', 'English')

        from app.ui.dialogs.generate_text import GenerateTextDialog
        dialog = GenerateTextDialog(self, words, language)
        dialog.text_saved.connect(self._on_text_generated)
        dialog.show()

    def _on_text_generated(self):
        show_toast(self, tr("Texts"), tr("Generated text saved."), "success")
        if self.stack.currentIndex() == PAGE_TEXTS:
            self.texts_page.load_texts()

    # ------------------------------------------------------------ export

    def _visible_rows_for_export(self):
        records = self.selected_records()
        if records:
            return records
        df = self.model.dataframe()
        return [df.iloc[i].to_dict() for i in range(len(df))]

    def _export_rows(self):
        rows = self._visible_rows_for_export()
        out = []
        for i, r in enumerate(rows, start=1):
            row = dict(r)
            row["RowNumber"] = i
            out.append(row)
        return out

    def export_pdf(self):
        rows = self._export_rows()
        if not rows:
            show_toast(self, tr("Export"), tr("Nothing to export."), "warning")
            return
        settings = load_settings()
        suggested = suggest_filename("pdf_export", word_count=len(rows),
                                     lang1=self.lang1_combo.currentData(),
                                     lang2=self.lang2_combo.currentData(),
                                     status=self.status_combo.currentData(), extension=".pdf")
        path, _ = QFileDialog.getSaveFileName(self, tr("Save PDF As"), suggested, tr("PDF files (*.pdf)"))
        if not path:
            return
        try:
            exporters.register_fonts()
            warnings = exporters.export_to_pdf_file(rows, path, settings)
            if warnings:
                show_toast(self, tr("Export"), f"PDF saved to {path}. " + " ".join(warnings), "warning")
            else:
                show_toast(self, tr("Export"), tr("PDF saved to {path}").format(path=path), "success")
        except Exception as exc:
            logging.error(f"PDF export failed: {exc}")
            QMessageBox.critical(self, tr("Export Error"), tr("Failed to export PDF:\n{error}").format(error=exc))

    def export_excel(self):
        rows = self._export_rows()
        if not rows:
            show_toast(self, tr("Export"), tr("Nothing to export."), "warning")
            return
        settings = load_settings()
        export_format = settings.get("excel_format", "Excel").strip()
        if export_format not in ("Excel", "CSV"):
            export_format = "Excel"
        ext = ".xlsx" if export_format == "Excel" else ".csv"
        flt = tr("Excel files (*.xlsx)") if export_format == "Excel" else tr("CSV files (*.csv)")
        suggested = suggest_filename("export", word_count=len(rows),
                                     lang1=self.lang1_combo.currentData(),
                                     lang2=self.lang2_combo.currentData(),
                                     status=self.status_combo.currentData(), extension=ext)
        path, _ = QFileDialog.getSaveFileName(self, tr("Save As"), suggested, flt)
        if not path:
            return
        try:
            if export_format == "Excel":
                exporters.export_to_excel_file(rows, path, settings)
            else:
                exporters.export_to_csv_file(rows, path, settings)
            show_toast(self, tr("Export"), tr("{format} file saved to {path}").format(format=export_format, path=path), "success")
        except Exception as exc:
            logging.error(f"Export failed: {exc}")
            QMessageBox.critical(self, tr("Export Error"), tr("Failed to export:\n{error}").format(error=exc))

    def export_txt(self):
        rows = self._export_rows()
        if not rows:
            show_toast(self, tr("Export"), tr("Nothing to export."), "warning")
            return
        settings = load_settings()
        suggested = suggest_filename("export", word_count=len(rows),
                                     lang1=self.lang1_combo.currentData(),
                                     lang2=self.lang2_combo.currentData(),
                                     status=self.status_combo.currentData(), extension=".txt")
        path, _ = QFileDialog.getSaveFileName(self, tr("Save As"), suggested, tr("Text files (*.txt)"))
        if not path:
            return
        try:
            exporters.export_to_txt_file(rows, path, settings)
            show_toast(self, tr("Export"), tr("TXT file saved to {path}").format(path=path), "success")
        except Exception as exc:
            logging.error(f"TXT export failed: {exc}")
            QMessageBox.critical(self, tr("Export Error"), tr("Failed to export TXT:\n{error}").format(error=exc))

    # ------------------------------------------------------------ import

    def open_table_action(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Open Excel Table"), "",
                                              tr("Excel files (*.xlsx *.xls)"))
        if not path:
            return
        try:
            self.df = open_words_from_excel(path)
            self.update_filter_combos()
            self.refresh_display()
            self._words_subtitle = os.path.basename(path)
            self._file_view = True
            self.switch_page(PAGE_WORDS)
            self.source_label.setText(self._words_subtitle)
            self._update_file_view()
        except Exception as exc:
            logging.error(f"Error importing file: {exc}")
            QMessageBox.critical(self, tr("Error"), tr("Failed to open table:\n{error}").format(error=exc))

    def import_excel(self):
        from app.ui.dialogs.import_excel import ImportExcelFlow
        flow = ImportExcelFlow(self, self.db_adapter)
        flow.run()

    def save_import_template(self):
        path, _ = QFileDialog.getSaveFileName(self, tr("Save Import Template"),
                                              "import-template.xlsx", tr("Excel files (*.xlsx)"))
        if not path:
            return
        try:
            from app.core.importer import create_import_template
            create_import_template(path)
            show_toast(self, tr("Import"), tr("Template saved to {path}").format(path=path), "success")
        except Exception as exc:
            logging.error(f"Template save failed: {exc}")
            QMessageBox.critical(self, tr("Error"), tr("Failed to save template:\n{error}").format(error=exc))

    # ------------------------------------------------------------- sync

    def _reapply_sync(self):
        """Re-apply sync configuration live (enable_sync and/or changed Supabase
        credentials) without an app restart: refresh both Supabase clients from
        the current environment and show/hide the sync button. Bin visibility
        follows the real connection state, resolved by the sync result in
        _update_sync_status_ui."""
        enabled = get_bool(self.settings, "enable_sync", False)
        self.sync_enabled = enabled
        self.db_adapter.set_use_cloud(enabled)
        try:
            self.sync_manager.supabase.reconfigure()
        except Exception as exc:
            logging.warning(f"Sync client reconfigure failed: {exc}")
        self.sync_button.setVisible(enabled)
        if enabled:
            run_in_thread(self._run_startup_sync)
        else:
            self._cloud_connected = False
            self.nav_bin.setVisible(False)
            self._update_sync_status_ui("idle")

    def _run_startup_sync(self):
        try:
            if not self.sync_manager.is_sync_enabled():
                self.sync_status_changed.emit("error", tr("Not connected. Check internet or credentials"))
                return
            self.sync_status_changed.emit("syncing", tr("Syncing with cloud…"))
            self.sync_manager.sync_on_startup()
            self.sync_status_changed.emit("success", tr("Sync completed successfully"))
            self.reload_requested.emit()
        except RuntimeError:
            pass  # app shut down mid-sync; nothing to report
        except Exception as exc:
            logging.error(f"Sync failed: {exc}")
            self.sync_status_changed.emit("error", "Sync failed: check internet or credentials")

    def _update_sync_status_ui(self, status, message=""):
        self._sync_running = status == "syncing"
        # Terminal sync results reflect real cloud reachability; the Bin button
        # (cloud-dependent) follows it.
        if status in ("success", "error"):
            self._cloud_connected = status == "success"
            if hasattr(self, "nav_bin"):
                self.nav_bin.setVisible(self.sync_enabled and self._cloud_connected)
        if self.sync_button is None:
            return
        name, color_key = SYNC_ICONS.get(status, SYNC_ICONS["idle"])
        self.sync_button.setIcon(self._icon(name, color_key, 19))
        self.sync_button.setToolTip(f"{tr('Cloud sync')}: {message or tr(status)}")
        self.status_message.setText(message)
        if status in ("success", "error"):
            QTimer.singleShot(5000, lambda: (
                self.sync_button.setIcon(self._icon("cloud", "text_dim", 19)),
                self.status_message.setText("")))

    def show_sync_info(self):
        if self.sync_popover is None:
            from app.ui.sync_popover import SyncPopover
            self.sync_popover = SyncPopover(self.colors, parent=self)
            self.sync_popover.sync_requested.connect(
                lambda: run_in_thread(self._run_startup_sync))
        self.sync_popover.show_below(self.sync_button,
                                     self.sync_manager.get_sync_status,
                                     syncing=self._sync_running)

    def _sync_before_db_operation(self, force=False):
        """Quick background pull from cloud before local writes."""
        if not self.sync_enabled or not self.sync_manager.is_sync_enabled():
            return

        if not force:
            try:
                last_sync = self.sync_manager._get_last_sync_time()
                if last_sync:
                    if 'T' in last_sync:
                        last_dt = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
                    else:
                        last_dt = datetime.strptime(last_sync, '%Y-%m-%d %H:%M:%S')
                    if last_dt.tzinfo is not None:
                        last_dt = last_dt.replace(tzinfo=None)
                    if datetime.now() - last_dt < timedelta(seconds=3):
                        return
            except Exception:
                pass

        def quick_pull():
            try:
                if self.sync_manager.db_adapter.is_sync_lock_held():
                    return False
                conn = sqlite3.connect('dictionary.db')
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), MAX(edited_at) FROM words")
                before = cursor.fetchone()
                conn.close()

                if not self.sync_manager.quick_pull_words():
                    return False

                conn = sqlite3.connect('dictionary.db')
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*), MAX(edited_at) FROM words")
                after = cursor.fetchone()
                conn.close()
                return before != after
            except Exception as exc:
                logging.warning(f"Quick pull failed: {exc}")
                return False

        run_in_thread(quick_pull,
                      on_result=lambda changed: changed and self.reload_requested.emit())

    # ------------------------------------------------------------ windows

    def open_bin(self):
        from app.ui.dialogs.bin_window import BinWindow
        win = BinWindow(self, self.db_adapter, on_restored=self.load_data)
        win.show()

    def open_backups(self):
        from app.ui.dialogs.backups import BackupsDialog
        dialog = BackupsDialog(self, on_restored=self.load_data)
        dialog.exec()

    def _apply_appearance(self):
        """Re-resolve and re-apply the theme from current settings."""
        app = QApplication.instance()
        self.colors = theme.apply_theme(
            app,
            self.settings.get("appearance_mode", "System"),
            get_float(self.settings, "widget_scaling", 1.0))
        self.model.set_colors(self.colors)
        self._refresh_icons()
        self._lock_filter_row_height()
        self._apply_table_density()
        self.refresh_display()

    def _on_system_color_scheme_changed(self, _scheme):
        """The desktop's light/dark preference changed. When following the
        system ("System" mode) re-apply the theme. This also covers the case
        where the app launches during login before the color-scheme portal is
        ready: the portal reports the real scheme shortly after and we recolor.
        """
        mode = (self.settings.get("appearance_mode", "System") or "System").strip().lower()
        if mode != "system":
            return
        crossfade_during(self, self._apply_appearance)

    def open_settings(self):
        from app.ui.dialogs.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self)
        if dialog.exec():
            self.settings = load_settings()
            # Mask the (unavoidable) full-app restyle behind a frozen snapshot,
            # then crossfade to the new theme so it reads as a smooth dissolve
            # rather than a ~2s freeze.
            crossfade_during(self, self._apply_appearance)
            self._apply_global_hotkey()
            self._reapply_sync()
            show_toast(self, tr("Settings"), tr("Settings saved."), "success")

    def open_log_window(self):
        from app.ui.dialogs.log_window import LogWindow
        existing = self._open_dialogs.get("log")
        try:
            if existing is not None and existing.isVisible():
                existing.raise_()
                return
        except RuntimeError:
            pass  # WA_DeleteOnClose: the C++ widget is already gone
        win = LogWindow(self, follow_app_log=True)
        self._open_dialogs["log"] = win
        win.show()

    # --------------------------------------------------------------- updates

    def _maybe_enable_autostart_default(self):
        """First-run default: register the app to start on login. Runs once —
        the `autostart_configured` flag then keeps a later opt-out from being
        silently undone on the next launch."""
        if get_bool(self.settings, "autostart_configured", False):
            return
        try:
            from app.system.autostart import set_autostart
            set_autostart(True)
        except Exception as exc:
            logging.warning(f"Could not enable autostart on first run: {exc}")
        self.settings["autostart_configured"] = "True"
        save_settings(self.settings)

    def _maybe_check_for_updates(self):
        """Startup check: respect the user's preference and the daily throttle."""
        if self._quitting or not get_bool(self.settings, "auto_check_updates", True):
            return
        from app.core import updater
        if updater.should_check_now():
            self._check_for_updates(manual=False)

    def _check_for_updates(self, manual=False):
        """Run the GitHub release check on a worker thread."""
        from app.core import updater
        run_in_thread(updater.check_for_update,
                      on_result=lambda info: self._on_update_result(info, manual))

    def _on_update_result(self, info, manual):
        from app.core import updater
        updater.record_check()
        if info is None:
            if manual:
                show_toast(self, tr("Updates"), tr("You're up to date."), "success")
            self._set_pending_update(None)
            return
        # On the silent startup path, honour a version the user chose to skip.
        if not manual and info.version == self.settings.get("skipped_version", ""):
            return
        if info.version != self.settings.get("skipped_version", ""):
            self._set_pending_update(info)
        self._show_update_dialog(info)

    def _show_update_dialog(self, info):
        from PySide6.QtCore import QUrl
        from app.ui.dialogs.base import FramelessDialog
        dialog = FramelessDialog(self, title=tr("Update available"))
        dialog.setMinimumWidth(460)
        heading = QLabel(
            tr("Lingueez {version} is available — you have {current}.").format(
                version=info.version, current=APP_VERSION))
        heading.setWordWrap(True)
        dialog.content_layout.addWidget(heading)
        if info.notes:
            from PySide6.QtWidgets import QTextBrowser
            notes = QTextBrowser()
            # GitHub release bodies are Markdown; render them instead of
            # showing raw "## What's Changed" / "* …" source. QTextBrowser
            # opens the changelog/PR links externally when clicked.
            notes.setOpenExternalLinks(True)
            notes.setMarkdown(info.notes)
            notes.setMaximumHeight(220)
            dialog.content_layout.addWidget(notes)

        row = QHBoxLayout()
        skip = QPushButton(tr("Skip this version"))
        skip.setCursor(Qt.PointingHandCursor)

        def _skip():
            self.settings["skipped_version"] = info.version
            save_settings(self.settings)
            self._set_pending_update(None)
            dialog.reject()

        skip.clicked.connect(_skip)
        row.addWidget(skip)
        row.addStretch(1)
        later = QPushButton(tr("Later"))
        later.setCursor(Qt.PointingHandCursor)
        later.clicked.connect(dialog.reject)
        row.addWidget(later)
        download = QPushButton(tr("Download"), objectName="primaryButton")
        download.setCursor(Qt.PointingHandCursor)
        download.setDefault(True)
        download.clicked.connect(lambda: (QDesktopServices.openUrl(QUrl(info.url)),
                                          dialog.accept()))
        row.addWidget(download)
        dialog.content_layout.addLayout(row)
        dialog.exec()

    def show_about(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QPixmap
        from app.core.updater import GITHUB_URL
        from app.ui.dialogs.base import FramelessDialog

        dialog = FramelessDialog(self, title=f"{tr('About')} {APP_NAME}")
        dialog.setMinimumWidth(460)

        # --- Header: logo + name / tagline / version ---
        header = QHBoxLayout()
        header.setSpacing(14)
        logo = QLabel()
        dpr = dialog.devicePixelRatioF()
        pm = QPixmap("assets/icons/icon.png").scaled(
            int(64 * dpr), int(64 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        logo.setPixmap(pm)
        logo.setFixedSize(64, 64)
        header.addWidget(logo, 0, Qt.AlignTop)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        name = QLabel(APP_NAME)
        name_font = name.font()
        name_font.setPointSize(name_font.pointSize() + 5)
        name_font.setWeight(QFont.DemiBold)
        name.setFont(name_font)
        title_box.addWidget(name)
        tagline = QLabel(tr("Your personal vocabulary companion"), objectName="dimLabel")
        title_box.addWidget(tagline)
        version = QLabel(f"{tr('Version')} {APP_VERSION} · {tr('Build')} {BUILD_NUMBER}",
                         objectName="dimLabel")
        title_box.addWidget(version)
        header.addLayout(title_box, 1)
        dialog.content_layout.addLayout(header)

        # --- Description ---
        desc = QLabel(tr("Build, study, and remember vocabulary across languages — with "
                         "cloud sync, AI-assisted definitions, translations, text-to-speech, "
                         "and flexible export."))
        desc.setWordWrap(True)
        dialog.content_layout.addWidget(desc)

        # --- Legal ---
        legal = QLabel(
            "© 2024–2026 Yurii Lysak<br>"
            + tr("Licensed under the GNU Affero General Public License v3.0. "
                 "This attribution must be preserved (AGPL §7)."),
            objectName="dimLabel")
        legal.setWordWrap(True)
        dialog.content_layout.addWidget(legal)

        # --- Links: source code / report an issue ---
        links = QHBoxLayout()
        for label, url in ((tr("Source code"), GITHUB_URL),
                           (tr("Report an issue"), f"{GITHUB_URL}/issues")):
            link = QPushButton(label, objectName="tonalButton")
            link.setCursor(Qt.PointingHandCursor)
            link.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            links.addWidget(link)
        links.addStretch(1)
        dialog.content_layout.addLayout(links)

        # --- Actions: check for updates / OK ---
        row = QHBoxLayout()
        check = QPushButton(tr("Check for updates"))
        check.setCursor(Qt.PointingHandCursor)
        check.clicked.connect(lambda: self._check_for_updates(manual=True))
        row.addWidget(check)
        row.addStretch(1)
        ok = QPushButton(tr("OK"), objectName="primaryButton")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setDefault(True)
        ok.clicked.connect(dialog.accept)
        row.addWidget(ok)
        dialog.content_layout.addLayout(row)
        dialog.exec()
