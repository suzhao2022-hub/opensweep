"""Last line of defence: paths OpenSweep must never delete, whatever a rule says.

Every deletion goes through `check_deletable`. Rules are data that anyone can
contribute, so this module must not trust them.
"""

from __future__ import annotations

import os
from pathlib import Path

from opensweep.core.platform import IS_WINDOWS


def _norm(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path))).rstrip("\\/")


def _env_path(name: str, *parts: str) -> str | None:
    value = os.environ.get(name)
    return _norm(os.path.join(value, *parts)) if value else None


def _protected_exact() -> set[str]:
    """Folders that may be cleaned *inside* (via allowed rules) but never removed themselves."""
    home = Path.home()
    paths = {
        _norm(home),
        _norm(home / ".cache"),
        _norm(home / ".config"),
        _norm(home / ".local"),
        _norm(home / ".local" / "share"),
    }
    if IS_WINDOWS:
        for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                     "ProgramData", "APPDATA", "LOCALAPPDATA", "PUBLIC", "TEMP", "TMP"):
            p = _env_path(name)
            if p:
                paths.add(p)
        local = os.environ.get("LOCALAPPDATA")
        if local:
            paths.add(_norm(os.path.join(local, "Temp")))
            paths.add(_norm(os.path.join(local, "Packages")))
            paths.add(_norm(os.path.join(local, "Programs")))
        users = Path(os.environ.get("SystemDrive", "C:") + "\\") / "Users"
        paths.add(_norm(users))
    else:
        paths.update(_norm(p) for p in ("/", "/usr", "/etc", "/var", "/opt", "/home", "/Users",
                                         "/Library", "/System", "/Applications", "/tmp"))
    paths.discard("")
    return paths


def _protected_trees() -> set[str]:
    """Folders whose whole subtree is off limits."""
    home = Path.home()
    trees = {_norm(home / name) for name in
             ("Documents", "Desktop", "Pictures", "Videos", "Music", "OneDrive", ".ssh", ".gnupg")}
    if IS_WINDOWS:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        for sub in ("System32", "SysWOW64", "WinSxS", "Boot", "servicing", "Fonts",
                    "assembly", "Microsoft.NET", "SystemApps", "Installer"):
            trees.add(_norm(os.path.join(root, sub)))
        drive = os.environ.get("SystemDrive", "C:")
        for sub in ("Recovery", "System Volume Information", "$Recycle.Bin", "Boot"):
            trees.add(_norm(os.path.join(drive + "\\", sub)))
    else:
        trees.update(_norm(p) for p in ("/bin", "/sbin", "/boot", "/lib", "/lib64", "/usr", "/etc",
                                         "/System", "/Library/Apple", "/private/etc"))
    return trees


_EXACT = _protected_exact()
_TREES = _protected_trees()


class UnsafePathError(Exception):
    pass


def is_drive_root(path: str) -> bool:
    p = os.path.abspath(path)
    return os.path.dirname(p.rstrip("\\/")) == p.rstrip("\\/") or p in ("/",) or (
        len(p.rstrip("\\/")) == 2 and p[1] == ":")


def is_protected(path: str | os.PathLike) -> bool:
    p = _norm(path)
    if not p or is_drive_root(p) or p in _EXACT:
        return True
    for tree in _TREES:
        if p == tree or p.startswith(tree + os.sep):
            return True
    # Anything that is a parent of a protected folder is protected too.
    for exact in _EXACT | _TREES:
        if exact.startswith(p + os.sep):
            return True
    return False


def check_deletable(path: str | os.PathLike, allowed_root: str | os.PathLike | None = None) -> None:
    """Raise UnsafePathError unless `path` is safe to delete.

    If `allowed_root` is given, `path` must be inside it (or equal to it) after
    resolving `..` segments. Symlinks are not resolved on purpose: we delete the
    link, never its target.
    """
    if is_protected(path):
        raise UnsafePathError(f"protected path: {path}")
    if allowed_root is not None:
        p, root = _norm(path), _norm(allowed_root)
        if p != root and not p.startswith(root + os.sep):
            raise UnsafePathError(f"{path} is outside {allowed_root}")
