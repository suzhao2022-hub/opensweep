"""Startup apps: plain-language advice for each one, and one click to follow it."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QProgressBar, QSplitter, QTableWidget,
                               QVBoxLayout, QWidget)

from opensweep.core import guard, memory, startup
from opensweep.core.platform import is_admin, open_in_file_manager, relaunch_as_admin
from opensweep.core.units import format_size
from opensweep.gui import theme
from opensweep.gui.boot_dialog import BootDialog
from opensweep.gui.panels import InfoPanel, exe_icon
from opensweep.gui.widgets import (Card, PageHeader, SortItem, button, confirm, error, icon_label, info, label,
                                   page_layout, style_table)
from opensweep.gui.workers import run_task
from opensweep.i18n import t

ADVICE_KIND = {"disable": "review", "optional": "info", "keep": "safe"}
ADVICE_ORDER = {"disable": 0, "optional": 1, "keep": 2}


def load_entries(include_system: bool = True, apps=None):
    """(startup entries with descriptions, running apps). Entries carry how much memory each one uses right now.
    Tasks and services come from PowerShell and take seconds, so the page asks for them in a second step."""
    entries = startup.describe(startup.list_startup(include_system=include_system))
    if apps is None:
        apps = memory.list_apps()
    by_exe = {os.path.normcase(a.exe): a for a in apps if a.exe}
    for e in entries:
        app = by_exe.get(os.path.normcase(e.exe)) if e.exe else None
        e.extra["memory"] = app.memory if app else 0
        e.extra["running"] = app is not None
    return entries, apps


def entry_key(e: startup.StartupEntry) -> tuple:
    return (e.approved_key, e.hive, e.reg_path, e.name)


def advice_text(e: startup.StartupEntry) -> tuple[str, str]:
    if not e.exists:
        return t("startup.a_broken"), "danger"
    return t(f"startup.a_{e.advice}"), ADVICE_KIND[e.advice]


def _guard_note(e: startup.StartupEntry) -> str:
    return "\n\n" + t("guard.entry_note") if e.name == startup.GUARD_VALUE else ""


def reason_text(e: startup.StartupEntry) -> str:
    text = t("startup.why_broken") if not e.exists and e.removable else t(f"startup.why_{e.kind or 'unknown'}")
    if e.approved_key == "Task":
        text += "\n\n" + t("startup.task_note")
    elif e.approved_key == "Service":
        text += "\n\n" + t("startup.service_note")
    elif e.approved_key == "RestartApps":
        text += "\n\n" + t("startup.restart_note")
    return text + _guard_note(e)


class StatCard(Card):
    def __init__(self, title: str, action: str, icon_name: str):
        super().__init__()
        self.layout_.addWidget(label(title, "muted"))
        self.value = label("—", "big")
        self.layout_.addWidget(self.value)
        self.hint = label("", "faint", wrap=True)
        self.layout_.addWidget(self.hint)
        self.button = button(action, "primary", icon_name) if action else None
        if self.button:
            self.button.setEnabled(False)
            self.layout_.addWidget(self.button, 0, Qt.AlignLeft)


class GuardCard(Card):
    """One switch: keep the apps the user turned off from turning themselves back on."""

    def __init__(self, page: "StartupPage"):
        super().__init__(margins=16)
        self.page = page
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(icon_label("shield", theme.C["accent"], 22), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(t("guard.title"), "section"))
        self.text = label("", "muted", wrap=True)
        col.addWidget(self.text)
        row.addLayout(col, 1)
        self.fix_btn = button(t("guard.fix_now"), "primary", "startup")
        self.fix_btn.clicked.connect(page.fix_came_back)
        self.fix_btn.setVisible(False)
        row.addWidget(self.fix_btn, 0, Qt.AlignVCenter)
        self.history_btn = button("", "ghost", "history")
        self.history_btn.clicked.connect(self._show_history)
        row.addWidget(self.history_btn, 0, Qt.AlignVCenter)
        self.switch = QCheckBox(t("guard.switch"))
        self.switch.setChecked(page.settings.guard)
        self.switch.toggled.connect(self._toggle)
        row.addWidget(self.switch, 0, Qt.AlignVCenter)
        self.layout_.addLayout(row)

    def refresh(self, came_back: list[startup.StartupEntry]) -> None:
        on = self.page.settings.guard
        if not on:
            self.text.setText(t("guard.off_text"))
        elif came_back:
            names = ", ".join(e.display_name for e in came_back[:3]) + ("…" if len(came_back) > 3 else "")
            self.text.setText(t("guard.came_back", count=len(came_back), names=names))
        else:
            self.text.setText(t("guard.on_text", count=guard.watched_count()))
        self.fix_btn.setVisible(on and bool(came_back))
        count = len(guard.history())
        self.history_btn.setText(t("guard.history_btn", count=count))
        self.history_btn.setVisible(count > 0)

    def _toggle(self, on: bool) -> None:
        self.page.settings.guard = on
        self.page.settings.save()
        run_task(guard.sync_logon_check, on)
        self.refresh(self.page.came_back)

    def _show_history(self) -> None:
        items = guard.history()
        lines = [f"{h.get('time', '')}   {h.get('display', '')}" for h in items[:40]]
        info(self, t("guard.history_title"), t("guard.history_intro") + "\n\n" + "\n".join(lines))


class StartupPage(QWidget):
    changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.entries: list[startup.StartupEntry] = []
        self.came_back: list[startup.StartupEntry] = []
        self.loaded = False
        self._filling = False
        self._generation = 0
        self._busy = False
        lay = page_layout(self)
        refresh = button(t("software.refresh"), None, "refresh")
        refresh.clicked.connect(self.load)
        trace = button(t("boot.button"), None, "history")
        trace.clicked.connect(lambda: BootDialog(self).exec())
        actions = QWidget()
        actions_row = QHBoxLayout(actions)
        actions_row.setContentsMargins(0, 0, 0, 0)
        self.off_all_btn = button(t("startup.off_all"), "primary", "check")
        self.off_all_btn.clicked.connect(self.turn_off_all)
        self.off_sel_btn = button(t("startup.off_selected"), None, None)
        self.off_sel_btn.clicked.connect(lambda: self._bulk_selected(False))
        self.on_sel_btn = button(t("startup.on_selected"), None, None)
        self.on_sel_btn.clicked.connect(lambda: self._bulk_selected(True))
        actions_row.addWidget(trace)
        actions_row.addWidget(refresh)
        lay.addWidget(PageHeader(t("startup.title"), t("startup.desc"), actions))
        self.guard_card = GuardCard(self)
        lay.addWidget(self.guard_card)
        lay.addWidget(label(t("startup.legend"), "muted", wrap=True))

        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.card_on = StatCard(t("startup.stat_on"), "", "")
        self.card_advice = StatCard(t("startup.stat_advice"), t("startup.apply_advice"), "check")
        self.card_advice.button.clicked.connect(self.apply_advice)
        self.card_broken = StatCard(t("startup.stat_broken"), t("startup.remove_broken"), "clean")
        self.card_broken.button.clicked.connect(self.remove_broken)
        for c in (self.card_on, self.card_advice, self.card_broken):
            cards.addWidget(c, 1)
        lay.addLayout(cards)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([t("startup.col_name"), t("startup.col_status"),
                                              t("startup.col_advice"), t("startup.col_memory"),
                                              t("startup.col_publisher")])
        style_table(self.table, 42)
        self.table.setIconSize(QSize(20, 20))
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)  # Ctrl/Shift-click to pick several
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._on_changed)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.itemSelectionChanged.connect(self._update_bulk)
        self.detail = InfoPanel(t("startup.pick"))
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([620, 440])
        split.setChildrenCollapsible(False)
        bulk = QHBoxLayout()  # one-click and multi-select actions, right above the list they act on
        for b in (self.off_all_btn, self.off_sel_btn, self.on_sel_btn):
            bulk.addWidget(b)
        bulk.addStretch(1)
        lay.addLayout(bulk)
        lay.addWidget(split, 1)
        self.status = label("", "muted")
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setMaximumWidth(220)
        self.busy_bar.setVisible(False)
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.busy_bar)
        lay.addLayout(status_row)
        self._update_bulk()

    def mark_stale(self) -> None:
        """Another page changed startup entries: read them again when this page is next shown."""
        self.loaded = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.loaded:
            self.load()

    def load(self) -> None:
        """Show registry and Startup-folder entries at once, then add tasks and services when they are read."""
        self.loaded = True
        self._generation += 1
        generation = self._generation  # a newer load makes the answers of this one irrelevant
        self.status.setText(t("software.loading"))

        def quick(result) -> None:
            entries, apps = result
            if generation != self._generation:
                return
            self._fill(entries)
            self._busy = True  # no changes until the full list is in, or they would be lost in the redraw
            self._set_busy_ui(True, t("startup.loading_system"))
            run_task(load_entries, True, apps, on_done=complete, on_error=failed)

        def complete(result) -> None:
            if generation != self._generation:
                return
            self._busy = False
            self._set_busy_ui(False)
            current = self.current()
            wanted = entry_key(current) if current else None
            self._fill(result[0])
            if wanted:
                for row in range(self.table.rowCount()):
                    if entry_key(self._entry(row)) == wanted:
                        self.table.selectRow(row)
                        break

        def failed(message: str) -> None:
            if generation == self._generation:
                self._busy = False
                self._set_busy_ui(False)
                self._update_stats()
            error(self, message)

        run_task(load_entries, False, None, on_done=quick, on_error=failed)

    # ------------------------------------------------------------- table

    def _fill(self, entries: list[startup.StartupEntry]) -> None:
        self.entries = sorted(entries, key=lambda e: (e.exists, ADVICE_ORDER[e.advice], not e.enabled,
                                                      e.display_name.lower()))
        self._filling = True
        self.table.setRowCount(len(self.entries))
        for row, e in enumerate(self.entries):
            name = SortItem(e.display_name)
            name.setIcon(exe_icon(e.exe) if e.exe.lower().endswith(".exe") else theme.icon("startup"))
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            name.setCheckState(Qt.Checked if e.enabled else Qt.Unchecked)
            name.setData(Qt.UserRole, row)
            name.setToolTip(t("startup.toggle_tip"))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, SortItem(""))
            self.table.setItem(row, 2, SortItem(""))
            self._show_state(row, e)
            mem = e.extra.get("memory", 0)
            mem_item = SortItem(format_size(mem) if mem else "—", mem)
            mem_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 3, mem_item)
            pub = SortItem(e.company)
            pub.setForeground(QBrush(QColor(theme.C["muted"])))
            self.table.setItem(row, 4, pub)
        self._filling = False
        self.came_back = guard.came_back(self.entries) if self.settings.guard else []
        self.guard_card.refresh(self.came_back)
        self._update_stats()
        self._on_select()
        self._update_bulk()

    def fix_came_back(self) -> None:
        """Switch off what came back but needs administrator rights."""
        todo = list(self.came_back)
        if not todo:
            return
        if any(e.needs_admin for e in todo) and not is_admin():
            if confirm(self, t("guard.title"), t("guard.need_admin_restart"), t("home.restart_admin")):
                if relaunch_as_admin():
                    self.window().close()
            return
        self._apply([], todo, [])

    def _update_stats(self) -> None:
        on = [e for e in self.entries if e.enabled and e.exists]
        todo = self._advised()
        broken = [e for e in self.entries if not e.exists and e.removable]
        self.card_on.value.setText(str(len(on)))
        self.card_on.hint.setText(t("startup.stat_on_hint", total=len(self.entries)))
        self.card_advice.value.setText(str(len(todo)))
        self.card_advice.hint.setText(t("startup.stat_advice_hint", mem=format_size(
            sum(e.extra.get("memory", 0) for e in todo))) if todo else t("startup.stat_advice_done"))
        self.card_advice.button.setEnabled(bool(todo))
        self.card_broken.value.setText(str(len(broken)))
        self.card_broken.hint.setText(t("startup.stat_broken_hint") if broken else t("startup.stat_broken_none"))
        self.card_broken.button.setEnabled(bool(broken))
        status = t("startup.count", on=len(on), total=len(self.entries))
        if not is_admin() and any(e.needs_admin for e in self.entries):
            status += "  " + t("startup.needs_admin")
        self.status.setText(status)

    def _advised(self) -> list[startup.StartupEntry]:
        return [e for e in self.entries if e.enabled and e.exists and e.advice == "disable"]

    # ------------------------------------------------------------- bulk

    def _selected(self) -> list[startup.StartupEntry]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [e for e in map(self._entry, rows) if e is not None and e.exists and e.name != startup.GUARD_VALUE]

    def _all_off_candidates(self) -> list[startup.StartupEntry]:
        """Everything that is on, except what the advice says Windows or OpenSweep itself needs."""
        return [e for e in self.entries if e.enabled and e.exists and e.advice != "keep"
                and e.name != startup.GUARD_VALUE]

    def _update_bulk(self) -> None:
        chosen = self._selected()
        self.off_sel_btn.setEnabled(not self._busy and any(e.enabled for e in chosen))
        self.on_sel_btn.setEnabled(not self._busy and any(not e.enabled for e in chosen))
        self.off_all_btn.setEnabled(not self._busy and bool(self._all_off_candidates()))

    def _bulk_selected(self, enable: bool) -> None:
        todo = [e for e in self._selected() if e.enabled != enable]
        if todo:
            self._apply(todo if enable else [], [] if enable else todo, [])

    def turn_off_all(self) -> None:
        todo = self._all_off_candidates()
        if todo and confirm(self, t("startup.off_all"), t("startup.off_all_confirm", count=len(todo)),
                            t("startup.off_all")):
            self._apply([], todo, [])

    def _entry(self, row: int) -> startup.StartupEntry | None:
        item = self.table.item(row, 0)
        return self.entries[item.data(Qt.UserRole)] if item else None

    def current(self) -> startup.StartupEntry | None:
        rows = self.table.selectionModel().selectedRows()
        return self._entry(rows[0].row()) if rows else None

    def _on_changed(self, item) -> None:
        if self._filling or item.column() != 0:
            return
        e = self.entries[item.data(Qt.UserRole)]
        self._set(e, item.checkState() == Qt.Checked)

    def _set(self, e: startup.StartupEntry, enabled: bool) -> None:
        if enabled == e.enabled:
            return
        self._apply([e] if enabled else [], [] if enabled else [e], [], quiet=True)

    def _apply(self, enable: list, disable: list, remove: list, quiet: bool = False) -> None:
        """Run startup changes in the background; tasks and services take a few seconds each."""
        if self._busy:
            return
        self._busy = True
        count = len(enable) + len(disable) + len(remove)
        self._set_busy_ui(True, t("startup.working", count=count))

        def done(result: startup.ChangeResult) -> None:
            self._busy = False
            self._set_busy_ui(False)
            self._sync_checks()
            self.came_back = guard.came_back(self.entries) if self.settings.guard else []
            self.guard_card.refresh(self.came_back)
            self._update_stats()
            self._on_select()
            if result.done:
                self.changed.emit()
                run_task(guard.sync_logon_check, self.settings.guard)
            if result.failed:
                lines = [f"• {e.display_name}: {msg.splitlines()[-1][:120] if msg else ''}" for e, msg in result.failed]
                head = t("startup.some_failed", done=len(result.done), failed=len(result.failed))
                if result.needs_admin:
                    head += "\n" + t("startup.needs_admin")
                error(self, head + "\n\n" + "\n".join(lines[:10]))
            elif not quiet:
                info(self, t("startup.apply_advice"), t("startup.applied", count=len(result.done)))
            if remove:
                self.load()

        def failed(message: str) -> None:
            self._busy = False
            self._set_busy_ui(False)
            self._sync_checks()
            error(self, message)

        run_task(startup.apply_changes, enable, disable, remove, on_done=done, on_error=failed)

    def _set_busy_ui(self, busy: bool, text: str = "") -> None:
        self.table.setEnabled(not busy)
        self.detail.setEnabled(not busy)
        for card in (self.card_advice, self.card_broken):
            card.button.setEnabled(not busy and card.button.isEnabled())
        if busy:
            self.status.setText(text)
            self.busy_bar.setVisible(True)
        else:
            self.busy_bar.setVisible(False)
        self._update_bulk()

    def _show_state(self, row: int, e: startup.StartupEntry) -> None:
        """Status (on/off right now) and the recommendation. Once an entry is off the advice is only a hint."""
        status = self.table.item(row, 1)
        status.setText(t("startup.on") if e.enabled else t("startup.off"))
        status.setForeground(QBrush(QColor(theme.C["muted" if e.enabled else "safe"])))
        text, kind = advice_text(e)
        advice = self.table.item(row, 2)
        advice.setText(text)
        color = {"review": "review", "info": "muted", "safe": "safe", "danger": "danger"}[kind]
        advice.setForeground(QBrush(QColor(theme.C[color if e.enabled or not e.exists else "muted"])))

    def _sync_checks(self) -> None:
        self._filling = True
        for row in range(self.table.rowCount()):
            e = self._entry(row)
            self.table.item(row, 0).setCheckState(Qt.Checked if e.enabled else Qt.Unchecked)
            self._show_state(row, e)
        self._filling = False

    # ------------------------------------------------------------- detail

    def _on_select(self) -> None:
        e = self.current()
        if e is None:
            self.detail.show_none()
            return
        text, kind = advice_text(e)
        badges = [(text, kind), (t("startup.on") if e.enabled else t("startup.off"), "info")]
        facts = [(t("startup.col_command"), e.command, True)]
        if e.exe and e.exe != e.command:
            facts.append((t("startup.program"), e.exe, True))
        facts.append((t("startup.col_location"), t(f"startup.src_{e.approved_key}_{e.hive}"), False))
        mem = e.extra.get("memory", 0)
        facts.append((t("startup.now"), t("startup.running", mem=format_size(mem)) if e.extra.get("running")
                      else t("startup.not_running"), False))
        actions = []
        if e.exists or not e.removable:
            toggle = button(t("startup.turn_off") if e.enabled else t("startup.turn_on"),
                            "primary" if e.enabled and e.advice == "disable" else None, "startup")
            toggle.clicked.connect(lambda: self._set(e, not e.enabled))
            actions.append(toggle)
            if e.exe and os.path.exists(e.exe):
                where = button(t("startup.open_location"), None, "folder")
                where.clicked.connect(lambda: open_in_file_manager(e.exe))
                actions.append(where)
        else:
            remove = button(t("startup.remove_one"), "danger", "clean")
            remove.clicked.connect(lambda: self._remove([e]))
            actions.append(remove)
        icon = exe_icon(e.exe) if e.exe.lower().endswith(".exe") else theme.icon("startup", size=40)
        self.detail.present(icon, e.display_name, e.company, badges,
                            format_size(mem) if mem else "", reason_text(e), facts, actions,
                            report={"Page": "Startup", "Entry": e.name, "Type": e.approved_key,
                                    "Command": e.command, "Publisher": e.company,
                                    "Category": e.kind or "unknown", "Advice": e.advice})

    # ------------------------------------------------------------- bulk actions

    def apply_advice(self) -> None:
        todo = self._advised()
        if not todo:
            return
        names = "\n".join(f"• {e.display_name}  —  {t('startup.kind_' + (e.kind or 'unknown'))}" for e in todo)
        if not confirm(self, t("startup.apply_advice"), t("startup.apply_confirm", count=len(todo)) + "\n\n" + names
                       + "\n\n" + t("startup.apply_undo"), t("startup.apply_advice")):
            return
        self._apply([], todo, [])

    def remove_broken(self) -> None:
        self._remove([e for e in self.entries if not e.exists and e.removable])

    def _remove(self, entries: list[startup.StartupEntry]) -> None:
        if not entries:
            return
        names = "\n".join(f"• {e.display_name}  ({e.exe or e.command})" for e in entries)
        if not confirm(self, t("startup.remove_broken"), t("startup.remove_confirm") + "\n\n" + names,
                       t("startup.remove_broken")):
            return
        self._apply([], [], entries, quiet=True)
