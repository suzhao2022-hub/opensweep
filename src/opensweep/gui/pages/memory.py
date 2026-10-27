"""Memory: where the RAM goes, in plain words, and which apps are worth closing."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLineEdit, QSplitter, QTableWidget,
                               QVBoxLayout, QWidget)

from opensweep.core import memory, startup
from opensweep.core.platform import open_in_file_manager
from opensweep.core.units import format_size
from opensweep.gui import theme
from opensweep.gui.panels import InfoPanel, exe_icon
from opensweep.gui.widgets import (ROLE_RATIO, Card, Dot, PageHeader, SizeBarDelegate, SortItem, StackedBar, button,
                                   confirm, error, icon_label, info, label, page_layout, style_table)
from opensweep.gui.workers import run_task
from opensweep.i18n import t

TOP = 80
KIND_COLORS = {"app": "#2F6BFF", "background": "#F08C00", "system": "#7A5AF8"}
BIG_BACKGROUND = 300 * 1024 * 1024


def _duration(started: float) -> str:
    if not started:
        return t("common.unknown")
    minutes = int((time.time() - started) // 60)
    if minutes < 60:
        return t("memory.minutes", n=max(minutes, 1))
    hours = minutes // 60
    return t("memory.hours", n=hours) if hours < 48 else t("memory.days", n=hours // 24)


class MemoryPage(QWidget):
    open_startup = Signal()
    startup_changed = Signal()  # an entry was switched off from here

    def __init__(self):
        super().__init__()
        self.apps: list[memory.AppUsage] = []
        self.startup_by_exe: dict[str, startup.StartupEntry] = {}
        self._loading = False
        self._selected_key = ""
        lay = page_layout(self)

        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.auto = QCheckBox(t("memory.auto"))
        self.auto.setChecked(True)
        refresh = button(t("software.refresh"), None, "refresh")
        refresh.clicked.connect(self.refresh)
        rl.addWidget(self.auto)
        rl.addWidget(refresh)
        lay.addWidget(PageHeader(t("memory.title"), t("memory.desc"), right))

        top = QHBoxLayout()
        top.setSpacing(14)
        usage = Card(margins=20)
        head = QHBoxLayout()
        self.percent = label("—", "huge")
        head.addWidget(self.percent)
        col = QVBoxLayout()
        col.addStretch(1)
        self.usage = label("", "muted")
        col.addWidget(self.usage)
        self.verdict = label("", "section")
        col.addWidget(self.verdict)
        head.addLayout(col, 1)
        usage.layout_.addLayout(head)
        self.bar = StackedBar(14)
        usage.layout_.addWidget(self.bar)
        self.legend = QHBoxLayout()
        self.legend.setSpacing(16)
        usage.layout_.addLayout(self.legend)
        top.addWidget(usage, 3)

        tip = Card(margins=20)
        th = QHBoxLayout()
        th.addWidget(icon_label("info", theme.C["accent"], 18))
        self.tip_title = label(t("memory.tip_title"), "section")
        th.addWidget(self.tip_title, 1)
        tip.layout_.addLayout(th)
        self.tip = label("", "muted", wrap=True)
        tip.layout_.addWidget(self.tip, 1)
        why = button(t("memory.why_title"), "ghost")
        why.clicked.connect(lambda: info(self, t("memory.why_title"), t("memory.why_body")))
        tip.layout_.addWidget(why, 0, Qt.AlignLeft)
        top.addWidget(tip, 2)
        lay.addLayout(top)

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("clean.search"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(theme.icon("search"), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._apply_filter)
        self.filter = QComboBox()
        for key in ("all", "app", "background", "system"):
            self.filter.addItem(t(f"memory.f_{key}"), key)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.filter)
        lay.addLayout(tools)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([t("memory.col_app"), t("memory.col_kind"), t("memory.col_memory"),
                                              t("memory.col_cpu"), t("memory.col_count")])
        style_table(self.table, 40)
        self.table.setIconSize(QSize(20, 20))
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.setItemDelegateForColumn(2, SizeBarDelegate(self.table))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.resizeSection(2, 130)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSortIndicator(2, Qt.DescendingOrder)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.detail = InfoPanel(t("memory.pick"))
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([640, 420])
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(lambda: self.auto.isChecked() and self.refresh())

    def mark_stale(self) -> None:
        self.startup_by_exe = {}  # re-read the startup entries when the page is next shown

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.startup_by_exe:
            run_task(lambda: startup.describe(startup.list_startup()), on_done=self._set_startup)
        self.refresh()
        self.timer.start()

    def focus(self, kind: str) -> None:
        index = self.filter.findData(kind)
        if index >= 0:
            self.filter.setCurrentIndex(index)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.timer.stop()

    def _set_startup(self, entries) -> None:
        self.startup_by_exe = {os.path.normcase(e.exe): e for e in entries if e.exe and e.enabled}
        self._on_select()

    # ------------------------------------------------------------- data

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        run_task(lambda: (memory.system_memory(), memory.list_apps()), on_done=self._fill,
                 on_error=lambda m: setattr(self, "_loading", False))

    def _fill(self, data) -> None:
        self._loading = False
        vm, apps = data
        self.apps = apps[:TOP]
        used = vm.total - vm.available
        self.percent.setText(f"{int(vm.percent)}%")
        self.usage.setText(t("memory.usage", used=format_size(used), total=format_size(vm.total),
                             percent=int(vm.percent)))
        level = "high" if vm.percent >= 85 else ("medium" if vm.percent >= 70 else "ok")
        self.verdict.setText(t(f"memory.level_{level}"))
        self.verdict.setStyleSheet(f"color: {theme.C['danger' if level == 'high' else 'review' if level == 'medium' else 'safe']};")

        parts = memory.breakdown(apps)
        self.bar.set_segments([(parts["app"], KIND_COLORS["app"]), (parts["background"], KIND_COLORS["background"]),
                               (parts["system"], KIND_COLORS["system"]), (parts["free"], theme.C["bar"])])
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
            elif item.widget():
                item.widget().deleteLater()
        for key in ("app", "background", "system", "free"):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(Dot(KIND_COLORS.get(key, theme.C["faint"])))
            row.addWidget(label(t(f"memory.k_{key}"), "muted"))
            row.addWidget(label(format_size(parts[key]), "section"))
            self.legend.addLayout(row)
        self.legend.addStretch(1)

        heavy = [a for a in apps if a.kind == "background" and a.memory >= BIG_BACKGROUND]
        if heavy:
            names = ", ".join(a.display_name for a in heavy[:3])
            self.tip.setText(t("memory.tip_background", names=names, size=format_size(sum(a.memory for a in heavy))))
        elif level != "ok":
            self.tip.setText(t("memory.tip_apps"))
        else:
            self.tip.setText(t("memory.tip_ok"))

        biggest = max((a.memory for a in self.apps), default=1) or 1
        header = self.table.horizontalHeader()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.apps))
        for row, app in enumerate(self.apps):
            name = SortItem(app.display_name)
            name.setIcon(exe_icon(app.exe))
            name.setData(Qt.UserRole, row)
            name.setToolTip(app.exe or app.name)
            self.table.setItem(row, 0, name)
            kind = SortItem(t(f"memory.k_{app.kind}"))
            kind.setForeground(QBrush(QColor(KIND_COLORS[app.kind])))
            self.table.setItem(row, 1, kind)
            mem = SortItem(format_size(app.memory), app.memory)
            mem.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            mem.setData(ROLE_RATIO, app.memory / biggest)
            mem.setData(ROLE_RATIO + 1, KIND_COLORS[app.kind])
            self.table.setItem(row, 2, mem)
            cpu = SortItem(f"{app.cpu:.1f}%", app.cpu)
            cpu.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, cpu)
            count = SortItem(str(len(app.pids)), len(app.pids))
            count.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 4, count)
        self.table.setSortingEnabled(True)
        self.table.sortItems(header.sortIndicatorSection(), header.sortIndicatorOrder())
        self._apply_filter()
        self._reselect()

    def _key(self, app: memory.AppUsage) -> str:
        return (app.exe or app.name).lower()

    def _reselect(self) -> None:
        if not self._selected_key:
            return
        for row in range(self.table.rowCount()):
            app = self._app(row)
            if app and self._key(app) == self._selected_key and not self.table.isRowHidden(row):
                self.table.blockSignals(True)
                self.table.selectRow(row)
                self.table.blockSignals(False)
                self._on_select()
                return

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        mode = self.filter.currentData()
        for row in range(self.table.rowCount()):
            app = self._app(row)
            if app is None:
                continue
            visible = (mode == "all" or app.kind == mode) and (
                not text or text in app.display_name.lower() or text in app.name.lower())
            self.table.setRowHidden(row, not visible)

    def _app(self, row: int) -> memory.AppUsage | None:
        item = self.table.item(row, 0)
        if item is None:
            return None
        idx = item.data(Qt.UserRole)
        return self.apps[idx] if idx is not None and idx < len(self.apps) else None

    def current(self) -> memory.AppUsage | None:
        rows = self.table.selectionModel().selectedRows()
        return self._app(rows[0].row()) if rows else None

    # ------------------------------------------------------------- detail

    def _on_select(self) -> None:
        app = self.current()
        if app is None:
            self.detail.show_none()
            return
        self._selected_key = self._key(app)
        entry = self.startup_by_exe.get(os.path.normcase(app.exe)) if app.exe else None
        badges = [(t(f"memory.k_{app.kind}"), "info")]
        if entry:
            badges.append((t("memory.starts_with_windows"), "review"))
        reason = t(f"memory.why_{app.kind}")
        if entry:
            reason += "\n\n" + t("memory.startup_hint")
        facts = [(t("memory.col_cpu"), f"{app.cpu:.1f}%", False),
                 (t("memory.col_count"), str(len(app.pids)), False),
                 (t("memory.running_for"), _duration(app.started), False)]
        if app.exe:
            facts.append((t("detail.location"), app.exe, True))
        actions = []
        close = button(t("memory.close"), "danger" if app.kind == "background" else None, "close")
        close.setEnabled(not app.critical and not app.is_self and app.kind != "system")
        close.clicked.connect(lambda: self._close_app(app))
        actions.append(close)
        if entry:
            off = button(t("memory.disable_startup"), None, "startup")
            off.clicked.connect(lambda: self._disable_startup(entry))
            actions.append(off)
        if app.exe and os.path.exists(app.exe):
            where = button(t("startup.open_location"), None, "folder")
            where.clicked.connect(lambda: open_in_file_manager(app.exe))
            actions.append(where)
        self.detail.present(exe_icon(app.exe), app.display_name, app.name if app.description else "", badges,
                            format_size(app.memory), reason, facts, actions,
                            report={"Page": "Memory", "Process": app.name, "Kind": app.kind, "Exe": app.exe})

    def _disable_startup(self, entry: startup.StartupEntry) -> None:
        self.detail.setEnabled(False)

        def done(result: startup.ChangeResult) -> None:
            self.detail.setEnabled(True)
            if result.failed:
                error(self, t("startup.needs_admin") if result.needs_admin else result.failed[0][1])
                return
            self.startup_by_exe.pop(os.path.normcase(entry.exe), None)
            self.startup_changed.emit()
            info(self, t("memory.disable_startup"), t("memory.startup_disabled", name=entry.display_name))
            self._on_select()

        run_task(startup.apply_changes, [], [entry], [], on_done=done,
                 on_error=lambda m: (self.detail.setEnabled(True), error(self, m)))

    def _close_app(self, app: memory.AppUsage) -> None:
        if app.critical or app.kind == "system":
            error(self, t("memory.critical", name=app.display_name))
            return
        if not confirm(self, t("memory.close"), t("memory.confirm", name=app.display_name), t("memory.close"),
                       danger=True):
            return
        self.timer.stop()

        def done(result):
            closed, failed = result
            if failed and not closed:
                error(self, t("memory.close_failed", name=app.display_name))
            else:
                info(self, t("memory.close"), t("memory.closed", name=app.display_name, size=format_size(app.memory)))
            self._selected_key = ""
            self.refresh()
            self.timer.start()

        run_task(memory.close_app, app, on_done=done, on_error=lambda m: error(self, m))
