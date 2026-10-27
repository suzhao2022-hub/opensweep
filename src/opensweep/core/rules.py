"""Cleanup rules: JSON data files that describe what can be cleaned and how.

Rules are deliberately data, not code, so that anyone can add support for a new
app (an AI tool, a game launcher...) by sending a small JSON file. See
docs/RULES.md for the format.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from opensweep.core.platform import platform_key, resource_path, user_rules_dir

CATEGORIES = ("system", "browser", "ai", "dev", "apps")
RISKS = ("safe", "review")
ACTIONS = ("delete", "report")
PROVIDERS = (None, "recycle_bin", "ollama", "chromium")
LABEL_STYLES = (None, "huggingface")


@dataclass
class Rule:
    id: str
    category: str
    name: dict[str, str]
    description: dict[str, str] = field(default_factory=dict)
    paths: dict[str, list[str]] = field(default_factory=dict)
    risk: str = "safe"
    action: str = "delete"
    provider: str | None = None
    itemize: int = 0
    item_patterns: list[str] = field(default_factory=list)
    label_style: str | None = None
    contents_only: bool = True
    min_age_days: int = 0
    requires_admin: bool = False
    close_apps: list[str] = field(default_factory=list)
    fallback: bool = False  # only takes what no other rule already covers
    source: str = ""

    def text(self, attr: str, lang: str) -> str:
        values: dict[str, str] = getattr(self, attr)
        return localized(values, lang)

    def resolve_paths(self) -> list[str]:
        """Expand env vars, ~ and wildcards for the current platform; drop missing ones."""
        found: list[str] = []
        seen: set[str] = set()
        for raw in self.paths.get(platform_key(), []):
            expanded = expand(raw)
            if expanded is None:
                continue
            matches = glob.glob(expanded) if any(c in expanded for c in "*?") else [expanded]
            for match in sorted(matches):
                if not os.path.lexists(match):
                    continue
                key = os.path.normcase(os.path.realpath(match))
                if key not in seen:
                    seen.add(key)
                    found.append(os.path.abspath(match))
        return found


def localized(values: dict[str, str], lang: str) -> str:
    if not values:
        return ""
    for key in (lang, lang.replace("_", "-"), lang.replace("-", "_"), lang.split("_")[0].split("-")[0], "en"):
        if key in values:
            return values[key]
    return next(iter(values.values()))


def expand(raw: str) -> str | None:
    """Expand %VAR%, $VAR and ~. Returns None if a variable is not set."""
    value = os.path.expanduser(os.path.expandvars(raw))
    if "%" in value or "$" in value:
        return None
    return os.path.normpath(value)


class RuleError(ValueError):
    pass


def validate(data: dict, source: str = "") -> list[str]:
    """Return a list of problems with a raw rule dict (empty when valid)."""
    errors = []
    where = f"{source}:{data.get('id', '?')}"
    for key in ("id", "category", "name", "paths"):
        if key not in data:
            errors.append(f"{where}: missing '{key}'")
    if errors:
        return errors
    if data["category"] not in CATEGORIES:
        errors.append(f"{where}: bad category {data['category']!r}")
    if data.get("risk", "safe") not in RISKS:
        errors.append(f"{where}: bad risk {data.get('risk')!r}")
    if data.get("action", "delete") not in ACTIONS:
        errors.append(f"{where}: bad action {data.get('action')!r}")
    if data.get("provider") not in PROVIDERS:
        errors.append(f"{where}: bad provider {data.get('provider')!r}")
    if data.get("label_style") not in LABEL_STYLES:
        errors.append(f"{where}: bad label_style {data.get('label_style')!r}")
    if not isinstance(data["name"], dict) or "en" not in data["name"]:
        errors.append(f"{where}: 'name' needs at least an 'en' entry")
    if not isinstance(data["paths"], dict):
        errors.append(f"{where}: 'paths' must map platform -> list of paths")
    else:
        for plat, paths in data["paths"].items():
            if plat not in ("windows", "linux", "darwin"):
                errors.append(f"{where}: unknown platform {plat!r}")
            for p in paths:
                stripped = p.strip().rstrip("\\/")
                if stripped in ("", "~", "*", "%USERPROFILE%", "$HOME", "%SystemDrive%", "/") or len(stripped) <= 3:
                    errors.append(f"{where}: path {p!r} is too broad")
    if not isinstance(data.get("itemize", 0), int) or not 0 <= data.get("itemize", 0) <= 3:
        errors.append(f"{where}: itemize must be 0-3")
    known = set(Rule.__dataclass_fields__) - {"source"}
    for key in data:
        if key not in known and not key.startswith("_"):
            errors.append(f"{where}: unknown key {key!r}")
    return errors


def _rule_files() -> list[Path]:
    builtin = sorted(resource_path("rules").glob("*.json"))
    try:
        user = sorted(user_rules_dir().glob("*.json"))
    except OSError:
        user = []
    return builtin + user


def load_file(path: Path) -> tuple[list[Rule], list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], [f"{path.name}: {exc}"]
    entries = raw.get("rules", []) if isinstance(raw, dict) else raw
    rules, errors = [], []
    for data in entries:
        problems = validate(data, path.name)
        if problems:
            errors.extend(problems)
            continue
        fields = {k: v for k, v in data.items() if not k.startswith("_")}
        rules.append(Rule(**fields, source=path.name))
    return rules, errors


def load_rules(files: list[Path] | None = None) -> tuple[list[Rule], list[str]]:
    """Load all rules. Later files override earlier ones with the same id."""
    by_id: dict[str, Rule] = {}
    errors: list[str] = []
    for path in files if files is not None else _rule_files():
        rules, errs = load_file(path)
        errors.extend(errs)
        for rule in rules:
            by_id[rule.id] = rule
    plat = platform_key()
    return [r for r in by_id.values() if plat in r.paths], errors
