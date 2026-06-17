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

"""OS autostart entry management (Linux .desktop / Windows registry).

Uses its own app name so it never clashes with the legacy app's entry.
"""
import logging
import os
import sys

_APP_NAME = "Lingueez"
_DISPLAY_NAME = "Lingueez"


def _get_app_command_and_workdir():
    if getattr(sys, 'frozen', False):
        exe = os.path.abspath(sys.executable)
        return exe, os.path.dirname(exe)
    app_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
    run_sh = os.path.join(app_dir, 'run.sh')
    if sys.platform != 'win32' and os.path.isfile(run_sh):
        return run_sh, app_dir
    return f"{sys.executable} {os.path.join(app_dir, 'main.py')}", app_dir


def set_autostart(enabled: bool):
    if sys.platform == 'win32':
        _set_autostart_windows(enabled)
    else:
        _set_autostart_linux(enabled)


def get_autostart_enabled() -> bool:
    if sys.platform == 'win32':
        return _get_autostart_windows()
    return _get_autostart_linux()


def sync_autostart_path():
    """Repair a stale autostart entry whose recorded executable path no longer
    matches the running one — e.g. after the user updates a Linux AppImage by
    saving it under a new filename, leaving the old `Exec=` pointing nowhere.

    No-op when autostart is disabled or the path already matches. Rewrites the
    entry (registry value / .desktop) with the current command otherwise.
    """
    if not get_autostart_enabled():
        return
    exec_cmd, _ = _get_app_command_and_workdir()
    expected = f"{exec_cmd} --minimized"
    try:
        if _current_autostart_command() != expected:
            logging.info("Refreshing stale autostart entry to current executable path.")
            set_autostart(True)
    except Exception as exc:
        logging.warning(f"Could not refresh autostart path: {exc}")


def _current_autostart_command():
    """The command string stored in the existing autostart entry, or None."""
    if sys.platform == 'win32':
        return _read_autostart_command_windows()
    return _read_autostart_command_linux()


def _read_autostart_command_linux():
    path = os.path.expanduser(f"~/.config/autostart/{_APP_NAME}.desktop")
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith("Exec="):
                    return line[len("Exec="):].strip()
    except OSError:
        pass
    return None


def _read_autostart_command_windows():
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        try:
            value, _ = winreg.QueryValueEx(key, _APP_NAME)
            return value
        except FileNotFoundError:
            return None
        finally:
            winreg.CloseKey(key)
    except Exception:
        return None


def _set_autostart_linux(enabled: bool):
    autostart_dir = os.path.expanduser("~/.config/autostart")
    desktop_file = os.path.join(autostart_dir, f"{_APP_NAME}.desktop")
    if enabled:
        os.makedirs(autostart_dir, exist_ok=True)
        exec_cmd, workdir = _get_app_command_and_workdir()
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={_DISPLAY_NAME}\n"
            f"Exec={exec_cmd} --minimized\n"
            f"Path={workdir}\n"
            "Hidden=false\n"
            "NoDisplay=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        with open(desktop_file, "w") as fh:
            fh.write(content)
    elif os.path.exists(desktop_file):
        os.remove(desktop_file)


def _get_autostart_linux() -> bool:
    return os.path.exists(os.path.expanduser(f"~/.config/autostart/{_APP_NAME}.desktop"))


def _set_autostart_windows(enabled: bool):
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        if enabled:
            exec_cmd, _ = _get_app_command_and_workdir()
            winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, f"{exec_cmd} --minimized")
        else:
            try:
                winreg.DeleteValue(key, _APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as exc:
        logging.error(f"Failed to set autostart registry key: {exc}")


def _get_autostart_windows() -> bool:
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, _APP_NAME)
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False
