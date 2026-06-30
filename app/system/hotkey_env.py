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

"""Capability detection for the global Add-Word hotkey.

The hotkey is registered differently per environment (Windows ``keyboard`` lib,
X11 pynput agent, Wayland GlobalShortcuts portal, or a GNOME gsettings keybinding
on pre-portal Wayland). Some environments simply cannot register a global hotkey,
and the right answer is to tell the user *why* and *how to fix it* rather than fail
silently. This module answers one question — :func:`hotkey_capability` — by probing
*capabilities* (is a portal present? are we sandboxed?) rather than sniffing desktop
versions, which is brittle across compositors and derivatives.

Pure logic only: it returns machine-readable reason keys, never user-facing text,
so the UI layer owns localization.
"""
import logging
import os
import sys

# Reason keys returned alongside availability. The UI maps these to localized
# explanations + remedies; keep them stable, they are not user-facing strings.
CAP_OK = "ok"
# Flatpak sandbox on a Wayland session that lacks the GlobalShortcuts portal
# (e.g. GNOME < 48): the portal isn't there and the sandbox can't reach the host's
# gsettings, so there is no mechanism at all. Remedies: X11 session, GNOME 48+/KDE,
# or the (unsandboxed) AppImage.
CAP_WAYLAND_SANDBOXED = "wayland_sandboxed"
# Non-sandboxed Wayland without the portal and not GNOME (so no gsettings path):
# needs an X11 session or a portal-capable desktop.
CAP_WAYLAND_NO_PORTAL = "wayland_no_portal"

# Hidden override so the graceful-degradation UI can be exercised on any session
# (e.g. forcing the Flatpak/Wayland notice while developing on X11):
#   LINGUEEZ_HOTKEY_FORCE=wayland_sandboxed
_FORCE_ENV = "LINGUEEZ_HOTKEY_FORCE"

_portal_cache = None


def is_wayland():
    return (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY")))


def is_flatpak():
    """True when running inside a Flatpak sandbox."""
    return bool(os.environ.get("FLATPAK_ID")) or os.path.exists("/.flatpak-info")


def desktop_is_gnome():
    return "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def global_shortcuts_portal_available():
    """Whether the desktop exposes org.freedesktop.portal.GlobalShortcuts.

    False on GNOME < 48 (the portal lands in GNOME 48); True on KDE Plasma and
    GNOME 48+. Cached — the answer can't change within a session. Uses QtDBus so it
    works regardless of session-bus tooling, and inside the Flatpak sandbox (where
    the portal is reachable when present)."""
    global _portal_cache
    if _portal_cache is not None:
        return _portal_cache
    available = False
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface
        iface = QDBusInterface("org.freedesktop.portal.Desktop",
                               "/org/freedesktop/portal/desktop",
                               "org.freedesktop.DBus.Properties",
                               QDBusConnection.sessionBus())
        reply = iface.call("Get", "org.freedesktop.portal.GlobalShortcuts", "version")
        available = reply.errorName() == ""  # no error => interface present
    except Exception as exc:
        logging.debug(f"GlobalShortcuts portal probe failed: {exc}")
    _portal_cache = available
    return available


def hotkey_capability():
    """Return ``(available: bool, reason: str)`` for the current environment.

    ``available`` is whether a global hotkey can actually be registered now;
    ``reason`` is a ``CAP_*`` key the UI maps to a localized explanation + remedy
    when it can't. Never raises.
    """
    forced = os.environ.get(_FORCE_ENV, "").strip()
    if forced:
        return forced == CAP_OK, forced

    if sys.platform == "win32":
        return True, CAP_OK
    if not is_wayland():
        return True, CAP_OK                      # X11: pynput agent
    # NOTE: the GlobalShortcuts portal would serve GNOME 48+/KDE here, but
    # _apply_portal_hotkey (main_window) is still a stub. Until it's implemented we
    # must NOT report the portal as a working mechanism — otherwise portal-capable
    # desktops (e.g. KDE Plasma Wayland) would claim the hotkey works when it can't
    # register. Re-add a global_shortcuts_portal_available() check once that lands.
    if is_flatpak():
        return False, CAP_WAYLAND_SANDBOXED      # sandbox can't reach host gsettings
    if desktop_is_gnome():
        return True, CAP_OK                      # native/AppImage: gsettings keybinding
    return False, CAP_WAYLAND_NO_PORTAL          # non-GNOME Wayland: no usable path
