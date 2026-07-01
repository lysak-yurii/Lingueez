# Flatpak packaging (build-from-source)

This directory contains the Flatpak manifest and metadata for Lingueez, targeting
Flathub. It builds the app **from source** (the Flathub-preferred way).

## Files

- `app.lingueez.Lingueez.yml` — the manifest (modules, runtime, permissions).
- `app.lingueez.Lingueez.desktop` — desktop entry.
- `app.lingueez.Lingueez.metainfo.xml` — AppStream metadata (required by Flathub).
- `lingueez.sh` — in-sandbox launcher (`flatpak run` → this → `main.py`).
- `python3-deps.json` — **committed**, offline-vendored Python deps (pinned wheels).

The runtime is `org.kde.Platform//6.10` (Python 3.13) plus the
`io.qt.PySide.BaseApp//6.10`, which provides PySide6/shiboken6 — `flatpak-pip-generator`
explicitly refuses to vendor PySide6 and points to this base app.

## One-time tooling

```bash
sudo apt install flatpak flatpak-builder
flatpak remote-add --if-not-exists --user flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.kde.Platform//6.10 org.kde.Sdk//6.10 io.qt.PySide.BaseApp//6.10
```

## 1. Python dependencies (offline-vendored — Flathub-compliant)

`python3-deps.json` pins every dep (except PySide6) as an **offline source**, so the
build needs no network. Native/Rust packages (numpy, pandas, grpcio, jiter,
pydantic-core, …) are pinned as prebuilt **cp313 manylinux wheels** (`cryptography` is
`cp311-abi3` — stable-ABI, so 3.13-compatible) to match the runtime's Python 3.13; only
`sgmllib3k` (pure Python) builds from sdist. Every install command carries
`--ignore-installed` so the pinned numpy overrides the older one the base app ships —
otherwise pandas segfaults (`pandas.date_range`, Stats page).

To regenerate after a `requirements.txt` change:

```bash
curl -L -o flatpak-pip-generator.py \
  https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator.py
grep -ivE '^\s*pyside6' ../../requirements.txt > /tmp/reqs.txt
python3 flatpak-pip-generator.py --requirements-file=/tmp/reqs.txt --output python3-deps
```

That defaults to sdists for native packages; convert each native/Rust sdist to its
cp313 (or abi3) x86_64 manylinux wheel from PyPI, and add `--ignore-installed` to
every install command. (Alternatively, generate with `--runtime=org.kde.Platform//6.10
--artifact-policy=platform`, which selects platform wheels directly but needs flatpak
+ the runtime installed locally.) x86_64 only for now — add aarch64 wheels for a
multi-arch Flathub build.

## 2. ffmpeg (not bundled)

ffmpeg/ffprobe are **not vendored** — `org.kde.Platform` already ships
`/usr/bin/ffmpeg` 7.x with mp3 decode + `libmp3lame` encode (plus aac/flac/opus/
vorbis). pydub finds it on PATH, so read-aloud and audio export work with no blob.

## 3. Build & install locally

```bash
flatpak-builder --user --install --force-clean build-dir app.lingueez.Lingueez.yml
flatpak run app.lingueez.Lingueez
```

## 4. Produce a single-file bundle (for sharing / branch testing)

```bash
flatpak-builder --repo=repo --force-clean build-dir app.lingueez.Lingueez.yml
flatpak build-bundle repo lingueez.flatpak app.lingueez.Lingueez
# install elsewhere: flatpak install --user ./lingueez.flatpak
```

The `.github/workflows/test-build.yml` workflow builds the bundle (and the Windows +
AppImage artifacts) on the `flatpak-support` branch.

## Notes / remaining Flathub tasks

- **Global hotkey on Wayland** needs the GlobalShortcuts portal (GNOME 48+/KDE); on
  pre-48 GNOME the app shows a graceful in-app notice (see `app/system/hotkey_env.py`).
- **Runtime**: on `org.kde.Platform//6.10` (Python 3.13, latest). `python3-deps.json`
  is pinned to cp313 wheels to match; regenerate if the runtime's Python version bumps.
- **metainfo**: replace the placeholder screenshot URL with a real one and validate.
- For the actual **Flathub submission**, point the `lingueez` module's source at a
  tagged release archive instead of `type: dir`, and open a PR to `flathub/flathub`.
