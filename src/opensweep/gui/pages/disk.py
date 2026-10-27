"""Disk map: see what fills a drive, with a plain-language note on every folder."""

from __future__ import annotations

import os
import re
import threading
import time

import psutil
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QProgressBar, QPushButton,
                               QSplitter, QStackedWidget, QTabBar, QTableWidget, QVBoxLayout, QWidget)

from opensweep.core import diskmap, identify
from opensweep.core.diskmap import DiskMap, Node
from opensweep.core.platform import open_in_file_manager
from opensweep.core.safety import check_deletable, UnsafePathError
from opensweep.core.units import format_size
from opensweep.gui import theme
from opensweep.gui.panels import InfoPanel, exe_icon
from opensweep.gui.treemap import SAFETY_COLORS, TYPE_COLORS, TreemapWidget
from opensweep.gui.widgets import (ROLE_RATIO, Card, Dot, PageHeader, SizeBarDelegate, SortItem, button, confirm,
                                   error, format_date, icon_label, info, label, page_layout, style_table)
from opensweep.gui.workers import run_task
from opensweep.i18n import t

SAFETY_BADGE = {"keep": "danger", "careful": "review", "user": "info", "clean": "safe"}
UNKNOWN = "#8B95A7"


def node_label(n: Node) -> str:
    if n.kind == "small":
        return t("disk.small_files", count=n.files)
    if n.kind == "unreadable":
        return t("disk.unreadable")
    return n.name


def explain(n: Node) -> tuple[diskmap.Known | None, str]:
    """(explanation, plain text)."""
    known = diskmap.explain(n)
    if known is None:
        return None, t("disk.k_unknown")
    if known.id.startswith("type:"):
        return known, t("disk.t_" + known.id[5:])
    if known.id.startswith("in:"):
        place = os.path.basename(known.where.rstrip("\\/")) or known.where
        return known, t("disk.inside", place=place) + "\n\n" + t("disk.k_" + known.id[3:])
    return known, t("disk.k_" + known.id)


IDENTITY_SAFETY = {"program": "careful", "data_of": "careful", "windows": "keep"}


def first_sentence(text: str) -> str:
    return re.split(r"(?<=[.!?。！？])\s*", text.strip(), maxsplit=1)[0]


def identity_text(ident: identify.Identity) -> str:
    return t("disk.id_" + ident.kind, who=ident.who)


def safety_color(n: Node) -> str:
    known = diskmap.explain(n)
    return SAFETY_COLORS.get(known.safety, UNKNOWN) if known else UNKNOWN


