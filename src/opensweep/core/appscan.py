"""Find programs that Windows does not know about.

Two kinds of folders take space but never show up in "Apps & features":

- portable ("green") programs that were just unzipped somewhere, and
- leftovers of programs that were uninstalled but left their folder behind.

We only look where programs usually live (Program Files on every drive,
%LOCALAPPDATA%\\Programs, and top-level folders of non-system drives), never
flag a top-level folder without an .exe (that is probably the user's data),
and skip anything that belongs to a registered program, is running, or looks
like a source code project. Nothing is deleted here; the user decides.
"""

from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from opensweep.core import software
from opensweep.core.engine import write_log
from opensweep.core.fsutil import Cancelled, DeleteResult, delete_tree, measure
from opensweep.core.platform import IS_WINDOWS
from opensweep.core.safety import check_deletable, is_protected

# Folders at a drive root that hold programs; their children are the candidates.
CONTAINERS = {"program files", "program files (x86)", "programs", "program", "software", "soft", "apps",
              "applications", "games", "tools", "软件", "游戏", "工具", "应用"}
# Never candidates themselves.
SKIP_NAMES = {"windows", "users", "programdata", "recovery", "perflogs", "system volume information",
              "$recycle.bin", "$windows.~bt", "$windows.~ws", "$winreagent", "msocache", "boot", "efi",
              "config.msi", "documents and settings", "onedrivetemp", "windowsapps", "wpsystem", "intel", "amd",
              "nvidia", "nvidia corporation", "drivers", "common files", "microsoft", "windows defender",
              "windows mail", "windows media player", "windows nt", "windows photo viewer", "windows portable devices",
              "windows security", "windows sidebar", "windowspowershell", "modifiablewindowsapps",
              "internet explorer", "microsoft.net", "reference assemblies", "msbuild", "dotnet", "uninstall information",
              "installshield installation information", "package cache", "steamlibrary", "xboxgames",
              # personal data folders people often create on other drives
              "downloads", "download", "下载", "documents", "文档", "desktop", "桌面", "pictures", "photos", "图片",
              "照片", "videos", "视频", "music", "音乐", "backup", "backups", "备份", "onedrive", "projects", "code",
              "workspace", "repos", "source",
              # SDKs that register many small pieces without a folder
              "windows kits", "microsoft sdks", "microsoft sql server", "iis express", "microsoft visual studio",
              "dotnet", "windowsapps"}
# Folder names containing these are downloads or chat data, not programs.
SKIP_SUBSTRINGS = ("download", "下载", "下載", " files", "_files", "backup", "备份", "聊天记录")
# Sub-folders that are parts of one program rather than separate programs.
APP_SUBDIRS = {"bin", "bin64", "lib", "libs", "plugins", "plugin", "resources", "res", "runtime", "jre", "jbr",
               "common7", "x64", "x86", "win64", "win32", "tools", "modules", "locales", "data", "engine",
               "redist", "redistributables", "support", "utils", "extensions", "app", "main", "client", "core"}
HELPER_WORDS = ("卸载", "uninstall", "setup", "安装", "installer", "更新", "update", "crash", "反馈", "repair")
# A folder with any of these at its top level is a code project, not an installed program.
PROJECT_MARKERS = {".git", ".svn", ".hg", "package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod",
                   "cmakelists.txt", "makefile", "pom.xml", "build.gradle", ".vscode", ".idea"}
HELPER_EXE = ("unins", "uninst", "uninstall", "update", "updater", "crash", "helper", "setup", "install",
              "vc_redist", "dxsetup", "repair", "bugreport", "elevate")

MIN_PORTABLE = 20 * 1024 * 1024
MIN_LEFTOVER = 1 * 1024 * 1024


@dataclass
class FolderApp:
    path: str
    name: str
    kind: str                     # "portable" | "leftover"
    size: int = 0
    files: int = 0
    main_exe: str = ""
    description: str = ""
    exes: list[str] = field(default_factory=list)
    last_used: float | None = None
    newest: float = 0.0
    running: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.description or self.name


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def fixed_drives() -> list[str]:
    try:
        import psutil
    except ImportError:
        return []
    drives = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype or "removable" in part.opts:
            continue
        drives.append(part.mountpoint)
    return drives


