# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build definition for Lingueez. Runs natively on each CI runner,
# so `sys.platform` here reflects the OS being built for.
#
# Layout: a **onedir** build with `contents_directory='.'`, i.e. the executable
# and its data (assets/, fonts/, locales/, ffmpeg/) sit side by side — matching
# the app's path logic, which chdir's to `dirname(sys.executable)` and reads
# *and writes* every file relative to it (dictionary.db, settings.cfg, backups/,
# .env, …). Run the extracted folder from a writable location.
#
# Build:  pyinstaller lingueez.spec
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None


def collect_dir(folder, includes=None):
    """Return (src, dest_dir) tuples for every file under *folder*, preserving
    the tree. *includes*, if given, keeps only basenames in that set."""
    items = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if includes is not None and name not in includes:
                continue
            src = os.path.join(root, name)
            items.append((src, root))  # dest mirrors the relative tree
    return items


datas = []
datas += collect_dir("assets")
datas += collect_dir("fonts")
# Ship the locale modules as real .py files beside the exe (contents_directory
# is '.'), not only inside the PYZ. main.py adds the bundle dir to sys.path, so
# importlib resolves locales.* from disk, and i18n._available_locales()'s
# os.listdir() can actually see them in the frozen build. Picks up every
# locales/*.py automatically, so future languages need no spec change.
datas += [(src, dest) for src, dest in collect_dir("locales")
          if src.endswith(".py")]
# Qt's own Ukrainian translation for the standard dialog buttons (OK/Cancel/…),
# loaded at runtime via QLibraryInfo(TranslationsPath) in main.py.
datas += collect_data_files("PySide6", includes=["Qt/translations/qtbase_uk.qm"])
# License/attribution must travel with the binary (AGPL §7 + ffmpeg).
datas += [("NOTICE", "."), ("THIRD-PARTY-LICENSES.md", "."), ("LICENSE.txt", ".")]

# ffmpeg: bundle ONLY the current OS's binaries into ffmpeg/bin/, the relative
# path read_ffmpeg_path() (app/core/shell_utils.py) looks for. Drop the other
# platform's binaries, ffplay, and the docs/presets — pydub only runs ffmpeg
# and ffprobe.
if sys.platform == "win32":
    ff = {"ffmpeg.exe", "ffprobe.exe"}
else:
    ff = {"ffmpeg", "ffprobe"}
datas += [(os.path.join("ffmpeg", "bin", n), os.path.join("ffmpeg", "bin"))
          for n in ff if os.path.isfile(os.path.join("ffmpeg", "bin", n))]
if os.path.isfile(os.path.join("ffmpeg", "LICENSE")):
    datas += [(os.path.join("ffmpeg", "LICENSE"), "ffmpeg")]

# Packages PyInstaller's static analysis tends to under-collect.
hiddenimports = [
    "supabase", "gotrue", "postgrest", "realtime", "storage3",
    "google.genai", "openai", "feedparser", "trafilatura",
    "pydub", "gtts", "google.cloud.texttospeech",
]
# i18n loads locale modules dynamically (importlib.import_module("locales.uk")),
# which static analysis can't see — collect them explicitly so the Ukrainian
# translation is actually bundled instead of silently falling back to English.
hiddenimports += collect_submodules("locales")

icon = os.path.join("assets", "icons",
                    "icon.ico" if sys.platform == "win32" else "icon.png")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Lingueez",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # GUI app: no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Lingueez",
    contents_directory=".",   # data beside the exe, matching the app's path logic
)
