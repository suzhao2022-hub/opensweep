"""Scanning and cleaning, built on rules + fsutil + providers."""

from __future__ import annotations

import fnmatch
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from opensweep.core import providers
from opensweep.core.fsutil import (Cancelled, DeleteResult, Entry, Inspection, Stats, delete_tree, inspect,
                                   measure)
from opensweep.core.platform import is_admin, log_dir
from opensweep.core.rules import Rule
from opensweep.core.safety import UnsafePathError, check_deletable, is_protected

Progress = Callable[[str], None]


@dataclass
class Target:
    """One thing the user can tick: a folder, a model, the Recycle Bin..."""

    rule: Rule
    path: str
    label: str
    size: int = 0
    files: int = 0
    newest: float = 0.0
    kind: str = "path"  # path | multi | ollama | recycle_bin
    keep_root: bool = False
    allowed_root: str = ""
    parts: list[str] = field(default_factory=list)  # kind "multi": folders emptied together

    @property
    def key(self) -> str:
        return f"{self.rule.id}|{self.kind}|{self.path}|{self.label}"


@dataclass
class RuleResult:
    rule: Rule
    targets: list[Target] = field(default_factory=list)
    blocked: str | None = None  # None | "admin" | "running"
    running_apps: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def size(self) -> int:
        return sum(t.size for t in self.targets)

    @property
    def cleanable(self) -> bool:
        return self.blocked is None and self.rule.action == "delete" and self.size > 0


@dataclass
class CleanedItem:
    target: Target
    freed: int = 0
    recycled: int = 0
    deleted: int = 0
    failed: int = 0
    error: str = ""


@dataclass
class CleanReport:
    freed: int = 0
    recycled: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    items: list[CleanedItem] = field(default_factory=list)


def inspect_target(t: Target, cancel: threading.Event | None = None) -> Inspection:
    """What is inside a target, for showing the user before they clean it."""
    if t.kind == "ollama":
        entries = []
        for model in providers.list_ollama_models():
            if model.name == t.label:
                for digest in model.blobs:
                    blob = providers.ollama_models_dir() / "blobs" / digest.replace(":", "-")
                    try:
                        st = blob.stat()
                    except OSError:
                        continue
                    entries.append(Entry(blob.name, str(blob), st.st_size, 1, False, st.st_mtime))
        entries.sort(key=lambda e: e.size, reverse=True)
        return Inspection(entries, [], Stats(t.size, t.files, t.newest))
    if t.kind == "recycle_bin" or not t.path:
        return Inspection([], [], Stats(t.size, t.files, t.newest))
    if t.kind == "multi":
        entries, types, total = [], {}, Stats()
        for part in t.parts:
            info = inspect(part, t.rule.min_age_days, cancel=cancel)
            total.add(info.total)
            for ext, size, count in info.types:
                bucket = types.setdefault(ext, [0, 0])
                bucket[0] += size
                bucket[1] += count
            entries.append(Entry(os.path.relpath(part, t.path), part, info.total.size, info.total.files, True,
                                 info.total.newest))
        entries.sort(key=lambda e: e.size, reverse=True)
        return Inspection(entries, sorted(((e, v[0], v[1]) for e, v in types.items()), key=lambda x: -x[1]), total)
    return inspect(t.path, t.rule.min_age_days, cancel=cancel)


def _covered(path: str, covered: set[str]) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    return any(p == c or p.startswith(c + os.sep) or c.startswith(p + os.sep) for c in covered)


def running_process_names() -> set[str]:
    try:
        import psutil
    except ImportError:
        return set()
    names = set()
    for proc in psutil.process_iter(["name"]):
        name = proc.info.get("name")
        if name:
            names.add(name.lower())
    return names


def _huggingface_label(name: str) -> str:
    kind, _, rest = name.partition("--")
    repo = rest.replace("--", "/") if rest else name
    return f"{repo}  [{kind.rstrip('s')}]" if rest else name


def _children(root: str, depth: int, patterns: list[str]) -> list[str]:
    level = [root]
    for i in range(depth):
        nxt = []
        for folder in level:
            try:
                with os.scandir(folder) as it:
                    for entry in it:
                        if i == depth - 1 and patterns and not any(
                                fnmatch.fnmatch(entry.name, p) for p in patterns):
                            continue
                        if i < depth - 1 and not entry.is_dir(follow_symlinks=False):
                            continue
                        nxt.append(entry.path)
            except OSError:
                continue
        level = nxt
    return sorted(level)


