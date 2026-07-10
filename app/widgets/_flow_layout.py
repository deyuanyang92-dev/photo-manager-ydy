"""_flow_layout.py — 自动换行的横向布局(Qt 官方 FlowLayout 精简版).

用途:工具栏按钮排一行,宽度够时排成一行、窗口窄时自动折到第 2/3 行,
不横向滚动、不裁切(2026-07-11 用户要求:一行不行就 2 行)。

与 QHBoxLayout 的关键差别:``heightForWidth`` 为 True —— 它按可用宽度算需要
几行、返回对应高度,所以放进垂直布局时上层能给它正确的高度;它的
``minimumSize`` 只取单个最宽子项的宽,不是所有子项宽度之和,因此不会像
QHBoxLayout 那样硬撑出一个巨大的最小宽度把邻栏挤穿。
"""
from __future__ import annotations

from PyQt6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(
        self,
        parent: QWidget | None = None,
        margin: int = 0,
        h_spacing: int = 6,
        v_spacing: int = 6,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    # ── QLayout 必须实现的接口 ──────────────────────────────────────────────
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        # 关键:最小宽 = 单个最宽子项(不是所有子项之和)→ 允许折行、不撑爆邻栏
        return QSize(
            size.width() + m.left() + m.right(),
            size.height() + m.top() + m.bottom(),
        )

    # ── 布局核心 ────────────────────────────────────────────────────────────
    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        right = rect.right() - m.right()
        line_height = 0
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w
            if next_x - 1 > right and line_height > 0:
                # 换行
                x = rect.x() + m.left()
                y = y + line_height + self._v_spacing
                next_x = x + w
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = next_x + self._h_spacing
            line_height = max(line_height, h)
        return y + line_height + m.bottom() - rect.y()
