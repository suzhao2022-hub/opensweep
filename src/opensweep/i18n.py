"""Translations. Strings live in locales/<code>.json; English is the fallback.

To add a language, copy locales/en.json, translate the values and add the
language's own name under "_language". No code changes needed.
"""

from __future__ import annotations

import json
import locale
from functools import lru_cache

from opensweep.core.platform import resource_path

_current = "en"


@lru_cache(maxsize=None)
def _load(code: str) -> dict[str, str]:
    try:
        return json.loads(resource_path("locales", f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def available() -> dict[str, str]:
    """language code -> language name in that language."""
    langs = {}
    for path in sorted(resource_path("locales").glob("*.json")):
        langs[path.stem] = _load(path.stem).get("_language", path.stem)
    return langs


def detect() -> str:
    codes = available()
    try:
        from PySide6.QtCore import QLocale
        names = [QLocale.system().name()] + list(QLocale.system().uiLanguages())
    except ImportError:
        names = [locale.getlocale()[0] or "en"]
    for name in names:
        name = name.replace("-", "_")
        if name in codes:
            return name
        if name.startswith("zh"):
            traditional = any(t in name for t in ("TW", "HK", "MO", "Hant"))
            return "zh_TW" if traditional and "zh_TW" in codes else "zh_CN"
        base = name.split("_")[0]
        for code in codes:
            if code.split("_")[0] == base:
                return code
    return "en"


def set_language(code: str) -> None:
    global _current
    _current = code if code in available() else "en"


def current() -> str:
    return _current


def t(key: str, **kwargs) -> str:
    text = _load(_current).get(key) or _load("en").get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
