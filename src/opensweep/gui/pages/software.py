"""Programs: registered software (with an 'is it still used?' verdict) and programs or
leftover folders that Windows does not know about at all."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView,
                               QLineEdit, QListWidget, QListWidgetItem, QSplitter, QTableWidget, QTabWidget,
                               QVBoxLayout, QWidget)

from opensweep.core import appscan, software
from opensweep.core.platform import open_in_file_manager
from opensweep.core.units import format_size
from opensweep.gui import theme
from opensweep.gui.panels import InfoPanel, exe_icon
from opensweep.gui.widgets import (Card, PageHeader, SortItem, button, confirm, error, format_ago, format_date,
                                   icon_label, info, label, page_layout, style_table)
from opensweep.gui.workers import run_task
from opensweep.i18n import t


def program_exe(p: software.Program) -> str:
    icon = software._icon_exe(p.icon)
    if icon and os.path.exists(icon):
        return icon
    if p.install_location and p.exes:
        path = os.path.join(p.install_location, p.exes[0])
        if os.path.exists(path):
            return path
    return ""


def verdict_badge(p: software.Program) -> tuple[str, str]:
    kind, days = software.usage_verdict(p)
    if kind == "unused":
        return t("software.v_unused", days=days), "review"
    if kind == "no_record":
        return t("software.v_no_record"), "info"
    if kind == "new":
        return t("software.v_new"), "safe"
    if kind == "component":
        return t("software.v_component"), "info"
    return t("software.v_recent"), "safe"


# --------------------------------------------------------------------------- dialogs


class LeftoverDialog(QDialog):
    def __init__(self, parent: QWidget, name: str, leftovers: list[tuple[str, int]]):
        super().__init__(parent)
        self.setWindowTitle(t("software.leftovers_title"))
        self.resize(640, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.addWidget(label(t("software.leftovers_body", name=name), wrap=True))
        self.list = QListWidget()
        self.list.setObjectName("plain")
        for path, size in leftovers:
            item = QListWidgetItem(f"{path}    ({format_size(size)})")
            item.setData(Qt.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.list.addItem(item)
        lay.addWidget(self.list, 1)
        buttons = QDialogButtonBox()
        buttons.addButton(t("software.leftovers_remove"), QDialogButtonBox.AcceptRole)
        buttons.addButton(t("common.skip"), QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class DeleteFolderDialog(QDialog):
    def __init__(self, parent: QWidget, app: appscan.FolderApp):
        super().__init__(parent)
        self.setWindowTitle(t("folders.delete"))
        self.resize(560, 260)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(icon_label("warning", theme.C["review"], 26))
        head.addWidget(label(t("folders.delete_title", name=app.display_name, size=format_size(app.size)),
                             "detailTitle", wrap=True), 1)
        lay.addLayout(head)
        path = label(app.path.replace("\\", "\\​"), "path", wrap=True, selectable=True)
        lay.addWidget(path)
        lay.addWidget(label(t("folders.delete_body"), "muted", wrap=True))
        self.recycle = QCheckBox(t("folders.delete_recycle"))
        self.recycle.setChecked(True)
        lay.addWidget(self.recycle)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(t("common.cancel"))
        cancel.clicked.connect(self.reject)
        ok = button(t("folders.delete"), "danger")
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)


def _recycle(paths: list[str]) -> list[str]:
    from send2trash import send2trash
    failed = []
    for path in paths:
        try:
            send2trash(path)
        except Exception as exc:
            failed.append(f"{path}: {exc}")
    return failed


# --------------------------------------------------------------------------- registered programs tab


class RegisteredTab(QWidget):
    COLS = ("name", "size", "last_used", "verdict")

    def __init__(self, page: "SoftwarePage"):
        super().__init__()
        self.page = page
        self.programs: list[software.Program] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(10)

        self.summary = QFrame()
        self.summary.setObjectName("banner")
        srow = QHBoxLayout(self.summary)
        srow.setContentsMargins(14, 8, 8, 8)
        srow.addWidget(icon_label("history", theme.C["review"], 18))
        self.summary_text = label("", wrap=True)
        srow.addWidget(self.summary_text, 1)
        show_unused = button(t("software.show_unused"), "ghost")
        show_unused.clicked.connect(lambda: self.filter.setCurrentIndex(1))
        srow.addWidget(show_unused)
        self.summary.setVisible(False)
        lay.addWidget(self.summary)

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("software.search"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(theme.icon("search"), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._apply_filter)
        self.filter = QComboBox()
        for key in ("all", "maybe", "unused"):
            self.filter.addItem(t(f"software.f_{key}", days=software.UNUSED_DAYS), key)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.filter)
        lay.addLayout(tools)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([t(f"software.col_{c}") for c in self.COLS])
        style_table(self.table, 40)
        self.table.setIconSize(QSize(20, 20))
        self.table.setSortingEnabled(True)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(self.COLS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.detail = InfoPanel(t("software.pick"))
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([620, 440])
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)
        self.status = label("", "muted")
        lay.addWidget(self.status)

    def load(self) -> None:
        self.status.setText(t("software.loading"))
        run_task(software.list_programs, on_done=self._fill_then_sizes, on_error=lambda m: error(self, m))

    def _fill_then_sizes(self, programs: list[software.Program]) -> None:
        """Show the list at once; measure programs whose size the registry doesn't know afterwards."""
        self._fill(programs)
        missing = [p for p in programs if not p.size and p.install_location]
        if not missing:
            return

        def measure_all():
            for p in missing:
                software.fill_missing_size(p)
            return programs

        run_task(measure_all, on_done=lambda progs: progs is self.programs and self._refresh_sizes())

    def _refresh_sizes(self) -> None:
        for row in range(self.table.rowCount()):
            p = self._program_at(row)
            item = self.table.item(row, 1)
            if p and p.size and item.key != p.size:
                item.key = p.size
                item.setText(format_size(p.size))
        total = sum(p.size for p in self.programs)
        self.status.setText(t("software.count", count=len(self.programs), size=format_size(total)))
        self._on_select()

    def _fill(self, programs: list[software.Program]) -> None:
        self.programs = programs
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(programs))
        for row, p in enumerate(programs):
            name = SortItem(p.name)
            name.setIcon(exe_icon(program_exe(p)))
            name.setData(Qt.UserRole, row)
            name.setToolTip(f"{p.name} {p.version}\n{p.publisher}")
            self.table.setItem(row, 0, name)
            size = SortItem(format_size(p.size) if p.size else t("common.unknown"), p.size)
            size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 1, size)
            self.table.setItem(row, 2, SortItem(format_ago(p.last_used), p.last_used or 0))
            text, kind = verdict_badge(p)
            verdict = SortItem(text, {"review": 0, "info": 1, "safe": 2}[kind])
            verdict.setForeground(QBrush(QColor(theme.C[{"review": "review", "info": "muted", "safe": "safe"}[kind]])))
            self.table.setItem(row, 3, verdict)
        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.DescendingOrder)
        total = sum(p.size for p in programs)
        self.status.setText(t("software.count", count=len(programs), size=format_size(total)))
        unused = [p for p in programs if software.usage_verdict(p)[0] == "unused"]
        if unused:
            self.summary_text.setText(t("software.summary", count=len(unused),
                                        size=format_size(sum(p.size for p in unused)),
                                        days=software.UNUSED_DAYS))
        self.summary.setVisible(bool(unused))
        self._apply_filter()
        self._on_select()

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        mode = self.filter.currentData()
        for row in range(self.table.rowCount()):
            p = self._program_at(row)
            if p is None:
                continue
            verdict = software.usage_verdict(p)[0]
            visible = not text or text in p.name.lower() or text in p.publisher.lower()
            if mode == "unused":
                visible = visible and verdict == "unused"
            elif mode == "maybe":
                visible = visible and verdict in ("unused", "no_record")
            self.table.setRowHidden(row, not visible)

    def _program_at(self, row: int) -> software.Program | None:
        item = self.table.item(row, 0)
        return self.programs[item.data(Qt.UserRole)] if item is not None else None

    def current(self) -> software.Program | None:
        rows = self.table.selectionModel().selectedRows()
        return self._program_at(rows[0].row()) if rows else None

    def _on_select(self) -> None:
        p = self.current()
        if p is None:
            self.detail.show_none()
            return
        kind, days = software.usage_verdict(p)
        text, badge_kind = verdict_badge(p)
        reason = t("software.reason_today") if kind == "recent" and days == 0 else \
            t(f"software.reason_{kind}", days=days)
        facts = []
        if p.publisher:
            facts.append((t("software.col_publisher"), p.publisher, False))
        if p.version:
            facts.append((t("software.version"), p.version, False))
        facts.append((t("software.col_installed"), format_date(p.install_date), False))
        facts.append((t("software.col_last_used"),
                      format_date(p.last_used) + (f" ({format_ago(p.last_used)})" if p.last_used else ""), False))
        if p.install_location:
            facts.append((t("detail.location"), p.install_location, True))
        uninstall = button(t("software.uninstall"), "primary", "clean")
        uninstall.clicked.connect(lambda: self.page.uninstall(p))
        actions = [uninstall]
        if p.install_location and os.path.isdir(p.install_location):
            open_btn = button(t("software.open_location"), None, "folder")
            open_btn.clicked.connect(lambda: open_in_file_manager(p.install_location))
            actions.append(open_btn)
        items = []
        if p.install_location and p.exes:
            items = [(exe_icon(os.path.join(p.install_location, e)), e, os.path.join(p.install_location, e))
                     for e in p.exes[:20]]
        self.detail.present(exe_icon(program_exe(p)), p.name, p.publisher, [(text, badge_kind)],
                         format_size(p.size) if p.size else t("common.unknown"), reason, facts, actions,
                         t("software.programs_inside"), items,
                         report={"Page": "Programs", "Program": p.name, "Publisher": p.publisher,
                                 "Verdict": kind, "Location": p.install_location})