class DiskPage(QWidget):
    open_page = Signal(str, str)
    changed = Signal()  # something was deleted: other pages' numbers are out of date

    def __init__(self):
        super().__init__()
        self.map: DiskMap | None = None
        self.current: Node | None = None
        self._cancel: threading.Event | None = None
        self._type_filter = ""
        self._keep_filter = False
        self._shown: Node | None = None
        self._largest_nodes: list[Node] = []
        self._ident: dict[str, identify.Identity | None] = {}   # path -> what Windows knows about it
        self._ident_pending: set[str] = set()
        self._shown_in: dict[int, tuple[QWidget, Node]] = {}     # panel -> node it shows
        lay = page_layout(self)

        right = QWidget()
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.drive = QComboBox()
        self.drive.setMinimumWidth(200)
        self._fill_drives()
        pick = button(t("disk.pick_folder"), None, "folder")
        pick.clicked.connect(self._pick_folder)
        self.go = button(t("disk.analyze"), "primary", "scan")
        self.go.clicked.connect(self._start_or_cancel)
        rl.addWidget(self.drive)
        rl.addWidget(pick)
        rl.addWidget(self.go)
        lay.addWidget(PageHeader(t("disk.title"), t("disk.desc"), right))

        self.body = QStackedWidget()
        self.body.addWidget(self._empty_view())
        self.body.addWidget(self._scanning_view())
        self.body.addWidget(self._result_view())
        lay.addWidget(self.body, 1)

    # ------------------------------------------------------------- views

    def _empty_view(self) -> QWidget:
        card = Card(margins=28)
        card.layout_.addStretch(1)
        card.layout_.addWidget(icon_label("folder", theme.C["accent"], 44), 0, Qt.AlignHCenter)
        text = label(t("disk.empty"), "muted", wrap=True)
        text.setAlignment(Qt.AlignCenter)
        card.layout_.addWidget(text)
        self.drive_buttons = QVBoxLayout()
        self.drive_buttons.setSpacing(8)
        holder = QWidget()
        holder.setLayout(self.drive_buttons)
        holder.setFixedWidth(520)
        card.layout_.addSpacing(8)
        card.layout_.addWidget(holder, 0, Qt.AlignHCenter)
        for part in self._drives():
            mount, usage = part
            b = QPushButton()
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(64)
            row = QHBoxLayout(b)
            row.setContentsMargins(16, 8, 16, 8)
            row.setSpacing(14)
            name = label(mount.rstrip("\\/"), "section")
            name.setMinimumWidth(34)
            name.setAttribute(Qt.WA_TransparentForMouseEvents)
            row.addWidget(name)
            col = QVBoxLayout()
            col.setSpacing(5)
            txt = label(t("home.disk_free", free=format_size(usage.free), total=format_size(usage.total)), "muted")
            txt.setAttribute(Qt.WA_TransparentForMouseEvents)
            col.addWidget(txt)
            bar = QProgressBar()
            bar.setValue(int(usage.percent))
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            bar.setAttribute(Qt.WA_TransparentForMouseEvents)
            col.addWidget(bar)
            row.addLayout(col, 1)
            b.clicked.connect(lambda _=False, m=mount: self.start(m))
            self.drive_buttons.addWidget(b)
        card.layout_.addStretch(2)
        return card

    def _scanning_view(self) -> QWidget:
        card = Card(margins=28)
        card.layout_.addStretch(1)
        self.scan_title = label("", "big")
        self.scan_title.setAlignment(Qt.AlignCenter)
        card.layout_.addWidget(self.scan_title)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setMaximumWidth(480)
        card.layout_.addWidget(bar, 0, Qt.AlignHCenter)
        self.scan_count = label("", "section")
        self.scan_count.setAlignment(Qt.AlignCenter)
        card.layout_.addWidget(self.scan_count)
        self.scan_path = label("", "path")
        self.scan_path.setAlignment(Qt.AlignCenter)
        self.scan_path.setMaximumWidth(700)
        card.layout_.addWidget(self.scan_path, 0, Qt.AlignHCenter)
        hint = label(t("disk.scan_hint"), "muted", wrap=True)
        hint.setAlignment(Qt.AlignCenter)
        card.layout_.addWidget(hint)
        card.layout_.addStretch(2)
        return card

    def _result_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.summary = label("", "muted", wrap=True)
        lay.addWidget(self.summary)

        self.wins = Card(margins=16)
        self.wins.layout_.addWidget(label(t("disk.wins_title"), "section"))
        self.wins_rows = QVBoxLayout()
        self.wins_rows.setSpacing(6)
        self.wins.layout_.addLayout(self.wins_rows)
        self.wins.setVisible(False)
        lay.addWidget(self.wins)

        bar = QHBoxLayout()
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        for key in ("map", "largest", "types"):
            self.tabs.addTab(t(f"disk.tab_{key}"))
        self.tabs.currentChanged.connect(self._on_tab)
        bar.addWidget(self.tabs)
        bar.addStretch(1)
        self.color_mode = QComboBox()
        self.color_mode.addItem(t("disk.color_safety"), "safety")
        self.color_mode.addItem(t("disk.color_type"), "type")
        self.color_mode.currentIndexChanged.connect(self._on_color_mode)
        bar.addWidget(self.color_mode)
        lay.addLayout(bar)

        self.views = QStackedWidget()
        self.views.addWidget(self._map_view())
        self.views.addWidget(self._largest_view())
        self.views.addWidget(self._types_view())
        lay.addWidget(self.views, 1)
        return w

    def _map_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        crumbs_row = QHBoxLayout()
        self.up = button(t("disk.up"), None, "refresh")
        self.up.clicked.connect(self._go_up)
        crumbs_row.addWidget(self.up)
        self.crumbs = QHBoxLayout()
        self.crumbs.setSpacing(2)
        crumbs_row.addLayout(self.crumbs, 1)
        lay.addLayout(crumbs_row)

        self.treemap = TreemapWidget()
        self.treemap.label_of = node_label
        self.treemap.color_of = safety_color
        self.treemap.selected.connect(self._show_node)
        self.treemap.opened.connect(self.open_folder)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("card")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(6, 6, 6, 6)
        fl.addWidget(self.treemap)
        ll.addWidget(frame, 1)
        self.legend = QHBoxLayout()
        self.legend.setSpacing(14)
        ll.addLayout(self.legend)
        self.map_detail = InfoPanel(t("disk.pick"))
        self.map_detail.list.itemDoubleClicked.connect(self._open_listed)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.map_detail)
        split.setSizes([700, 400])
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)
        return w

    def _largest_view(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.largest_title = label("", "muted")
        lay.addWidget(self.largest_title)
        self.largest = QTableWidget(0, 4)
        self.largest.setHorizontalHeaderLabels([t("detail.col_name"), t("detail.col_size"), t("disk.col_type"),
                                                t("detail.col_modified")])
        style_table(self.largest, 38)
        self.largest.setIconSize(QSize(18, 18))
        self.largest.setSortingEnabled(True)
        self.largest.setSelectionMode(QTableWidget.SingleSelection)
        self.largest.setTextElideMode(Qt.ElideMiddle)
        header = self.largest.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.largest.itemSelectionChanged.connect(self._on_largest_select)
        self.largest_detail = InfoPanel(t("disk.pick_file"))
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.largest)
        split.addWidget(self.largest_detail)
        split.setSizes([700, 400])
        split.setChildrenCollapsible(False)
        lay.addWidget(split, 1)
        return w

    def _types_view(self) -> QWidget:
        card = Card(margins=20)
        card.layout_.addWidget(label(t("disk.types_title"), "section"))
        card.layout_.addWidget(label(t("disk.types_hint"), "muted", wrap=True))
        self.types_table = QTableWidget(0, 3)
        self.types_table.setHorizontalHeaderLabels([t("disk.col_type"), t("detail.col_size"), t("detail.col_files")])
        style_table(self.types_table, 40)
        self.types_table.setItemDelegateForColumn(1, SizeBarDelegate(self.types_table))
        header = self.types_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.resizeSection(1, 220)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.types_table.cellDoubleClicked.connect(self._open_type)
        self.types_table.cellClicked.connect(self._open_type)
        card.layout_.addWidget(self.types_table, 1)
        return card

    # ------------------------------------------------------------- scanning

    @staticmethod
    def _drives():
        out = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            try:
                out.append((part.mountpoint, psutil.disk_usage(part.mountpoint)))
            except OSError:
                continue
        return out

    def _fill_drives(self) -> None:
        self.drive.clear()
        for mount, usage in self._drives():
            self.drive.addItem(f"{mount.rstrip(chr(92))}   {format_size(usage.used)} / {format_size(usage.total)}",
                               mount)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("disk.pick_folder"))
        if folder:
            self.start(os.path.normpath(folder))

    def _start_or_cancel(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        else:
            self.start(self.drive.currentData())

    def start(self, path: str) -> None:
        if not path or self._cancel is not None:
            return
        self._cancel = threading.Event()
        self._started = time.monotonic()
        self.go.setText(t("common.cancel"))
        self.scan_title.setText(t("disk.scanning", path=path.rstrip("\\/") or path))
        self.scan_count.setText("")
        self.scan_path.setText("")
        self.body.setCurrentIndex(1)

        def progress(text: str) -> None:
            count, _, where = text.partition("|")
            self.scan_count.setText(t("disk.scan_count", count=f"{int(count):,}",
                                      seconds=int(time.monotonic() - self._started)))
            self.scan_path.setText(where if len(where) < 90 else "…" + where[-88:])

        def done(result: DiskMap) -> None:
            self._cancel = None
            self.go.setText(t("disk.analyze"))
            self.show_map(result)

        def failed(msg: str) -> None:
            self._cancel = None
            self.go.setText(t("disk.analyze"))
            self.body.setCurrentIndex(2 if self.map else 0)
            if not msg.startswith("Cancelled"):
                error(self, msg)

        run_task(diskmap.scan, path, cancel=self._cancel, on_done=done, on_error=failed, on_progress=progress)

    # ------------------------------------------------------------- results

    @staticmethod
    def _summary_text(result: DiskMap) -> str:
        text = t("disk.summary", size=format_size(result.root.size), files=f"{result.files:,}",
                 folders=f"{result.folders:,}", seconds=f"{result.seconds:.1f}")
        if result.denied:
            text += " " + t("disk.denied", count=result.denied)
        return text

    def show_map(self, result: DiskMap) -> None:
        self.map = result
        self.body.setCurrentIndex(2)
        self.summary.setText(self._summary_text(result))
        self.open_folder(result.root)
        self._fill_largest()
        self._fill_types()
        self._render_legend()
        self._fill_wins()

    def _refresh_totals(self) -> None:
        """Everything on the page that adds up the whole map, after something was removed from it."""
        self._fill_largest()
        self._fill_types()
        self._fill_wins()
        self._render_legend()
        self.summary.setText(self._summary_text(self.map))

    def _fill_wins(self) -> None:
        """A short "start here" list: the biggest things OpenSweep can clean safely."""
        while self.wins_rows.count():
            item = self.wins_rows.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
        wins = diskmap.quick_wins(self.map.root) if self.map else []
        for node, known in wins:
            row = QHBoxLayout()
            row.addWidget(Dot(theme.C["safe"]))
            row.addWidget(label(f"{node_label(node)}  —  {first_sentence(t('disk.k_' + known.id))}", wrap=True), 1)
            row.addWidget(label(format_size(node.size), "section"))
            go = button(t("disk.go_page_" + known.page), "primary", "clean")
            go.clicked.connect(lambda _=False, p=known.page: self.open_page.emit(p, ""))
            row.addWidget(go)
            self.wins_rows.addLayout(row)
        self.wins.setVisible(bool(wins))

    def open_folder(self, node: Node) -> None:
        self.current = node
        self.treemap.set_node(node)
        self._render_crumbs()
        self.up.setEnabled(node.parent is not None)
        self._show_node(node)

    def _go_up(self) -> None:
        if self.current is not None and self.current.parent is not None:
            self.open_folder(self.current.parent)

    def _render_crumbs(self) -> None:
        while self.crumbs.count():
            item = self.crumbs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        chain = diskmap.path_to(self.current)
        if len(chain) > 6:
            chain = chain[:1] + chain[-5:]
        for i, n in enumerate(chain):
            if i:
                self.crumbs.addWidget(label("›", "faint"))
            b = button(n.name if i else n.name.rstrip("\\/") or n.path, "ghost")
            b.clicked.connect(lambda _=False, node=n: self.open_folder(node))
            self.crumbs.addWidget(b)
        self.crumbs.addStretch(1)

    def _render_legend(self) -> None:
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
        if self.color_mode.currentData() == "safety":
            entries = [(SAFETY_COLORS[k], t(f"disk.s_{k}")) for k in ("clean", "user", "careful", "keep")]
            entries.append((UNKNOWN, t("disk.s_unknown")))
        else:
            entries = [(TYPE_COLORS[k], t(f"disk.type_{k}")) for k in
                       ("folder", "video", "image", "archive", "installer", "diskimage", "model", "other")]
        for color, text in entries:
            row = QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(Dot(color, 9))
            row.addWidget(label(text, "muted"))
            self.legend.addLayout(row)
        self.legend.addStretch(1)

    def _on_color_mode(self) -> None:
        mode = self.color_mode.currentData()
        self.treemap.color_of = safety_color if mode == "safety" else \
            (lambda n: TYPE_COLORS.get(n.kind or n.type, TYPE_COLORS["other"]))
        self.treemap.update()
        self._render_legend()

    def _on_tab(self, index: int) -> None:
        self.views.setCurrentIndex(index)
        self.color_mode.setVisible(index == 0)
        if index == 1 and not self._keep_filter and self._type_filter:
            self._type_filter = ""  # opened the tab directly: show all types again
            self._fill_largest()
        self._keep_filter = False

    # ------------------------------------------------------------- detail

    def _present(self, panel: InfoPanel, n: Node) -> None:
        known, text = explain(n)
        ident = self._identity(n, known)
        if ident:
            extra = identity_text(ident)
            generic = known is None or known.id.startswith("in:")  # "somewhere in AppData" says less than the owner
            text = extra + "\n\n" + text if generic else text + "\n\n" + extra
        if known is None and n.is_dir and n.children:
            mix = diskmap.type_mix(n)
            if mix:
                text += "\n\n" + t("disk.id_contents", types=", ".join(
                    f"{t('disk.type_' + kind)} {share:.0%}" for kind, share in mix))
        safety = known.safety if known and known.safety in SAFETY_BADGE else ""
        if not safety and ident:
            safety = IDENTITY_SAFETY.get(ident.kind, "")
        badges = []
        if safety:
            badges.append((t(f"disk.s_{safety}"), SAFETY_BADGE[safety]))
        else:
            badges.append((t("disk.s_unknown"), "info"))
        if n.denied:
            badges.append((t("disk.no_access"), "danger"))
        parent = n.parent
        share = ""
        if self.map and self.map.root.size:
            share = t("disk.share", pct=f"{100 * n.size / self.map.root.size:.1f}")
        facts = []
        if share:
            facts.append((t("disk.of_total"), share, False))
        if n.files:
            facts.append((t("detail.col_files"), f"{n.files:,}", False))
        if n.mtime:
            facts.append((t("detail.modified"), format_date(n.mtime), False))
        if n.kind not in ("small", "unreadable"):
            facts.append((t("detail.location"), n.path, True))
        actions = []
        if n.is_dir and n.children and n is not self.current and panel is self.map_detail:
            dig = button(t("disk.open_in_map"), "primary", "folder")
            dig.clicked.connect(lambda: self.open_folder(n))
            actions.append(dig)
        if known and known.page:
            go = button(t("disk.go_page_" + known.page), "primary" if safety == "clean" else None, "clean")
            go.clicked.connect(lambda: self.open_page.emit(known.page, ""))
            actions.append(go)
        if n.kind not in ("small", "unreadable") and os.path.exists(n.path):
            where = button(t("clean.open_folder"), None, "folder")
            where.clicked.connect(lambda: open_in_file_manager(n.path))
            actions.append(where)
        if self._deletable(n, known, ident):
            delete = button(t("disk.recycle"), "danger", "clean")
            delete.clicked.connect(lambda: self._recycle(n))
            actions.append(delete)
        items = []
        if n.is_dir and n.children:
            for child in n.children[:15]:
                share_child = f"{100 * child.size / n.size:.0f}%" if n.size else ""
                items.append((theme.icon("folder", TYPE_COLORS["folder"]) if child.is_dir else exe_icon(""),
                              f"{node_label(child)}    {format_size(child.size)}  ·  {share_child}", child.path))
        title = node_label(n) if parent is not None else (n.path.rstrip("\\/") or n.path)
        panel.present(theme.icon("folder", TYPE_COLORS["folder"] if n.is_dir else "#8B95A7", size=40), title,
                      t(f"disk.type_{n.type}") if not n.is_dir and n.kind == "" else "", badges,
                      format_size(n.size), text, facts, actions, t("disk.biggest_inside"), items,
                      report={"Page": "Disk map", "Path": n.path, "Type": n.type,
                              "Explanation": known.id if known else "unknown", "Safety": safety or "unknown"})
        if panel is self.map_detail:
            self._shown = n
        self._shown_in[id(panel)] = (panel, n)

    def _identity(self, n: Node, known) -> identify.Identity | None:
        """What Windows knows about an unfamiliar path, looked up in the background and shown when it arrives."""
        if n.kind in ("small", "unreadable") or n.parent is None:
            return None
        if known is not None and not known.id.startswith(("in:", "type:")):
            return None
        if n.path in self._ident:
            return self._ident[n.path]
        if n.path not in self._ident_pending:
            self._ident_pending.add(n.path)
            path = n.path
            run_task(identify.identify, path, n.is_dir, on_done=lambda r: self._identity_done(path, r),
                     on_error=lambda _m: self._identity_done(path, None))
        return None

    def _identity_done(self, path: str, result) -> None:
        self._ident_pending.discard(path)
        self._ident[path] = result
        if result is None:
            return
        for panel, node in list(self._shown_in.values()):
            if node.path == path:
                self._present(panel, node)

    def _open_listed(self, item) -> None:
        node = self._shown
        index = self.map_detail.list.row(item)
        if node and 0 <= index < len(node.children):
            child = node.children[index]
            if child.is_dir and child.children:
                self.open_folder(child)
            else:
                self.treemap.set_selected(child)
                self._present(self.map_detail, child)

    def _show_node(self, n: Node) -> None:
        self.treemap.set_selected(n if n is not self.current else None)
        self._present(self.map_detail, n)

    @staticmethod
    def _deletable(n: Node, known, ident=None) -> bool:
        if n.kind in ("small", "unreadable") or n.parent is None:
            return False
        if ident is not None and ident.kind in IDENTITY_SAFETY:
            return False
        if known is not None and known.safety in ("keep", "careful", "clean"):
            return False
        try:
            check_deletable(n.path)
        except UnsafePathError:
            return False
        return True

    def _recycle(self, n: Node) -> None:
        text = t("disk.recycle_confirm", name=node_label(n), size=format_size(n.size)) + "\n\n" + n.path
        if not confirm(self, t("disk.recycle"), text, t("disk.recycle"), danger=True):
            return

        def work():
            from send2trash import send2trash
            check_deletable(n.path)
            send2trash(n.path)

        def done(_):
            diskmap.discount(self.map, n)
            diskmap.remove_node(n)
            info(self, t("disk.recycle"), t("disk.recycled", name=node_label(n), size=format_size(n.size)))
            self.open_folder(self.current if self.current is not n else n.parent)
            self._refresh_totals()
            self.changed.emit()

        run_task(work, on_done=done, on_error=lambda m: error(self, m))

    # ------------------------------------------------------------- largest files / types

    def _fill_largest(self) -> None:
        if not self.map:
            return
        files = [f for f in self.map.largest if not self._type_filter or f.type == self._type_filter]
        self.largest_title.setText(
            t("disk.largest_title_type", type=t(f"disk.type_{self._type_filter}"), count=len(files))
            if self._type_filter else t("disk.largest_title", count=len(files)))
        self.largest.setSortingEnabled(False)
        self.largest.setRowCount(len(files))
        self._largest_nodes = files
        for row, f in enumerate(files):
            name = SortItem(f.name)
            name.setIcon(exe_icon(f.path) if f.type == "installer" else theme.icon("copy", TYPE_COLORS.get(f.type)))
            name.setToolTip(f.path)
            name.setData(Qt.UserRole, row)
            self.largest.setItem(row, 0, name)
            size = SortItem(format_size(f.size), f.size)
            size.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.largest.setItem(row, 1, size)
            kind = SortItem(t(f"disk.type_{f.type}"))
            kind.setForeground(QBrush(QColor(TYPE_COLORS.get(f.type, UNKNOWN))))
            self.largest.setItem(row, 2, kind)
            self.largest.setItem(row, 3, SortItem(format_date(f.mtime), f.mtime))
        self.largest.setSortingEnabled(True)
        self.largest.sortItems(1, Qt.DescendingOrder)

    def _on_largest_select(self) -> None:
        rows = self.largest.selectionModel().selectedRows()
        if not rows:
            self.largest_detail.show_none()
            return
        node = self._largest_nodes[self.largest.item(rows[0].row(), 0).data(Qt.UserRole)]
        self._present(self.largest_detail, node)

    def _fill_types(self) -> None:
        if not self.map:
            return
        rows = sorted(((k, v[0], v[1]) for k, v in self.map.types.items() if v[0]), key=lambda x: -x[1])
        biggest = rows[0][1] if rows else 1
        self._type_rows = rows
        self.types_table.setRowCount(len(rows))
        for i, (kind, size, count) in enumerate(rows):
            name = SortItem(t(f"disk.type_{kind}"))
            name.setIcon(theme.icon("copy", TYPE_COLORS.get(kind, UNKNOWN), TYPE_COLORS.get(kind, UNKNOWN)))
            name.setToolTip(t(f"disk.t_{kind}"))
            self.types_table.setItem(i, 0, name)
            s = SortItem(format_size(size), size)
            s.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            s.setData(ROLE_RATIO, size / biggest)
            s.setData(ROLE_RATIO + 1, TYPE_COLORS.get(kind, UNKNOWN))
            self.types_table.setItem(i, 1, s)
            c = SortItem(f"{count:,}", count)
            c.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.types_table.setItem(i, 2, c)

    def _open_type(self, row: int, _col: int) -> None:
        kind = self._type_rows[row][0]
        self._type_filter = kind
        self._fill_largest()
        self._keep_filter = True
        self.tabs.setCurrentIndex(1)
