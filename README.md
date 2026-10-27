# OpenSweep

**A free, open-source PC cleaner that tells you, in plain words, what it's about to delete and why.**

[简体中文](README.zh-CN.md)

No ads, no "upgrade to Pro", no telemetry. OpenSweep only connects to the internet when you click
*Check for updates* (or turn on the automatic check in Settings).

## What it does

| | |
|---|---|
| **PC checkup** | One click checks junk files, startup apps, unused programs, disk space and memory, gives a score, explains each finding and fixes the recommended ones. Afterwards it shows what got better – and what's left and why. |
| **Junk files** | Temp files, update leftovers, browser and app caches. Every item says what it is, whether it's safe, and shows exactly which files are inside before you delete anything. |
| **AI models & caches** | Hugging Face, Ollama, LM Studio, ModelScope, PyTorch, Whisper models listed one by one, plus Cursor/Claude/Trae caches and developer caches (pip, npm, uv, Gradle…). Ollama models are removed without breaking layers other models share. |
| **Disk map** | A treemap of what fills your drive, coloured by *can I delete this?*, with a plain-language note on well-known folders (pagefile.sys, WinSxS, chat history…). Reads the NTFS Master File Table directly when run as administrator – much faster on hard disks. |
| **Programs** | Installed programs with an estimate of when you last used them, uninstall plus leftover cleanup, and **programs Windows doesn't know about** (unzipped "portable" apps, folders left after an uninstall). |
| **Startup apps** | Run keys, Startup folders, scheduled tasks and third-party services, each with a recommendation (turn off / your choice / keep) and why. Changes are reversible. |
| **Memory** | Which apps use your RAM, which ones only run in the background, and why there's deliberately no "free RAM" button. |

10 languages: English, 简体中文, 繁體中文, 日本語, 한국어, Español, Français, Deutsch, Русский, Português (Brasil).

## Safety

- Deletions go through a hard-coded list of protected folders (Windows, Program Files, your Documents/Desktop/Pictures…) that no rule can override.
- Symlinks and junctions are never followed, so nothing outside the chosen folder can be deleted.
- Files in use are skipped, not forced. Items marked *Review* go to the Recycle Bin by default.
- Every cleanup is logged in `%APPDATA%\OpenSweep\logs`.
- OpenSweep never touches documents, photos, videos, downloads, chat history, game saves, passwords or browser logins.

## Install

Download `OpenSweep-x.y.z-setup.exe` (installer) or the portable zip from [Releases](https://github.com/opensweep/opensweep/releases).
Windows 10/11, 64-bit.

Run it as administrator to include Windows' own junk (update cache, system temp) and to use the fast disk map.

## Run from source

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
python -m opensweep            # the app
python -m opensweep scan       # command line: what can be cleaned
python -m pytest
```

## Contributing

The most useful contribution needs no programming: **cleanup rules, startup advice and folder explanations are JSON files**.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/RULES.md](docs/RULES.md).
Saw OpenSweep say something wrong? Use the *Is this wrong?* link under any item – it opens a pre-filled issue.

## License

[MIT](LICENSE)
