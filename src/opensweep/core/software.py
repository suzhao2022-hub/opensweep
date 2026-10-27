"""Installed programs (Windows): list, estimate last use, uninstall, find leftovers."""

from __future__ import annotations

import codecs
import ctypes
import os
import re
import shlex
import struct
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from opensweep.core.fsutil import measure
from opensweep.core.platform import IS_WINDOWS
from opensweep.core.safety import is_protected

if IS_WINDOWS:
    import winreg

UNINSTALL_KEYS = [
    ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKLM", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKCU", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


@dataclass
class Program:
    key: str
    hive: str
    name: str
    version: str = ""
    publisher: str = ""
    install_date: float | None = None
    size: int = 0
    install_location: str = ""
    uninstall_cmd: str = ""
    quiet_uninstall_cmd: str = ""
    icon: str = ""
    last_used: float | None = None
    run_count: int = 0
    exes: list[str] = field(default_factory=list)

    @property
    def needs_admin(self) -> bool:
        return self.hive == "HKLM"


def _hive(name: str):
    return winreg.HKEY_LOCAL_MACHINE if name == "HKLM" else winreg.HKEY_CURRENT_USER


def _value(key, name: str, default=None):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return default


def _parse_date(raw) -> float | None:
    if not raw or not re.fullmatch(r"\d{8}", str(raw)):
        return None
    try:
        return time.mktime(time.strptime(str(raw), "%Y%m%d"))
    except (ValueError, OverflowError):
        return None


def _icon_exe(icon: str) -> str:
    path = icon.split(",")[0].strip().strip('"')
    return path if path.lower().endswith(".exe") else ""


def list_programs(with_usage: bool = True) -> list[Program]:
    if not IS_WINDOWS:
        return []
    programs: dict[str, Program] = {}
    for hive, path in UNINSTALL_KEYS:
        try:
            root = winreg.OpenKey(_hive(hive), path)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    sub = winreg.EnumKey(root, i)
                    key = winreg.OpenKey(root, sub)
                except OSError:
                    continue
                with key:
                    name = _value(key, "DisplayName")
                    uninstall = _value(key, "UninstallString", "")
                    if not name or not uninstall:
                        continue
                    if _value(key, "SystemComponent", 0) == 1 or _value(key, "ParentKeyName"):
                        continue
                    if _value(key, "ReleaseType", "") in ("Update", "Hotfix", "Security Update"):
                        continue
                    prog = Program(
                        key=f"{hive}\\{path}\\{sub}",
                        hive=hive,
                        name=str(name).strip(),
                        version=str(_value(key, "DisplayVersion", "") or ""),
                        publisher=str(_value(key, "Publisher", "") or ""),
                        install_date=_parse_date(_value(key, "InstallDate")),
                        size=int(_value(key, "EstimatedSize", 0) or 0) * 1024,
                        install_location=str(_value(key, "InstallLocation", "") or "").strip().strip('"'),
                        uninstall_cmd=str(uninstall),
                        quiet_uninstall_cmd=str(_value(key, "QuietUninstallString", "") or ""),
                        icon=str(_value(key, "DisplayIcon", "") or ""),
                    )
                    dedupe = (prog.name.lower(), prog.version)
                    programs.setdefault("|".join(dedupe), prog)
    result = sorted(programs.values(), key=lambda p: p.name.lower())
    if with_usage:
        apply_usage(result)
    return result


# ------------------------------------------------------------- usage history

_KNOWN_FOLDERS = {
    "{6D809377-6AF0-444B-8957-A3773F02200E}": os.environ.get("ProgramW6432", r"C:\Program Files"),
    "{7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E}": os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
    "{F38BF404-1D43-42F2-9305-67DE0B28FC23}": os.environ.get("SystemRoot", r"C:\Windows"),
    "{D65231B0-B2F1-4857-A4CE-A8E7C6EA7D27}": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64"),
    "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}": os.environ.get("LOCALAPPDATA", ""),
    "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}": os.environ.get("APPDATA", ""),
    "{5E6C858F-0E22-4760-9AFE-EA3317B67173}": os.environ.get("USERPROFILE", ""),
}


def _filetime_to_unix(ft: int) -> float | None:
    if ft <= 0:
        return None
    return ft / 10_000_000 - 11644473600


def read_userassist() -> dict[str, tuple[float | None, int]]:
    """Map lowercase exe path -> (last run time, run count) from Explorer's UserAssist."""
    usage: dict[str, tuple[float | None, int]] = {}
    if not IS_WINDOWS:
        return usage
    base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return usage
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                count_key = winreg.OpenKey(root, winreg.EnumKey(root, i) + r"\Count")
            except OSError:
                continue
            with count_key:
                for j in range(winreg.QueryInfoKey(count_key)[1]):
                    try:
                        name, data, _ = winreg.EnumValue(count_key, j)
                    except OSError:
                        continue
                    parsed = parse_userassist_entry(name, data)
                    if parsed:
                        path, last, count = parsed
                        old = usage.get(path)
                        if not old or (last or 0) > (old[0] or 0):
                            usage[path] = (last, count)
    return usage


def parse_userassist_entry(name: str, data: bytes) -> tuple[str, float | None, int] | None:
    decoded = codecs.decode(name, "rot13")
    if not decoded.lower().endswith(".exe") or not isinstance(data, bytes) or len(data) < 68:
        return None
    for guid, folder in _KNOWN_FOLDERS.items():
        if decoded.upper().startswith(guid) and folder:
            decoded = folder + decoded[len(guid):]
            break
    count = struct.unpack_from("<I", data, 4)[0]
    last = _filetime_to_unix(struct.unpack_from("<Q", data, 60)[0])
    return os.path.normcase(decoded), last, count


def prefetch_times() -> dict[str, float]:
    return _prefetch_times()


def service_dirs() -> list[str]:
    """Folders of programs that run as Windows services or drivers (they are in use)."""
    if not IS_WINDOWS:
        return []
    dirs = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services")
    except OSError:
        return dirs
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                with winreg.OpenKey(root, winreg.EnumKey(root, i)) as key:
                    image = _value(key, "ImagePath")
            except OSError:
                continue
            if not image:
                continue
            exe = os.path.expandvars(_split_command(str(image))[0])
            if exe.startswith("\\??\\"):
                exe = exe[4:]
            if os.path.isabs(exe):
                dirs.append(os.path.normcase(os.path.dirname(os.path.abspath(exe))))
    return dirs


def exe_description(path: str) -> str:
    """FileDescription or ProductName from an .exe's version resource ('' if none)."""
    return _version_string(path, ("FileDescription", "ProductName"))


def exe_company(path: str) -> str:
    return _version_string(path, ("CompanyName",))


def _version_string(path: str, fields: tuple[str, ...]) -> str:
    if not IS_WINDOWS or not path:
        return ""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return ""
        ptr, length = ctypes.c_void_p(), ctypes.c_uint()
        langs = []
        if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) \
                and length.value >= 4:
            raw = ctypes.string_at(ptr, length.value)
            langs = [struct.unpack_from("<HH", raw, i) for i in range(0, length.value - 3, 4)]
        langs += [(0x0409, 0x04B0), (0x0804, 0x04B0), (0x0409, 0x04E4)]
        for name in fields:
            for lang, cp in langs:
                sub = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{name}"
                if ver.VerQueryValueW(buf, sub, ctypes.byref(ptr), ctypes.byref(length)) and length.value > 1:
                    text = ctypes.wstring_at(ptr, length.value).strip("\x00").strip()
                    if text:
                        return text
    except (OSError, AttributeError, ValueError):
        pass
    return ""


