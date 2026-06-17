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

"""Lingueez — modern edition. Entry point."""
import logging
import os
import sys


def _user_data_dir(app_id):
    """OS-standard per-user, writable data directory for the app."""
    if sys.platform == 'win32':
        root = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        root = os.path.expanduser('~/Library/Application Support')
    else:
        root = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return os.path.join(root, app_id)


def _setup_paths():
    """Establish the working directory so relative data files resolve.

    Dev: run from the project directory (unchanged).

    Frozen: read-only resources (assets/, fonts/, ffmpeg/) are bundled with the
    executable, but user data (dictionary.db, settings.cfg, backups/, .env, logs)
    must live in a writable per-user directory — the bundle dir can be read-only
    (AppImage) or a temporary extract (one-file builds). So seed the bundled
    resources into that data dir on first run / version change, then chdir there
    so every relative path the app uses resolves correctly.
    """
    if not getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(__file__))
        os.chdir(base)
        if base not in sys.path:
            sys.path.insert(0, base)
        return

    import shutil
    from app.version import APP_ID, BUILD_NUMBER

    bundle = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    data_dir = _user_data_dir(APP_ID)
    os.makedirs(data_dir, exist_ok=True)

    # Seed/refresh the read-only resources whenever the bundled build changes.
    marker = os.path.join(data_dir, '.bundle_version')
    try:
        seeded = open(marker, encoding='utf-8').read().strip()
    except OSError:
        seeded = ''
    if seeded != BUILD_NUMBER:
        for name in ('assets', 'fonts', 'ffmpeg'):
            src = os.path.join(bundle, name)
            if os.path.isdir(src):
                # dirs_exist_ok merges, so user-written files (assets/generated)
                # survive while shipped resources are refreshed.
                shutil.copytree(src, os.path.join(data_dir, name), dirs_exist_ok=True)
        try:
            with open(marker, 'w', encoding='utf-8') as fh:
                fh.write(BUILD_NUMBER)
        except OSError:
            pass

    os.chdir(data_dir)
    if bundle not in sys.path:        # locales are imported from the bundle (PYZ)
        sys.path.insert(0, bundle)


def _detect_os_language(available):
    """Pick the UI language to start in based on the OS's preferred languages.

    Walks QLocale's ordered ui-language list (e.g. ['uk-UA', 'uk', 'en-US']),
    reduces each tag to its base code ('uk') and returns the first that the app
    ships a locale for. Falls back to 'en' when nothing matches.
    """
    from PySide6.QtCore import QLocale
    codes = set(available)
    for tag in QLocale.system().uiLanguages():
        base = tag.replace('-', '_').split('_')[0].lower()
        if base in codes:
            return base
    return "en"


def _resolve_startup_language(settings):
    """Return the language to launch in, running first-run OS detection once.

    If a language has already been resolved (``language_configured``), respect
    the stored choice. Otherwise detect from the OS, persist both the choice and
    the flag, and never auto-detect again — so a later manual change always wins.
    """
    from app.config import get_bool, save_settings
    from app.i18n import available_languages

    if get_bool(settings, "language_configured", False):
        return settings.get("language", "en")

    detected = _detect_os_language([code for code, _label in available_languages()])
    settings["language"] = detected
    settings["language_configured"] = "True"
    try:
        save_settings(settings)
    except Exception as exc:
        logging.error(f"Could not persist detected language: {exc}")
    logging.info(f"First-run language detection selected '{detected}'.")
    return detected


def _setup_logging():
    logging.basicConfig(
        filename='app.log',
        filemode='a',
        format='%(asctime)s - %(levelname)s - %(message)s',
        level=logging.INFO,
    )
    # Native-crash stack traces land in crash.log (segfaults bypass app.log)
    import faulthandler
    global _crash_log
    _crash_log = open('crash.log', 'a')
    faulthandler.enable(file=_crash_log)


def main():
    _setup_paths()
    _setup_logging()

    from app.version import APP_ID, APP_NAME, APP_VERSION
    logging.info("The application is launched.")
    logging.info(f"Application version: {APP_VERSION}")

    from PySide6.QtCore import QLockFile, QStandardPaths, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    start_hidden = "--minimized" in sys.argv

    app = QApplication(sys.argv)
    # applicationName becomes the X11 WM_CLASS — must match StartupWMClass
    # in the installed .desktop file for proper dock/taskbar association
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(QIcon("assets/icons/icon.png"))
    app.setQuitOnLastWindowClosed(False)  # we live in the tray

    # Single instance guard. Keep the lock in a stable, writable runtime dir
    # (not os.getcwd(), which varies by how the app was launched) so every
    # launch contends for the same lock. A non-zero stale time lets a crashed
    # instance's lock be reclaimed instead of blocking startup forever.
    lock_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
    os.makedirs(lock_dir, exist_ok=True)
    lock = QLockFile(os.path.join(lock_dir, f"{APP_ID}.lock"))
    lock.setStaleLockTime(30000)
    # On a self-restart (e.g. after a language change) the outgoing instance is
    # still shutting down and holding the lock, so wait for it to free up rather
    # than refusing to start.
    lock_wait = 8000 if "--relaunch" in sys.argv else 100
    if not lock.tryLock(lock_wait):
        QMessageBox.warning(None, APP_NAME, f"{APP_NAME} is already running.")
        return 0

    from app.config import get_float, load_settings
    from app.core.backup_management import manage_backups
    from app.core.db import initialize_database
    from app.i18n import set_language
    from app.ui import theme

    settings = load_settings()
    # Must run before importing any UI module: some modules resolve tr() into
    # module-level constants at import time, so the language has to be set first.
    # On the very first run this also detects the language from the OS.
    language = _resolve_startup_language(settings)
    set_language(language)

    # Standard dialog buttons (Yes/No/OK/Cancel…) are drawn by Qt, not our code,
    # so tr() never sees them. Install Qt's own bundled translation to localize
    # them; parented to *app* so it outlives this scope.
    if language != "en":
        from PySide6.QtCore import QLibraryInfo, QTranslator
        qt_translator = QTranslator(app)
        if qt_translator.load(f"qtbase_{language}",
                              QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(qt_translator)
        else:
            logging.warning(f"No Qt translation bundled for language '{language}'.")

    from app.ui.main_window import MainWindow

    os.makedirs('backups', exist_ok=True)
    try:
        manage_backups('backups')
    except Exception as exc:
        logging.error(f"Backup management failed: {exc}")

    initialize_database()

    theme.apply_theme(app,
                      settings.get("appearance_mode", "System"),
                      get_float(settings, "widget_scaling", 1.0))

    window = MainWindow(settings, start_hidden=start_hidden)
    if not start_hidden:
        window.show()

    rc = app.exec()

    # Give background workers (sync, TTS) a moment to wind down cleanly
    from PySide6.QtCore import QThreadPool
    QThreadPool.globalInstance().waitForDone(3000)

    lock.unlock()
    return rc


if __name__ == "__main__":
    sys.exit(main())
