"""Disk usage map: how big every folder is, the largest files, and space by file type.

Walks a drive or folder once (in parallel per top-level folder) and keeps a
tree of folders. To keep memory low on big drives, each folder only keeps its
largest files as separate nodes; the rest are merged into one "small files"
node. Links and junctions are not followed, so nothing is counted twice
(except hard links, which Windows uses heavily in WinSxS).
"""

from __future__ import annotations

import fnmatch
import heapq
import json
import os
import stat
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache

from opensweep.core import mft
from opensweep.core.fsutil import Cancelled, is_link
from opensweep.core.platform import resource_path

FILES_PER_DIR = 30          # largest files kept per folder
MIN_FILE_NODE = 1024 ** 2   # files smaller than this are always merged
LARGEST = 300               # size of the global "largest files" list

TYPES = {
    "video": (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".rmvb", ".mpg", ".mpeg"),
    "image": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".tif", ".tiff", ".raw", ".cr2", ".nef",
              ".psd", ".svg", ".ico"),
    "audio": (".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg", ".wma", ".ape"),
    "document": (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".epub", ".wps", ".csv"),
    "archive": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".zst", ".cab"),
    "installer": (".exe", ".msi", ".msix", ".appx", ".apk", ".dmg", ".pkg"),
    "diskimage": (".iso", ".img", ".vhd", ".vhdx", ".vmdk", ".qcow2", ".wim", ".esd", ".gho"),
    "model": (".safetensors", ".gguf", ".ckpt", ".pt", ".pth", ".bin", ".onnx", ".h5", ".pb", ".tflite"),
    "program": (".dll", ".sys", ".so", ".jar", ".pak", ".asar", ".pyd", ".node"),
    "cache": (".tmp", ".temp", ".cache", ".log", ".dmp", ".etl", ".old", ".bak"),
}
EXT_TYPE = {ext: kind for kind, exts in TYPES.items() for ext in exts}


def file_type(name: str) -> str:
    return EXT_TYPE.get(os.path.splitext(name)[1].lower(), "other")


@dataclass(eq=False)
class Node:
    name: str
    path: str
    size: int = 0
    files: int = 0
    is_dir: bool = True
    mtime: float = 0.0
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None
    kind: str = ""        # "small" for merged small files, "unreadable" for space we could not see
    denied: bool = False

    @property
    def type(self) -> str:
        return "folder" if self.is_dir else (self.kind or file_type(self.name))


@dataclass
class DiskMap:
    root: Node
    largest: list[Node]
    types: dict[str, list[int]]   # type -> [bytes, count]
    files: int
    folders: int
    denied: int
    seconds: float
    used: int = 0                 # what the drive reports as used (whole drives only)


class _Walker:
    def __init__(self, cancel: threading.Event | None, progress):
        self.cancel = cancel
        self.progress = progress
        self.lock = threading.Lock()
        self.files = 0
        self.folders = 0
        self.denied = 0
        self.last_report = 0.0

    def walk(self, path: str, name: str) -> tuple[Node, list, dict]:
        """Iterative walk of one subtree. Returns (node, largest-files heap, type totals)."""
        largest: list = []
        types: dict[str, list[int]] = {}
        root = Node(name, path)
        stack = [root]
        order = []
        files = folders = denied = 0
        while stack:
            if self.cancel is not None and self.cancel.is_set():
                raise Cancelled
            node = stack.pop()
            order.append(node)
            folders += 1
            small_size = small_count = 0
            small_mtime = 0.0
            kept: list[Node] = []
            try:
                with os.scandir(node.path) as it:
                    entries = list(it)
            except OSError:
                node.denied = True
                denied += 1
                continue
            for entry in entries:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if is_link(st):
                    continue
                if stat.S_ISDIR(st.st_mode):
                    child = Node(entry.name, entry.path, parent=node)
                    node.children.append(child)
                    stack.append(child)
                    continue
                files += 1
                size = st.st_size
                ext_type = file_type(entry.name)
                bucket = types.setdefault(ext_type, [0, 0])
                bucket[0] += size
                bucket[1] += 1
                node.size += size
                node.files += 1
                if st.st_mtime > node.mtime:
                    node.mtime = st.st_mtime
                if size >= MIN_FILE_NODE:
                    f = Node(entry.name, entry.path, size, 1, False, st.st_mtime, parent=node)
                    kept.append(f)
                    if len(largest) < LARGEST:
                        heapq.heappush(largest, (size, id(f), f))
                    elif size > largest[0][0]:
                        heapq.heapreplace(largest, (size, id(f), f))
                else:
                    small_size += size
                    small_count += 1
                    small_mtime = max(small_mtime, st.st_mtime)
            kept.sort(key=lambda n: n.size, reverse=True)
            for extra in kept[FILES_PER_DIR:]:
                small_size += extra.size
                small_count += 1
                small_mtime = max(small_mtime, extra.mtime)
            node.children.extend(kept[:FILES_PER_DIR])
            if small_count:
                node.children.append(Node("", node.path, small_size, small_count, False, small_mtime,
                                          parent=node, kind="small"))
            now = time.monotonic()
            if self.progress and now - self.last_report > 0.25:
                with self.lock:
                    self.last_report = now
                    total = self.files + files
                self.progress(f"{total}|{node.path}")
        # Folder sizes: children were pushed after their parents, so walk the visit order backwards.
        for node in reversed(order):
            for child in node.children:
                if child.is_dir:
                    node.size += child.size
                    node.files += child.files
                    node.mtime = max(node.mtime, child.mtime)
            node.children.sort(key=lambda n: n.size, reverse=True)
        with self.lock:
            self.files += files
            self.folders += folders
            self.denied += denied
        return root, largest, types


