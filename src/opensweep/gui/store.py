"""Shared scan state: one scan feeds the home page and both cleanup pages."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from opensweep.core import engine
from opensweep.core.rules import Rule
from opensweep.gui.workers import run_task
from opensweep.i18n import current


class ScanStore(QObject):
    busy_changed = Signal(bool)
    progress = Signal(str)          # already localized text
    scan_finished = Signal()
    clean_finished = Signal(object)  # engine.CleanReport
    failed = Signal(str)

    def __init__(self, rules: list[Rule]):
        super().__init__()
        self.rules = {r.id: r for r in rules}
        self.results: dict[str, engine.RuleResult] = {}
        self.scanned = False
        self.busy = False
        self._cancel: threading.Event | None = None

    # -------------------------------------------------------------- queries

    def results_for(self, categories: tuple[str, ...] | None = None) -> list[engine.RuleResult]:
        return [r for r in self.results.values()
                if r.targets and (categories is None or r.rule.category in categories)]

    def safe_targets(self) -> list[engine.Target]:
        return [t for r in self.results.values() if r.rule.risk == "safe" and r.cleanable for t in r.targets]

    def safe_size(self) -> int:
        return sum(t.size for t in self.safe_targets())

    def review_size(self, categories: tuple[str, ...]) -> int:
        return sum(r.size for r in self.results_for(categories)
                   if r.rule.risk == "review" and r.rule.action == "delete")

    def category_size(self, categories: tuple[str, ...]) -> int:
        return sum(r.size for r in self.results_for(categories) if r.rule.action == "delete")

    def blocked_count(self) -> int:
        return sum(1 for r in self.results.values() if r.targets and r.blocked)

    # -------------------------------------------------------------- actions

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.busy_changed.emit(busy)

    def cancel(self) -> None:
        if self._cancel:
            self._cancel.set()

    def scan(self, rule_ids: list[str] | None = None) -> None:
        if self.busy:
            return
        rules = [self.rules[i] for i in rule_ids if i in self.rules] if rule_ids else list(self.rules.values())
        self._cancel = threading.Event()
        self._set_busy(True)

        def on_progress(rule_id: str) -> None:
            rule = self.rules.get(rule_id)
            self.progress.emit(rule.text("name", current()) if rule else rule_id)

        def done(results: list[engine.RuleResult]) -> None:
            if rule_ids is None:
                self.results = {}
            for res in results:
                self.results[res.rule.id] = res
            self.scanned = True
            self._set_busy(False)
            self.scan_finished.emit()

        run_task(engine.scan, rules, cancel=self._cancel, on_done=done, on_error=self._error,
                 on_progress=on_progress)

    def clean(self, targets: list[engine.Target], use_recycle_bin: bool) -> None:
        if self.busy or not targets:
            return
        self._cancel = threading.Event()
        self._set_busy(True)
        affected = sorted({t.rule.id for t in targets})

        def done(report: engine.CleanReport) -> None:
            self._set_busy(False)
            self.clean_finished.emit(report)
            self.scan(affected)

        run_task(engine.clean, targets, use_recycle_bin, cancel=self._cancel, on_done=done,
                 on_error=self._error, on_progress=self.progress.emit)

    def _error(self, message: str) -> None:
        self._set_busy(False)
        if message.startswith("Cancelled"):
            self.scan_finished.emit()
        else:
            self.failed.emit(message)
