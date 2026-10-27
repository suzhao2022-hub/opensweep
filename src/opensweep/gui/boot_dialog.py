"""'What started after the last boot?': every program that came up by itself, and who started it."""

from __future__ import annotations

import time

from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem, QVBoxLayout

from opensweep.core import boottrace
from opensweep.gui import theme
from opensweep.gui.widgets import button, error, label, style_table
from opensweep.gui.workers import run_task
from opensweep.i18n import t

# Helper processes that say nothing useful to the user.
HIDDEN = {"crashpad_handler.exe", "conhost.exe", "msedgewebview2.exe"}
ORDER = {boottrace.OFF: 0, boottrace.OTHER: 1, boottrace.SERVICE: 2, boottrace.VIA: 3, boottrace.ON: 4}


def explain(item: boottrace.BootItem) -> str:
    """One plain sentence on why this program started."""
    parent = item.parent or ""
    if item.verdict == boottrace.ON:
        return t("boot.v_on", entry=item.entry)
    if item.verdict == boottrace.OFF:
        key = "boot.v_off" if parent else "boot.v_off_gone"
        return t(key, entry=item.entry, parent=parent)
    if item.verdict == boottrace.VIA:
        return t("boot.v_via_on" if item.entry_on else "boot.v_via_off", entry=item.entry, parent=parent)
    if item.verdict == boottrace.SERVICE:
        return t("boot.v_service")
    return t("boot.v_other", parent=parent) if parent else t("boot.v_other_gone")


def when_text(seconds: int) -> str:
    return t("boot.sec", n=max(seconds, 0))


def report_text(report: boottrace.BootReport) -> str:
    return report.to_json()


class BootDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(t("boot.title"))
        self.resize(1040, 620)
        self.items: list[boottrace.BootItem] = []
        self.report: boottrace.BootReport | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(label(t("boot.title"), "big"))
        self.intro = label(t("boot.loading"), "muted", wrap=True)
        lay.addWidget(self.intro)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([t("boot.col_app"), t("boot.col_when"), t("boot.col_why")])
        style_table(self.table, 44)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.copy_btn = button(t("boot.copy"), None, "copy")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        close = button(t("boot.close"), "primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        run_task(boottrace.collect, on_done=self._fill, on_error=self._failed)

    def _failed(self, message: str) -> None:
        self.intro.setText(t("boot.failed"))
        error(self, message)

    def _fill(self, report: boottrace.BootReport) -> None:
        self.report = report
        items = [i for i in report.items if i.name.lower() not in HIDDEN]
        self.items = sorted(items, key=lambda i: (ORDER[i.verdict], i.seconds))
        booted = time.strftime("%Y-%m-%d %H:%M", time.localtime(report.booted_at))
        text = t("boot.intro", when=booted, count=len(self.items))
        if report.errors:
            text += "\n" + t("boot.partial")
        self.intro.setText(text)
        self.table.setRowCount(len(self.items))
        warn = QColor(theme.C["review"])
        for r, i in enumerate(self.items):
            problem = i.verdict in (boottrace.OFF, boottrace.OTHER)
            cells = (i.name, when_text(i.seconds), explain(i))
            for c, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setToolTip(i.exe)
                if problem and c == 2:
                    cell.setForeground(warn)
                self.table.setItem(r, c, cell)
        self.table.resizeRowsToContents()
        self.copy_btn.setEnabled(True)

    def _copy(self) -> None:
        if self.report is None:
            return
        QGuiApplication.clipboard().setText(report_text(self.report))
        self.copy_btn.setText(t("boot.copied"))
