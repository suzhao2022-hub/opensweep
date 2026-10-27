"""Live startup diagnostic. Matches are candidates, not historical launch proof."""
from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict, dataclass, field

import psutil

from opensweep import __version__
from opensweep.core import startup

WINDOW = 15 * 60
ON, OFF, VIA, SERVICE, OTHER = "on", "off", "via", "service", "other"


@dataclass
class BootItem:
    name: str
    exe: str
    seconds: int
    verdict: str
    parent: str = ""
    entry: str = ""
    entry_on: bool = False
    pid: int = 0
    ppid: int = 0
    command: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)


@dataclass
class BootReport:
    captured_at: float
    booted_at: float
    items: list[BootItem]
    entries: list[startup.StartupEntry]
    processes: list[dict]
    guard: dict
    errors: list[str] = field(default_factory=list)
    launch_points: list[dict] = field(default_factory=list)  # read-only: Winlogon, Active Setup, ...

    def to_dict(self) -> dict:
        return {
            "schema": 1, "version": __version__, "os": platform.platform(),
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.captured_at)),
            "booted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.booted_at)),
            "scope": "Live processes and current settings, not a historical boot log. "
                     "Matches are candidates; manual launches and session restore are possible. "
                     "Exited processes/parents are unavailable. No startup settings are changed.",
            "errors": self.errors,
            "items": [asdict(i) for i in self.items],
            "startup_entries": [asdict(e) for e in self.entries],
            "processes": self.processes, "guard": self.guard, "launch_points": self.launch_points,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)) if path else ""


def _skip(exe: str) -> bool:
    low = _norm(exe)
    windir = _norm(os.environ.get("SystemRoot", r"C:\Windows"))
    return not os.path.isabs(exe) or low.startswith(windir + os.sep) or "\\windowsapps\\" in low


def _snapshot() -> list[dict]:
    result = []
    for p in psutil.process_iter(["pid", "ppid", "name", "exe", "create_time", "cmdline"]):
        if p.info.get("create_time") is not None:
            result.append(dict(p.info))
    return result


def _trace(entries, processes, booted_at, window):
    by_exe: dict[str, list[startup.StartupEntry]] = {}
    for e in entries:
        paths = [e.exe] + [startup.command_exe(c) for c in e.extra.get("commands", [])]
        for path in dict.fromkeys(paths):
            if path:
                by_exe.setdefault(_norm(path), []).append(e)
    procs = {p["pid"]: p for p in processes}

    def parent_of(info):
        parent = procs.get(info.get("ppid"))
        if parent and parent["pid"] != info["pid"] and parent["create_time"] <= info["create_time"]:
            return parent
        return None

    def entry_for(exe, parent_name=""):
        found = by_exe.get(_norm(exe), [])
        kind = {"services.exe": "Service", "svchost.exe": "Task"}.get(parent_name.lower())
        if kind:
            found = [e for e in found if e.approved_key == kind] or found
        return next((e for e in found if e.enabled), found[0] if found else None)

    items = {}
    for info in sorted(processes, key=lambda p: p["create_time"]):
        exe = info.get("exe") or ""
        seconds = int(info["create_time"] - booted_at)
        if seconds < 0 or (window is not None and seconds > window) or _skip(exe) or _norm(exe) in items:
            continue
        parent = parent_of(info)
        pname = (parent or {}).get("name") or ""
        own = entry_for(exe, pname)
        item = BootItem(info.get("name") or os.path.basename(exe), exe, seconds, OTHER,
                        pname, pid=info["pid"], ppid=info.get("ppid") or 0,
                        command=info.get("cmdline") or [])
        item.candidates = [{"name": e.name, "type": e.approved_key, "path": e.reg_path,
                            "enabled": e.enabled, "hive": e.hive}
                           for e in by_exe.get(_norm(exe), [])]
        chain, ancestor, service_parent = parent, None, False
        visited = {info["pid"]}
        while chain is not None and chain["pid"] not in visited:
            visited.add(chain["pid"])
            lowname = (chain.get("name") or "").lower()
            if lowname == "services.exe":
                service_parent = True
                break
            if lowname in ("explorer.exe", "svchost.exe", "winlogon.exe", "userinit.exe"):
                break
            ancestor = entry_for(chain.get("exe") or "")
            if ancestor:
                break
            chain = parent_of(chain)
        if ancestor and not (own and own.enabled):
            item.verdict, item.entry, item.entry_on = VIA, ancestor.display_name, ancestor.enabled
        elif own:
            item.verdict = ON if own.enabled else OFF
            item.entry, item.entry_on = own.display_name, own.enabled
        elif service_parent:
            item.verdict = SERVICE
        items[_norm(exe)] = item
    return list(items.values())


def trace(entries: list[startup.StartupEntry] | None = None, window: int | None = None) -> list[BootItem]:
    if entries is None:
        entries = startup.describe(startup.list_startup())
    return _trace(entries, _snapshot(), psutil.boot_time(), window)


def collect() -> BootReport:
    """Read-only report of all startup settings and currently visible processes."""
    from opensweep.core import guard
    from opensweep.settings import Settings
    errors = []
    entries = startup.describe(startup.list_startup(errors=errors))
    processes = _snapshot()
    booted = psutil.boot_time()
    return BootReport(time.time(), booted, _trace(entries, processes, booted, None),
                      entries, processes, {"enabled": Settings.load().guard,
                                           "watched": guard.watched_count(), "history": guard.history()},
                      errors, startup.launch_points())


def boot_time() -> float:
    return psutil.boot_time()
