"""Squarified treemap of a disk map folder: each box's area is its size."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from opensweep.core.diskmap import Node
from opensweep.core.units import format_size
from opensweep.gui import theme

TYPE_COLORS = {
    "folder": "#4C7DFF", "video": "#E5484D", "image": "#F08C00", "audio": "#D6409F", "document": "#12A594",
    "archive": "#8E4EC6", "installer": "#3E63DD", "diskimage": "#6E56CF", "model": "#7A5AF8",
    "program": "#5B6B85", "cache": "#AD7F58", "other": "#8B95A7", "small": "#A3ADBD", "unreadable": "#C2C8D2",
}
SAFETY_COLORS = {"keep": "#7A5AF8", "careful": "#F08C00", "user": "#2F6BFF", "clean": "#0E9F5B"}


def squarify(sizes: list[float], rect: QRectF) -> list[QRectF]:
    """Squarified treemap layout (Bruls et al.). `sizes` must be sorted largest first."""
    rects: list[QRectF] = []
    total = sum(sizes)
    if total <= 0 or rect.width() <= 0 or rect.height() <= 0:
        return [QRectF() for _ in sizes]
    scale = rect.width() * rect.height() / total
    areas = [s * scale for s in sizes]
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    i = 0
    while i < len(areas):
        short = min(w, h)
        row = [areas[i]]
        j = i + 1

        def worst(r):
            s = sum(r)
            if s <= 0:
                return float("inf")
            return max(max(short * short * a / (s * s), (s * s) / (short * short * a)) for a in r if a > 0) \
                if any(a > 0 for a in r) else float("inf")

        while j < len(areas) and worst(row + [areas[j]]) <= worst(row):
            row.append(areas[j])
            j += 1
        s = sum(row)
        if w >= h:  # lay the row out as a column on the left
            col_w = s / h if h else 0
            yy = y
            for a in row:
                hh = a / col_w if col_w else 0
                rects.append(QRectF(x, yy, col_w, hh))
                yy += hh
            x += col_w
            w -= col_w
        else:       # lay the row out along the top
            row_h = s / w if w else 0
            xx = x
            for a in row:
                ww = a / row_h if row_h else 0
                rects.append(QRectF(xx, y, ww, row_h))
                xx += ww
            y += row_h
            h -= row_h
        i = j
    return rects


class TreemapWidget(QWidget):
    hovered = Signal(object)    # Node | None
    selected = Signal(object)   # Node
    opened = Signal(object)     # Node (double click on a folder)

    MAX_BOXES = 160

    def __init__(self):
        super().__init__()
        self.node: Node | None = None
        self.color_of = lambda n: TYPE_COLORS.get(n.kind or n.type, TYPE_COLORS["other"])
        self.label_of = lambda n: n.name
        self._boxes: list[tuple[QRectF, Node, int]] = []  # rect, node, depth
        self._hover: Node | None = None
        self._selected: Node | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(260)

    def set_node(self, node: Node | None) -> None:
        self.node = node
        self._selected = None
        self._layout()
        self.update()

    def set_selected(self, node: Node | None) -> None:
        self._selected = node
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout()

    def _layout(self) -> None:
        self._boxes = []
        if self.node is None or self.node.size <= 0:
            return
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        self._place(self.node, area, 0)

    def _place(self, parent: Node, area: QRectF, depth: int) -> None:
        kids = [c for c in parent.children if c.size > 0][: self.MAX_BOXES]
        if not kids:
            return
        rects = squarify([c.size for c in kids], area)
        for rect, child in zip(rects, kids):
            if rect.width() < 2 or rect.height() < 2:
                continue
            self._boxes.append((rect, child, depth))
            # Show one more level inside big folders, below a title strip.
            if depth == 0 and child.is_dir and child.children and rect.width() > 90 and rect.height() > 60:
                inner = rect.adjusted(3, 20, -3, -3)
                self._place(child, inner, 1)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.C["surface"]))
        if not self._boxes:
            p.end()
            return
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        p.setFont(font)
        metrics = QFontMetrics(font)
        bold = QFont(font)
        bold.setWeight(QFont.DemiBold)
        metrics = QFontMetrics(bold)
        dark = theme.dark_skin()
        for rect, node, depth in self._boxes:
            color = QColor(self.color_of(node))
            header = depth == 0 and node.is_dir and node.children and rect.width() > 90 and rect.height() > 60
            if header:
                color = color.lighter(165) if not dark else color.darker(250)
            elif depth == 1:
                color = color.lighter(115)
            if node is self._hover:
                color = color.lighter(120)
            r = rect.adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(QPen(QColor(theme.C["surface"]), 1.2))
            p.setBrush(color)
            p.drawRoundedRect(r, 3, 3)
            if node is self._selected:
                p.setPen(QPen(QColor(theme.C["text"]), 2.2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 3, 3)
            if rect.width() > 46 and rect.height() > 18:
                # Pick dark or white text from the box color itself, so every label stays readable.
                p.setPen(theme.text_on(color))
                p.setFont(bold)
                name = metrics.elidedText(self.label_of(node), Qt.ElideMiddle, int(rect.width() - 10))
                p.drawText(QRectF(rect.x() + 5, rect.y() + 3, rect.width() - 10, 16), Qt.AlignLeft | Qt.AlignVCenter,
                           name)
                p.setFont(font)
                if rect.height() > 36 and not header:
                    p.drawText(QRectF(rect.x() + 5, rect.y() + 18, rect.width() - 10, 16),
                               Qt.AlignLeft | Qt.AlignVCenter, format_size(node.size))
        p.end()

    def node_at(self, pos: QPointF) -> Node | None:
        best = None
        for rect, node, depth in self._boxes:
            if rect.contains(pos):
                best = node  # deeper boxes come later, so the last hit is the most specific
        return best

    def mouseMoveEvent(self, event) -> None:
        node = self.node_at(event.position())
        if node is not self._hover:
            self._hover = node
            self.hovered.emit(node)
            self.update()
        if node is not None:
            QToolTip.showText(event.globalPosition().toPoint(), f"{self.label_of(node)}\n{format_size(node.size)}",
                              self)

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.hovered.emit(None)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        node = self.node_at(event.position())
        if node is not None and event.button() == Qt.LeftButton:
            self._selected = node
            self.selected.emit(node)
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        node = self.node_at(event.position())
        if node is None:
            return
        # Double clicking a box inside a folder opens the folder that holds it.
        target = node if node.is_dir else node.parent
        if target is not None and target is not self.node and target.is_dir and target.children:
            self.opened.emit(target)
