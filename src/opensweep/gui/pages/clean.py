"""Cleanup page: a checklist of everything found, and a detail panel that shows
exactly what is inside the selected item before anything is deleted."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLineEdit, QProgressBar,
                               QScrollArea, QSplitter, QStackedWidget, QTableWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from opensweep.core import engine
from opensweep.core.engine import RuleResult, Target
from opensweep.core.fsutil import Entry, Inspection, Stats
from opensweep.core.platform import open_in_file_manager
from opensweep.core.units import format_size
from opensweep.gui import theme
from opensweep.gui.dialogs import CleanConfirmDialog, CleanResultDialog
from opensweep.gui.panels import open_report, report_button
from opensweep.gui.store import ScanStore
from opensweep.gui.widgets import (ROLE_RATIO, Card, PageHeader, SizeBarDelegate, SortItem, badge, button,
                                   format_date, icon_label, label, page_layout, style_table)
from opensweep.gui.workers import run_task
from opensweep.i18n import current, t
from opensweep.settings import Settings

ROLE_TARGETS = Qt.UserRole + 1
ROLE_RESULT = Qt.UserRole + 2
ROLE_CATEGORY = Qt.UserRole + 3
COL_NAME, COL_SIZE, COL_TYPE = range(3)


def _inspect_targets(targets: list[Target]) -> Inspection:
    """One target: what is inside it. Several: one row per target, file types merged."""
    if len(targets) == 1:
        return engine.inspect_target(targets[0])
    entries, types, total, oldest = [], {}, Stats(), 0.0
    for target in targets:
        info = engine.inspect_target(target)
        total.add(info.total)
        if info.oldest and (not oldest or info.oldest < oldest):
            oldest = info.oldest
        for ext, size, count in info.types:
            bucket = types.setdefault(ext, [0, 0])
            bucket[0] += size
            bucket[1] += count
        name = target.label if (target.rule.itemize or target.kind == "ollama") else _short_path(target.path)
        entries.append(Entry(name, target.path, info.total.size or target.size, info.total.files or target.files,
                             bool(target.path) and os.path.isdir(target.path), info.total.newest or target.newest))
    entries.sort(key=lambda e: e.size, reverse=True)
    type_list = sorted(((e, v[0], v[1]) for e, v in types.items()), key=lambda x: x[1], reverse=True)
    return Inspection(entries, type_list, total, oldest)


def _short_path(path: str) -> str:
    parts = os.path.normpath(path).split(os.sep)
    return os.sep.join(parts[-3:]) if len(parts) > 3 else path


# --------------------------------------------------------------------------- detail panel


class DetailPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumWidth(400)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._gen = 0
        self._paths: list[str] = []

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        el.addWidget(icon_label("info", theme.C["faint"], 32), 0, Qt.AlignHCenter)
        hint = label(t("detail.pick"), "muted", wrap=True)
        hint.setAlignment(Qt.AlignCenter)
        el.addWidget(hint)
        el.addStretch(2)

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        self.kicker = label("", "faint")
        lay.addWidget(self.kicker)
        self.title = label("", "detailTitle", wrap=True, selectable=True)
        lay.addWidget(self.title)
        self.badges = QHBoxLayout()
        self.badges.setSpacing(6)
        lay.addLayout(self.badges)
        size_row = QHBoxLayout()
        self.size = label("", "big")
        self.count = label("", "muted")
        size_row.addWidget(self.size)
        size_row.addWidget(self.count, 0, Qt.AlignBottom)
        size_row.addStretch(1)
        lay.addLayout(size_row)
        self.desc = label("", "", wrap=True, selectable=True)
        lay.addWidget(self.desc)

        self.facts = QGridLayout()
        self.facts.setHorizontalSpacing(14)
        self.facts.setVerticalSpacing(6)
        lay.addLayout(self.facts)

        actions = QHBoxLayout()
        self.open_btn = button(t("clean.open_folder"), None, "folder")
        self.open_btn.clicked.connect(self._open)
        self.copy_btn = button(t("detail.copy_path"), None, "copy")
        self.copy_btn.clicked.connect(self._copy)
        actions.addWidget(self.open_btn)
        actions.addWidget(self.copy_btn)
        actions.addStretch(1)
        lay.addLayout(actions)

        lay.addSpacing(4)
        lay.addWidget(label(t("detail.contents"), "section"))
        self.loading = label(t("detail.loading"), "muted")
        lay.addWidget(self.loading)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([t("detail.col_name"), t("detail.col_size"), t("detail.col_files"),
                                              t("detail.col_modified")])
        style_table(self.table, 32)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(220)
        self.table.cellDoubleClicked.connect(self._open_entry)
        lay.addWidget(self.table)
        self.more = label("", "faint")
        lay.addWidget(self.more)

        self.types_title = label(t("detail.types"), "section")
        lay.addWidget(self.types_title)
        self.types = QGridLayout()
        self.types.setHorizontalSpacing(10)
        self.types.setVerticalSpacing(6)
        lay.addLayout(self.types)
        lay.addStretch(1)
        self.report_btn = report_button()
        self.report_btn.clicked.connect(lambda: self._report and open_report(*self._report))
        lay.addWidget(self.report_btn, 0, Qt.AlignLeft)
        self._report: tuple[str, dict] | None = None

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(scroll)
        outer.addWidget(self.stack)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _fact(self, row: int, name: str, value: str, path: bool = False) -> None:
        self.facts.addWidget(label(name, "muted"), row, 0, Qt.AlignTop)
        if path:  # let long paths wrap after each separator
            value = value.replace("\\", "\\​").replace("/", "/​")
        v = label(value, "path" if path else "", wrap=True, selectable=True)
        self.facts.addWidget(v, row, 1)
        self.facts.setColumnStretch(1, 1)

    def show_none(self) -> None:
        self._gen += 1
        self.stack.setCurrentIndex(0)

    def show_category(self, category: str, results: list[RuleResult]) -> None:
        size = sum(r.size for r in results if r.rule.action == "delete")
        self._report = None
        self._show(t(f"cat.{category}"), t("nav.clean"), size, t("detail.category", count=len(results)),
                   "", [], [], targets_for_contents=None, rule_rows=results)

    def show_rule(self, res: RuleResult, targets: list[Target], item_label: str | None = None) -> None:
        rule = res.rule
        lang = current()
        title = item_label or rule.text("name", lang)
        kicker = t(f"cat.{rule.category}") + (f"  ›  {rule.text('name', lang)}" if item_label else "")
        size = sum(x.size for x in targets)
        files = sum(x.files for x in targets)
        badges = []
        if rule.action == "report":
            badges.append((t("risk.report"), "info"))
        else:
            badges.append((t(f"risk.{rule.risk}"), "safe" if rule.risk == "safe" else "review"))
        if res.blocked == "admin":
            badges.append((t("blocked.admin"), "danger"))
        elif res.blocked == "running":
            badges.append((t("blocked.running", apps=", ".join(res.running_apps)), "danger"))
        desc = rule.text("description", lang)
        if targets and targets[0].kind == "ollama":
            desc += "\n\n" + t("detail.ollama")
        if targets and targets[0].kind == "recycle_bin":
            desc += "\n\n" + t("detail.recycle_bin", count=files)
        facts = []
        paths = [x.path for x in targets if x.path and x.kind in ("path", "multi")]
        if paths:
            shown = "\n".join(paths[:4]) + ("\n…" if len(paths) > 4 else "")
            facts.append((t("detail.location"), shown, True))
        newest = max((x.newest for x in targets), default=0)
        if newest:
            facts.append((t("detail.modified"), format_date(newest), False))
        if rule.min_age_days:
            facts.append((t("detail.kept"), t("detail.kept_value", days=rule.min_age_days), False))
        self._report = (title, {"Page": "Junk files", "Rule": rule.id, "Item": item_label or "",
                                "Risk": rule.risk, "Paths": "; ".join(paths[:3]),
                                "Shown text": rule.text("description", "en")})
        self._show(title, kicker, size, t("detail.files", count=files), desc, badges, facts,
                   targets_for_contents=targets, paths=paths)

    def _show(self, title, kicker, size, count, desc, badges, facts, targets_for_contents, paths=None,
              rule_rows=None) -> None:
        self._gen += 1
        gen = self._gen
        self.stack.setCurrentIndex(1)
        self.kicker.setText(kicker)
        self.title.setText(title)
        self._clear(self.badges)
        for text, kind in badges:
            self.badges.addWidget(badge(text, kind))
        self.badges.addStretch(1)
        self.size.setText(format_size(size))
        self.count.setText(count)
        self.desc.setText(desc)
        self.desc.setVisible(bool(desc))
        self.report_btn.setVisible(self._report is not None)
        self._clear(self.facts)
        for row, (name, value, is_path) in enumerate(facts):
            self._fact(row, name, value, is_path)
        self._paths = paths or []
        self.open_btn.setVisible(bool(self._paths))
        self.copy_btn.setVisible(bool(self._paths))
        self.table.setRowCount(0)
        self.more.setText("")
        self._clear(self.types)
        self.types_title.setVisible(False)

        if rule_rows is not None:
            lang = current()
            entries = [Entry(r.rule.text("name", lang), "", r.size, sum(x.files for x in r.targets), False,
                             max((x.newest for x in r.targets), default=0)) for r in rule_rows]
            entries.sort(key=lambda e: e.size, reverse=True)
            self.loading.setVisible(False)
            self._fill(Inspection(entries, [], Stats()))
            return
        if not targets_for_contents:
            self.loading.setVisible(False)
            return
        self.loading.setText(t("detail.loading"))
        self.loading.setVisible(True)
        run_task(_inspect_targets, list(targets_for_contents),
                 on_done=lambda info: gen == self._gen and self._fill(info),
                 on_error=lambda msg: gen == self._gen and self.loading.setText(msg))

    def _fill(self, info: Inspection) -> None:
        self.loading.setVisible(not info.entries)
        if not info.entries:
            self.loading.setText(t("detail.empty"))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(info.entries))
        folder_icon = theme.icon("folder", theme.C["review"])
        file_icon = theme.icon("copy", theme.C["faint"])
        for row, e in enumerate(info.entries):
            name = SortItem(e.name)
            name.setIcon(folder_icon if e.is_dir else file_icon)
            name.setToolTip(e.path or e.name)
            name.setData(Qt.UserRole, e.path)
            self.table.setItem(row, 0, name)
            size = SortItem(format_size(e.size), e.size)
            size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 1, size)
            files = SortItem(str(e.files), e.files)
            files.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 2, files)
            self.table.setItem(row, 3, SortItem(format_date(e.newest) if e.newest else "", e.newest))
        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.DescendingOrder)
        if info.more:
            self.more.setText(t("detail.more", count=info.more))
        if info.oldest:
            self._fact(self.facts.rowCount(), t("detail.oldest"), format_date(info.oldest))
        if info.types:
            self.types_title.setVisible(True)
            top = info.types[:6]
            biggest = top[0][1] or 1
            for row, (ext, size, count) in enumerate(top):
                name = ext if ext else t("detail.no_ext")
                self.types.addWidget(label(name), row, 0)
                bar = QProgressBar()
                bar.setRange(0, 1000)
                bar.setValue(int(1000 * size / biggest))
                bar.setTextVisible(False)
                self.types.addWidget(bar, row, 1)
                self.types.addWidget(label(f"{format_size(size)} · {t('detail.files', count=count)}", "muted"),
                                     row, 2)
            self.types.setColumnStretch(1, 1)

    def _open(self) -> None:
        if self._paths:
            open_in_file_manager(self._paths[0])

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(self._paths))

    def _open_entry(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        path = item.data(Qt.UserRole) if item else None
        if path and os.path.exists(path):
            open_in_file_manager(path)


# --------------------------------------------------------------------------- page


class CleanPage(QWidget):
    def __init__(self, store: ScanStore, settings: Settings, categories: tuple[str, ...], title: str, desc: str):
        super().__init__()
        self.store, self.settings, self.categories = store, settings, categories
        self._updating = False
        lay = page_layout(self)

        self.scan_btn = button(t("clean.scan"), None, "refresh")
        self.scan_btn.clicked.connect(lambda: self.store.cancel() if self.store.busy else self.store.scan())
        lay.addWidget(PageHeader(title, desc, self.scan_btn))

        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(t("clean.search"))
        self.search.setClearButtonEnabled(True)
        self.search.addAction(theme.icon("search"), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self._apply_filter)
        self.filter = QComboBox()
        for key in ("all", "safe", "review"):
            self.filter.addItem(t(f"clean.filter_{key}"), key)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        select_safe = button(t("clean.select_recommended"), "ghost")
        select_safe.clicked.connect(lambda: self._select(recommended=True))
        select_none = button(t("clean.select_none"), "ghost")
        select_none.clicked.connect(lambda: self._select(recommended=False))
        tools.addWidget(self.search, 1)
        tools.addWidget(self.filter)
        tools.addSpacing(8)
        tools.addWidget(select_safe)
        tools.addWidget(select_none)
        lay.addLayout(tools)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels([t("clean.col_name"), t("clean.col_size"), t("clean.col_type")])
        header = self.tree.header()
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_SIZE, QHeaderView.Fixed)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeToContents)
        header.resizeSection(COL_SIZE, 130)
        header.setStretchLastSection(False)
        self.tree.setItemDelegateForColumn(COL_SIZE, SizeBarDelegate(self.tree))
        self.tree.setIndentation(18)
        self.tree.setAnimated(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.currentItemChanged.connect(self._on_current)

        self.detail = DetailPanel()
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([540, 500])
        splitter.setChildrenCollapsible(False)

        empty = Card()
        empty.layout_.addStretch(1)
        empty.layout_.addWidget(icon_label("scan", theme.C["accent"], 40), 0, Qt.AlignHCenter)
        self.empty_text = label(t("clean.not_scanned"), "muted", wrap=True)
        self.empty_text.setAlignment(Qt.AlignCenter)
        empty.layout_.addWidget(self.empty_text)
        self.empty_btn = button(t("home.scan"), "primary", "scan")
        self.empty_btn.clicked.connect(lambda: self.store.scan())
        empty.layout_.addWidget(self.empty_btn, 0, Qt.AlignHCenter)
        empty.layout_.addStretch(2)

        self.body = QStackedWidget()
        self.body.addWidget(empty)
        self.body.addWidget(splitter)
        lay.addWidget(self.body, 1)

        footer = QHBoxLayout()
        self.status = label("", "muted")
        self.selected = label("", "section")
        self.clean_btn = button(t("clean.clean_selected", size="0 B"), "primary", "clean")
        self.clean_btn.clicked.connect(self._clean)
        self.clean_btn.setEnabled(False)
        footer.addWidget(self.status, 1)
        footer.addWidget(self.selected)
        footer.addSpacing(12)
        footer.addWidget(self.clean_btn)
        lay.addLayout(footer)

        store.scan_finished.connect(self.populate)
        store.busy_changed.connect(self._on_busy)
        store.progress.connect(lambda text: self.status.setText(t("home.working", name=text)))
        store.clean_finished.connect(self._on_cleaned)
        self.populate()

    # ----------------------------------------------------------- building

    def _rule_row(self, res: RuleResult, biggest: int) -> QTreeWidgetItem:
        rule = res.rule
        lang = current()
        color = theme.CATEGORY_COLORS.get(rule.category, theme.C["accent"])
        item = QTreeWidgetItem([rule.text("name", lang), format_size(res.size), ""])
        item.setTextAlignment(COL_SIZE, Qt.AlignRight | Qt.AlignVCenter)
        item.setData(COL_SIZE, ROLE_RATIO, res.size / biggest if biggest else 0)
        item.setData(COL_SIZE, ROLE_RATIO + 1, color)
        item.setData(COL_NAME, ROLE_RESULT, res)
        item.setToolTip(COL_NAME, rule.text("description", lang))
        if rule.action == "report":
            item.setText(COL_TYPE, t("risk.report"))
            item.setForeground(COL_TYPE, QBrush(QColor(theme.C["muted"])))
        elif res.blocked:
            item.setText(COL_TYPE, t("blocked.admin") if res.blocked == "admin"
                         else t("blocked.running", apps=", ".join(res.running_apps)))
            item.setForeground(COL_TYPE, QBrush(QColor(theme.C["danger"])))
        else:
            item.setText(COL_TYPE, t(f"risk.{rule.risk}"))
            item.setForeground(COL_TYPE, QBrush(QColor(theme.C["safe" if rule.risk == "safe" else "review"])))
        enabled = res.cleanable
        if rule.itemize > 0 or rule.provider == "ollama":
            if enabled:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            for target in res.targets:
                child = QTreeWidgetItem([target.label, format_size(target.size), ""])
                child.setTextAlignment(COL_SIZE, Qt.AlignRight | Qt.AlignVCenter)
                child.setData(COL_SIZE, ROLE_RATIO, target.size / res.size if res.size else 0)
                child.setData(COL_SIZE, ROLE_RATIO + 1, color)
                child.setToolTip(COL_NAME, target.path or target.label)
                child.setData(COL_NAME, ROLE_TARGETS, [target])
                child.setData(COL_NAME, ROLE_RESULT, res)
                if target.newest:
                    child.setText(COL_TYPE, format_date(target.newest))
                    child.setForeground(COL_TYPE, QBrush(QColor(theme.C["faint"])))
                if enabled:
                    child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                    child.setCheckState(COL_NAME, Qt.Unchecked)
                else:
                    child.setForeground(COL_NAME, QBrush(QColor(theme.C["faint"])))
                item.addChild(child)
            if enabled:
                item.setCheckState(COL_NAME, Qt.Unchecked)
        else:
            item.setData(COL_NAME, ROLE_TARGETS, list(res.targets))
            if enabled:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(COL_NAME, Qt.Checked if rule.risk == "safe" else Qt.Unchecked)
        if not enabled:
            item.setForeground(COL_NAME, QBrush(QColor(theme.C["faint"])))
            item.setForeground(COL_SIZE, QBrush(QColor(theme.C["faint"])))
        return item

    def populate(self) -> None:
        self._updating = True
        current_rule = self._current_rule_id()
        self.tree.clear()
        results = self.store.results_for(self.categories)
        biggest = max((r.size for r in results), default=0)
        by_cat: dict[str, list[RuleResult]] = {}
        for res in results:
            by_cat.setdefault(res.rule.category, []).append(res)
        for cat in self.categories:
            group = sorted(by_cat.get(cat, []), key=lambda r: r.size, reverse=True)
            if not group:
                continue
            size = sum(r.size for r in group if r.rule.action == "delete")
            color = theme.CATEGORY_COLORS.get(cat, theme.C["accent"])
            parent = QTreeWidgetItem([f"{t(f'cat.{cat}')}", format_size(size), t("detail.category", count=len(group))])
            parent.setIcon(COL_NAME, theme.icon(cat, color, color))
            parent.setTextAlignment(COL_SIZE, Qt.AlignRight | Qt.AlignVCenter)
            parent.setForeground(COL_TYPE, QBrush(QColor(theme.C["faint"])))
            parent.setData(COL_NAME, ROLE_CATEGORY, cat)
            font = parent.font(COL_NAME)
            font.setBold(True)
            parent.setFont(COL_NAME, font)
            parent.setFont(COL_SIZE, font)
            if any(r.cleanable for r in group):
                parent.setFlags(parent.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(True)
            for res in group:
                row = self._rule_row(res, biggest)
                parent.addChild(row)
                if 0 < row.childCount() <= 12:
                    row.setExpanded(True)
        self._updating = False
        self.body.setCurrentIndex(1 if results else 0)
        if self.store.scanned and not results:
            self.empty_text.setText(t("clean.nothing"))
        self._apply_filter()
        self._update_total()
        if self.store.scanned:
            total = sum(r.size for r in results if r.rule.action == "delete")
            self.status.setText(t("clean.found", size=format_size(total)) if results else "")
        if current_rule:
            self.focus_rule(current_rule)
        else:
            self.detail.show_none()

    # ----------------------------------------------------------- selection

    def _all_items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _leaves(self):
        for item in self._all_items():
            if item.childCount() == 0 and item.data(COL_NAME, ROLE_TARGETS) is not None:
                yield item

    def _checked_leaves(self) -> list[QTreeWidgetItem]:
        return [item for item in self._leaves()
                if item.flags() & Qt.ItemIsUserCheckable and item.checkState(COL_NAME) == Qt.Checked
                and not item.isHidden()]

    def selected_targets(self) -> list[Target]:
        return [x for item in self._checked_leaves() for x in item.data(COL_NAME, ROLE_TARGETS)]

    def _select(self, recommended: bool) -> None:
        self._updating = True
        for item in self._leaves():
            if not item.flags() & Qt.ItemIsUserCheckable:
                continue
            targets: list[Target] = item.data(COL_NAME, ROLE_TARGETS)
            safe = bool(targets) and targets[0].rule.risk == "safe"
            item.setCheckState(COL_NAME, Qt.Checked if (recommended and safe) else Qt.Unchecked)
        self._updating = False
        self._update_total()

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        mode = self.filter.currentData()
        for cat_index in range(self.tree.topLevelItemCount()):
            cat = self.tree.topLevelItem(cat_index)
            any_visible = False
            for i in range(cat.childCount()):
                row = cat.child(i)
                res: RuleResult = row.data(COL_NAME, ROLE_RESULT)
                risk_ok = mode == "all" or res.rule.risk == mode
                row_match = not text or text in row.text(COL_NAME).lower()
                child_match = False
                for j in range(row.childCount()):
                    child = row.child(j)
                    match = not text or row_match or text in child.text(COL_NAME).lower()
                    child.setHidden(not (risk_ok and match))
                    child_match = child_match or match
                visible = risk_ok and (row_match or child_match)
                row.setHidden(not visible)
                any_visible = any_visible or visible
            cat.setHidden(not any_visible)
        self._update_total()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if not self._updating and column == COL_NAME:
            self._update_total()

    def _update_total(self) -> None:
        rows = self._checked_leaves()
        targets = [x for item in rows for x in item.data(COL_NAME, ROLE_TARGETS)]
        size = sum(x.size for x in targets)
        self.selected.setText(t("clean.selected", count=len(rows), size=format_size(size)) if targets else "")
        self.clean_btn.setText(t("clean.clean_selected", size=format_size(size)))
        self.clean_btn.setEnabled(bool(targets) and not self.store.busy)

    def _current_rule_id(self) -> str:
        item = self.tree.currentItem()
        res = item.data(COL_NAME, ROLE_RESULT) if item else None
        return res.rule.id if res else ""

    def focus(self, rule_id: str) -> None:
        self.focus_rule(rule_id)

    def focus_rule(self, rule_id: str) -> None:
        for item in self._all_items():
            res = item.data(COL_NAME, ROLE_RESULT)
            if res and res.rule.id == rule_id and item.parent() and item.parent().data(COL_NAME, ROLE_CATEGORY):
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return

    def _on_current(self, item: QTreeWidgetItem | None, _prev=None) -> None:
        if item is None:
            self.detail.show_none()
            return
        category = item.data(COL_NAME, ROLE_CATEGORY)
        if category:
            results = [item.child(i).data(COL_NAME, ROLE_RESULT) for i in range(item.childCount())]
            self.detail.show_category(category, results)
            return
        res: RuleResult = item.data(COL_NAME, ROLE_RESULT)
        targets = item.data(COL_NAME, ROLE_TARGETS)
        if targets is None:  # an itemized rule row: show all its items
            self.detail.show_rule(res, list(res.targets))
        elif item.parent() is not None and item.parent().data(COL_NAME, ROLE_CATEGORY) is None:
            self.detail.show_rule(res, targets, item_label=targets[0].label)
        else:
            self.detail.show_rule(res, targets)

    # ----------------------------------------------------------- actions

    def _on_busy(self, busy: bool) -> None:
        self.scan_btn.setText(t("common.cancel") if busy else t("clean.scan"))
        self.empty_btn.setEnabled(not busy)
        if busy:
            self.empty_text.setText(t("home.starting"))
        self.tree.setEnabled(not busy)
        self._update_total()

    def _clean(self) -> None:
        targets = self.selected_targets()
        if not targets:
            return
        dlg = CleanConfirmDialog(self, targets, self.settings.use_recycle_bin)
        if dlg.exec():
            self._mine = True
            self.store.clean(targets, self.settings.use_recycle_bin)

    def _on_cleaned(self, report) -> None:
        if getattr(self, "_mine", False):
            self._mine = False
            CleanResultDialog(self, report, self.store).exec()
