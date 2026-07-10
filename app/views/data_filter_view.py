"""data_filter_view.py — 数据筛选(跨断面 specimen 查询/筛选/编辑).

spec: docs/specs/2026-07-08-data-filter-view-design.md

从「项目树」打开(弹窗), 不在顶栏单独占一页:
- 顶部: 数据源多选(最近工作区 list_projects + 树所选断面)。
- 筛选条: 动态字段( specimen_fields PRAGMA+注册表) + 操作符 + 值, 多条件 AND, [+条件]。
- 状态: 默认只读; [🔒 解锁编辑] 会话登录(密码默认 123 + 修改人姓名)。
- 统计: 总数 / 已取RNA / 按拍摄人·地区分组计数。
- 结果表: 编号/断面/省/地区/拍摄人/保存/已取RNA/学名; 双击编辑(需解锁, 写回该断面 db)。

保护范围: 仅此页(A)。workbench 不动。
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.config.specimen_fields import (
    eval_derived,
    filterable_fields,
    is_derived,
)
from app.services import edit_lock_service
from app.services import specimen_filter_service as filter_svc
from app.utils import ui

# 停不下来的查询线程退休名单(模式同 project_tree_view._RETIRED_WARMUP_WORKERS):
# 保持模块级引用防 GC, 避免 destroyed-while-running 原生 abort。
_RETIRED_FILTER_WORKERS: list = []


class _FilterQueryWorker(QThread):
    """跨断面 specimens 查询后台线程 — N 库全表扫不进 Qt 主线程."""

    finished_rows = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, paths, conditions, labels, parent=None) -> None:
        super().__init__(parent)
        self._paths = list(paths)
        self._conditions = list(conditions or [])
        self._labels = list(labels or [])
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            rows = filter_svc.query_specimens(
                self._paths, self._conditions, labels=self._labels
            )
        except Exception as exc:
            if not self._cancel_requested:
                self.failed.emit(str(exc))
            return
        if self._cancel_requested:
            return
        self.finished_rows.emit(rows)
from app.utils.image_thumbnail import decode_image_data, make_pixmap
from app.views.base_view import BaseView

_OP_LABELS = {"eq": "等于", "contains": "包含", "is_empty": "为空", "not_empty": "非空"}
_OP_BY_LABEL = {v: k for k, v in _OP_LABELS.items()}

from app.config.specimen_fields import field_label as specimen_field_label

# 结果表固定列(展示用, 编辑只允许改 specimens 实列)
_COLUMNS = [
    ("uid", specimen_field_label("uid")),
    ("_workspace_label", specimen_field_label("_workspace_label")),
    ("province", specimen_field_label("province")),
    ("site", specimen_field_label("site")),
    ("photographer", specimen_field_label("photographer")),
    ("storage", specimen_field_label("storage")),
    ("__rna", "已取RNA"),
    ("scientific_name", specimen_field_label("scientific_name")),
]
# 可编辑的实列(解锁后双击改); 派生/断面/编号/计算列禁改
_EDITABLE_FIELDS = {"province", "site", "photographer", "storage", "scientific_name",
                    "scientific_name_cn", "family", "genus", "collector", "identifier",
                    "station", "geo_area", "notes", "photo_notes", "collection_date",
                    "photo_date"}


class DataFilterView(BaseView):
    view_id = "data_filter"
    nav_title = "数据筛选"
    nav_icon = "mdi6.filter-variant"

    def __init__(self, ctx) -> None:
        self._cond_rows: list[dict[str, QWidget]] = []
        self._rows: list[dict[str, Any]] = []
        self._suspend_cell_signal: bool = False
        super().__init__(ctx)

    # ── UI ────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        # 数据源
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("数据源(勾选断面, 可跨断面):"))
        self._btn_refresh_src = QPushButton("刷新")
        self._btn_refresh_src.clicked.connect(self._refresh_workspaces)
        src_row.addWidget(self._btn_refresh_src)
        src_row.addStretch(1)
        root.addLayout(src_row)
        self._src_list = QListWidget()
        self._src_list.setObjectName("DataFilterSourceList")
        self._src_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # 复选框多选(checkState), 数据源勾选 → 刷新字段下拉
        self._src_list.itemChanged.connect(self._on_source_changed)
        self._src_list.setMaximumHeight(110)
        root.addWidget(self._src_list)

        # 筛选条件
        cond_head = QHBoxLayout()
        cond_head.addWidget(QLabel("筛选条件:"))
        self._btn_add_cond = QPushButton("+ 条件")
        self._btn_add_cond.clicked.connect(lambda: self._add_condition_row())
        self._btn_clear_cond = QPushButton("清除条件")
        self._btn_clear_cond.clicked.connect(self._clear_conditions)
        self._btn_run = QPushButton("查询")
        self._btn_run.setObjectName("Primary")
        self._btn_run.clicked.connect(self._run_query)
        self._btn_export = QPushButton("导出 CSV")
        self._btn_export.setObjectName("Outline")
        self._btn_export.setToolTip("导出当前查询结果（全部汇总字段）")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._export_csv)
        cond_head.addWidget(self._btn_add_cond)
        cond_head.addWidget(self._btn_clear_cond)
        cond_head.addStretch(1)
        cond_head.addWidget(self._btn_export)
        cond_head.addWidget(self._btn_run)
        root.addLayout(cond_head)
        self._cond_box = QVBoxLayout()
        self._cond_box.setSpacing(4)
        root.addLayout(self._cond_box)

        # 状态 + 解锁
        status_row = QHBoxLayout()
        self._lock_lbl = QLabel("🔒 只读预览")
        self._lock_lbl.setObjectName("MutedSmall")
        self._btn_unlock = QPushButton("解锁编辑…")
        self._btn_unlock.setObjectName("Outline")
        self._btn_unlock.clicked.connect(self._on_unlock)
        status_row.addWidget(self._lock_lbl)
        status_row.addStretch(1)
        status_row.addWidget(self._btn_unlock)
        root.addLayout(status_row)

        # 统计
        self._stats_lbl = QLabel("尚未查询")
        self._stats_lbl.setObjectName("MutedSmall")
        self._stats_lbl.setWordWrap(True)
        root.addWidget(self._stats_lbl)

        # 结果表
        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("DataFilterResultTable")
        self._table.setHorizontalHeaderLabels([label for _, label in _COLUMNS])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemChanged.connect(self._on_cell_changed)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        from app.utils.tooltip_policy import suppress_popup_tooltip

        suppress_popup_tooltip(self._table)
        root.addWidget(self._table, 1)

        # 照片预览(选中编号 → 该 workspace 下成果 tif 缩略, 同步解码 max 280)
        self._photo_label = QLabel("选中编号查看照片")
        self._photo_label.setObjectName("DataFilterPhotoLabel")
        self._photo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._photo_label.setMinimumHeight(220)
        root.addWidget(self._photo_label)

        # 默认一个条件行
        self._add_condition_row()

    # ── 数据源 ─────────────────────────────────────────────────────────
    def on_activate(self) -> None:
        self._refresh_workspaces()

    def _refresh_workspaces(self) -> None:
        """从 user_projects.json 读最近工作区, 填数据源 list(保留勾选状态)。"""
        from app.services.project_service import (
            default_user_projects_json_path,
            list_projects,
        )
        try:
            projects = list_projects(default_user_projects_json_path())
        except Exception:
            projects = []
        # 保留旧勾选
        was_checked = {
            self._src_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._src_list.count())
            if self._src_list.item(i).checkState() == Qt.CheckState.Checked
        }
        self._src_list.blockSignals(True)
        self._src_list.clear()
        for p in projects:
            d = p.get("directory") or p.get("dir") or ""
            if not d:
                continue
            name = p.get("name") or d.split("/")[-1] or d
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, d)
            item.setCheckState(Qt.CheckState.Checked if d in was_checked else Qt.CheckState.Unchecked)
            self._src_list.addItem(item)
        self._src_list.blockSignals(False)

    @staticmethod
    def _norm_workspace_path(path: str) -> str:
        try:
            return str(Path(path).expanduser().resolve())
        except OSError:
            return str(path)

    def preselect_workspaces(self, directories: list[str]) -> None:
        """勾选指定断面; 不在最近列表里的也会补进数据源。"""
        want = {self._norm_workspace_path(d) for d in directories if d}
        if not want:
            return
        existing = {
            self._norm_workspace_path(str(self._src_list.item(i).data(Qt.ItemDataRole.UserRole)))
            for i in range(self._src_list.count())
        }
        for d in directories:
            if not d:
                continue
            np = self._norm_workspace_path(d)
            if np in existing:
                continue
            label = Path(d).name or d
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, d)
            self._src_list.addItem(it)
            existing.add(np)
        self._src_list.blockSignals(True)
        for i in range(self._src_list.count()):
            it = self._src_list.item(i)
            p = self._norm_workspace_path(str(it.data(Qt.ItemDataRole.UserRole)))
            it.setCheckState(
                Qt.CheckState.Checked if p in want else Qt.CheckState.Unchecked
            )
        self._src_list.blockSignals(False)
        self._on_source_changed()

    def _set_workspaces(self, items: list[tuple[str, str]]) -> None:
        """测试/外部注入数据源 (path, label)。"""
        self._src_list.blockSignals(True)
        self._src_list.clear()
        for path, label in items:
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, path)
            it.setCheckState(Qt.CheckState.Checked)
            self._src_list.addItem(it)
        self._src_list.blockSignals(False)
        self._on_source_changed()

    def _checked_workspaces(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for i in range(self._src_list.count()):
            it = self._src_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                out.append((str(it.data(Qt.ItemDataRole.UserRole)), it.text()))
        return out

    def _first_checked_db(self) -> Optional[str]:
        import sqlite3 as _sq
        from pathlib import Path
        for path, _ in self._checked_workspaces():
            db = Path(path) / "_data" / "project.db"
            if db.exists():
                return str(db)
        return None

    def _on_source_changed(self, *_args) -> None:
        """数据源变化 → 刷新字段下拉候选(取首个可用工作区的 schema)。"""
        db = self._first_checked_db()
        fields = filterable_fields(db) if db else []
        for row in self._cond_rows:
            self._repopulate_field_combo(row, fields)

    # ── 筛选条件行 ────────────────────────────────────────────────────
    def _add_condition_row(self, field: str = "photographer", op: str = "eq", value: str = "") -> None:
        row: dict[str, QWidget] = {}
        bar = QHBoxLayout()
        fcombo = QComboBox()
        row["field"] = fcombo
        ocombo = QComboBox()
        row["op"] = ocombo
        vcombo = QComboBox()
        vcombo.setEditable(True)
        row["value"] = vcombo
        fcombo.currentIndexChanged.connect(lambda *_: self._on_field_changed(row))
        ocombo.currentIndexChanged.connect(lambda *_: self._on_op_changed(row))
        bar.addWidget(fcombo)
        bar.addWidget(ocombo)
        bar.addWidget(vcombo, 1)
        delbtn = QPushButton("✕")
        delbtn.setFixedWidth(28)
        delbtn.clicked.connect(lambda _=False, b=bar, r=row: self._remove_condition_row(b, r))
        bar.addWidget(delbtn)
        row["bar"] = bar
        self._cond_box.addLayout(bar)
        self._cond_rows.append(row)
        self._repopulate_field_combo(row, filterable_fields(self._first_checked_db()) if self._first_checked_db() else [])
        # 设默认 field/op/value
        idx = fcombo.findData(field)
        if idx >= 0:
            fcombo.setCurrentIndex(idx)
        self._repopulate_op_combo(row)
        self._repopulate_value_combo(row)
        if value:
            vcombo.setEditText(value)

    def _remove_condition_row(self, bar: QHBoxLayout, row: dict[str, QWidget]) -> None:
        from app.utils.ui import dispose_widget

        for w in row.values():
            if isinstance(w, QWidget):
                dispose_widget(w)
        self._cond_box.removeItem(bar)
        if row in self._cond_rows:
            self._cond_rows.remove(row)

    def _clear_conditions(self) -> None:
        for row in list(self._cond_rows):
            self._remove_condition_row(row["bar"], row)
        self._add_condition_row()

    def _repopulate_field_combo(self, row: dict[str, QWidget], fields: list[dict]) -> None:
        combo: QComboBox = row["field"]
        cur = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for f in fields:
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
        ops = ["eq"] if is_derived(field or "") else ["eq", "contains", "is_empty", "not_empty"]
        for op in ops:
            combo.addItem(_OP_LABELS[op], op)
        if cur in ops:
            combo.setCurrentIndex(combo.findData(cur))
        combo.blockSignals(False)

    def _repopulate_value_combo(self, row: dict[str, QWidget]) -> None:
        combo: QComboBox = row["value"]
        field = row["field"].currentData()
        op = row["op"].currentData()
        cur = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        if op in ("is_empty", "not_empty"):
            combo.setEnabled(False)
        else:
            combo.setEnabled(True)
            ws = [p for p, _ in self._checked_workspaces()]
            if field and ws:
                for v in filter_svc.field_choices(ws, field):
                    combo.addItem(v)
        combo.setEditText(cur)
        combo.blockSignals(False)

    def _on_field_changed(self, row: dict[str, QWidget]) -> None:
        self._repopulate_op_combo(row)
        self._repopulate_value_combo(row)

    def _on_op_changed(self, row: dict[str, QWidget]) -> None:
        self._repopulate_value_combo(row)

    def _collect_conditions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in self._cond_rows:
            field = row["field"].currentData()
            op = row["op"].currentData()
            value = row["value"].currentText()
            if not field or not op:
                continue
            out.append({"field": field, "op": op, "value": value})
        return out

    # ── 查询 / 结果 ───────────────────────────────────────────────────
    def _run_query(self) -> None:
        ws = self._checked_workspaces()
        if not ws:
            ui.warn(self, "未选数据源", "请先在上方勾选至少一个断面。")
            return
        paths = [p for p, _ in ws]
        labels = [l for _, l in ws]
        # §7 旧: 主线程同步跑 query_specimens(N 库全表扫+内存合并), 多断面
        # 大表按钮 handler 直接假死(用户裁定: 点击零反馈=bug)。
        # rows = filter_svc.query_specimens(paths, self._collect_conditions(), labels=labels)
        # self._rows = rows
        # self._fill_table(rows)
        # self._fill_stats(rows)
        # if hasattr(self, "_btn_export"):
        #     self._btn_export.setEnabled(bool(rows))
        token = getattr(self, "_query_token", 0) + 1
        self._query_token = token
        self._stop_filter_query_worker(wait_ms=200)

        # busy 反馈复用现有控件状态, 不新增控件
        self._btn_run.setEnabled(False)
        self._btn_run.setText("查询中…")
        self._stats_lbl.setText(f"查询中… ({len(paths)} 个断面)")

        worker = _FilterQueryWorker(
            paths, self._collect_conditions(), labels, parent=self
        )
        worker.finished_rows.connect(
            lambda rows, t=token: self._on_query_done(t, rows)
        )
        worker.failed.connect(lambda msg, t=token: self._on_query_failed(t, msg))
        self._query_worker = worker
        worker.start()

    def _restore_run_button(self) -> None:
        self._btn_run.setEnabled(True)
        self._btn_run.setText("查询")

    def _on_query_done(self, token: int, rows: list) -> None:
        if token != getattr(self, "_query_token", 0):
            return
        self._restore_run_button()
        self._rows = rows
        self._fill_table(rows)
        self._fill_stats(rows)
        if hasattr(self, "_btn_export"):
            self._btn_export.setEnabled(bool(rows))

    def _on_query_failed(self, token: int, msg: str) -> None:
        if token != getattr(self, "_query_token", 0):
            return
        self._restore_run_button()
        self._stats_lbl.setText("查询失败")
        ui.warn(self, "查询失败", msg)

    def _stop_filter_query_worker(self, wait_ms: int = 200) -> None:
        worker = getattr(self, "_query_worker", None)
        if worker is None:
            return
        try:
            if worker.isRunning():
                worker.cancel()
                if not worker.wait(wait_ms):
                    # 停不下来: 脱父防 destroyed-while-running abort,
                    # 交回收前的信号已被 token 判过期丢弃。
                    for sig in ("finished_rows", "failed"):
                        try:
                            getattr(worker, sig).disconnect()
                        except Exception:
                            pass
                    try:
                        worker.setParent(None)
                    except Exception:
                        pass
                    _RETIRED_FILTER_WORKERS.append(worker)
        except Exception:
            pass
        self._query_worker = None

    def _export_csv(self) -> None:
        if not self._rows:
            ui.warn(self, "导出", "请先查询出结果再导出。")
            return
        path = ui.get_save_file_name(
            self,
            "导出筛选结果 CSV",
            "数据筛选结果.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        from app.services import cross_workspace_query_service as cwq

        ws_paths = sorted({
            str(r.get("_workspace") or "").strip()
            for r in self._rows
            if str(r.get("_workspace") or "").strip()
        })
        columns = cwq.summary_all_columns(ws_paths)
        try:
            out = cwq.export_filtered_specimens_csv(
                self._rows, path, columns=columns or None,
            )
        except Exception as exc:
            ui.warn(self, "导出失败", str(exc))
            return
        ui.info(self, "导出完成", f"已保存：\n{out}")

    def _fill_table(self, rows: list[dict[str, Any]]) -> None:
        self._suspend_cell_signal = True
        self._table.setRowCount(0)
        for r in rows:
            crow = self._table.rowCount()
            self._table.insertRow(crow)
            for ci, (key, _label) in enumerate(_COLUMNS):
                if key == "__rna":
                    val = "是" if eval_derived("storage_is_rna", r) else "否"
                else:
                    cell = r.get(key)
                    val = "" if cell is None else str(cell)
                item = QTableWidgetItem(val)
                editable = (
                    edit_lock_service.is_unlocked(self.ctx)
                    and key in _EDITABLE_FIELDS
                )
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if editable:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
                self._table.setItem(crow, ci, item)
        self._suspend_cell_signal = False

    def _fill_stats(self, rows: list[dict[str, Any]]) -> None:
        total = len(rows)
        rna = sum(1 for r in rows if eval_derived("storage_is_rna", r))
        by_photo = Counter(r.get("photographer") or "(未填)" for r in rows)
        by_site = Counter(r.get("site") or "(未填)" for r in rows)
        photo_s = " / ".join(f"{k}:{v}" for k, v in by_photo.most_common(5))
        site_s = " / ".join(f"{k}:{v}" for k, v in by_site.most_common(5))
        self._stats_lbl.setText(
            f"共 {total} 个编号 | 已取RNA {rna} | 拍摄人〔{photo_s}〕| 地区〔{site_s}〕"
        )

    # ── 解锁 / 编辑 ───────────────────────────────────────────────────
    def _update_lock_button(self) -> None:
        if edit_lock_service.is_unlocked(self.ctx):
            actor = edit_lock_service.current_actor(self.ctx)
            self._lock_lbl.setText(f"🔓 已解锁: {actor}")
            self._btn_unlock.setText("锁定")
        else:
            self._lock_lbl.setText("🔒 只读预览")
            self._btn_unlock.setText("解锁编辑…")

    def _on_unlock(self) -> None:
        if edit_lock_service.is_unlocked(self.ctx):
            edit_lock_service.lock(self.ctx)
            self._refresh_edit_state()
            return
        pwd, ok1 = QInputDialog.getText(self, "解锁编辑", "管理密码:", QLineEdit.EchoMode.Password)
        if not ok1:
            return
        actor, ok2 = QInputDialog.getText(self, "解锁编辑", "修改人姓名:")
        if not ok2 or not actor.strip():
            ui.warn(self, "需填姓名", "请填写修改人姓名(写入审计)。")
            return
        if edit_lock_service.unlock(self.ctx, actor.strip(), pwd):
            self._refresh_edit_state()
        else:
            ui.warn(self, "密码错误", "管理密码不正确, 仍为只读。")

    def _refresh_edit_state(self) -> None:
        self._update_lock_button()
        # 重填以应用 editable flags
        self._fill_table(self._rows)

    def _on_cell_changed(self, item: QTableWidgetItem) -> None:
        if self._suspend_cell_signal or not edit_lock_service.is_unlocked(self.ctx):
            return
        col = item.column()
        key = _COLUMNS[col][0]
        if key not in _EDITABLE_FIELDS:
            return
        row_idx = item.row()
        if row_idx >= len(self._rows):
            return
        spec_row = self._rows[row_idx]
        uid = spec_row.get("uid")
        workspace = spec_row.get("_workspace")
        if not uid or not workspace:
            return
        self._persist_edit(str(uid), key, item.text(), str(workspace))
        # 派生/统计随改值刷新
        if key == "storage":
            spec_row[key] = item.text()
            self._fill_table(self._rows)
            self._fill_stats(self._rows)

    def _on_selection_changed(self) -> None:
        """选中结果行 → 右下显示该编号成果照片(首个 .tif 缩略)。"""
        sm = self._table.selectionModel()
        if sm is None:
            return
        idxs = sm.selectedRows()
        if not idxs:
            self._photo_label.clear()
            self._photo_label.setText("选中编号查看照片")
            return
        row_idx = idxs[0].row()
        if row_idx >= len(self._rows):
            return
        r = self._rows[row_idx]
        path = self._find_specimen_photo(r.get("uid"), r.get("_workspace"))
        if not path:
            self._photo_label.clear()
            self._photo_label.setText("(该编号暂无成果照片)")
            return
        pm = make_pixmap(decode_image_data(path, 280))
        if pm is not None and not pm.isNull():
            self._photo_label.setPixmap(pm.scaled(
                280, 220, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        else:
            self._photo_label.clear()
            self._photo_label.setText("(照片解码失败)")

    def _find_specimen_photo(self, uid: Optional[str], workspace: Optional[str]) -> Optional[str]:
        """该 specimen 在所属 workspace 下的代表成果照片(首个 .tif, uid 前缀匹配)。

        成果文件名 = uid + (-seq) + .tif(oracle parseTiffBasename); 故 uid 前缀 glob 命中。
        incoming-jpg 的 jpg 命名自由(无 uid), 不在此关联。
        """
        from pathlib import Path
        if not uid or not workspace:
            return None
        root = Path(workspace)
        for sub in ("results", "results/freeform"):
            d = root / sub
            if not d.is_dir():
                continue
            try:
                matches = sorted(list(d.glob(f"{uid}*.tif")) + list(d.glob(f"{uid}*.tiff")))
            except OSError:
                continue
            if matches:
                return str(matches[0])
        return None

    def _persist_edit(self, uid: str, field: str, value: str, workspace: str) -> bool:
        """解锁后: 写回该 specimen 所属工作区 db + 记审计。"""
        from pathlib import Path
        db_path = Path(workspace) / "_data" / "project.db"
        if not db_path.exists():
            return False
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                # field 来自 _EDITABLE_FIELDS 白名单(实列名), 防 SQL 注入
                conn.execute(f'UPDATE specimens SET "{field}"=? WHERE uid=?', (value, uid))
                conn.commit()
                self._rows  # row 缓存同步由调用方负责
            finally:
                conn.close()
        except sqlite3.Error as exc:
            ui.warn(self, "保存失败", f"写入数据库失败:\n{exc}")
            return False
        self._log_edit_audit(uid, field, value, workspace)
        return True

    def _log_edit_audit(self, uid: str, field: str, value: str, workspace: str) -> None:
        """best-effort 审计: 优先 activity_audit_service, 无表/失败静默。"""
        try:
            from app.services.activity_audit_service import default_actor, log_event
            # from app.db.db_manager import open_project_db  # §7 旧: 缓存任意被聚合工作区的连接 → 锁泄漏
            from app.db.db_manager import open_project_db_private
            # db = open_project_db(workspace)  # §7 旧
            db = open_project_db_private(workspace)
            try:
                log_event(
                    db,
                    actor=edit_lock_service.current_actor(self.ctx) or default_actor(self.ctx),
                    action="specimen.edit",
                    entity_type="specimen",
                    entity_id=uid,
                    new_value={"field": field, "value": value},
                )
            finally:
                # 私有连接用完即 close(跨工作区读写规约, log_event 自带 commit)
                db.close()
        except Exception:
            # 审计失败不应阻断编辑
            pass

    # ── 生命周期 ──────────────────────────────────────────────────────
    def stop_background_work(self) -> None:
        # §7 旧: 本 view 无后台线程(查询同步, 简单稳) —— 查询已移入
        # _FilterQueryWorker, 退出/关窗前必须停掉。
        self._stop_filter_query_worker(wait_ms=2000)

    def closeEvent(self, event) -> None:  # noqa: D401 - Qt override
        # 本 view 宿主是模态对话框(data_filter_dialog): 对话框关闭 → 视图
        # 销毁, 运行中的 QThread 不停会原生 abort。
        self.stop_background_work()
        super().closeEvent(event)
