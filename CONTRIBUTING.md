# Contributing to OpenSweep

Thanks for helping! The ground rules come from what OpenSweep promises its users:

1. **Never delete what the user can't get back** unless they clearly chose it. When in doubt, a rule is `review`, not `safe`.
2. **Explain in plain words.** Every rule, advice and folder note is read by people who don't know what a cache is.
3. **No dark patterns.** No fake "speed boosts", no RAM cleaners, no registry cleaners, no scare tactics, no network
   access except the user-initiated update check.

## Easy contributions

- **Report a misjudgment** – click *Is this wrong?* under any item in the app.
- **Add or fix a cleanup rule, startup advice or folder note** – see [docs/RULES.md](docs/RULES.md).
- **Improve a translation** – `src/opensweep/locales/<code>.json`. Only English and Simplified Chinese were written by
  native speakers so far; reviews of the other languages are very welcome.

## Code

```bash
pip install -e ".[dev]" pyflakes
python -m pytest
python -m pyflakes src tests
```

- `src/opensweep/core` – everything that reads or changes the system (no Qt). Every deletion goes through
  `core/safety.py`; don't bypass it.
- `src/opensweep/gui` – PySide6 interface. Long work runs in `gui/workers.run_task`, never on the UI thread.
- Tests must not touch real user data: use `tmp_path` and monkeypatch system queries.

## Releases

Bump `__version__` in `src/opensweep/__init__.py`, update `CHANGELOG.md`, push a tag `vX.Y.Z`. The *Release* workflow
builds the installer and portable zip and drafts a GitHub release. Code signing: [docs/SIGNING.md](docs/SIGNING.md).
