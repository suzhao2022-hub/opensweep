"""Cleanup confirmation and result dialogs: always show exactly what is (or was) removed."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QHeaderView, QTableWidget, QVBoxLayout

from opensweep.core.engine import CleanReport, Target
from opensweep.core.platform import log_dir, open_in_file_manager
from opensweep.core.units import format_size
from opensweep.gui import theme
from opensweep.gui.widgets import SortItem, badge, button, icon_label, label, style_table
from opensweep.i18n import current, t


PHOTO = 3 * 1024 ** 2  # a typical phone photo, to say "freed 3 GB" in terms anyone can picture


def _item_name(target: Target) -> str:
    name = target.rule.text("name", current())
    if target.rule.itemize or target.kind == "ollama":
        return f"{name} › {target.label}"
    return name


def _group_key(target: Target) -> str:
    """Folders of one rule (e.g. several shader caches) are shown as one row; models stay separate."""
    if target.rule.itemize or target.kind == "ollama":
        return target.key
    return target.rule.id


def group_targets(targets: list[Target]) -> list[list[Target]]:
    groups: dict[str, list[Target]] = {}
    for target in targets:
        groups.setdefault(_group_key(target), []).append(target)
    return sorted(groups.values(), key=lambda g: sum(x.size for x in g), reverse=True)


def _location(group: list[Target]) -> tuple[str, str]:
    paths = [x.path for x in group if x.path]
    if not paths:
        return "", ""
    text = paths[0] if len(paths) == 1 else t("confirm.more_places", path=paths[0], count=len(paths) - 1)
    return text, "\n".join(paths)


def _action(target: Target, use_recycle_bin: bool) -> tuple[str, str]:
    if target.kind == "recycle_bin":
        return t("confirm.action_empty"), "danger"
    if target.kind == "ollama":
        return t("confirm.action_ollama"), "danger"
    if target.rule.risk == "review" and use_recycle_bin and not target.keep_root:
        return t("confirm.action_recycle"), "safe"
    return t("confirm.action_delete"), "info"


class _TableDialog(QDialog):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 600)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 22, 24, 18)
        self.lay.setSpacing(12)

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        style_table(table, 40)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideMiddle)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in range(2, len(headers)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        return table

    def _fill_row(self, table: QTableWidget, row: int, group: list[Target], size: int, cell) -> None:
        name = SortItem(_item_name(group[0]))
        table.setItem(row, 0, name)
        text, tip = _location(group)
        where = SortItem(text)
        where.setToolTip(tip)
        where.setForeground(QColor(theme.C["muted"]))
        table.setItem(row, 1, where)
        size_item = SortItem(format_size(size), size)
        size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.setItem(row, 2, size_item)
        table.setCellWidget(row, 3, cell)


class CleanConfirmDialog(_TableDialog):
    def __init__(self, parent, targets: list[Target], use_recycle_bin: bool):
        super().__init__(parent, t("confirm.title"))
        total = sum(x.size for x in targets)
        groups = group_targets(targets)
        head = QHBoxLayout()
        head.addWidget(icon_label("clean", theme.C["accent"], 26))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(t("confirm.total", count=len(groups), size=format_size(total)), "detailTitle"))
        col.addWidget(label(t("confirm.hint"), "muted", wrap=True))
        head.addLayout(col, 1)
        self.lay.addLayout(head)

        table = self._table([t("confirm.col_item"), t("detail.location"), t("confirm.col_size"),
                             t("confirm.col_action")])
        table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            text, kind = _action(group[0], use_recycle_bin)
            self._fill_row(table, row, group, sum(x.size for x in group), _badge_cell(text, kind))
        self.lay.addWidget(table, 1)

        notes = [t("confirm.permanent")]
        if any(x.rule.risk == "review" for x in targets):
            notes.append(t("confirm.recycle") if use_recycle_bin else t("confirm.recycle_off"))
        if any(x.kind in ("ollama", "recycle_bin") for x in targets):
            notes.append(t("confirm.no_undo"))
        self.lay.addWidget(label("\n".join(notes), "muted", wrap=True))

        cancel = button(t("common.cancel"))
        cancel.clicked.connect(self.reject)
        ok = button(t("confirm.ok"), "primary", "clean")
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.lay.addLayout(row)


def leftovers_explained(report: CleanReport, store=None) -> list[tuple[str, str]]:
    """What was not cleaned and why, as (level, text) lines a non-expert can act on."""
    lines = []
    if report.failed:
        lines.append(("review", t("done.left_in_use", count=report.failed)))
    if report.recycled:
        lines.append(("info", t("done.left_recycled", size=format_size(report.recycled))))
    if store is not None:
        blocked = [r for r in store.results.values() if r.targets and r.blocked == "running"
                   and r.rule.action == "delete"]
        if blocked:
            apps = sorted({a for r in blocked for a in r.running_apps})
            lines.append(("info", t("done.left_running", size=format_size(sum(r.size for r in blocked)),
                                    apps=", ".join(apps[:4]))))
        admin = [r for r in store.results.values() if r.targets and r.blocked == "admin"]
        if admin:
            lines.append(("info", t("done.left_admin", size=format_size(sum(r.size for r in admin)))))
        cleaned = {i.target.rule.id for i in report.items}
        review = [r for r in store.results.values() if r.rule.risk == "review" and r.rule.action == "delete"
                  and r.size and r.rule.id not in cleaned]
        if review:
            lines.append(("info", t("done.left_review", size=format_size(sum(r.size for r in review)))))
    return lines


class CleanResultDialog(_TableDialog):
    def __init__(self, parent, report: CleanReport, store=None):
        super().__init__(parent, t("done.title"))
        left = leftovers_explained(report, store)
        clean = not any(level == "review" for level, _ in left)
        head = QHBoxLayout()
        head.addWidget(icon_label("check" if clean else "info", theme.C["safe" if clean else "review"], 28))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(t("done.freed", size=format_size(report.freed)), "big"))
        col.addWidget(label(t("done.verdict_clean") if clean else t("done.verdict_mostly"), "section"))
        photos = report.freed // PHOTO
        if photos >= 10:
            col.addWidget(label(t("done.equiv", photos=f"{photos:,}"), "muted"))
        head.addLayout(col, 1)
        self.lay.addLayout(head)
        if left:
            box = QVBoxLayout()
            box.setSpacing(4)
            box.addWidget(label(t("done.left_title"), "section"))
            for level, text in left:
                row = QHBoxLayout()
                row.addWidget(icon_label("warning" if level == "review" else "info",
                                         theme.C["review" if level == "review" else "faint"], 16), 0, Qt.AlignTop)
                row.addWidget(label(text, "muted", wrap=True), 1)
                box.addLayout(row)
            self.lay.addLayout(box)

        table = self._table([t("confirm.col_item"), t("detail.location"), t("done.col_freed"), t("done.col_status")])
        by_key: dict[str, list] = {}
        for item in report.items:
            by_key.setdefault(_group_key(item.target), []).append(item)
        groups = sorted(by_key.values(), key=lambda g: sum(i.freed + i.recycled for i in g), reverse=True)
        table.setRowCount(len(groups))
        for row, items in enumerate(groups):
            freed = sum(i.freed + i.recycled for i in items)
            failed = sum(i.failed for i in items)
            errors = [i.error for i in items if i.error]
            if errors:
                cell = _badge_cell(t("done.status_error"), "danger", "\n".join(errors))
            elif any(i.recycled for i in items):
                cell = _badge_cell(t("done.status_recycled"), "safe")
            elif failed:
                cell = _badge_cell(t("done.status_in_use", count=failed), "review")
            else:
                cell = _badge_cell(t("done.status_ok"), "safe")
            self._fill_row(table, row, [i.target for i in items], freed, cell)
        self.lay.addWidget(table, 1)

        logs = button(t("done.open_log"), "ghost", "history")
        logs.clicked.connect(lambda: open_in_file_manager(log_dir()))
        close = button(t("done.close"), "primary")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(logs)
        if report.recycled:  # these can be put back from the Recycle Bin
            bin_btn = button(t("done.open_bin"), "ghost", "history")
            bin_btn.clicked.connect(open_recycle_bin)
            row.addWidget(bin_btn)
        row.addStretch(1)
        row.addWidget(close)
        self.lay.addLayout(row)


def open_recycle_bin() -> None:
    try:
        os.startfile("shell:RecycleBinFolder")  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        pass


def _badge_cell(text: str, kind: str, tooltip: str = ""):
    from PySide6.QtWidgets import QWidget
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(6, 0, 10, 0)
    b = badge(text, kind)
    if tooltip:
        b.setToolTip(tooltip)
    lay.addWidget(b, 0, Qt.AlignVCenter)
    lay.addStretch(1)
    return w