UNUSED_DAYS = 90


_COMPONENT = re.compile(r"runtime|redistributable|driver|驱动|驅動|\bsdk\b|framework|\.net|visual c\+\+|webview2|"
                        r"physx|directx|vcredist|microsoft update health|运行库|執行階段", re.IGNORECASE)


def is_component(prog: Program) -> bool:
    """Runtimes, drivers and SDKs: other programs need them even though nobody 'opens' them."""
    return bool(_COMPONENT.search(prog.name))


def usage_verdict(prog: Program, now: float | None = None) -> tuple[str, int]:
    """('unused', days) | ('no_record', 0) | ('recent', days) | ('new', 0) | ('component', 0)"""
    now = now or time.time()
    if is_component(prog):
        return "component", 0
    if prog.last_used:
        days = int((now - prog.last_used) // 86400)
        return ("unused", days) if days >= UNUSED_DAYS else ("recent", days)
    if prog.install_date and now - prog.install_date < 30 * 86400:
        return "new", 0
    return "no_record", 0


def _prefetch_times() -> dict[str, float]:
    """exe name (lowercase) -> last run time, from C:\\Windows\\Prefetch (admin only)."""
    folder = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Prefetch"
    times: dict[str, float] = {}
    try:
        for entry in os.scandir(folder):
            if entry.name.lower().endswith(".pf") and "-" in entry.name:
                exe = entry.name.rsplit("-", 1)[0].lower()
                times[exe] = max(times.get(exe, 0), entry.stat().st_mtime)
    except OSError:
        pass
    return times


def _running_exe_dirs() -> set[str]:
    try:
        import psutil
    except ImportError:
        return set()
    dirs = set()
    for proc in psutil.process_iter(["exe"]):
        exe = proc.info.get("exe")
        if exe:
            dirs.add(os.path.normcase(os.path.dirname(exe)))
    return dirs


def _exes_under(root: str, depth: int = 2, limit: int = 200) -> list[str]:
    """.exe files (relative paths) in `root` and up to `depth` levels below it."""
    found: list[str] = []
    base = root.rstrip("\\/").count(os.sep)
    for current, dirs, files in os.walk(root):
        if current.count(os.sep) - base >= depth:
            dirs[:] = []
        found.extend(os.path.relpath(os.path.join(current, f), root) for f in files if f.lower().endswith(".exe"))
        if len(found) >= limit:
            break
    return found


def apply_usage(programs: list[Program]) -> None:
    """Best-effort 'last used' estimate. Windows does not track this reliably."""
    userassist = read_userassist()
    prefetch = _prefetch_times()
    running = _running_exe_dirs()
    for prog in programs:
        roots = []
        if prog.install_location and os.path.isdir(prog.install_location):
            roots.append(os.path.normcase(os.path.abspath(prog.install_location)))
        icon_exe = _icon_exe(prog.icon)
        if icon_exe:
            roots.append(os.path.normcase(os.path.dirname(icon_exe)))
        best, count = None, 0
        for path, (last, runs) in userassist.items():
            if any(path.startswith(r + os.sep) for r in roots):
                count += runs
                if last and (best is None or last > best):
                    best = last
        exe_names = set()
        rel_exes = []
        if icon_exe:
            exe_names.add(os.path.basename(icon_exe).lower())
        for root in roots[:1]:
            rel_exes = _exes_under(root, depth=2)
            exe_names.update(os.path.basename(e).lower() for e in rel_exes)
        exe_names = {n for n in exe_names if not n.startswith(("unins", "uninst", "update"))}
        for exe in exe_names:
            t = prefetch.get(exe)
            if t and (best is None or t > best):
                best = t
        if any(d == r or d.startswith(r + os.sep) for d in running for r in roots):
            best = time.time()  # running right now
        prog.last_used, prog.run_count = best, count
        prog.exes = [e for e in rel_exes if not os.path.basename(e).lower().startswith(("unins", "uninst"))][:30]


def fill_missing_size(prog: Program) -> None:
    if prog.size == 0 and prog.install_location and os.path.isdir(prog.install_location):
        if not is_protected(prog.install_location):
            prog.size = measure(prog.install_location).size


def list_programs_full() -> list[Program]:
    """Programs with usage estimates and sizes filled in from disk where the registry has none."""
    programs = list_programs()
    for prog in programs:
        fill_missing_size(prog)
    return programs


# ---------------------------------------------------------------- uninstall


def _split_command(cmd: str) -> list[str]:
    cmd = cmd.strip()
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        exe, rest = cmd[1:end], cmd[end + 1:]
    else:
        m = re.match(r"(.+?\.exe)(\s.*)?$", cmd, re.IGNORECASE)
        exe, rest = (m.group(1), m.group(2) or "") if m else (cmd, "")
    try:
        args = shlex.split(rest, posix=False)
    except ValueError:
        args = rest.split()
    return [exe] + args


def uninstall(prog: Program, wait: bool = True) -> int:
    """Run the program's own uninstaller. Returns its exit code (-1 if it could not start)."""
    cmd = prog.uninstall_cmd
    # MsiExec /I opens the repair UI; /X is uninstall.
    cmd = re.sub(r"(?i)(msiexec(?:\.exe)?\s+)/I", r"\1/X", cmd)
    parts = _split_command(cmd)
    try:
        proc = subprocess.Popen(parts)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 740:  # elevation required
            return _run_elevated(parts, wait)
        try:
            proc = subprocess.Popen(cmd, shell=True)
        except OSError:
            return -1
    return proc.wait() if wait else 0


class _SHELLEXECUTEINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p), ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_ulong), ("hIcon", ctypes.c_void_p), ("hProcess", ctypes.c_void_p),
    ]