def scan_rule(rule: Rule, running: set[str] | None = None, admin: bool | None = None,
              cancel: threading.Event | None = None, covered: set[str] | None = None) -> RuleResult:
    """Measure one rule. `covered`: folders other rules already handle (for fallback rules)."""
    result = RuleResult(rule)
    admin = is_admin() if admin is None else admin
    if rule.requires_admin and not admin:
        result.blocked = "admin"
    apps = [a for a in rule.close_apps if a.lower() in (running or set())]
    if apps and result.blocked is None:
        result.blocked, result.running_apps = "running", apps

    try:
        if rule.provider == "recycle_bin":
            size, count = providers.recycle_bin_stats()
            if count:
                result.targets.append(Target(rule, "", rule.text("name", "en"), size, count, kind="recycle_bin"))
            return result
        if rule.provider == "ollama":
            for model in providers.list_ollama_models():
                result.targets.append(Target(rule, str(model.manifest), model.name, model.size, len(model.blobs),
                                             model.modified, kind="ollama"))
            return result
        if rule.provider == "chromium":
            for app in providers.find_chromium_apps():
                if covered and any(_covered(c, covered) for c in app.caches):
                    continue  # this app has its own rule (with its own "close the app first" check)
                stats = Stats()
                for part in app.caches:
                    stats.add(measure(part, cancel=cancel))
                if stats.size:
                    result.targets.append(Target(rule, app.root, app.label, stats.size, stats.files, stats.newest,
                                                 kind="multi", keep_root=True, allowed_root=app.root,
                                                 parts=list(app.caches)))
            result.targets.sort(key=lambda t: t.size, reverse=True)
            return result

        for root in rule.resolve_paths():
            if rule.itemize:
                for child in _children(root, rule.itemize, rule.item_patterns):
                    if is_protected(child):
                        continue
                    stats = measure(child, rule.min_age_days, cancel)
                    if stats.size == 0 and stats.files == 0:
                        continue
                    name = os.path.relpath(child, root)
                    label = _huggingface_label(name) if rule.label_style == "huggingface" else name
                    result.targets.append(Target(rule, child, label, stats.size, stats.files, stats.newest,
                                                 allowed_root=root))
            else:
                keep_root = rule.contents_only and os.path.isdir(root)
                if not keep_root and is_protected(root):
                    continue
                stats = measure(root, rule.min_age_days, cancel)
                if stats.size == 0 and stats.files == 0:
                    continue
                result.targets.append(Target(rule, root, root, stats.size, stats.files, stats.newest,
                                             keep_root=keep_root, allowed_root=root))
    except Cancelled:
        raise
    except Exception as exc:  # a broken rule must never break the whole scan
        result.error = str(exc)
    result.targets.sort(key=lambda t: t.size, reverse=True)
    return result


def scan(rules: list[Rule], progress: Progress | None = None,
         cancel: threading.Event | None = None, workers: int = 8) -> list[RuleResult]:
    running = running_process_names()
    admin = is_admin()
    results: list[RuleResult] = []
    normal = [r for r in rules if not r.fallback]
    fallback = [r for r in rules if r.fallback]

    def run(batch, covered=None):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(scan_rule, r, running, admin, cancel, covered): r for r in batch}
            for fut in futures:
                if progress:
                    progress(futures[fut].id)
                try:
                    results.append(fut.result())
                except Cancelled:
                    for f in futures:
                        f.cancel()
                    raise

    run(normal)
    if fallback:
        covered = {os.path.normcase(os.path.abspath(p)) for res in results for t in res.targets
                   if t.kind in ("path", "multi") for p in ([t.path] + t.parts) if p}
        run(fallback, covered)
    return results


# ---------------------------------------------------------------- cleaning


def _delete_target(t: Target, use_recycle_bin: bool, cancel: threading.Event | None) -> tuple[DeleteResult, int]:
    """Returns (permanently deleted result, bytes moved to Recycle Bin)."""
    if t.kind == "recycle_bin":
        return providers.empty_recycle_bin(), 0
    if t.kind == "ollama":
        return providers.remove_ollama_model(t.label), 0
    if t.kind == "multi":
        total = DeleteResult()
        for part in t.parts:
            try:
                check_deletable(part, t.allowed_root)
            except UnsafePathError:
                continue
            total.add(delete_tree(part, t.rule.min_age_days, keep_root=True, cancel=cancel))
        return total, 0

    if t.keep_root:
        check_deletable(t.path + os.sep + "x", t.allowed_root)  # sanity: root resolves where expected
        total = DeleteResult()
        try:
            children = [e.path for e in os.scandir(t.path)]
        except OSError:
            return total, 0
        for child in children:
            try:
                check_deletable(child, t.allowed_root)
            except UnsafePathError:
                continue
            total.add(delete_tree(child, t.rule.min_age_days, cancel=cancel))
        return total, 0

    check_deletable(t.path, t.allowed_root or None)
    if use_recycle_bin and t.rule.risk == "review":
        from send2trash import send2trash
        send2trash(t.path)
        return DeleteResult(deleted=t.files), t.size
    return delete_tree(t.path, t.rule.min_age_days, cancel=cancel), 0


def clean(targets: list[Target], use_recycle_bin: bool = True, progress: Progress | None = None,
          cancel: threading.Event | None = None) -> CleanReport:
    report = CleanReport()
    log_entries = []
    for t in targets:
        if cancel is not None and cancel.is_set():
            break
        if t.rule.action != "delete":
            continue
        if progress:
            progress(t.label)
        try:
            res, recycled = _delete_target(t, use_recycle_bin, cancel)
        except Cancelled:
            break
        except UnsafePathError as exc:
            report.skipped.append(str(exc))
            report.items.append(CleanedItem(t, error=str(exc)))
            continue
        except Exception as exc:
            report.errors.append(f"{t.label}: {exc}")
            report.items.append(CleanedItem(t, error=str(exc)))
            continue
        report.items.append(CleanedItem(t, res.freed, recycled, res.deleted, res.failed))
        report.freed += res.freed
        report.recycled += recycled
        report.deleted += res.deleted
        report.failed += res.failed
        log_entries.append({"rule": t.rule.id, "target": t.path or t.label, "freed": res.freed,
                            "recycled": recycled, "deleted": res.deleted, "failed": res.failed})
    write_log(log_entries)
    return report


def write_log(entries: list[dict]) -> None:
    if not entries:
        return
    try:
        path = log_dir() / f"clean-{time.strftime('%Y-%m')}.jsonl"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps({"time": stamp, **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass
