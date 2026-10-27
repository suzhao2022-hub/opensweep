"""Home: a PC checkup. Finds problems, explains them in plain words, fixes them on request,
and shows how much better things are afterwards."""

from __future__ import annotations

import psutil
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea, QVBoxLayout, QWidget

from opensweep.core import checkup, startup
from opensweep.core.checkup import CHECK, FIX, OK, Finding
from opensweep.core.platform import IS_WINDOWS, is_admin, relaunch_as_admin
from opensweep.core.units import format_size
from opensweep.gui import theme
from opensweep.gui.dialogs import CleanConfirmDialog, CleanResultDialog
from opensweep.gui.store import ScanStore
from opensweep.gui.widgets import (Card, Dot, PageHeader, StackedBar, button, confirm, error, icon_label, label,
                                   page_layout)
from opensweep.gui.workers import run_task
from opensweep.i18n import t
from opensweep.settings import Settings

AI = ("ai",)
OTHER = ("system", "browser", "dev", "apps")
ALL = ("system", "browser", "ai", "dev", "apps")
LEVEL_STYLE = {FIX: ("warning", "review"), CHECK: ("info", "accent"), OK: ("check", "safe")}


class ScoreRing(QWidget):
    def __init__(self, size: int = 132):
        super().__init__()
        self.value: int | None = None
        self.setFixedSize(size, size)

    def set_value(self, value: int | None) -> None:
        self.value = value
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width = 11
        rect = QRectF(self.rect()).adjusted(width, width, -width, -width)
        filled = theme.S["hero"] == "filled"  # ring sits on the accent-colored hero card
        p.setPen(QPen(QColor(theme.C["ring_track"] if filled else theme.C["bar"]), width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 0, 360 * 16)
        if self.value is not None:
            grade = checkup.grade(self.value)
            color = theme.C["ring_arc"] if filled and grade == "good" else \
                theme.C[{"good": "safe", "fair": "review", "poor": "danger"}[grade]]
            p.setPen(QPen(QColor(color), width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, 90 * 16, -int(360 * 16 * self.value / 100))
        p.setPen(QColor(theme.C["hero_text"]))
        font = QFont(self.font())
        font.setPixelSize(int(self.height() * 0.3))
        font.setBold(True)
        p.setFont(font)
        p.drawText(self.rect().adjusted(0, -8, 0, -8), Qt.AlignCenter, "—" if self.value is None else str(self.value))
        font.setPixelSize(int(self.height() * 0.1))
        font.setBold(False)
        p.setFont(font)
        p.setPen(QColor(theme.C["hero_muted"]))
        p.drawText(self.rect().adjusted(0, int(self.height() * 0.3), 0, 0), Qt.AlignCenter, t("home.score"))
        p.end()


class FindingRow(QFrame):
    def __init__(self, level: str, title: str, detail: str, actions: list[QWidget]):
        super().__init__()
        self.setObjectName("findingRow")
        self.setStyleSheet(f"QFrame#findingRow {{ border-bottom: 1px solid {theme.C['border']}; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 12, 4, 12)
        row.setSpacing(14)
        glyph, color = LEVEL_STYLE[level]
        if theme.S["finding"] == "circle":  # icon in a soft colored circle (fresh skin)
            soft = {"review": "review_soft", "accent": "accent_soft", "safe": "safe_soft"}[color]
            bubble = QLabel()
            bubble.setFixedSize(34, 34)
            bubble.setAlignment(Qt.AlignCenter)
            bubble.setPixmap(theme.glyph_pixmap(glyph, theme.C[color], 18))
            bubble.setStyleSheet(f"background: {theme.C[soft]}; border-radius: 17px;")
            row.addWidget(bubble, 0, Qt.AlignTop)
        else:
            row.addWidget(icon_label(glyph, theme.C[color], 22), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(label(title, "section", wrap=True))
        if detail:
            col.addWidget(label(detail, "muted", wrap=True))
        row.addLayout(col, 1)
        for w in actions:
            row.addWidget(w, 0, Qt.AlignVCenter)


class HomePage(QWidget):
    open_page = Signal(str, str)  # page key, focus argument
    startup_changed = Signal()    # this page switched startup entries on or off

    def __init__(self, store: ScanStore, settings: Settings):
        super().__init__()
        self.store, self.settings = store, settings
        self.facts: checkup.SystemFacts | None = None
        self.findings: list[Finding] = []
        self.score: int | None = None
        self.score_before: int | None = None
        self._facts_busy = False
        self._mine = False
        self._stale = False
        self._auto = False                           # "quick" button: offer the fix as soon as the checkup ends
        self._undo: list[startup.StartupEntry] = []  # startup entries the last fix turned off
        lay = page_layout(self)

        self.scan_btn = button(t("home.scan"), "hero", "scan")
        self.scan_btn.clicked.connect(self._scan_or_cancel)
        lay.addWidget(PageHeader(t("home.title"), t("home.subtitle"), self.scan_btn))

        if IS_WINDOWS and not is_admin():
            banner = QFrame()
            banner.setObjectName("banner")
            row = QHBoxLayout(banner)
            row.setContentsMargins(14, 8, 8, 8)
            row.addWidget(icon_label("shield", theme.C["review"], 18))
            row.addWidget(label(t("home.admin_banner"), wrap=True), 1)
            restart = button(t("home.restart_admin"), "ghost")
            restart.clicked.connect(self._restart_admin)
            row.addWidget(restart)
            lay.addWidget(banner)

        # what the startup guard did when OpenSweep opened
        self.guard_banner = QFrame()
        self.guard_banner.setObjectName("card")
        grow = QHBoxLayout(self.guard_banner)
        grow.setContentsMargins(14, 10, 10, 10)
        grow.addWidget(icon_label("shield", theme.C["safe"], 20))
        self.guard_text = label("", wrap=True)
        grow.addWidget(self.guard_text, 1)
        guard_more = button(t("home.look"), "ghost")
        guard_more.clicked.connect(lambda: self.open_page.emit("startup", ""))
        grow.addWidget(guard_more)
        self.guard_banner.setVisible(False)
        lay.addWidget(self.guard_banner)

        # what the last fix switched off, with a way back
        self.undo_banner = QFrame()
        self.undo_banner.setObjectName("card")
        urow = QHBoxLayout(self.undo_banner)
        urow.setContentsMargins(14, 10, 10, 10)
        urow.addWidget(icon_label("check", theme.C["safe"], 20))
        self.undo_text = label("", wrap=True)
        urow.addWidget(self.undo_text, 1)
        self.undo_btn = button(t("home.undo"), "ghost", "refresh")
        self.undo_btn.clicked.connect(self._undo_startup)
        urow.addWidget(self.undo_btn)
        self.undo_banner.setVisible(False)
        lay.addWidget(self.undo_banner)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)

        # hero: score + summary + fix-all
        hero = Card(name="hero", margins=22)
        top = QHBoxLayout()
        top.setSpacing(22)
        self.ring = ScoreRing()
        top.addWidget(self.ring)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addStretch(1)
        self.verdict = label(t("home.verdict_none"), "big", wrap=True)
        col.addWidget(self.verdict)
        self.summary = label(t("home.empty_hint"), "muted", wrap=True)
        col.addWidget(self.summary)
        self.delta = label("", "section")
        self.delta.setStyleSheet(f"color: {theme.C['safe']};")
        self.delta.setVisible(False)
        col.addWidget(self.delta)
        buttons = QHBoxLayout()
        # First visit: one obvious button. Checks the PC, then shows what it would do and asks once.
        self.quick_btn = button(t("home.quick"), "primary", "check")
        self.quick_btn.clicked.connect(self.quick_start)
        buttons.addWidget(self.quick_btn)
        self.fix_btn = button(t("home.fix_all"), "primary", "check")
        self.fix_btn.clicked.connect(self._fix_all)
        self.fix_btn.setVisible(False)
        buttons.addWidget(self.fix_btn)
        buttons.addStretch(1)
        col.addSpacing(6)
        col.addLayout(buttons)
        col.addStretch(1)
        top.addLayout(col, 1)
        hero.layout_.addLayout(top)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        hero.layout_.addWidget(self.progress)
        self.bar = StackedBar(10, theme.C["hero_bg"])
        self.bar.setVisible(False)
        hero.layout_.addWidget(self.bar)
        self.legend = QHBoxLayout()
        self.legend.setSpacing(16)
        hero.layout_.addLayout(self.legend)
        left.addWidget(hero)

        # findings
        self.findings_card = Card(margins=20)
        self.findings_card.layout_.addWidget(label(t("home.findings"), "section"))
        self.findings_box = QVBoxLayout()
        self.findings_box.setSpacing(0)
        holder = QWidget()
        holder.setLayout(self.findings_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        self.findings_card.layout_.addWidget(scroll, 1)
        left.addWidget(self.findings_card, 1)
        body.addLayout(left, 3)

        # right column: drives + promise
        right = QVBoxLayout()
        right.setSpacing(14)
        sys_card = Card(margins=20)
        sys_card.layout_.addWidget(label(t("home.this_pc"), "section"))
        self.drive_box = QVBoxLayout()
        self.drive_box.setSpacing(10)
        sys_card.layout_.addLayout(self.drive_box)
        right.addWidget(sys_card)
        promise = Card(margins=20)
        ph = QHBoxLayout()
        ph.addWidget(icon_label("shield", theme.C["safe"], 18))
        ph.addWidget(label(t("home.promise_title"), "section"), 1)
        promise.layout_.addLayout(ph)
        promise.layout_.addWidget(label(t("home.promise_body"), "muted", wrap=True))
        right.addWidget(promise)
        right.addStretch(1)
        body.addLayout(right, 2)
        lay.addLayout(body, 1)

        store.busy_changed.connect(self._on_busy)
        store.progress.connect(lambda text: self.summary.setText(t("home.working", name=text)))
        store.scan_finished.connect(self._render)
        store.clean_finished.connect(self._on_cleaned)
        store.failed.connect(lambda msg: error(self, msg))
        self._placeholder()
        self.refresh_system()

    # ------------------------------------------------------------- checkup

    def _scan_or_cancel(self) -> None:
        if self.store.busy:
            self._auto = False
            self.store.cancel()
            self.quick_btn.setVisible(not self.store.scanned)
        else:
            self.run_checkup()

    def run_checkup(self) -> None:
        self.store.scan()
        self._collect_facts()

    def quick_start(self) -> None:
        """Check the PC and, when that is done, show what would be fixed and ask once."""
        if self.store.busy:
            return
        self._auto = True
        self.quick_btn.setVisible(False)
        self.run_checkup()

    def _collect_facts(self) -> None:
        if self._facts_busy:
            return
        self._facts_busy = True
        self._stale = False

        def done(facts):
            self._facts_busy = False
            self.facts = facts
            self._render()

        def failed(msg):
            self._facts_busy = False
            self._render()

        run_task(checkup.collect_facts, on_done=done, on_error=failed)

    def show_guard_notice(self, result) -> None:
        def names(entries):
            return ", ".join(e.display_name for e in entries[:3]) + ("…" if len(entries) > 3 else "")
        parts = []
        if result.fixed:
            parts.append(t("guard.home_fixed", count=len(result.fixed), names=names(result.fixed)))
        if result.need_admin:
            parts.append(t("guard.home_admin", count=len(result.need_admin), names=names(result.need_admin)))
        self.guard_text.setText("\n".join(parts))
        self.guard_banner.setVisible(bool(parts))

    def mark_stale(self) -> None:
        self._stale = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._stale and self.store.scanned:
            self._collect_facts()

    def _on_busy(self, busy: bool) -> None:
        self.scan_btn.setText(t("common.cancel") if busy else (t("home.rescan") if self.store.scanned
                                                                 else t("home.scan")))
        self.progress.setVisible(busy or self._facts_busy)
        self.fix_btn.setEnabled(not busy)
        self.quick_btn.setEnabled(not busy)
        if busy:
            self.summary.setText(t("home.starting"))

    def _render(self) -> None:
        if self.store.busy or self._facts_busy or not self.store.scanned:
            return
        self.progress.setVisible(False)
        if self.facts is not None:  # cleaning frees space and closing apps frees memory: don't judge by old numbers
            self.facts.disks = checkup.read_disks()
            self.facts.memory_percent = psutil.virtual_memory().percent
        results = list(self.store.results.values())
        self.findings = checkup.analyze(results, self.facts)
        self.score = checkup.score(self.findings)
        self.ring.set_value(self.score)
        grade = checkup.grade(self.score)
        self.verdict.setText(t(f"home.verdict_{grade}"))
        problems = [f for f in self.findings if f.level != OK]
        freeable = sum(f.size for f in self.findings if f.id in ("junk",))
        self.summary.setText(t("home.summary", count=len(problems), size=format_size(freeable))
                             if problems else t("home.summary_clean"))
        if self.score_before is not None and self.score != self.score_before:
            self.delta.setText(t("home.delta", before=self.score_before, after=self.score))
            self.delta.setVisible(True)
        self.fix_btn.setVisible(bool(self._fix_all_plan()))
        self.quick_btn.setVisible(False)
        self.scan_btn.setText(t("home.rescan"))
        self._render_bar()
        self._render_findings()
        self.refresh_system()
        if self._auto:
            self._auto = False
            if self.facts is not None and self._fix_all_plan():
                self._fix_all()

    def _render_bar(self) -> None:
        self._clear(self.legend)
        segments = []
        for cat in ALL:
            size = self.store.category_size((cat,))
            color = theme.CATEGORY_COLORS[cat]
            segments.append((size, color))
            if size:
                item = QHBoxLayout()
                item.setSpacing(6)
                item.addWidget(Dot(color))
                item.addWidget(label(t(f"cat.{cat}"), "muted"))
                item.addWidget(label(format_size(size), "section"))
                self.legend.addLayout(item)
        self.legend.addStretch(1)
        self.bar.set_segments(segments)
        self.bar.setVisible(any(v for v, _ in segments))

    # ------------------------------------------------------------- findings

    def _placeholder(self) -> None:
        self._clear(self.findings_box)
        hint = label(t("home.findings_empty"), "muted", wrap=True)
        hint.setContentsMargins(0, 8, 0, 8)
        self.findings_box.addWidget(hint)
        self.findings_box.addStretch(1)

    def _render_findings(self) -> None:
        self._clear(self.findings_box)
        for f in self.findings:
            title, detail, actions = self._describe(f)
            self.findings_box.addWidget(FindingRow(f.level, title, detail, actions))
        self.findings_box.addStretch(1)

    def _go(self, text: str, page: str, arg: str = "") -> QWidget:
        b = button(text, None)
        b.clicked.connect(lambda: self.open_page.emit(page, arg))
        return b

    def _describe(self, f: Finding) -> tuple[str, str, list[QWidget]]:
        size = format_size(f.size)
        if f.id == "junk":
            if f.level == OK:
                return t("home.f_junk_ok"), t("home.f_junk_ok_d"), []
            detail = t("home.f_junk_d")
            if f.extra.get("blocked"):
                detail += " " + t("home.f_junk_blocked", count=f.extra["blocked"],
                                  size=format_size(f.extra["blocked_size"]))
            clean = button(t("home.clean_now"), "primary", "clean")
            clean.clicked.connect(self._clean_safe)
            return t("home.f_junk", size=size), detail, [self._go(t("home.details"), "clean"), clean]
        if f.id == "review":
            return t("home.f_review", size=size), t("home.f_review_d"), [self._go(t("home.look"), "clean")]
        if f.id == "ai":
            return t("home.f_ai", size=size, count=f.count), t("home.f_ai_d"), [self._go(t("home.look"), "ai")]
        if f.id.startswith("disk:"):
            key = "home.f_disk_sys" if f.extra["system"] else "home.f_disk"
            return (t(key, drive=f.extra["drive"], free=size, total=format_size(f.extra["total"])),
                    t(key + "_d"), [self._go(t("home.look"), "software", "folders")])
        if f.id == "startup":
            if f.level == OK:
                return t("home.f_startup_ok", count=f.extra["enabled"]), t("home.f_startup_ok_d"), []
            names = ", ".join(f.extra["names"]) + ("…" if f.count > len(f.extra["names"]) else "")
            fix = button(t("home.startup_fix"), "primary", "startup")
            fix.clicked.connect(self._fix_startup)
            return (t("home.f_startup", count=f.count), t("home.f_startup_d", names=names),
                    [self._go(t("home.look"), "startup"), fix])
        if f.id == "startup_broken":
            fix = button(t("startup.remove_broken"), "primary", "clean")
            fix.clicked.connect(self._fix_broken)
            return t("home.f_broken", count=f.count), t("home.f_broken_d"), [fix]
        if f.id == "unused":
            names = ", ".join(f.extra["names"]) + ("…" if f.count > len(f.extra["names"]) else "")
            return (t("home.f_unused", count=f.count, size=size), t("home.f_unused_d", names=names),
                    [self._go(t("home.look"), "software", "unused")])
        if f.id == "memory":
            if f.level == OK:
                return t("home.f_memory_ok", percent=f.extra["percent"]), t("home.f_memory_ok_d"), []
            return (t("home.f_memory", percent=f.extra["percent"]),
                    t("home.f_memory_d", names=", ".join(f.extra["names"]), size=size),
                    [self._go(t("home.look"), "memory", "background")])
        return f.id, "", []

    # ------------------------------------------------------------- fixes

    def _advised_startup(self) -> list[startup.StartupEntry]:
        if not self.facts:
            return []
        return [e for e in self.facts.startup if e.enabled and e.exists and e.advice == "disable"]

    def _broken_startup(self) -> list[startup.StartupEntry]:
        return [e for e in self.facts.startup if not e.exists and e.removable] if self.facts else []

    def _fix_all_plan(self) -> list[str]:
        plan = []
        safe = self.store.safe_size()
        if safe >= 100 * 1024 ** 2:
            plan.append(t("home.plan_junk", size=format_size(safe)))
        if self._advised_startup():
            plan.append(t("home.plan_startup", count=len(self._advised_startup())))
        if self._broken_startup():
            plan.append(t("home.plan_broken", count=len(self._broken_startup())))
        return plan

    def _fix_all(self) -> None:
        plan = self._fix_all_plan()
        if not plan:
            return
        text = t("home.plan_intro") + "\n\n" + "\n".join(f"•  {p}" for p in plan) + "\n\n" + t("home.plan_safe")
        if not confirm(self, t("home.fix_all"), text, t("home.fix_all")):
            return
        self.score_before = self.score
        targets = self.store.safe_targets()
        if targets:
            self._mine = True
            self.store.clean(targets, self.settings.use_recycle_bin)
        self._apply_startup(self._advised_startup(), self._broken_startup())

    def _apply_startup(self, disable: list, remove: list) -> None:
        """Startup changes run in the background (tasks and services take seconds each), then re-check."""
        if not disable and not remove:
            self._collect_facts()
            return
        self.progress.setVisible(True)
        self.summary.setText(t("startup.working", count=len(disable) + len(remove)))
        self.fix_btn.setEnabled(False)

        def done(result: startup.ChangeResult) -> None:
            self.fix_btn.setEnabled(True)
            if result.done:
                self.startup_changed.emit()
            turned_off = [e for e in result.done if e in disable]
            if turned_off:
                self._undo = turned_off
                self.undo_text.setText(t("home.undo_text", count=len(turned_off)))
                self.undo_btn.setEnabled(True)
                self.undo_btn.setVisible(True)
                self.undo_banner.setVisible(True)
            if result.failed:
                lines = [f"• {e.display_name}" for e, _msg in result.failed]
                head = t("startup.some_failed", done=len(result.done), failed=len(result.failed))
                if result.needs_admin:
                    head += "\n" + t("startup.needs_admin")
                error(self, head + "\n\n" + "\n".join(lines[:10]))
            self._collect_facts()

        def failed(message: str) -> None:
            self.fix_btn.setEnabled(True)
            error(self, message)
            self._collect_facts()

        run_task(startup.apply_changes, [], disable, remove, on_done=done, on_error=failed)

    def _undo_startup(self) -> None:
        """Turn back on what the last fix turned off."""
        todo, self._undo = self._undo, []
        if not todo:
            return
        self.undo_btn.setEnabled(False)

        def done(result: startup.ChangeResult) -> None:
            if result.failed:
                self._undo = [e for e, _msg in result.failed]
                self.undo_btn.setEnabled(True)
                head = t("startup.some_failed", done=len(result.done), failed=len(result.failed))
                if result.needs_admin:
                    head += "\n" + t("startup.needs_admin")
                error(self, head + "\n\n" + "\n".join(f"• {e.display_name}" for e, _m in result.failed[:10]))
                return
            self.startup_changed.emit()
            self.undo_text.setText(t("home.undo_done", count=len(result.done)))
            self.undo_btn.setVisible(False)
            self._collect_facts()

        run_task(startup.apply_changes, todo, [], [], on_done=done, on_error=lambda m: error(self, m))

    def _clean_safe(self) -> None:
        targets = self.store.safe_targets()
        if targets and CleanConfirmDialog(self, targets, self.settings.use_recycle_bin).exec():
            self.score_before = self.score
            self._mine = True
            self.store.clean(targets, self.settings.use_recycle_bin)

    def _fix_startup(self) -> None:
        todo = self._advised_startup()
        names = "\n".join(f"•  {e.display_name}  —  {t('startup.kind_' + (e.kind or 'unknown'))}" for e in todo)
        if todo and confirm(self, t("home.startup_fix"), t("startup.apply_confirm", count=len(todo)) + "\n\n"
                            + names + "\n\n" + t("startup.apply_undo"), t("home.startup_fix")):
            self.score_before = self.score
            self._apply_startup(todo, [])

    def _fix_broken(self) -> None:
        todo = self._broken_startup()
        names = "\n".join(f"•  {e.display_name}  ({e.exe or e.command})" for e in todo)
        if todo and confirm(self, t("startup.remove_broken"), t("startup.remove_confirm") + "\n\n" + names,
                            t("startup.remove_broken")):
            self.score_before = self.score
            self._apply_startup([], todo)

    def _on_cleaned(self, report) -> None:
        if self._mine:
            self._mine = False
            CleanResultDialog(self, report, self.store).exec()

    # ------------------------------------------------------------- misc

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                HomePage._clear(item.layout())

    def refresh_system(self) -> None:
        self._clear(self.drive_box)

        def row(name: str, percent: float, text: str) -> None:
            box = QVBoxLayout()
            box.setSpacing(4)
            head = QHBoxLayout()
            head.addWidget(label(name, "section"))
            head.addStretch(1)
            head.addWidget(label(text, "muted"))
            box.addLayout(head)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(percent))
            bar.setTextVisible(False)
            if percent >= 90:
                bar.setStyleSheet(f"QProgressBar::chunk {{ background: {theme.C['danger']}; }}")
            box.addWidget(bar)
            self.drive_box.addLayout(box)

        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            row(part.mountpoint.rstrip("\\/") or part.mountpoint, usage.percent,
                t("home.disk_free", free=format_size(usage.free), total=format_size(usage.total)))
        mem = psutil.virtual_memory()
        row(t("home.memory"), mem.percent,
            t("home.memory_used", used=format_size(mem.total - mem.available), total=format_size(mem.total)))

    def _restart_admin(self) -> None:
        if relaunch_as_admin():
            self.window().close()
