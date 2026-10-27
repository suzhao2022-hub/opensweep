"""Shared right-hand detail panel and program icons."""

from __future__ import annotations

import os
import re

from PySide6.QtCore import QFileInfo, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QFileIconProvider, QFrame, QGridLayout, QHBoxLayout, QListWidget, QListWidgetItem,
                               QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from opensweep.core import feedback
from opensweep.gui import theme
from opensweep.gui.widgets import badge, button, icon_label, label
from opensweep.i18n import t

_icons: QFileIconProvider | None = None


def report_button() -> QWidget:
    b = button(t("feedback.wrong"), "ghost", "info")
    b.setToolTip(t("feedback.wrong_tip"))
    return b


def open_report(title: str, details: dict) -> None:
    """Open a pre-filled GitHub issue in the browser; the user reviews it there before sending."""
    url = feedback.issue_url("misjudgment", f"[Misjudgment] {title}", details)
    QDesktopServices.openUrl(QUrl(url))


def exe_icon(path: str) -> QIcon:
    global _icons
    if path and os.path.exists(path):
        if _icons is None:
            _icons = QFileIconProvider()
        return _icons.icon(QFileInfo(path))
    return theme.icon("software")


# --------------------------------------------------------------------------- detail panel


class InfoPanel(QFrame):
    """Right-hand panel showing everything we know about one program or folder."""

    def __init__(self, empty_text: str):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumWidth(380)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        el.addWidget(icon_label("info", theme.C["faint"], 32), 0, Qt.AlignHCenter)
        hint = label(empty_text, "muted", wrap=True)
        hint.setAlignment(Qt.AlignCenter)
        el.addWidget(hint)
        el.addStretch(2)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.icon = label()
        self.icon.setFixedSize(40, 40)
        head.addWidget(self.icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = label("", "detailTitle", wrap=True, selectable=True)
        self.kicker = label("", "muted", wrap=True)
        col.addWidget(self.title)
        col.addWidget(self.kicker)
        head.addLayout(col, 1)
        lay.addLayout(head)
        self.badges = QHBoxLayout()
        self.badges.setSpacing(6)
        lay.addLayout(self.badges)
        self.size = label("", "big")
        lay.addWidget(self.size)
        self.reason = label("", "", wrap=True)
        lay.addWidget(self.reason)
        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(14)
        self.facts.setVerticalSpacing(6)
        lay.addLayout(self.facts)
        self.actions = QHBoxLayout()
        lay.addLayout(self.actions)
        self.list_title = label("", "section")
        lay.addWidget(self.list_title)
        self.list = QListWidget()
        self.list.setObjectName("plain")
        self.list.setIconSize(QSize(18, 18))
        self.list.setMinimumHeight(160)
        lay.addWidget(self.list)
        lay.addStretch(1)
        self.report_btn = report_button()
        self.report_btn.clicked.connect(self._report)
        lay.addWidget(self.report_btn, 0, Qt.AlignLeft)
        self._report_info: tuple[str, dict] | None = None

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(scroll)
        outer.addWidget(self.stack)

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def show_none(self) -> None:
        self.stack.setCurrentIndex(0)

    def _report(self) -> None:
        if self._report_info:
            open_report(*self._report_info)

    def present(self, icon: QIcon, title: str, kicker: str, badges: list[tuple[str, str]], size: str, reason: str,
                facts: list[tuple[str, str, bool]], actions: list[QWidget], list_title: str = "",
                items: list[tuple[QIcon, str, str]] | None = None, report: dict | None = None) -> None:
        """`report`: details sent along if the user clicks "Is this wrong?" (they see it before sending)."""
        self.stack.setCurrentIndex(1)
        self._report_info = (title, {**(report or {}), "Shown advice": " / ".join(b[0] for b in badges),
                                     "Shown text": reason[:600]})
        self.icon.setPixmap(icon.pixmap(40, 40))
        self.title.setText(re.sub(r"([_.\\/-])", "\\1\u200b", title) if len(title) > 30 else title)
        # Let long paths wrap after each separator instead of widening the panel.
        self.kicker.setText(kicker.replace("\\", "\\​").replace("/", "/​"))
        self.kicker.setVisible(bool(kicker))
        self._clear(self.badges)
        for text, kind in badges:
            self.badges.addWidget(badge(text, kind))
        self.badges.addStretch(1)
        self.size.setText(size)
        self.reason.setText(reason)
        self.reason.setVisible(bool(reason))
        self._clear(self.facts)
        for row, (name, value, is_path) in enumerate(facts):
            self.facts.addWidget(label(name, "muted"), row, 0, Qt.AlignTop)
            if is_path:
                value = value.replace("\\", "\\​")
            self.facts.addWidget(label(value, "path" if is_path else "", wrap=True, selectable=True), row, 1)
        self.facts.setColumnStretch(1, 1)
        self._clear(self.actions)
        for w in actions:
            self.actions.addWidget(w)
        self.actions.addStretch(1)
        self.list_title.setText(list_title)
        self.list_title.setVisible(bool(items))
        self.list.setVisible(bool(items))
        self.list.clear()
        for ic, text, tip in items or []:
            it = QListWidgetItem(ic, text)
            it.setToolTip(tip)
            self.list.addItem(it)
