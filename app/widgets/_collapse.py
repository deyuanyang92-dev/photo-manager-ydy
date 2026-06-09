"""_collapse.py — shared helper for card collapse behaviour.

Right-rail cards (照片编号 / 分类标签 / 元数据) each keep a header row and
hide everything below it when collapsed, mirroring the web oracle's per-card
▾/▸ toggle (``collapsedCards`` in app.js). This helper hides/shows every
widget owned by a layout from ``start_index`` onward, recursing into nested
layouts so a collapsed card leaves only its header visible.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLayout


def set_layout_children_visible(layout: QLayout, start_index: int, visible: bool) -> None:
    """Show/hide every widget under *layout* from *start_index* onward.

    Nested layouts are recursed into (start_index 0) so all descendant
    widgets follow the same visibility.
    """
    for i in range(start_index, layout.count()):
        item = layout.itemAt(i)
        if item is None:
            continue
        w = item.widget()
        if w is not None:
            w.setVisible(visible)
            continue
        child = item.layout()
        if child is not None:
            set_layout_children_visible(child, 0, visible)
