# Third-Party Licenses

Lingueez is built on open-source components, each distributed under its own
license. This file acknowledges them. The list reflects the project's declared
dependencies; for the exact terms and versions, consult each package directly
(`pip show <package>` and the project's homepage).

All licenses below are compatible with the project's GNU Affero General Public
License v3.0.

## Python dependencies

| Component | Purpose | License |
| --- | --- | --- |
| PySide6 (Qt for Python) | Graphical interface | LGPL-3.0 |
| openai | ChatGPT integration | Apache-2.0 |
| google-genai | Gemini integration | Apache-2.0 |
| google-cloud-texttospeech | Premium text-to-speech | Apache-2.0 |
| requests | HTTP client | Apache-2.0 |
| pandas | Data processing | BSD-3-Clause |
| numpy | Numerical computing | BSD-3-Clause |
| openpyxl | Excel import/export | MIT |
| reportlab | PDF export | BSD-3-Clause |
| trafilatura | Web article extraction | Apache-2.0 |
| feedparser | RSS parsing | BSD-2-Clause |
| supabase | Cloud sync client | MIT |
| python-dotenv | Environment configuration | BSD-3-Clause |
| keyring | Secure credential storage (OS keychain) | MIT |
| cryptography | Encrypts the local session fallback | Apache-2.0 / BSD-3-Clause |
| keyboard | Global hotkey (Windows) | MIT |
| pynput | Global hotkey (macOS/Linux) | LGPL-3.0 |
| pygame | Audio playback | LGPL-2.1 |
| gtts | Free text-to-speech | MIT |
| pydub | Audio processing | MIT |

## Bundled tools

| Component | Purpose | License |
| --- | --- | --- |
| FFmpeg | MP3 export (optional, used via pydub) | LGPL-2.1 / GPL-2.0+ depending on build |

## Notes

- **LGPL components** (PySide6, pynput, pygame, FFmpeg) are used as separate,
  replaceable libraries and are not statically modified.
- License identifiers follow the [SPDX](https://spdx.org/licenses/) convention.
- If a dependency's license differs for the version you install, the version you
  install governs.
