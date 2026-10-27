"""Startup guard: keep apps from sneaking back into startup.

Many apps quietly turn their startup entry back on (after an update, or a
"guard" process does it). The guard remembers every entry the user turned off
and, at each logon and each time OpenSweep opens, turns it off again and says
so. It does not stay running: at logon it checks once (about a second) and exits.

Entries that need administrator rights (all-users entries, scheduled tasks,
services) can't be switched off at logon; they are reported so the user can
fix them with one click the next time OpenSweep is open.
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
import time
from dataclasses import dataclass, field

from opensweep.core import startup
from opensweep.core.platform import IS_WINDOWS, config_dir

GUARD_VALUE = startup.GUARD_VALUE
HISTORY_LIMIT = 200


def _file():
    return config_dir() / "guard.json"


def key_of(entry: startup.StartupEntry) -> str:
    return f"{entry.approved_key}|{entry.hive}|{entry.reg_path}|{entry.name}".lower()


def _load() -> dict:
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("watched", {})
            data.setdefault("history", [])
            return data
    except (OSError, ValueError):
        pass
    return {"watched": {}, "history": [], "seeded": False}


def _save(data: dict) -> None:
    data["history"] = data["history"][-HISTORY_LIMIT:]
    try:
        _file().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def _record(entry: startup.StartupEntry) -> dict:
    return {"name": entry.name, "display": entry.display_name, "exe": entry.exe, "type": entry.approved_key,
            "since": time.strftime("%Y-%m-%d")}


def remember(entries: list[startup.StartupEntry]) -> None:
    """The user turned these off: keep them off from now on."""
    if not entries:
        return
    data = _load()
    for e in entries:
        data["watched"][key_of(e)] = _record(e)
    _save(data)


def forget(entries: list[startup.StartupEntry]) -> None:
    """The user turned these back on themselves: stop guarding them."""
    if not entries:
        return
    data = _load()
    for e in entries:
        data["watched"].pop(key_of(e), None)
    _save(data)


def seed(entries: list[startup.StartupEntry]) -> None:
    """First run: everything that is already off is something the user (or OpenSweep) turned off."""
    data = _load()
    if data.get("seeded"):
        return
    for e in entries:
        if not e.enabled and e.exists:
            data["watched"].setdefault(key_of(e), _record(e))
    data["seeded"] = True
    _save(data)


def watched_services() -> set[str]:
    return {parts[2] for key in _load()["watched"] if
            len(parts := key.split("|")) == 4 and parts[0] == "service"}


def watched_count() -> int:
    return len(_load()["watched"])


def history(days: int | None = None) -> list[dict]:
    items = _load()["history"]
    if days is not None:
        cutoff = time.time() - days * 86400
        items = [h for h in items if h.get("ts", 0) >= cutoff]
    return list(reversed(items))


@dataclass
class GuardResult:
    fixed: list[startup.StartupEntry] = field(default_factory=list)
    need_admin: list[startup.StartupEntry] = field(default_factory=list)
    failed: list[startup.StartupEntry] = field(default_factory=list)


def came_back(entries: list[startup.StartupEntry]) -> list[startup.StartupEntry]:
    """Watched entries that are switched on again."""
    watched = _load()["watched"]
    return [e for e in entries if (e.enabled or e.extra.get("needs_block"))
            and e.name != GUARD_VALUE and key_of(e) in watched]


def enforce(admin: bool, entries: list[startup.StartupEntry] | None = None) -> GuardResult:
    """Switch every entry that came back off again (as far as our rights allow) and log it."""
    if entries is None:
        entries = startup.describe(startup.list_startup())
    result = GuardResult()
    data = _load()
    now = time.time()
    for e in came_back(entries):
        if e.needs_admin and not admin:
            result.need_admin.append(e)
            continue
        try:
            startup.set_enabled(e, False)
        except (OSError, ValueError, subprocess.SubprocessError):
            result.failed.append(e)
            continue
        result.fixed.append(e)
        data["history"].append({"ts": now, "time": time.strftime("%Y-%m-%d %H:%M"), "display": e.display_name,
                                "type": e.approved_key})
    if result.fixed:
        _save(data)
    return result


# ------------------------------------------------------------------ logon check

def guard_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --guard'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Development checkout: run the package from its source folder.
    return f'"{exe}" -c "import sys; sys.path.insert(0, r\'{src}\'); import runpy; ' \
           f'sys.argv=[\'opensweep\', \'--guard\']; runpy.run_module(\'opensweep\', run_name=\'__main__\')"'


def set_logon_check(on: bool) -> None:
    """Add or remove our own small logon check (a per-user Run entry; no admin needed)."""
    if not IS_WINDOWS:
        return
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, startup.RUN, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, GUARD_VALUE, 0, winreg.REG_SZ, guard_command())
        else:
            try:
                winreg.DeleteValue(key, GUARD_VALUE)
            except OSError:
                pass


def logon_check_installed() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup.RUN) as key:
            winreg.QueryValueEx(key, GUARD_VALUE)
            return True
    except OSError:
        return False


def _installed_command() -> str:
    if not IS_WINDOWS:
        return ""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, startup.RUN) as key:
            return winreg.QueryValueEx(key, GUARD_VALUE)[0]
    except OSError:
        return ""


def sync_logon_check(enabled: bool) -> None:
    """Keep the logon check installed exactly when the guard is on and has something to watch."""
    want = enabled and watched_count() > 0
    if want and logon_check_installed() and _installed_command() != guard_command():
        # The app moved (reinstalled, or a different copy was opened): point the entry at this one.
        try:
            set_logon_check(True)
        except OSError:
            pass
        return
    if want != logon_check_installed():
        try:
            set_logon_check(want)
        except OSError:
            pass