# --------------------------------------------------------------------------- folder apps tab


class FoldersTab(QWidget):
    COLS = ("name", "kind", "size", "last_used")

    def __init__(self, page: "SoftwarePage"):
        super().__init__()
        self.page = page
        self.apps: list[appscan.FolderApp] = []
        self.scanned = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 0)
        lay.setSpacing(10)

        intro = Card()
        row = QHBoxLayout()
        row.addWidget(icon_label("info", theme.C["accent"], 18), 0, Qt.AlignTop)
        row.addWidget(label(t("folders.intro"), "muted", wrap=True), 1)
        self.scan_btn = button(t("folders.scan"), "primary", "scan")
        self.scan_btn.clicked.connect(self.scan)
        row.addWidget(self.scan_btn, 0, Qt.AlignVCenter)
        intro.layout_.addLayout(row)
        lay.addWidget(intro)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([t(f"folders.col_{c}") for c in self.COLS])
        style_table(self.table, 40)
        self.table.setIconSize(QSize(20, 20))
        self.table.setSortingEnabled(True)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(self.COLS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.detail = InfoPanel(t("folders.pick"))
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.table)
        split.addWidget(self.detail)
        split.setSizes([620, 440])
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)
        self.status = label(t("folders.not_scanned"), "muted")
        lay.addWidget(self.status)

    def scan(self) -> None:
        self.scanned = True
        self.scan_btn.setEnabled(False)
        self.status.setText(t("folders.scanning", path=""))
        programs = self.page.registered.programs or None
        run_task(appscan.find_folder_apps, programs, on_done=self._fill,
                 on_error=lambda m: (self.scan_btn.setEnabled(True), error(self, m)),
                 on_progress=lambda p: self.status.setText(t("folders.scanning", path=p)))

    def _fill(self, apps: list[appscan.FolderApp]) -> None:
        self.apps = apps
        self.scan_btn.setEnabled(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(apps))
        for row, a in enumerate(apps):
            text = a.name if not a.description or a.description.lower() == a.name.lower() \
                else f"{a.name}  —  {a.description}"
            name = SortItem(text)
            name.setIcon(exe_icon(a.main_exe) if a.main_exe else theme.icon("folder", theme.C["review"]))
            name.setData(Qt.UserRole, row)
            name.setToolTip(a.path)
            self.table.setItem(row, 0, name)
            kind = SortItem(t(f"folders.kind_{a.kind}"))
            kind.setForeground(QBrush(QColor(theme.C["review" if a.kind == "leftover" else "muted"])))
            self.table.setItem(row, 1, kind)
            size = SortItem(format_size(a.size), a.size)
            size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 2, size)
            self.table.setItem(row, 3, SortItem(format_ago(a.last_used), a.last_used or 0))
        self.table.setSortingEnabled(True)
        self.table.sortItems(2, Qt.DescendingOrder)
        self.status.setText(t("folders.found", count=len(apps), size=format_size(sum(a.size for a in apps))))
        self._on_select()

    def current(self) -> appscan.FolderApp | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        idx = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        return self.apps[idx]

    def _on_select(self) -> None:
        a = self.current()
        if a is None:
            self.detail.show_none()
            return
        badges = [(t(f"folders.kind_{a.kind}"), "review" if a.kind == "leftover" else "info")]
        if a.running:
            badges.append((t("folders.running"), "danger"))
        reason = t(f"folders.reason_{a.kind}")
        if a.running:
            reason += "\n\n" + t("folders.running_hint", apps=", ".join(a.running))
        facts = [(t("detail.location"), a.path, True)]
        if a.main_exe:
            facts.append((t("folders.main_exe"), os.path.relpath(a.main_exe, a.path), True))
        facts.append((t("software.col_last_used"),
                      format_date(a.last_used) + (f" ({format_ago(a.last_used)})" if a.last_used else ""), False))
        facts.append((t("detail.modified"), format_date(a.newest), False))
        facts.append((t("detail.col_files"), str(a.files), False))
        delete = button(t("folders.delete"), "danger", "clean")
        delete.setEnabled(not a.running)
        delete.clicked.connect(lambda: self._delete(a))
        open_btn = button(t("clean.open_folder"), None, "folder")
        open_btn.clicked.connect(lambda: open_in_file_manager(a.path))
        items = [(exe_icon(e), os.path.relpath(e, a.path), e) for e in a.exes[:20]]
        self.detail.present(exe_icon(a.main_exe) if a.main_exe else theme.icon("folder", theme.C["review"], size=40),
                         a.name, a.description, badges, format_size(a.size), reason,
                         facts, [delete, open_btn], t("software.programs_inside"), items,
                         report={"Page": "Unlisted programs", "Folder": a.path, "Kind": a.kind,
                                 "Main program": a.main_exe, "Description": a.description})

    def _delete(self, a: appscan.FolderApp) -> None:
        dlg = DeleteFolderDialog(self, a)
        if not dlg.exec():
            return
        recycle = dlg.recycle.isChecked()
        self.status.setText(t("folders.deleting", name=a.display_name))

        def done(result) -> None:
            if a in self.apps:
                row = self.apps.index(a)
                for r in range(self.table.rowCount()):
                    if self.table.item(r, 0).data(Qt.UserRole) == row:
                        self.table.setRowHidden(r, True)
            msg = t("folders.deleted_recycle", name=a.display_name) if recycle else \
                t("folders.deleted", name=a.display_name, size=format_size(result.freed))
            if result.failed:
                msg += "\n" + t("done.in_use", count=result.failed)
            self.status.setText(msg)
            info(self, t("folders.delete"), msg)
            self.table.clearSelection()
            self.page.changed.emit()

        run_task(appscan.delete_folder_app, a, recycle, on_done=done,
                 on_error=lambda m: error(self, t("folders.delete_failed", name=a.display_name) + "\n" + m))