def _is_drive_root(path: str) -> bool:
    p = path.rstrip("\\/")
    return len(p) == 2 and p[1] == ":"


def has_seek_penalty(path: str) -> bool | None:
    """True for spinning hard disks, False for SSDs, None if Windows won't say."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    letter = os.path.splitdrive(os.path.abspath(path))[0]
    if not letter:
        return None
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = wintypes.HANDLE
    handle = k32.CreateFileW(f"\\\\.\\{letter}", 0, 3, None, 3, 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        query = struct.pack("<II4x", 7, 0)  # StorageDeviceSeekPenaltyProperty, PropertyStandardQuery
        out = ctypes.create_string_buffer(12)
        returned = wintypes.DWORD()
        ok = k32.DeviceIoControl(wintypes.HANDLE(handle), 0x2D1400, query, len(query), out, 12,
                                 ctypes.byref(returned), None)
        return bool(out.raw[8]) if ok and returned.value >= 9 else None
    finally:
        k32.CloseHandle(wintypes.HANDLE(handle))


def scan(path: str, cancel: threading.Event | None = None, progress=None, workers: int = 8) -> DiskMap:
    """Map everything under `path`. `progress` gets "files|current folder" strings.

    Whole NTFS drives are read straight from the MFT when we're administrator
    (much faster, especially on hard disks); everything else is walked folder
    by folder, with fewer threads on hard disks so the head doesn't thrash.
    """
    path = os.path.abspath(path)
    if _is_drive_root(path):
        from opensweep.core.platform import is_admin
        if is_admin():
            try:
                return scan_mft(path, cancel, progress)
            except mft.MftUnavailable:
                pass
    if has_seek_penalty(path):
        workers = 1
    return scan_walk(path, cancel, progress, workers)


def scan_mft(path: str, cancel: threading.Event | None = None, progress=None) -> DiskMap:
    started = time.monotonic()
    rec = mft.read_records(path, cancel, progress)
    root = Node(path.rstrip("\\/"), path)
    nodes: dict[int, Node] = {mft.ROOT_RECORD: root}
    child_dirs: dict[int, list[int]] = {}
    files_of: dict[int, list[int]] = {}
    for r, parent in rec.parents.items():
        if r == mft.ROOT_RECORD:
            continue
        (child_dirs if r in rec.dirs else files_of).setdefault(parent, []).append(r)

    order = [root]
    queue = [mft.ROOT_RECORD]
    while queue:
        if cancel is not None and cancel.is_set():
            raise Cancelled
        r = queue.pop()
        node = nodes[r]
        for c in child_dirs.get(r, ()):
            if c in rec.reparse or c in nodes:
                continue  # junctions and symlinked folders: their contents live elsewhere
            name = rec.names[c]
            child = Node(name, os.path.join(node.path, name), parent=node,
                         mtime=mft.filetime_to_unix(rec.mtimes.get(c, 0)))
            node.children.append(child)
            nodes[c] = child
            queue.append(c)
            order.append(child)

    largest: list = []
    types: dict[str, list[int]] = {}
    files = 0
    for parent_rec, members in files_of.items():
        node = nodes.get(parent_rec)
        if node is None:
            continue  # files in folders we skipped (inside junctions) or orphans
        small_size = small_count = 0
        small_mtime = 0.0
        kept: list[Node] = []
        for f in members:
            if f in rec.reparse:
                continue
            size = rec.sizes.get(f, 0)
            name = rec.names[f]
            mtime = mft.filetime_to_unix(rec.mtimes.get(f, 0))
            files += 1
            bucket = types.setdefault(file_type(name), [0, 0])
            bucket[0] += size
            bucket[1] += 1
            node.size += size
            node.files += 1
            if mtime > node.mtime:
                node.mtime = mtime
            if size >= MIN_FILE_NODE:
                fn = Node(name, os.path.join(node.path, name), size, 1, False, mtime, parent=node)
                kept.append(fn)
                if len(largest) < LARGEST:
                    heapq.heappush(largest, (size, id(fn), fn))
                elif size > largest[0][0]:
                    heapq.heapreplace(largest, (size, id(fn), fn))
            else:
                small_size += size
                small_count += 1
                small_mtime = max(small_mtime, mtime)
        kept.sort(key=lambda n: n.size, reverse=True)
        for extra in kept[FILES_PER_DIR:]:
            small_size += extra.size
            small_count += 1
            small_mtime = max(small_mtime, extra.mtime)
        node.children.extend(kept[:FILES_PER_DIR])
        if small_count:
            node.children.append(Node("", node.path, small_size, small_count, False, small_mtime,
                                      parent=node, kind="small"))
    for node in reversed(order):
        for child in node.children:
            if child.is_dir:
                node.size += child.size
                node.files += child.files
                node.mtime = max(node.mtime, child.mtime)
        node.children.sort(key=lambda n: n.size, reverse=True)
    return _finish(root, largest, types, files, len(nodes), 0, started, path)


def _finish(root: Node, largest: list, types, files: int, folders: int, denied: int, started: float,
            path: str) -> DiskMap:
    used = 0
    if _is_drive_root(path) or os.path.dirname(path.rstrip("\\/")) == path.rstrip("\\/"):
        try:
            import psutil
            used = psutil.disk_usage(path).used
        except (ImportError, OSError):
            used = 0
        missing = used - root.size
        if missing > max(used * 0.01, 256 * 1024 ** 2):
            root.children.append(Node("", path, missing, 0, False, parent=root, kind="unreadable"))
            root.size += missing
    root.children.sort(key=lambda n: n.size, reverse=True)
    top = [n for _, _, n in heapq.nlargest(LARGEST, largest, key=lambda x: x[0])]
    return DiskMap(root, top, types, files, folders, denied, time.monotonic() - started, used)


def scan_walk(path: str, cancel: threading.Event | None = None, progress=None, workers: int = 8) -> DiskMap:
    started = time.monotonic()
    walker = _Walker(cancel, progress)
    root = Node(path.rstrip("\\/") or path, path)
    largest: list = []
    types: dict[str, list[int]] = {}

    try:
        entries = list(os.scandir(path))
    except OSError:
        entries = []
        root.denied = True
    subdirs, top_files = [], []
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if is_link(st):
            continue
        (subdirs if stat.S_ISDIR(st.st_mode) else top_files).append((entry, st))

    for entry, st in top_files:
        f = Node(entry.name, entry.path, st.st_size, 1, False, st.st_mtime, parent=root)
        root.children.append(f)
        root.files += 1
        bucket = types.setdefault(file_type(entry.name), [0, 0])
        bucket[0] += st.st_size
        bucket[1] += 1
        heapq.heappush(largest, (st.st_size, id(f), f))
    walker.files += len(top_files)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(walker.walk, e.path, e.name) for e, _ in subdirs]
        for fut in futures:
            node, heap, sub_types = fut.result()
            node.parent = root
            root.children.append(node)
            largest.extend(heap)
            for k, (size, count) in sub_types.items():
                bucket = types.setdefault(k, [0, 0])
                bucket[0] += size
                bucket[1] += count

    root.size = sum(c.size for c in root.children)
    root.files = sum(c.files for c in root.children)
    root.mtime = max((c.mtime for c in root.children), default=0)
    return _finish(root, largest, types, walker.files, walker.folders, walker.denied, started, path)


# --------------------------------------------------------------------------- explanations


GENERIC_PLACES = {"profile", "users", "downloads", "user_files"}


@dataclass
class Known:
    id: str
    safety: str       # keep | careful | user | clean
    page: str = ""
    where: str = ""   # for "in:<id>": the known folder this node is inside


@lru_cache(maxsize=1)
def _known_patterns() -> list[tuple[str, Known]]:
    try:
        data = json.loads(resource_path("data", "known_paths.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    patterns = []
    for entry in data.get("paths", []):
        known = Known(entry["id"], entry.get("safety", "careful"), entry.get("page", ""))
        for raw in entry["match"]:
            value = os.path.expanduser(os.path.expandvars(raw))
            if "%" in value:
                continue
            patterns.append((os.path.normcase(os.path.normpath(value)), known))
    return patterns


@lru_cache(maxsize=1)
def _type_safety() -> dict[str, str]:
    try:
        data = json.loads(resource_path("data", "known_paths.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("types", {})


def _matches(path: str, pattern: str) -> bool:
    if "*" not in pattern:
        return path == pattern
    p_parts, q_parts = path.split(os.sep), pattern.split(os.sep)
    if pattern.startswith("*" + os.sep):  # "*\steamapps": any folder with that name
        return fnmatch.fnmatch(p_parts[-1], q_parts[-1])
    return len(p_parts) == len(q_parts) and all(fnmatch.fnmatch(a, b) for a, b in zip(p_parts, q_parts))


_UNSET = object()


def explain(node: Node) -> Known | None:
    """The best explanation for a node: the node itself, else its nearest known ancestor
    (returned with safety inherited, id prefixed with 'in:'). Cached on the node."""
    cached = node.__dict__.get("_known", _UNSET)
    if cached is _UNSET:
        cached = node.__dict__["_known"] = _explain(node)
    return cached


def _explain(node: Node) -> Known | None:
    if node.kind == "unreadable":
        return Known("unreadable", "careful")
    if node.kind == "small":  # merged small files are as safe as the folder they are in
        parent = explain(node.parent) if node.parent is not None else None
        return Known("small", parent.safety if parent else "", parent.page if parent else "")
    current: Node | None = node
    while current is not None:
        path = os.path.normcase(os.path.normpath(current.path))
        for pattern, known in _known_patterns():
            if _matches(path, pattern):
                if current is node:
                    return known
                if not node.is_dir and known.id in GENERIC_PLACES:
                    # A video in Downloads is better explained by "it's a video" than "it's in your folder".
                    return Known("type:" + node.type, _type_safety().get(node.type, "careful"))
                return Known("in:" + known.id, known.safety, known.page, current.path)
        current = current.parent
    if not node.is_dir:
        return Known("type:" + node.type, _type_safety().get(node.type, "careful"))
    return None


def type_mix(node: Node, top: int = 3, minimum: float = 0.1, limit: int = 200_000) -> list[tuple[str, float]]:
    """What a folder mostly holds: (file type, share of the sized files), largest first.
    Merged small files and unreadable space are left out, so the shares describe what we can name."""
    sizes: dict[str, int] = {}
    stack, seen = [node], 0
    while stack and seen < limit:
        current = stack.pop()
        seen += 1
        if current.is_dir:
            stack.extend(current.children)
        elif current.kind == "":
            sizes[current.type] = sizes.get(current.type, 0) + current.size
    total = sum(sizes.values())
    if not total:
        return []
    ranked = sorted(sizes.items(), key=lambda kv: -kv[1])
    return [(k, v / total) for k, v in ranked[:top] if v / total >= minimum]


def quick_wins(root: Node, limit: int = 5, minimum: int = 200 * 1024 ** 2, max_depth: int = 8) -> list[tuple[Node, Known]]:
    """The biggest folders OpenSweep itself knows how to clean, for a "start here" list.
    Only exact matches count (a Temp folder, the Windows Update cache...), never "somewhere inside"."""
    found: list[tuple[Node, Known]] = []
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if node.size < minimum:
            continue
        known = explain(node) if node.parent is not None else None
        if known is not None and known.safety == "clean" and known.page and not known.id.startswith(("in:", "type:")):
            found.append((node, known))
            continue  # everything inside is covered by this folder
        if depth < max_depth:
            stack.extend((c, depth + 1) for c in node.children if c.is_dir)
    return sorted(found, key=lambda item: -item[0].size)[:limit]


def path_to(node: Node) -> list[Node]:
    chain = []
    while node is not None:
        chain.append(node)
        node = node.parent
    return list(reversed(chain))


def discount(m: DiskMap, node: Node) -> None:
    """Take a node that is about to be deleted out of the map-wide totals (types, largest files, counts).
    Merged small files carry no type, so their share comes off "other"."""
    stack = [node]
    while stack:
        current = stack.pop()
        if current.is_dir:
            stack.extend(current.children)
        elif current.kind == "":
            bucket = m.types.get(current.type)
            if bucket:
                bucket[0] = max(0, bucket[0] - current.size)
                bucket[1] = max(0, bucket[1] - 1)
        elif current.kind == "small":
            bucket = m.types.setdefault("other", [0, 0])
            bucket[0] = max(0, bucket[0] - current.size)
            bucket[1] = max(0, bucket[1] - current.files)
    prefix = node.path.rstrip("\\/") + os.sep
    m.largest = [f for f in m.largest if f is not node and not f.path.startswith(prefix)]
    m.files = max(0, m.files - node.files)


def remove_node(node: Node) -> None:
    """Take a deleted node out of the tree and subtract its size from its ancestors."""
    parent = node.parent
    if parent is None:
        return
    if node in parent.children:
        parent.children.remove(node)
    p = parent
    while p is not None:
        p.size -= node.size
        p.files -= node.files
        p = p.parent
