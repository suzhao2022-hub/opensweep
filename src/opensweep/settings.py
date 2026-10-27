"""User settings stored as JSON in the config folder."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from opensweep.core.platform import config_dir


@dataclass
class Settings:
    language: str = ""            # empty = follow the system
    use_recycle_bin: bool = True  # for "review" items
    check_updates: bool = False   # look for a new version at start (the only automatic network access)
    theme: str = "fresh"          # fresh | tech | fluent
    guard: bool = True            # keep switched-off startup apps switched off (core/guard.py)
    onboarded: bool = False       # the first-run welcome has been shown

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads((config_dir() / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self) -> None:
        try:
            (config_dir() / "settings.json").write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            pass
