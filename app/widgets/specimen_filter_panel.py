"""specimen_filter_panel.py — 通用标本筛选条（项目树数据汇总用）."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.config.specimen_fields import COMMON_FILTER_PRIORITY, filterable_fields, is_derived
from app.services import specimen_filter_service as filter_svc

_OP_LABELS = {
    "eq": "等于",
    "in": "任一",
    "contains": "包含",
    "is_empty": "为空",
    "not_empty": "非空",
    "gte": "不早于",
    "lte": "不晚于",
    "between": "区间",
}

# 快捷筛选：一键插入常用条件行
_QUICK_FILTERS: tuple[tuple[str, str], ...] = (
    ("photographer", "拍摄人"),
    ("station", "站位"),
    ("storage_is_rna", "已取RNA"),
    ("collection_date", "采集日期"),
    ("scientific_name", "学名"),
)


class SpecimenFilterPanel(QFrame):
    """动态多条件筛选；workspaces 由宿主设置."""

    filter_changed = pyqtSignal(list)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SpecimenFilterPanel")
        self._workspaces: list[str] = []
        self._cond_rows: list[dict[str, QWidget]] = []
        self._choice_cache: dict[str, list[str]] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self._body = QWidget()
        self._body.setObjectName("SpecimenFilterBody")
        body_root = QVBoxLayout(self._body)
        body_root.setContentsMargins(0, 0, 0, 0)
        body_root.setSpacing(6)

        quick_row = QHBoxLayout()
        quick_row.setSpacing(6)
        quick_lbl = QLabel("快捷")
        quick_lbl.setObjectName("MutedSmall")
        quick_row.addWidget(quick_lbl)
        for field, label in _QUICK_FILTERS:
            btn = QPushButton(label)
            btn.setObjectName("FilterChip")
            btn.setFixedHeight(26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"添加「{label}」条件，并从现有数据中点选")
            btn.clicked.connect(
                lambda _checked=False, f=field: self._add_quick_condition(f)
            )
            quick_row.addWidget(btn)
        body_root.addLayout(quick_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        action_row.addStretch(1)
        self._btn_add = QPushButton("+ 条件")
        self._btn_add.setObjectName("Outline")
        self._btn_add.setFixedHeight(26)
        self._btn_add.clicked.connect(lambda: self._add_condition_row(open_popup=True))
        action_row.addWidget(self._btn_add)
        self._btn_clear = QPushButton("清除条件")
        self._btn_clear.setObjectName("Ghost")
        self._btn_clear.setFixedHeight(26)
        self._btn_clear.clicked.connect(self._clear_conditions)
        self._btn_clear.hide()
        action_row.addWidget(self._btn_clear)
        self._btn_apply = QPushButton("应用筛选")
        self._btn_apply.setObjectName("Primary")
        self._btn_apply.setFixedHeight(28)
        self._btn_apply.clicked.connect(self._emit_conditions)
        action_row.addWidget(self._btn_apply)
        body_root.addLayout(action_row)

        self._cond_frame = QFrame()
        self._cond_frame.setObjectName("SpecimenFilterCondBox")
        cond_lay = QVBoxLayout(self._cond_frame)
        cond_lay.setContentsMargins(8, 5, 8, 5)
        cond_lay.setSpacing(6)
        self._cond_box = QVBoxLayout()
        self._cond_box.setSpacing(6)
        cond_lay.addLayout(self._cond_box)
        self._empty_hint = QLabel(
            "未设条件时显示全部编号。点快捷项后，在下拉中点选已有值（也可手输）。"
        )
        self._empty_hint.setObjectName("MutedSmall")
        self._empty_hint.setWordWrap(True)
        cond_lay.addWidget(self._empty_hint)
        self._cond_frame.hide()
        body_root.addWidget(self._cond_frame)

        root.addWidget(self._body)

    def set_body_expanded(self, expanded: bool) -> None:
        self._body.setVisible(bool(expanded))

    def is_body_expanded(self) -> bool:
        return self._body.isVisible()

    def active_condition_count(self) -> int:
        return len(self._cond_rows)

    def set_workspaces(self, workspaces: list[str]) -> None:
        self._workspaces = [str(w) for w in (workspaces or []) if w]
        self._choice_cache.clear()
        fields = self._fields()
        for row in self._cond_rows:
            self._repopulate_field_combo(row, fields)
            self._repopulate_value_combo(row)

    def conditions(self) -> list[dict[str, Any]]:
        return self._collect_conditions()

    def _fields(self) -> list[dict[str, Any]]:
        for ws in self._workspaces:
            db = Path(ws) / "_data" / "project.db"
            if db.exists():
                return filterable_fields(str(db))
        return filterable_fields("")

    def _choices_for(self, field: str) -> list[str]:
        key = str(field or "")
        if not key:
            return []
        if key in self._choice_cache:
            return self._choice_cache[key]
        if not self._workspaces:
            choices: list[str] = []
        else:
            choices = filter_svc.field_choices(self._workspaces, key)
        self._choice_cache[key] = choices
        return choices

    def _sync_empty_hint(self) -> None:
        has_conditions = bool(self._cond_rows)
        self._empty_hint.hide()
        self._cond_frame.setVisible(has_conditions)
        self._btn_clear.setVisible(has_conditions)

    def _add_quick_condition(self, field: str) -> None:
        for row in self._cond_rows:
            if row["field"].currentData() == field:
                self._repopulate_value_combo(row)
                self._open_value_popup(row)
                return
        op = "eq"
        value = ""
        if field == "collection_date":
            op = "between"
        elif field == "storage_is_rna":
            value = "是"
        self._add_condition_row(field=field, op=op, value=value, open_popup=not value)
        # RNA 等已有默认值：直接应用；其余等用户点选后再点「应用筛选」
        if value:
            self._emit_conditions()

    def _add_condition_row(
        self,
        field: str = "photographer",
        op: str = "eq",
        value: str = "",
        *,
        open_popup: bool = False,
    ) -> None:
        row: dict[str, QWidget] = {}
        bar = QHBoxLayout()
        bar.setSpacing(6)
        fcombo = QComboBox()
        fcombo.setMinimumWidth(108)
        fcombo.setMaximumWidth(140)
        row["field"] = fcombo
        ocombo = QComboBox()
        ocombo.setMinimumWidth(72)
        ocombo.setMaximumWidth(88)
        row["op"] = ocombo
        vcombo = QComboBox()
        vcombo.setEditable(True)
        vcombo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        vcombo.setMinimumWidth(160)
        vcombo.setMaxVisibleItems(16)
        vcombo.setToolTip("从当前选中工作区自动列出可选值；可点选，也可输入后筛选")
        row["value"] = vcombo
        fcombo.currentIndexChanged.connect(lambda *_: self._on_field_changed(row))
        ocombo.currentIndexChanged.connect(lambda *_: self._on_op_changed(row))
        vcombo.activated.connect(lambda idx, r=row: self._on_value_activated(r, idx))
        bar.addWidget(fcombo)
        bar.addWidget(ocombo)
        bar.addWidget(vcombo, 1)
        delbtn = QPushButton("删除")
        delbtn.setObjectName("Ghost")
        delbtn.setFixedHeight(26)
        delbtn.clicked.connect(lambda _=False, b=bar, r=row: self._remove_row(b, r))
        bar.addWidget(delbtn)
        row["bar"] = bar
        self._cond_box.addLayout(bar)
        self._cond_rows.append(row)
        self._sync_empty_hint()
        self._repopulate_field_combo(row, self._fields())
        idx = fcombo.findData(field)
        if idx >= 0:
            fcombo.setCurrentIndex(idx)
        self._repopulate_op_combo(row)
        oidx = ocombo.findData(op)
        if oidx >= 0:
            ocombo.setCurrentIndex(oidx)
        self._repopulate_value_combo(row)
        if value:
            vcombo.setEditText(value)
            found = vcombo.findText(value)
            if found >= 0:
                vcombo.setCurrentIndex(found)
        if open_popup:
            self._open_value_popup(row)

    def _open_value_popup(self, row: dict[str, QWidget]) -> None:
        combo: QComboBox = row["value"]
        if not combo.isEnabled() or combo.count() <= 0:
            return
        combo.setFocus(Qt.FocusReason.OtherFocusReason)
        combo.showPopup()

    def _remove_row(self, bar: QHBoxLayout, row: dict[str, QWidget]) -> None:
        from app.utils.ui import dispose_widget

        for w in row.values():
            if isinstance(w, QWidget):
                dispose_widget(w)
        self._cond_box.removeItem(bar)
        if row in self._cond_rows:
            self._cond_rows.remove(row)
        self._sync_empty_hint()

    def _clear_conditions(self) -> None:
        for row in list(self._cond_rows):
            self._remove_row(row["bar"], row)
        self._emit_conditions()

    def _repopulate_field_combo(self, row: dict[str, QWidget], fields: list[dict]) -> None:
        combo: QComboBox = row["field"]
        cur = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        common_keys = set(COMMON_FILTER_PRIORITY)
        common = [f for f in fields if f["key"] in common_keys]
        other = [f for f in fields if f["key"] not in common_keys]
        for f in common:
            combo.addItem(f["label"], f["key"])
        if other:
            combo.insertSeparator(combo.count())
            for f in other:
                combo.addItem(f["label"], f["key"])
        if cur:
            idx = combo.findData(cur)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _repopulate_op_combo(self, row: dict[str, QWidget]) -> None:
        combo: QComboBox = row["op"]
        field = row["field"].currentData()
        cur = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for op in filter_svc.operators_for_field(str(field or "")):
            combo.addItem(_OP_LABELS.get(op, op), op)
        if cur:
            idx = combo.findData(cur)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _repopulate_value_combo(self, row: dict[str, QWidget]) -> None:
        combo: QComboBox = row["value"]
        field = row["field"].currentData()
        op = row["op"].currentData()
        cur = combo.currentText().strip()
        selected_values = _split_ui_values(cur)
        combo.blockSignals(True)
        combo.clear()
        if op in ("is_empty", "not_empty"):
            combo.setEnabled(False)
            combo.setPlaceholderText("")
            combo.setCompleter(None)
        else:
            combo.setEnabled(True)
            choices = self._choices_for(str(field or "")) if field else []
            for v in choices:
                combo.addItem(v)
            if choices:
                completer = QCompleter(choices, combo)
                completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                completer.setFilterMode(Qt.MatchFlag.MatchContains)
                completer.setCompletionMode(
                    QCompleter.CompletionMode.PopupCompletion
                )
                combo.setCompleter(completer)
            else:
                combo.setCompleter(None)
            if op == "in":
                row["multi_values"] = selected_values
            if cur:
                found = combo.findText(cur)
                if found >= 0:
                    combo.setCurrentIndex(found)
                else:
                    combo.setEditText(cur)
            elif is_derived(str(field or "")) and combo.count() > 0:
                combo.setCurrentIndex(0)
            else:
                combo.setCurrentIndex(-1)
                combo.setEditText("")
            if op == "between":
                combo.setPlaceholderText("起始-结束；如 2025-2026 / 202501-202601")
            elif op == "in":
                combo.setPlaceholderText("可点选多个值；用 | 分隔")
            elif choices:
                combo.setPlaceholderText(f"共 {len(choices)} 项 · 点选或输入…")
            elif not self._workspaces:
                combo.setPlaceholderText("请先选择工作区")
            else:
                combo.setPlaceholderText("暂无可选值 · 可手输")
        combo.blockSignals(False)

    def _on_field_changed(self, row: dict[str, QWidget]) -> None:
        self._repopulate_op_combo(row)
        self._repopulate_value_combo(row)
        self._open_value_popup(row)

    def _on_op_changed(self, row: dict[str, QWidget]) -> None:
        self._repopulate_value_combo(row)

    def _on_value_activated(self, row: dict[str, QWidget], index: int) -> None:
        combo: QComboBox = row["value"]
        op = row["op"].currentData()
        if op != "in":
            self._emit_conditions()
            return
        picked = combo.itemText(index).strip() if index >= 0 else combo.currentText().strip()
        values = list(row.get("multi_values") or _split_ui_values(combo.currentText()))
        if picked and picked not in values:
            values.append(picked)
        row["multi_values"] = values
        combo.setEditText("|".join(values))
        self._emit_conditions()

    def _collect_conditions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in self._cond_rows:
            field = row["field"].currentData()
            op = row["op"].currentData()
            if not field or not op:
                continue
            if op in ("is_empty", "not_empty"):
                out.append({"field": str(field), "op": str(op), "value": ""})
                continue
            val = row["value"].currentText().strip()
            if op == "in":
                stored = row.get("multi_values")
                if stored:
                    val = "|".join(str(v).strip() for v in stored if str(v).strip())
            if not val:
                continue
            if is_derived(str(field)):
                val = "是" if val in ("是", "true", "1", "yes") else val
            out.append({"field": str(field), "op": str(op), "value": val})
        return out

    def _emit_conditions(self) -> None:
        self.filter_changed.emit(self._collect_conditions())


def _split_ui_values(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    import re

    return [
        part.strip()
        for part in re.split(r"[|,，;；\n]+", text)
        if part.strip()
    ]
