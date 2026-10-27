"""File system walking and deleting that never follows links and tolerates locked files."""

from __future__ import annotations

import os
import stat
import threading
import time
from dataclasses import dataclass

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class Cancelled(Exception):
    pass


@dataclass
class Stats:
    size: int = 0
    files: int = 0
    newest: float = 0.0

    def add(self, other: "Stats") -> None:
        self.size += other.size
        self.files += other.files
        self.newest = max(self.newest, other.newest)


@dataclass
class DeleteResult:
    freed: int = 0
    deleted: int = 0
    failed: int = 0

    def add(self, other: "DeleteResult") -> None:
        self.freed += other.freed
        self.deleted += other.deleted
        self.failed += other.failed


def is_link(st: os.stat_result) -> bool:
    """True for symlinks and Windows junctions / other reparse points."""
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled


def _old_enough(st: os.stat_result, cutoff: float) -> bool:
    return cutoff <= 0 or st.st_mtime < cutoff


def cutoff_for(min_age_days: int) -> float:
    return time.time() - min_age_days * 86400 if min_age_days > 0 else 0.0


def measure(path: str, min_age_days: int = 0, cancel: threading.Event | None = None) -> Stats:
    """Total size of `path` (file or folder). Links are not followed or counted."""
    cutoff = cutoff_for(min_age_days)
    result = Stats()
    try:
        st = os.lstat(path)
    except OSError:
        return result
    if is_link(st):
        return result
    if not stat.S_ISDIR(st.st_mode):
        if _old_enough(st, cutoff):
            result.size, result.files, result.newest = st.st_size, 1, st.st_mtime
        return result
    stack = [path]
    while stack:
        _check(cancel)
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        est = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_link(est):
                        continue
                    if stat.S_ISDIR(est.st_mode):
                        stack.append(entry.path)
                    elif _old_enough(est, cutoff):
                        result.size += est.st_size
                        result.files += 1
                        if est.st_mtime > result.newest:
                            result.newest = est.st_mtime
        except OSError:
            continue
    return result


@dataclass
class Entry:
    name: str
    path: str
    size: int
    files: int
    is_dir: bool
    newest: float


@dataclass
class Inspection:
    """What is inside a cleanup target: its biggest children and file types."""

    entries: list[Entry]
    types: list[tuple[str, int, int]]  # (extension, bytes, file count), biggest first
    total: Stats
    oldest: float = 0.0
    more: int = 0  # children not listed because of `limit`


def _walk_types(path: str, cutoff: float, types: dict[str, list[int]], stats: Stats, oldest: list[float],
                cancel: threading.Event | None) -> None:
    stack = [path]
    while stack:
        _check(cancel)
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        est = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_link(est):
                        continue
                    if stat.S_ISDIR(est.st_mode):
                        stack.append(entry.path)
                    elif _old_enough(est, cutoff):
                        _count_file(entry.name, est, types, stats, oldest)
        except OSError:
            continue


def _count_file(name: str, st: os.stat_result, types: dict[str, list[int]], stats: Stats,
                oldest: list[float]) -> None:
    ext = os.path.splitext(name)[1].lower() or ""
    if len(ext) > 12:
        ext = ""
    bucket = types.setdefault(ext, [0, 0])
    bucket[0] += st.st_size
    bucket[1] += 1
    stats.size += st.st_size
    stats.files += 1
    stats.newest = max(stats.newest, st.st_mtime)
    if not oldest[0] or st.st_mtime < oldest[0]:
        oldest[0] = st.st_mtime


def inspect(path: str, min_age_days: int = 0, limit: int = 200,
            cancel: threading.Event | None = None) -> Inspection:
    """List the children of `path` with their sizes, plus a breakdown by file type."""
    cutoff = cutoff_for(min_age_days)
    types: dict[str, list[int]] = {}
    total = Stats()
    oldest = [0.0]
    entries: list[Entry] = []
    try:
        st = os.lstat(path)
    except OSError:
        return Inspection([], [], total)
    if is_link(st):
        return Inspection([], [], total)
    if not stat.S_ISDIR(st.st_mode):
        if _old_enough(st, cutoff):
            _count_file(os.path.basename(path), st, types, total, oldest)
            entries.append(Entry(os.path.basename(path), path, st.st_size, 1, False, st.st_mtime))
    else:
        try:
            children = list(os.scandir(path))
        except OSError:
            children = []
        for child in children:
            _check(cancel)
            try:
                cst = child.stat(follow_symlinks=False)
            except OSError:
                continue
            if is_link(cst):
                continue
            if stat.S_ISDIR(cst.st_mode):
                sub = Stats()
                _walk_types(child.path, cutoff, types, sub, oldest, cancel)
                total.add(sub)
                if sub.files:
                    entries.append(Entry(child.name, child.path, sub.size, sub.files, True, sub.newest))
            elif _old_enough(cst, cutoff):
                before = total.size
                _count_file(child.name, cst, types, total, oldest)
                entries.append(Entry(child.name, child.path, total.size - before, 1, False, cst.st_mtime))
    entries.sort(key=lambda e: e.size, reverse=True)
    type_list = sorted(((ext, v[0], v[1]) for ext, v in types.items()), key=lambda x: x[1], reverse=True)
    return Inspection(entries[:limit], type_list, total, oldest[0], max(0, len(entries) - limit))


def _unlink(path: str) -> bool:
    try:
        os.unlink(path)
        return True
    except PermissionError:
        try:
            os.chmod(path, stat.S_IWRITE)
            os.unlink(path)
            return True
        except OSError:
            return False
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _remove_link(path: str, st: os.stat_result) -> None:
    """Remove a symlink/junction itself, never what it points to."""
    try:
        if stat.S_ISDIR(st.st_mode) or (getattr(st, "st_file_attributes", 0) & 0x10):
            os.rmdir(path)
        else:
            os.unlink(path)
    except OSError:
        pass


def delete_tree(path: str, min_age_days: int = 0, keep_root: bool = False,
                cancel: threading.Event | None = None) -> DeleteResult:
    """Delete a file or folder, skipping anything locked. With `keep_root`, only empty it.

    With `min_age_days`, only files older than that are removed (and folders that
    end up empty).
    """
    cutoff = cutoff_for(min_age_days)
    result = DeleteResult()
    try:
        st = os.lstat(path)
    except OSError:
        return result
    if is_link(st):
        if not keep_root:
            _remove_link(path, st)
        return result
    if not stat.S_ISDIR(st.st_mode):
        if _old_enough(st, cutoff):
            if _unlink(path):
                result.freed, result.deleted = st.st_size, 1
            else:
                result.failed = 1
        return result

    # Iterative post-order walk: collect directories, delete files as we go.
    dirs: list[str] = []
    stack = [path]
    while stack:
        _check(cancel)
        current = stack.pop()
        dirs.append(current)
        try:
            entries = list(os.scandir(current))
        except OSError:
            result.failed += 1
            continue
        for entry in entries:
            try:
                est = entry.stat(follow_symlinks=False)
            except OSError:
                result.failed += 1
                continue
            if is_link(est):
                _remove_link(entry.path, est)
            elif stat.S_ISDIR(est.st_mode):
                stack.append(entry.path)
            elif _old_enough(est, cutoff):
                if _unlink(entry.path):
                    result.freed += est.st_size
                    result.deleted += 1
                else:
                    result.failed += 1
    for d in reversed(dirs):
        if keep_root and d == path:
            continue
        try:
            os.rmdir(d)  # only succeeds when empty, which is what we want
        except OSError:
            pass
    return result
