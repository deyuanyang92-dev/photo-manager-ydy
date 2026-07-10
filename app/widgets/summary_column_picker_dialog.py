"""summary_column_picker_dialog.py — 数据汇总表格列显示选择（按类别分组）."""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config.specimen_fields import group_summary_columns
from app.utils import ui


class SummaryColumnPickerDialog(QDialog):
    """勾选要在数据汇总表格中显示的列（CSV 仍导出全部列）。"""

    def __init__(
        self,
        all_columns: list[tuple[str, str]],
        visible_keys: list[str],
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._all_columns = list(all_columns or [])
        self._visible_keys = list(visible_keys or [])
        self._visible = set(self._visible_keys)
        self._checks: dict[str, QCheckBox] = {}
        self._category_checks: dict[str, QCheckBox] = {}
        self._label_by_key = {k: lbl for k, lbl in self._all_columns}
        self._groups = group_summary_columns(self._all_columns)
        self.setWindowTitle("选择显示列")
        self.setMinimumWidth(480)
        self.setMinimumHeight(520)
        self._build_ui()
        ui.center_on(self, parent)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        title = QLabel("选择要在表格中显示的列")
        title.setObjectName("PaneTitle")
        root.addWidget(title)

        hint = QLabel(
            f"共 {len(self._all_columns)} 个字段，分组对齐工作台右栏"
            "（照片编号 / 分类标签 / 其它 / 拍照与相机）；"
            "未勾选的列不在表格显示，但「导出 CSV」仍包含全部字段。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        root.addWidget(hint)

        tools = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_default = QPushButton("恢复默认")
        btn_default.clicked.connect(self._reset_default)
        btn_none = QPushButton("全不选")
        btn_none.clicked.connect(lambda: self._set_all(False))
        tools.addWidget(btn_all)
        tools.addWidget(btn_default)
        tools.addWidget(btn_none)
        tools.addStretch(1)
        root.addLayout(tools)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(10)

        for group in self._groups:
            body_lay.addWidget(self._make_group_section(group))

        body_lay.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        order_title = QLabel("显示顺序（拖动调整）")
        order_title.setObjectName("Section")
        root.addWidget(order_title)
        order_hint = QLabel("表格列从左到右按下列顺序显示；也可在表格表头直接拖动列名调序。")
        order_hint.setWordWrap(True)
        order_hint.setObjectName("Muted")
        root.addWidget(order_hint)
        self._order_list = QListWidget()
        self._order_list.setObjectName("SummaryColumnOrderList")
        self._order_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._order_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._order_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._order_list.setMinimumHeight(120)
        self._order_list.setMaximumHeight(180)
        root.addWidget(self._order_list)
        self._rebuild_order_list()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for cat_id in self._category_checks:
            self._sync_category_header(cat_id)

    def _make_group_section(self, group: dict[str, Any]) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SummaryColumnGroup")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        cat_id = str(group.get("id") or "")
        columns: list[tuple[str, str]] = list(group.get("columns") or [])
        header = QCheckBox(f"{group.get('label') or cat_id}  ·  {len(columns)} 项")
        header.setObjectName("SummaryColumnGroupHeader")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setTristate(True)
        self._category_checks[cat_id] = header
        header.clicked.connect(lambda _checked=False, cid=cat_id: self._on_category_clicked(cid))
        lay.addWidget(header)

        items = QWidget()
        items_lay = QVBoxLayout(items)
        items_lay.setContentsMargins(18, 0, 0, 0)
        items_lay.setSpacing(2)
        for key, label in columns:
            cb = QCheckBox(label)
            cb.setChecked(key in self._visible)
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.stateChanged.connect(
                lambda _state=0, cid=cat_id: self._on_field_check_changed(cid)
            )
            self._checks[key] = cb
            items_lay.addWidget(cb)
        lay.addWidget(items)
        return frame

    def _category_field_keys(self, cat_id: str) -> list[str]:
        for group in self._groups:
            if str(group.get("id") or "") == cat_id:
                return [key for key, _label in group.get("columns") or []]
        return []

    def _on_category_clicked(self, cat_id: str) -> None:
        keys = self._category_field_keys(cat_id)
        if not keys:
            return
        states = [self._checks[k].isChecked() for k in keys if k in self._checks]
        select_all = not all(states)
        for key in keys:
            cb = self._checks.get(key)
            if cb is not None:
                cb.blockSignals(True)
                cb.setChecked(select_all)
                cb.blockSignals(False)
        self._sync_category_header(cat_id)

    def _sync_category_header(self, cat_id: str) -> None:
        header = self._category_checks.get(cat_id)
        if header is None:
            return
        keys = self._category_field_keys(cat_id)
        if not keys:
            return
        states = [self._checks[k].isChecked() for k in keys if k in self._checks]
        if not states:
            return
        header.blockSignals(True)
        if all(states):
            header.setCheckState(Qt.CheckState.Checked)
        elif any(states):
            header.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            header.setCheckState(Qt.CheckState.Unchecked)
        header.blockSignals(False)

    def _on_field_check_changed(self, cat_id: str) -> None:
        self._sync_category_header(cat_id)
        self._rebuild_order_list()

    def _set_all(self, checked: bool) -> None:
        for cb in self._checks.values():
            cb.setChecked(checked)
        for cat_id in self._category_checks:
            self._sync_category_header(cat_id)
        self._rebuild_order_list()

    def _reset_default(self) -> None:
        from app.services.cross_workspace_query_service import DEFAULT_SUMMARY_VISIBLE_KEYS

        defaults = set(DEFAULT_SUMMARY_VISIBLE_KEYS)
        for key, cb in self._checks.items():
            cb.setChecked(key in defaults)
        for cat_id in self._category_checks:
            self._sync_category_header(cat_id)
        self._rebuild_order_list()

    def _checked_keys(self) -> list[str]:
        out: list[str] = []
        for key, _label in self._all_columns:
            cb = self._checks.get(key)
            if cb is not None and cb.isChecked():
                out.append(key)
        return out

    def _rebuild_order_list(self) -> None:
        checked = set(self._checked_keys())
        preserved: list[str] = []
        for row in range(self._order_list.count()):
            key = str(self._order_list.item(row).data(Qt.ItemDataRole.UserRole) or "")
            if key in checked and key not in preserved:
                preserved.append(key)
        if not preserved:
            preserved = [k for k in self._visible_keys if k in checked]
        for key in self._checked_keys():
            if key not in preserved:
                preserved.append(key)

        self._order_list.blockSignals(True)
        self._order_list.clear()
        for key in preserved:
            if key not in checked:
                continue
            item = QListWidgetItem(self._label_by_key.get(key, key))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._order_list.addItem(item)
        self._order_list.blockSignals(False)

    def selected_keys(self) -> list[str]:
        """按用户排序返回勾选的 key。"""
        out: list[str] = []
        for row in range(self._order_list.count()):
            key = str(self._order_list.item(row).data(Qt.ItemDataRole.UserRole) or "")
            cb = self._checks.get(key)
            if cb is not None and cb.isChecked() and key not in out:
                out.append(key)
        for key in self._checked_keys():
            if key not in out:
                out.append(key)
        return out
