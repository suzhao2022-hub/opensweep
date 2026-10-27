"""Command line interface: `opensweep-cli scan`, `opensweep-cli clean --safe`."""

from __future__ import annotations

import argparse
import json
import sys

from opensweep import __version__
from opensweep.core import engine
from opensweep.core.rules import load_rules
from opensweep.core.units import format_size


def _select(results: list[engine.RuleResult], args) -> list[engine.Target]:
    wanted = set(args.rule or [])
    targets = []
    for res in results:
        if not res.cleanable:
            continue
        if wanted and res.rule.id not in wanted:
            continue
        if not wanted and res.rule.risk != "safe":
            continue
        targets.extend(res.targets)
    return targets


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="opensweep-cli", description="OpenSweep disk cleaner")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="show what can be cleaned")
    p_scan.add_argument("--json", action="store_true", help="machine readable output")
    p_scan.add_argument("--category", action="append", help="only these categories")

    p_clean = sub.add_parser("clean", help="clean safe items (or the rules given with --rule)")
    p_clean.add_argument("--rule", action="append", help="rule id to clean (repeatable)")
    p_clean.add_argument("--dry-run", action="store_true", help="only show what would be deleted")
    p_clean.add_argument("--no-recycle-bin", action="store_true", help="delete review items permanently")
    p_clean.add_argument("--category", action="append", help="only these categories")

    p_report = sub.add_parser("startup-report", help="read-only startup diagnostic as JSON")
    p_report.add_argument("--output", help="write the report to this file instead of stdout")
    sub.add_parser("rules", help="list rules and validate rule files")
    args = parser.parse_args(argv)

    if args.command == "startup-report":
        from pathlib import Path
        from opensweep.core.boottrace import collect
        text = collect().to_json()
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
    rules, errors = load_rules()
    if args.command == "rules":
        for r in rules:
            print(f"{r.id:28} {r.category:8} {r.risk:7} {r.name['en']}")
        for e in errors:
            print("ERROR:", e, file=sys.stderr)
        return 1 if errors else 0

    if args.category:
        rules = [r for r in rules if r.category in args.category]
    results = engine.scan(rules)

    if args.command == "scan":
        if args.json:
            out = [{"id": r.rule.id, "category": r.rule.category, "risk": r.rule.risk, "action": r.rule.action,
                    "size": r.size, "blocked": r.blocked,
                    "targets": [{"label": t.label, "path": t.path, "size": t.size} for t in r.targets]}
                   for r in results if r.targets]
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 0
        total = 0
        for r in sorted(results, key=lambda r: r.size, reverse=True):
            if not r.targets:
                continue
            flag = f" [{r.blocked}]" if r.blocked else ""
            flag += " [report only]" if r.rule.action == "report" else ""
            print(f"{format_size(r.size):>10}  {r.rule.risk:6} {r.rule.id}{flag}")
            if r.rule.risk == "safe" and r.cleanable:
                total += r.size
        print(f"\nSafe to clean now: {format_size(total)}")
        return 0

    targets = _select(results, args)
    if args.dry_run:
        for t in targets:
            print(f"{format_size(t.size):>10}  {t.rule.id}  {t.path or t.label}")
        print(f"\nWould free about {format_size(sum(t.size for t in targets))}")
        return 0
    report = engine.clean(targets, use_recycle_bin=not args.no_recycle_bin)
    print(f"Freed {format_size(report.freed)}, moved to Recycle Bin {format_size(report.recycled)}, "
          f"{report.deleted} files deleted, {report.failed} skipped (in use).")
    for e in report.errors + report.skipped:
        print("  !", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
