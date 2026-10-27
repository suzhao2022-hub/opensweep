"""PC checkup: turn raw scan results into a short list of findings anyone can act on.

Each finding says what was found, whether it is worth doing something about,
and which action fixes it. The score is only a summary for people who don't
want to read the details; it is not a precise measurement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import psutil

from opensweep.core import memory, software, startup
from opensweep.core.engine import RuleResult

GB = 1024 ** 3

# Finding levels, most urgent first.
FIX, CHECK, OK = "fix", "check", "ok"


@dataclass
class Finding:
    id: str
    level: str
    size: int = 0
    count: int = 0
    penalty: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class SystemFacts:
    """Everything the checkup needs besides the junk scan. Collected off the UI thread."""
    startup: list[startup.StartupEntry]
    programs: list[software.Program]
    apps: list[memory.AppUsage]
    disks: list[tuple[str, int, int]]  # (mount point, free, total)
    memory_percent: float


def read_disks() -> list[tuple[str, int, int]]:
    """(mount point, free, total) for every fixed drive, right now."""
    disks = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append((part.mountpoint, usage.free, usage.total))
    return disks


def collect_facts() -> SystemFacts:
    entries = startup.describe(startup.list_startup())
    programs = software.list_programs()
    apps = memory.list_apps()
    return SystemFacts(entries, programs, apps, read_disks(), psutil.virtual_memory().percent)


def analyze(results: list[RuleResult], facts: SystemFacts | None) -> list[Finding]:
    findings: list[Finding] = []

    safe = [x for r in results if r.rule.risk == "safe" and r.cleanable for x in r.targets]
    safe_size = sum(x.size for x in safe)
    blocked = [r for r in results if r.targets and r.blocked and r.rule.action == "delete"]
    findings.append(Finding("junk", FIX if safe_size >= 500 * 1024 ** 2 else OK, safe_size, len(safe),
                            min(30, int(safe_size / GB * 4)), {"blocked": len(blocked),
                                                               "blocked_size": sum(r.size for r in blocked)}))

    review = [r for r in results if r.rule.risk == "review" and r.rule.action == "delete" and r.size
              and r.rule.category != "ai"]
    review_size = sum(r.size for r in review)
    if review_size >= 1 * GB:
        findings.append(Finding("review", CHECK, review_size, len(review)))

    ai = [r for r in results if r.rule.category == "ai" and r.rule.action == "delete" and r.size]
    ai_size = sum(r.size for r in ai)
    if ai_size >= 2 * GB:
        findings.append(Finding("ai", CHECK, ai_size, sum(len(r.targets) for r in ai)))

    if facts is None:
        return _sorted(findings)

    root = os.environ.get("SystemDrive", "C:")
    for mount, free, total in facts.disks:
        ratio = free / total if total else 1
        is_system = mount.upper().startswith(root.upper())
        if (is_system and (ratio < 0.10 or free < 10 * GB)) or (not is_system and ratio < 0.05):
            findings.append(Finding(f"disk:{mount}", FIX if is_system else CHECK, free, 0,
                                    15 if is_system else 5, {"drive": mount.rstrip("\\/"), "total": total,
                                                             "system": is_system}))

    advised = [e for e in facts.startup if e.enabled and e.exists and e.advice == "disable"]
    findings.append(Finding("startup", FIX if advised else OK, 0, len(advised), min(15, len(advised) * 3),
                            {"names": [e.display_name for e in advised[:4]],
                             "enabled": sum(1 for e in facts.startup if e.enabled and e.exists)}))

    broken = [e for e in facts.startup if not e.exists and e.removable]
    if broken:
        findings.append(Finding("startup_broken", FIX, 0, len(broken), min(6, len(broken) * 2)))

    unused = [p for p in facts.programs if software.usage_verdict(p)[0] == "unused"]
    if unused:
        findings.append(Finding("unused", CHECK, sum(p.size for p in unused), len(unused),
                                min(10, len(unused) * 2), {"names": [p.name for p in unused[:4]]}))

    heavy = [a for a in facts.apps if a.kind == "background" and a.memory >= 300 * 1024 ** 2]
    if facts.memory_percent >= 80 and heavy:
        findings.append(Finding("memory", CHECK, sum(a.memory for a in heavy), len(heavy), 5,
                                {"percent": int(facts.memory_percent),
                                 "names": [a.display_name for a in heavy[:3]]}))
    elif facts.memory_percent < 80:
        findings.append(Finding("memory", OK, 0, 0, 0, {"percent": int(facts.memory_percent)}))
    return _sorted(findings)


def _sorted(findings: list[Finding]) -> list[Finding]:
    order = {FIX: 0, CHECK: 1, OK: 2}
    return sorted(findings, key=lambda f: (order[f.level], -f.penalty, -f.size))


def score(findings: list[Finding]) -> int:
    return max(0, 100 - sum(f.penalty for f in findings))


def grade(value: int) -> str:
    """good | fair | poor"""
    return "good" if value >= 85 else ("fair" if value >= 60 else "poor")
