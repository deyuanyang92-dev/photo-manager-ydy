"""_form_row.py — shared single-column form-row builder for the right-rail cards.

Produces the clean `右对齐定宽标签(+ * 必填 + ? 帮助) | 输入(撑满)` row used across
照片编号 / 标本元数据 / 分类标签 cards, mirroring the reference layout the user gave.

The `?` help dot shows ``help_text`` as a tooltip on hover — keeps long field
explanations out of the way (short label + on-demand help), instead of cramming
them into the label or a separate window.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget


def help_dot(help_text: str, parent: Optional[QWidget] = None) -> QToolButton:
    """A small flat ``?`` button whose tooltip carries the field help text."""
    btn = QToolButton(parent)
    btn.setObjectName("HelpDot")
    btn.setText("?")
    btn.setCursor(Qt.CursorShape.WhatsThisCursor)
    btn.setToolTip(help_text)
    btn.setFixedSize(18, 18)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setAutoRaise(True)
    # Theme-neutral muted dot (reads on both light and dark backgrounds).
    btn.setStyleSheet(
        "QToolButton#HelpDot{border:1px solid rgba(128,128,128,0.55);"
        "border-radius:9px;color:rgba(128,128,128,0.95);"
        "font-size:11px;font-weight:700;padding:0;}"
        "QToolButton#HelpDot:hover{border-color:#0078d4;color:#0078d4;}"
    )
    # Tooltip-only: clicking also surfaces it (helps touch / impatient users).
    btn.clicked.connect(
        lambda: __import__("PyQt6.QtWidgets", fromlist=["QToolTip"]).QToolTip.showText(
            btn.mapToGlobal(btn.rect().bottomLeft()), help_text, btn
        )
    )
    return btn


def form_row(
    label_text: str,
    field: QWidget,
    *,
    required: bool = False,
    help_text: Optional[str] = None,
    label_width: int = 96,
) -> QWidget:
    """Return a row widget: ``[label(+*) | field(stretch) | ?]``.

    - ``required`` appends a red ``*`` to the label.
    - ``help_text`` adds a trailing ``?`` dot whose tooltip is the text.
    - ``label_width`` keeps every label column aligned across rows.
    """
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)

    lbl = QLabel()
    lbl.setObjectName("FormLabel")
    if required:
        lbl.setText(f"{label_text} <span style='color:#e06a5a;'>*</span>")
        lbl.setTextFormat(Qt.TextFormat.RichText)
    else:
        lbl.setText(label_text)
    lbl.setFixedWidth(label_width)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    h.addWidget(lbl)

    h.addWidget(field, 1)

    if help_text:
        field.setToolTip(help_text)

    return row