def _run_elevated(parts: list[str], wait: bool) -> int:
    info = _SHELLEXECUTEINFO()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = parts[0]
    info.lpParameters = subprocess.list2cmdline(parts[1:])
    info.nShow = 1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        return -1
    if wait and info.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(info.hProcess), 0xFFFFFFFF)
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(ctypes.c_void_p(info.hProcess), ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(info.hProcess))
        return code.value
    return 0


def still_installed(prog: Program) -> bool:
    hive, _, path = prog.key.partition("\\")
    try:
        winreg.CloseKey(winreg.OpenKey(_hive(hive), path))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- leftovers

_GENERIC_NAMES = {"microsoft", "windows", "common files", "programs", "temp", "google", "intel", "nvidia",
                  "amd", "packages", "app", "apps", "data", "cache", "local", "roaming", "default"}


def _clean_name(name: str) -> str:
    name = re.sub(r"\s*\(.*?\)", "", name)          # drop "(x64)", "(remove only)"
    name = re.sub(r"\s+v?\d+(\.\d+)*\s*$", "", name)  # drop trailing version
    return name.strip()


def program_dirs(programs: list[Program]) -> list[str]:
    dirs = []
    for p in programs:
        for path in (p.install_location, os.path.dirname(_icon_exe(p.icon))):
            if path:
                dirs.append(os.path.normcase(os.path.abspath(path)).rstrip("\\/"))
    return dirs


