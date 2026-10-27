"""Work out what an unfamiliar folder or file on a drive is, from what Windows already knows.

Used by the disk map when a path is not in data/known_paths.json:

- it sits inside an installed program's folder          -> "program"
- it is that program's data under AppData / ProgramData -> "data_of" (matched by name, so only a hint)
- a folder that contains a program nobody registered    -> "exe_folder" (portable program or leftovers),
                                                           or "windows" when that program is Microsoft's
- a program file or library                             -> "file", from its version information

Nothing here touches the disk beyond reading names and version resources.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

from opensweep.core import software
from opensweep.core.platform import IS_WINDOWS

VERSIONED = {".exe", ".dll", ".sys", ".ocx", ".scr", ".cpl", ".drv"}
GENERIC = software._GENERIC_NAMES | {"cache", "caches", "logs", "log", "crashpad", "tmp", "backup", "backups",
                                     "config", "settings", "updates", "update", "installer", "plugins",
                                     "extensions", "storage", "users", "user", "system", "shared", "common", "application",
                                     "uninstallinformation", "uninstall information", "bin", "files"}


@dataclass
class Identity:
    kind: str            # program | data_of | exe_folder | windows | file
    name: str
    publisher: str = ""
    detail: str = ""

    @property
    def who(self) -> str:
        return f"{self.name} · {self.publisher}" if self.publisher and self.publisher != self.name else self.name


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _key(text: str) -> str:
    return re.sub(r"[^0-9a-z一-鿿]", "", text.lower())


@lru_cache(maxsize=1)
def _catalog() -> tuple[list[tuple[str, software.Program]], list[tuple[set[str], software.Program]]]:
    """(install folder, program) pairs, and (names the program's data folders may use, program)."""
    if not IS_WINDOWS:
        return [], []
    try:
        programs = [p for p in software.list_programs(with_usage=False)]
    except OSError:
        return [], []
    dirs, names = [], []
    for p in programs:
        for folder in {_norm_path(d) for d in (p.install_location, os.path.dirname(software._icon_exe(p.icon)))
                       if d}:
            # Shared roots (Program Files itself, a drive) never identify a program.
            if len(folder) > 3 and os.path.basename(folder) not in ("program files", "program files (x86)"):
                dirs.append((folder, p))
        keys = {_key(software._clean_name(p.name))}
        if p.install_location:
            keys.add(_key(os.path.basename(p.install_location.rstrip("\\/"))))
        icon = software._icon_exe(p.icon)
        if icon:
            keys.add(_key(os.path.splitext(os.path.basename(icon))[0]))
        names.append(({k for k in keys if len(k) >= 3 and k not in GENERIC}, p))
    names += _store_apps()
    dirs.sort(key=lambda item: -len(item[0]))  # most specific folder first
    return dirs, names


def _store_apps() -> list[tuple[set[str], software.Program]]:
    """Store (MSIX) apps never appear in the uninstall list; Windows keeps their package names here."""
    import winreg
    path = (r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel"
            r"\Repository\Packages")
    found = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    full = winreg.EnumKey(root, i)
                except OSError:
                    continue
                package = full.split("_")[0]
                if package.lower().startswith(("microsoft.", "windows.", "ms-resource")):
                    continue  # Windows' own apps
                found.setdefault(package, None)
    except OSError:
        return []
    result = []
    for package in found:
        publisher, _, name = package.rpartition(".")
        keys = {k for k in (_key(package), _key(name)) if len(k) >= 3 and k not in GENERIC}
        if keys:
            result.append((keys, software.Program("store:" + package, "HKCU", name or package,
                                                  publisher=publisher)))
    return result


def reset() -> None:
    _catalog.cache_clear()


def _containers(programs: bool = False) -> list[str]:
    if programs:
        return [_norm_path(r) for r in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""))
                if r]
    home = os.environ.get("USERPROFILE", "")
    roots = [os.environ.get(v, "") for v in ("LOCALAPPDATA", "APPDATA", "ProgramData", "ProgramFiles",
                                              "ProgramFiles(x86)")]
    roots += [os.path.join(home, "AppData", "LocalLow"), os.path.join(home, "Documents"),
              os.path.join(home, "Saved Games")] if home else []
    return [_norm_path(r) for r in roots if r]


def _app_folder(path: str) -> str:
    """The first folder name under AppData\\Local, ProgramData, ...: where one app keeps its things."""
    low = _norm_path(path)
    for root in _containers():
        if low.startswith(root + os.sep):
            return low[len(root) + 1:].split(os.sep)[0]
    return ""


def _program_for_data(folder: str) -> software.Program | None:
    key = _key(folder)
    if len(key) < 3 or key in GENERIC:
        return None
    _, names = _catalog()
    for keys, prog in names:
        if key in keys:
            return prog
    if len(key) >= 5:  # "spotify" for "Spotify Premium", "obs" is too short to guess
        for keys, prog in names:
            if any(len(k) >= 5 and (key in k or k in key) for k in keys):
                return prog
    return None


def _first_program_file(folder: str, depth: int = 2, limit: int = 150) -> str:
    seen = 0
    stack = [(folder, 0)]
    while stack:
        current, level = stack.pop(0)
        try:
            entries = sorted(os.scandir(current), key=lambda e: (not e.is_file(), e.name.lower()))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > limit:
                return ""
            try:
                if entry.is_file() and entry.name.lower().endswith(".exe"):
                    return entry.path
                if entry.is_dir(follow_symlinks=False) and level < depth:
                    stack.append((entry.path, level + 1))
            except OSError:
                continue
    return ""


def identify(path: str, is_dir: bool) -> Identity | None:
    if not IS_WINDOWS or not path or not os.path.isabs(path):
        return None
    dirs, _ = _catalog()
    low = _norm_path(path)
    for folder, prog in dirs:
        if low == folder or low.startswith(folder + os.sep):
            return Identity("program", software._clean_name(prog.name), prog.publisher)
    app = _app_folder(path)
    if app:
        prog = _program_for_data(app)
        if prog is not None:
            inside = any(low.startswith(root + os.sep) for root in _containers(programs=True))
            return Identity("program" if inside else "data_of", software._clean_name(prog.name), prog.publisher)
    if is_dir:
        exe = _first_program_file(path)
        if exe:
            desc, company = software.exe_description(exe), software.exe_company(exe)
            if desc or company:
                kind = "windows" if "microsoft" in company.lower() else "exe_folder"
                return Identity(kind, desc or os.path.basename(exe), company, os.path.basename(exe))
        return None
    if os.path.splitext(path)[1].lower() in VERSIONED:
        desc, company = software.exe_description(path), software.exe_company(path)
        if desc or company:
            return Identity("file", desc or os.path.basename(path), company)
    return None
