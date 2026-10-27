"""Memory overview: which apps use RAM and CPU, and closing them on request.

There is deliberately no "free RAM" button. Forcing Windows to trim working
sets only pushes pages out to be read back moments later; it makes the PC
slower, not faster. Closing apps you don't need is what actually helps, so
this module tells apps with a window apart from programs that only run in the
background, which are the ones people tend to forget about.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass, field
from functools import lru_cache

import psutil

from opensweep.core.platform import IS_WINDOWS

# Closing these would crash or log out the session.
CRITICAL = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "memory compression",
    "secure system", "sihost.exe", "ctfmon.exe", "audiodg.exe", "spoolsv.exe", "msmpeng.exe", "conhost.exe",
    "runtimebroker.exe", "taskhostw.exe", "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
    "shellexperiencehost.exe", "textinputhost.exe", "wudfhost.exe", "dllhost.exe", "explorer.exe",
    "searchindexer.exe", "wmiprvse.exe", "nissrv.exe", "securityhealthservice.exe", "smartscreen.exe",
    "launchd", "kernel_task", "systemd", "init", "kthreadd", "xorg", "gnome-shell",
}


@dataclass
class AppUsage:
    name: str
    memory: int = 0
    pids: list[int] = field(default_factory=list)
    exe: str = ""
    cpu: float = 0.0          # percent of the whole CPU
    has_window: bool = False
    started: float = 0.0
    description: str = ""

    @property
    def critical(self) -> bool:
        return self.name.lower() in CRITICAL

    @property
    def is_self(self) -> bool:
        return os.getpid() in self.pids

    @property
    def kind(self) -> str:
        """system | app (has a window) | background"""
        if self.critical:
            return "system"
        root = os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows"))
        if self.exe and os.path.normcase(self.exe).startswith(root + os.sep):
            return "system"
        return "app" if self.has_window else "background"

    @property
    def display_name(self) -> str:
        return self.description if self.description and len(self.description) <= 40 else self.name


def system_memory():
    return psutil.virtual_memory()


def visible_window_pids() -> set[int]:
    """Processes that own a visible top-level window with a title."""
    if not IS_WINDOWS:
        return set()
    user32 = ctypes.windll.user32
    pids: set[int] = set()
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 \
                and not user32.GetWindow(hwnd, 4):  # GW_OWNER: skip owned pop-ups
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(proc_type(callback), 0)
    return pids


@lru_cache(maxsize=512)
def _description(exe: str) -> str:
    from opensweep.core.software import exe_description
    return exe_description(exe)


_procs: dict[int, psutil.Process] = {}


def _cpu(pid: int) -> float:
    """CPU use since the previous call for this process (0 the first time)."""
    proc = _procs.get(pid)
    try:
        if proc is None:
            proc = _procs[pid] = psutil.Process(pid)
            proc.cpu_percent(None)
            return 0.0
        return proc.cpu_percent(None)
    except psutil.Error:
        _procs.pop(pid, None)
        return 0.0


def list_apps() -> list[AppUsage]:
    windows = visible_window_pids()
    cpus = psutil.cpu_count() or 1
    groups: dict[str, AppUsage] = {}
    alive = set()
    for proc in psutil.process_iter(["name", "exe", "memory_info", "create_time"]):
        info = proc.info
        name = info.get("name") or "?"
        mem = info.get("memory_info")
        if mem is None:
            continue
        alive.add(proc.pid)
        used = getattr(mem, "private", 0) or mem.rss
        key = (info.get("exe") or name).lower()
        app = groups.setdefault(key, AppUsage(name))
        app.memory += used
        app.pids.append(proc.pid)
        app.cpu += _cpu(proc.pid) / cpus
        app.has_window = app.has_window or proc.pid in windows
        created = info.get("create_time") or 0
        if created and (not app.started or created < app.started):
            app.started = created
        if not app.exe and info.get("exe"):
            app.exe = info["exe"]
    for pid in list(_procs):
        if pid not in alive:
            _procs.pop(pid, None)
    for app in groups.values():
        if app.exe:
            app.description = _description(app.exe)
    return sorted(groups.values(), key=lambda a: a.memory, reverse=True)


def breakdown(apps: list[AppUsage]) -> dict[str, int]:
    """Memory in use split by kind, scaled so the parts add up to what Windows reports as used."""
    vm = psutil.virtual_memory()
    used = vm.total - vm.available
    sums = {"app": 0, "background": 0, "system": 0}
    for app in apps:
        sums[app.kind] += app.memory
    total = sum(sums.values()) or 1
    parts = {k: int(used * v / total) for k, v in sums.items()}
    parts["free"] = vm.available
    return parts


def close_app(app: AppUsage, timeout: float = 5.0) -> tuple[int, int]:
    """Ask every process of the app to exit, kill stragglers. Returns (closed, failed)."""
    if app.critical or app.is_self:
        return 0, len(app.pids)
    procs, denied = [], 0
    for pid in app.pids:
        try:
            p = psutil.Process(pid)
            p.terminate()
            procs.append(p)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            denied += 1
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    _, still = psutil.wait_procs(alive, timeout=2)
    return len(procs) - len(still), denied + len(still)