def candidate_roots() -> list[tuple[str, bool]]:
    """(folder, is_container). Children of containers may be leftovers; drive-root children may not."""
    roots: list[tuple[str, bool]] = []
    system_drive = _norm(os.environ.get("SystemDrive", "C:") + "\\")
    for drive in fixed_drives():
        try:
            names = os.listdir(drive)
        except OSError:
            continue
        for name in names:
            if name.lower() in CONTAINERS:
                roots.append((os.path.join(drive, name), True))
        if _norm(drive) != system_drive:
            roots.append((drive, False))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append((os.path.join(local, "Programs"), True))
    return roots


def _hidden_or_system(entry: os.DirEntry) -> bool:
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & 0x2 or attrs & 0x4 or attrs & 0x400)  # hidden, system, reparse point


def _find_exes(folder: str, max_depth: int = 3, limit: int = 300) -> tuple[list[str], bool]:
    """(.exe files, whether there are .dll files) in the first few levels of `folder`."""
    exes: list[str] = []
    has_dll = False
    base_depth = folder.rstrip("\\/").count(os.sep)
    for current, dirs, files in os.walk(folder):
        if current.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            low = f.lower()
            if low.endswith(".exe"):
                exes.append(os.path.join(current, f))
            elif low.endswith(".dll"):
                has_dll = True
        if len(exes) >= limit:
            break
    return exes, has_dll


def _ranked_exes(exes: list[str], folder_name: str) -> list[str]:
    def score(path: str):
        name = os.path.basename(path).lower()
        helper = name.startswith(HELPER_EXE)
        depth = path.count(os.sep)
        matches = folder_name.lower().replace(" ", "") in name.replace(" ", "")
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return (not helper, matches, -depth, size)
    return sorted(exes, key=score, reverse=True)


def _clean_description(text: str) -> str:
    text = re.split(r"[<\r\n\t]", text)[0].strip()
    return text[:60]


def _pick_main(exes: list[str], folder_name: str) -> tuple[str, str]:
    """(main exe, its description), skipping installers and uninstallers."""
    ranked = _ranked_exes(exes, folder_name)
    for exe in ranked[:8]:
        desc = _clean_description(software.exe_description(exe))
        if not any(w in desc.lower() for w in HELPER_WORDS):
            return exe, desc
    return (ranked[0], "") if ranked else ("", "")


def _norm_name(name: str) -> str:
    return re.sub(r"[^0-9a-z一-鿿]", "", software._clean_name(name).lower())


def _matches_program(names: list[str], program_names: list[str]) -> bool:
    for name in filter(None, (_norm_name(n) for n in names)):
        for prog in program_names:
            if name == prog or (len(name) >= 4 and len(prog) >= 4 and (name in prog or prog in name)):
                return True
    return False


def _is_project(folder: str) -> bool:
    try:
        names = {n.lower() for n in os.listdir(folder)}
    except OSError:
        return False
    return bool(names & PROJECT_MARKERS) or any(n.endswith((".sln", ".csproj")) for n in names)


def _overlap(path: str, registered: list[str]) -> str:
    """'inside' if path is in a registered folder, 'contains' if it holds one, '' otherwise."""
    p = _norm(path)
    for r in registered:
        if p == r or p.startswith(r + os.sep):
            return "inside"
    for r in registered:
        if r.startswith(p + os.sep):
            return "contains"
    return ""


def _running_dirs() -> list[tuple[str, str]]:
    try:
        import psutil
    except ImportError:
        return []
    found = []
    for proc in psutil.process_iter(["exe", "name"]):
        exe = proc.info.get("exe")
        if exe:
            found.append((_norm(os.path.dirname(exe)), proc.info.get("name") or os.path.basename(exe)))
    return found