# --------------------------------------------------------------------------- page


class SoftwarePage(QWidget):
    changed = Signal()  # a program or folder was removed: startup entries and the checkup are out of date

    def __init__(self):
        super().__init__()
        self.loaded = False
        lay = page_layout(self)
        self.refresh_btn = button(t("software.refresh"), None, "refresh")
        self.refresh_btn.clicked.connect(self.load)
        lay.addWidget(PageHeader(t("software.title"), t("software.desc"), self.refresh_btn))

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.registered = RegisteredTab(self)
        self.folders = FoldersTab(self)
        self.tabs.addTab(self.registered, theme.icon("software"), t("software.tab_registered"))
        self.tabs.addTab(self.folders, theme.icon("folder"), t("software.tab_folders"))
        self.tabs.currentChanged.connect(self._on_tab)
        lay.addWidget(self.tabs, 1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self.loaded:
            self.load()

    def load(self) -> None:
        self.loaded = True
        self.registered.load()
        if self.folders.scanned:
            self.folders.scan()

    def focus(self, what: str) -> None:
        """'unused' shows unused programs, 'folders' the unlisted-programs tab."""
        if what == "folders":
            self.tabs.setCurrentWidget(self.folders)
        elif what == "unused":
            self.tabs.setCurrentWidget(self.registered)
            self.registered.filter.setCurrentIndex(2)

    def _on_tab(self, index: int) -> None:
        if self.tabs.widget(index) is self.folders and not self.folders.scanned:
            self.folders.scan()

    def uninstall(self, p: software.Program) -> None:
        if not confirm(self, t("software.uninstall"), t("software.confirm", name=p.name), t("software.uninstall")):
            return
        self.registered.status.setText(t("software.waiting", name=p.name))
        self.tabs.setEnabled(False)

        def work():
            code = software.uninstall(p)
            if code == -1:
                return "failed", []
            # Many uninstallers copy themselves to %TEMP% and exit at once, so poll for a while.
            deadline = time.time() + 45
            while software.still_installed(p) and time.time() < deadline:
                time.sleep(1.5)
            if software.still_installed(p):
                return "still", []
            return "ok", software.find_leftovers(p)

        run_task(work, on_done=lambda res: self._after_uninstall(p, *res),
                 on_error=lambda m: self._after_uninstall(p, "error", [], m))

    def _after_uninstall(self, p: software.Program, state: str, leftovers, message: str = "") -> None:
        self.tabs.setEnabled(True)
        if state in ("failed", "error"):
            error(self, t("software.failed", name=p.name) + (f"\n{message}" if message else ""))
        elif state == "still":
            info(self, t("software.uninstall"), t("software.still_installed", name=p.name))
        elif leftovers:
            dlg = LeftoverDialog(self, p.name, leftovers)
            if dlg.exec() == QDialog.Accepted and dlg.selected():
                failed = _recycle(dlg.selected())
                if failed:
                    error(self, "\n".join(failed))
                else:
                    info(self, t("software.uninstall"), t("software.leftovers_done"))
        else:
            info(self, t("software.uninstall"), t("software.removed", name=p.name))
        if state == "ok":
            self.changed.emit()
        self.registered.load()