_program_dirs = program_dirs


def _overlaps(path: str, others: list[str]) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    return any(o == p or o.startswith(p + os.sep) or p.startswith(o + os.sep) for o in others)


def find_leftovers(prog: Program, remaining: list[Program] | None = None) -> list[tuple[str, int]]:
    """Folders that look like they belonged to an uninstalled program.

    Only exact folder-name matches are returned, never fuzzy guesses, and the
    user still has to tick each one. Folders used by any program that is still
    installed (`remaining`, read from the registry when omitted) are excluded,
    e.g. "Python" when another Python version is still there.
    """
    if remaining is None:
        remaining = [p for p in list_programs(with_usage=False) if p.key != prog.key]
    others = _program_dirs(remaining)
    candidates = []
    if prog.install_location:
        candidates.append(prog.install_location)
    names = {_clean_name(prog.name)}
    if prog.publisher:
        names.add(f"{prog.publisher}\\{_clean_name(prog.name)}")
    bases = [os.environ.get(v, "") for v in ("APPDATA", "LOCALAPPDATA", "ProgramData", "ProgramFiles",
                                              "ProgramFiles(x86)")]
    bases.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"))
    for base in filter(None, bases):
        for name in names:
            if name and name.lower() not in _GENERIC_NAMES and len(name) >= 3:
                candidates.append(os.path.join(base, name))
    found, seen = [], set()
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen or not os.path.isdir(path) or is_protected(path) or _overlaps(path, others):
            continue
        seen.add(key)
        found.append((path, measure(path).size))
    return found
