"""Small shared widgets and helpers for the GUI."""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QStyledItemDelegate, QStyleOptionViewItem, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from opensweep.gui import theme
from opensweep.i18n import t

ROLE_RATIO = Qt.UserRole + 20


def label(text: str = "", name: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    if selectable:
        w.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return w


def button(text: str, name: str | None = None, icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    if icon_name:
        color = theme.C["on_accent"] if name in ("primary", "hero", "danger") else (
            theme.C["accent"] if name == "ghost" else theme.C["muted"])
        b.setIcon(theme.icon(icon_name, color, color, 16))
        b.setIconSize(QSize(16, 16))
    b.setCursor(Qt.PointingHandCursor)
    return b


def badge(text: str, kind: str) -> QLabel:
    """kind: safe | review | info | danger"""
    w = QLabel(text)
    w.setObjectName(f"badge_{kind}")
    return w


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("divider")
    return f


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None, name: str = "card", margins: int = 18):
        super().__init__(parent)
        self.setObjectName(name)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(margins, margins - 2, margins, margins - 2)
        self.layout_.setSpacing(8)


class PageHeader(QWidget):
    def __init__(self, title: str, description: str = "", right: QWidget | None = None):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 6)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title, "title"))
        if description:
            col.addWidget(label(description, "subtitle", wrap=True))
        row.addLayout(col, 1)
        if right is not None:
            row.addWidget(right, 0, Qt.AlignBottom)


def page_layout(widget: QWidget) -> QVBoxLayout:
    widget.setObjectName("page")
    lay = QVBoxLayout(widget)
    lay.setContentsMargins(32, 26, 32, 22)
    lay.setSpacing(14)
    return lay


def hbox(*widgets, stretch_at: int | None = None, spacing: int = 8) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    for i, w in enumerate(widgets):
        if i == stretch_at:
            lay.addStretch(1)
        if isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if stretch_at is not None and stretch_at >= len(widgets):
        lay.addStretch(1)
    return lay


def style_table(table: QTableWidget, row_height: int = 36) -> None:
    table.setShowGrid(False)
    table.setAlternatingRowColors(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setFocusPolicy(Qt.NoFocus)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)


class SortItem(QTableWidgetItem):
    """Table item that sorts by a hidden key instead of its text."""

    def __init__(self, text: str, key=None):
        super().__init__(text)
        self.key = key if key is not None else text.lower()

    def __lt__(self, other):
        if isinstance(other, SortItem):
            try:
                return self.key < other.key
            except TypeError:
                return str(self.key) < str(other.key)
        return super().__lt__(other)


class SizeBarDelegate(QStyledItemDelegate):
    """Draws a small proportional bar under the size text (ratio stored in ROLE_RATIO)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        ratio = index.data(ROLE_RATIO)
        super().paint(painter, option, index)
        if ratio is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(8, 0, -8, 0)
        bar = QRectF(r.left(), r.bottom() - 6, r.width(), 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.C["bar"]))
        painter.drawRoundedRect(bar, 1.5, 1.5)
        if ratio > 0:
            fill = QRectF(bar.left(), bar.top(), max(3.0, bar.width() * min(1.0, ratio)), bar.height())
            painter.setBrush(QColor(index.data(ROLE_RATIO + 1) or theme.C["accent"]))
            painter.drawRoundedRect(fill, 1.5, 1.5)
        painter.restore()


class StackedBar(QWidget):
    """Horizontal bar split into colored segments: [(value, color), ...]."""

    def __init__(self, height: int = 12, gap_color: str | None = None):
        super().__init__()
        self.segments: list[tuple[float, str]] = []
        self.gap_color = gap_color  # color of the thin gaps between segments (the card behind the bar)
        self.setFixedHeight(height)

    def set_segments(self, segments: list[tuple[float, str]]) -> None:
        self.segments = [(v, c) for v, c in segments if v > 0]
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.setClipPath(clip)
        p.fillRect(rect, QColor(theme.C["bar"]))
        total = sum(v for v, _ in self.segments)
        x = rect.left()
        for value, color in self.segments:
            w = rect.width() * value / total if total else 0
            p.fillRect(QRectF(x, rect.top(), w, rect.height()), QColor(color))
            x += w
            if x < rect.right():
                p.fillRect(QRectF(x - 1, rect.top(), 2, rect.height()), QColor(self.gap_color or theme.C["surface"]))
        p.end()


class Dot(QWidget):
    def __init__(self, color: str, size: int = 10):
        super().__init__()
        self.color = color
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color))
        p.drawEllipse(self.rect())
        p.end()


def icon_label(name: str, color: str, size: int = 18) -> QLabel:
    w = QLabel()
    w.setPixmap(theme.glyph_pixmap(name, color, size))
    w.setFixedSize(size, size)
    return w


def format_date(ts: float | None) -> str:
    if not ts:
        return t("common.unknown")
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def format_ago(ts: float | None) -> str:
    if not ts:
        return t("common.unknown")
    days = int((time.time() - ts) // 86400)
    if days <= 0:
        return t("common.today")
    return t("common.days_ago", n=days)


def confirm(parent: QWidget, title: str, text: str, ok: str | None = None, danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    yes = box.addButton(ok or t("common.ok"), QMessageBox.AcceptRole)
    box.addButton(t("common.cancel"), QMessageBox.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes


def info(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def error(parent: QWidget, text: str) -> None:
    QMessageBox.critical(parent, t("common.error"), text)


def app_icon() -> QIcon:
    """The OpenSweep logo, drawn in code so there is no binary asset to maintain."""
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(draw_logo(size))
    return ic


def draw_logo(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#4F86FF"))
    grad.setColorAt(1, QColor("#2350D8"))
    path = QPainterPath()
    path.addRoundedRect(2 * s, 2 * s, 60 * s, 60 * s, 15 * s, 15 * s)
    p.fillPath(path, grad)
    p.setPen(QPen(QColor("white"), max(1.0, 5 * s), Qt.SolidLine, Qt.RoundCap))
    p.drawLine(int(42 * s), int(12 * s), int(28 * s), int(34 * s))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    head = QPainterPath()
    head.moveTo(20 * s, 32 * s)
    head.lineTo(34 * s, 38 * s)
    head.lineTo(26 * s, 52 * s)
    head.lineTo(12 * s, 46 * s)
    head.closeSubpath()
    p.drawPath(head)
    for cx, cy, r in ((46, 40, 4.5), (52, 28, 2.5), (40, 50, 2.5)):
        star = QPainterPath()
        star.moveTo(cx * s, (cy - r) * s)
        star.quadTo(cx * s, cy * s, (cx + r) * s, cy * s)
        star.quadTo(cx * s, cy * s, cx * s, (cy + r) * s)
        star.quadTo(cx * s, cy * s, (cx - r) * s, cy * s)
        star.quadTo(cx * s, cy * s, cx * s, (cy - r) * s)
        p.drawPath(star)
    p.end()
    return pm