def find_folder_apps(programs: list[software.Program] | None = None,
                     roots: list[tuple[str, bool]] | None = None,
                     cancel: threading.Event | None = None, progress=None) -> list[FolderApp]:
    if programs is None:
        programs = software.list_programs(with_usage=False)
    registered = [d for d in software.program_dirs(programs) if d and not is_protected(d)]
    roots = candidate_roots() if roots is None else roots
    usage = software.read_userassist() if IS_WINDOWS else {}
    prefetch = software.prefetch_times() if IS_WINDOWS else {}
    running = _running_dirs()
    services = software.service_dirs()

    # Registered programs that the registry gives no folder for are matched by name instead.
    nameless = [_norm_name(p.name) for p in programs
                if not p.install_location and not software._icon_exe(p.icon)]
    nameless = [n for n in nameless if n]

    seen: set[str] = set()
    found: list[FolderApp] = []

    def consider(folder: str, in_container: bool, depth: int) -> None:
        if cancel is not None and cancel.is_set():
            raise Cancelled
        key = _norm(folder)
        name = os.path.basename(folder.rstrip("\\/"))
        if key in seen or any(s in name.lower() for s in SKIP_SUBSTRINGS):
            return
        seen.add(key)
        overlap = _overlap(folder, registered)
        if overlap == "inside":
            return
        if overlap == "contains":
            if depth < 2:  # holds registered programs: look one level deeper
                for child in _subdirs(folder):
                    consider(child, in_container, depth + 1)
            return
        if is_protected(folder) or _is_project(folder):
            return
        if any(s == key or s.startswith(key + os.sep) for s in services):
            return  # runs as a Windows service or driver: in use even if never opened
        if progress:
            progress(folder)
        exes, has_dll = _find_exes(folder)
        real = [e for e in exes if not os.path.basename(e).lower().startswith(HELPER_EXE)]
        # A folder full of separate programs (a games or tools folder): list each one instead.
        top_level = [e for e in real if os.path.dirname(e) == folder]
        groups = {os.path.relpath(e, folder).split(os.sep)[0] for e in real if e not in top_level}
        one_app = any(g.lower() in APP_SUBDIRS for g in groups)  # bin/, lib/, plugins/ ... belong together
        if not top_level and len(groups) >= 2 and not one_app and depth < 2:
            for child in _subdirs(folder):
                consider(child, in_container, depth + 1)
            return
        if not in_container and not (real and has_dll):
            # Outside Program Files we need a real program (an .exe next to its .dll files);
            # a folder of installers, photos or documents is the user's own data.
            return
        main, desc = _pick_main(real or exes, name)
        if nameless and _matches_program([name, desc], nameless):
            return  # a registered program whose folder the registry does not record
        found.append(FolderApp(folder, name, "portable" if real else "leftover", main_exe=main,
                               description=desc, exes=exes[:50]))

    for root, is_container in roots:
        for child in _subdirs(root):
            if not is_container and os.path.basename(child).lower() in CONTAINERS:
                continue  # handled as its own root
            consider(child, is_container, 0)

    def finish(app: FolderApp) -> FolderApp | None:
        stats = measure(app.path, cancel=cancel)
        if stats.size < (MIN_PORTABLE if app.kind == "portable" else MIN_LEFTOVER):
            return None
        app.size, app.files, app.newest = stats.size, stats.files, stats.newest
        app.last_used = _last_used(app.path, app.exes, usage, prefetch)
        key = _norm(app.path)
        app.running = sorted({n for d, n in running if d == key or d.startswith(key + os.sep)})
        return app

    results: list[FolderApp] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, app in enumerate(pool.map(finish, found)):
            if progress:
                progress(f"{i + 1}/{len(found)}")
            if app is not None:
                results.append(app)
    return sorted(results, key=lambda a: a.size, reverse=True)


def delete_folder_app(app: FolderApp, recycle: bool = True) -> DeleteResult:
    """Remove a folder the user chose. Refuses protected folders and running programs."""
    check_deletable(app.path)
    still_running = [n for d, n in _running_dirs()
                     if d == _norm(app.path) or d.startswith(_norm(app.path) + os.sep)]
    if still_running:
        raise RuntimeError(", ".join(sorted(set(still_running))))
    if recycle:
        from send2trash import send2trash
        send2trash(app.path)
        result = DeleteResult(deleted=app.files)
    else:
        result = delete_tree(app.path)
    write_log([{"rule": "folder-app", "target": app.path, "freed": result.freed,
                "recycled": app.size if recycle else 0, "deleted": result.deleted, "failed": result.failed}])
    return result


def _subdirs(folder: str) -> list[str]:
    try:
        with os.scandir(folder) as it:
            return sorted(e.path for e in it
                          if e.is_dir(follow_symlinks=False) and not _hidden_or_system(e)
                          and e.name.lower() not in SKIP_NAMES and not e.name.startswith(("$", ".")))
    except OSError:
        return []


def _last_used(folder: str, exes: list[str], usage, prefetch) -> float | None:
    key = _norm(folder)
    best = None
    for path, (last, _count) in usage.items():
        if last and (path == key or path.startswith(key + os.sep)) and (best is None or last > best):
            best = last
    for exe in exes:
        last = prefetch.get(os.path.basename(exe).lower())
        if last and (best is None or last > best):
            best = last
    return best
