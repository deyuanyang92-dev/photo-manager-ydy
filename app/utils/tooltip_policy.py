"""tooltip_policy.py — 交互控件不弹大块 ToolTip（拖动/排序时遮挡内容）。"""
from __future__ import annotations

from typing import Iterable

from PyQt6.QtWidgets import QWidget


def suppress_popup_tooltip(widget: QWidget | None) -> None:
    """清空控件 ToolTip，避免 Qt 在拖动/调整时弹出浮层。"""
    if widget is not None:
        widget.setToolTip("")


def suppress_popup_tooltips(widgets: Iterable[QWidget | None]) -> None:
    for widget in widgets:
        suppress_popup_tooltip(widget)
