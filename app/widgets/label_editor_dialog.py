# ============================================================================
# 已退役 (2026-06-07): QGraphicsScene 编辑器的模态包装。App 已不再引用
# (统一用 LabelDesignerDialog)。保留可回退 + 既有测试 import；确认后可删。
# ============================================================================
"""label_editor_dialog.py — modal wrapper around LabelEditorWidget.

The new 标签打印 page (Label Print Studio) edits a label template in a popup,
not inline.  This dialog hosts the existing WYSIWYG ``LabelEditorWidget`` (rows /
fields / QR drag) and returns the edited template on accept.

Reused logic (unchanged): ``app.widgets.label_editor.LabelEditorWidget`` — the
template-editing component; ``normalize_template`` for the canonical shape.
"""
from __future__ import annotations

import copy
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QVBoxLayout,
    QWidget,
)

from app.utils.label_core import normalize_template
from app.widgets.label_editor import LabelEditorWidget


class LabelEditorDialog(QDialog):
    """Modal editor for a single label template.

    Usage::

        dlg = LabelEditorDialog(template, dims, label_data, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_tmpl = dlg.edited_template()

    The wrapped ``LabelEditorWidget`` emits ``template_changed(dict)`` after each
    structural edit; the latest payload is captured and returned by
    :meth:`edited_template`.
    """

    def __init__(
        self,
        template: Optional[dict],
        dims: Optional[dict],
        label_data: Optional[dict],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑标签模板")
        self.setMinimumSize(560, 460)
        self.setStyleSheet("background: #0c1e26; color: #eef3ef;")

        self._dims = dims or {"w": 60, "h": 40}
        # The working copy starts from the normalized input; template_changed
        # overwrites it as the user edits.
        self._result_template: dict = normalize_template(template)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._editor = LabelEditorWidget(template, self._dims, label_data, self)
        self._editor.template_changed.connect(self._on_template_changed)
        root.addWidget(self._editor, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("确定")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_template_changed(self, tmpl: dict) -> None:
        self._result_template = normalize_template(tmpl)

    def edited_template(self) -> dict:
        """Return the latest edited template (normalized).  A deep copy so the
        caller can mutate / persist without aliasing the editor's state."""
        return copy.deepcopy(self._result_template)
