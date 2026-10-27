# Changelog

## 1.0.1 – startup reliability fix (2026-10-25)

- Back up Run values and Startup-folder files outside launch locations when disabling; restore originals when enabled.
- Disable services instead of merely making them Manual; preserve and restore prior mode and delayed start.
- Retain disabled services in the list, upgrade guarded legacy flag-only entries, and verify changes.
- Read task Settings.Enabled even when a disabled task is still running.
- Repair the startup page header and add translated startup diagnostics in all ten languages.
- Add a read-only JSON report with version, timestamps, process/parent details, startup settings and guard history.
- Treat path matches as candidates rather than proven launch sources; include processes started after delayed logins.
- Exclude conflicting third-party ICU DLLs captured from the build PATH; verify frozen GUI and CLI entry points.
- List and switch off more launch sources: RunOnce, policy Run keys, Store app startup tasks and Windows' "reopen apps after sign-in" setting.
- Report Winlogon, logon-script, AppInit and Active Setup launch points in the diagnostic (read-only).
- Disk map: unfamiliar folders and program files are now explained from what Windows knows (owning installed or Store program, program version info, what the folder mostly holds); node_modules, .git, venv and similar folders get notes.
- Startup page: multi-select, turn off/on selected, and one-click turn off all unneeded entries.
- Home: a "make my PC faster in one click" button (check, show the plan, ask once); an Undo bar after startup changes.
- Disk map: a "start here" list of the biggest safe-to-clean folders.
- Cleanup result says how many phone photos' worth of space was freed and links to the Recycle Bin.
- First-run welcome (three steps and the startup-guard choice); plainer wording in settings and the startup guard.
- Fix stale numbers: the startup list now shows live On/Off status; startup, memory, home, software and disk pages refresh each other after changes; deleting in the disk map updates totals, types and largest files; the checkup re-reads free space and memory.
- Add regressions for backup failure, reversible changes, multiple launch sources, stale PIDs and GUI construction.

## 1.0.0 – first public beta

- PC checkup with score, plain-language findings, one-click recommended fixes and a before/after result.
- Junk files with a detail panel showing exactly what's inside every item; confirmation and result lists.
- AI models & caches: Hugging Face, Ollama (shared layers kept), LM Studio, ModelScope, PyTorch, Whisper, Keras,
  EasyOCR/InsightFace, Claude/Cursor/Windsurf/Trae caches.
- Caches of common apps, including WeChat, QQ logs, NetEase Cloud Music and any Chromium/Electron app.
- Disk map: treemap coloured by "can I delete this?", explanations for well-known folders, largest files, file types;
  reads the NTFS MFT directly when run as administrator.
- Programs: last-used estimates, uninstall with leftover cleanup, unlisted portable programs and leftover folders.
- Startup: Run keys, Startup folders, scheduled tasks and third-party services with advice; broken entries removed safely.
- Memory: apps vs background programs vs Windows, CPU, and turning off startup from the same place.
- "Is this wrong?" feedback link on every item; optional update check.
- 10 languages.
