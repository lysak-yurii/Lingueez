# Flatpak packaging (build-from-source)

This directory contains the Flatpak manifest and metadata for Lingueez, targeting
Flathub. It builds the app **from source** (the Flathub-preferred way).

## Files

- `app.lingueez.Lingueez.yml` — the manifest (modules, runtime, permissions).
- `app.lingueez.Lingueez.desktop` — desktop entry.
- `app.lingueez.Lingueez.metainfo.xml` — AppStream metadata (required by Flathub).
- `lingueez.sh` — in-sandbox launcher (`flatpak run` → this → `main.py`).
- `python3-deps.json` — **committed**; pinned Python deps (everything *except*
  PySide6, which comes from the base app).

The runtime is `org.kde.Platform//6.8` (Python 3.12) plus the
`io.qt.PySide.BaseApp//6.8`, which provides PySide6/shiboken6 — `flatpak-pip-generator`
explicitly refuses to vendor PySide6 and points to this base app.

## One-time tooling

```bash
sudo apt install flatpak flatpak-builder
flatpak remote-add --if-not-exists --user flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.kde.Platform//6.8 org.kde.Sdk//6.8 io.qt.PySide.BaseApp//6.8
```

## 1. Python dependencies (committed)

`python3-deps.json` is committed, so a normal build needs no generation step. Only
regenerate it when `requirements.txt` changes — and exclude PySide6, which the base
app provides:

```bash
curl -L -o flatpak-pip-generator.py \
  https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator.py
grep -v PySide6 ../../requirements.txt > /tmp/reqs.txt
python3 flatpak-pip-generator.py --requirements-file=/tmp/reqs.txt --output python3-deps
```

Run it with **Python 3.12** (matching `org.kde.Platform//6.8`) so the pinned wheels
are the right ABI.

## 2. ffmpeg (already pinned)

The `ffmpeg-static` module is pinned to a dated, immutable BtbN static build
(`autobuild-*`) with a real `sha256`, so it's reproducible — pydub finds the
`ffmpeg`/`ffprobe` binaries at `/app/bin` (on PATH) for read-aloud/audio export.
To refresh: pick a newer `autobuild-*` release, download its `linux64-gpl` asset,
`sha256sum` it, and update the `url` + `sha256` in the manifest. (For a stricter
Flathub submission you may later prefer a `shared-modules` source-built ffmpeg.)

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

The branch CI workflow (`.github/workflows/flatpak.yml`) does steps 1, 3 and 4 and
uploads the `.flatpak` bundle as an artifact.

## Notes / known iteration points

- **PySide6 vendoring** via flatpak-pip-generator is the most likely thing to need
  tuning on the first build.
- **ffmpeg** sha256 must be pinned (see step 2).
- **Global hotkey on Wayland** needs the GlobalShortcuts portal (GNOME 48+/KDE); on
  pre-48 GNOME the app shows a graceful in-app notice (see `app/system/hotkey_env.py`).
- For the actual **Flathub submission**, commit `python3-deps.json`, point the
  `lingueez` module's source at a tagged release archive instead of `type: dir`, and
  open a PR to `flathub/flathub`.
