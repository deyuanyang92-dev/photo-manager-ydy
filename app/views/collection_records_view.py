"""collection_records_view.py — 采集记录：野外采集记录簿（站位登记 + 自动填充源）.

提前为项目录入每个采样站位/采集事件的完整元数据（经纬度 / 生境 / 潮水 / 采集人 /
采集·拍摄时间 / 拍摄地点 …），后续在工作台拍照时按 (地区+样地+站位+采集时间) 四键
自动填充。本页是这些记录的 CRUD 入口；自动填充消费方在 workbench_view。

数据键 = (province, site, station, collection_date)，对齐 UID 地点段
（app/utils/naming.py:42-60）。持久化经 app/services/collection_record_service.py。

注：本功能超出 web oracle（其 code_labels.stations 仅 {码:标签}，不带元数据），
是 Qt 版新增的"野外采集记录簿"能力。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services import collection_record_service as crs
from app.views.base_view import BaseView
from app.widgets._form_row import form_row

if TYPE_CHECKING:
    from app.app_context import AppContext


# ── Form field spec ───────────────────────────────────────────────────────────
# (key, 中文标签, help_text)。分组用 None 分隔小节标题。
_KEY_FIELDS = ("province", "site", "station", "collection_date")

_SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("站位标识", [
        ("province",        "地区",   "地区代码，如 ZJ＝浙江。与样地/站位/采集时间共同唯一确定一条记录。"),
        ("site",            "样地",   "样地代码，如 SMW＝三门湾。"),
        ("station",         "站位",   "采集站位，如 B2。"),
        ("collection_date", "采集时间", "采集日期 YYYYMMDD，如 20260518。四键之一。"),
        ("station_label",   "站位说明", "站位中文说明，如 北滩二区。"),
    ]),
    ("位置", [
        ("lon",      "经度",     "十进制度，留空存 NULL。"),
        ("lat",      "纬度",     "十进制度，留空存 NULL。"),
        ("geo_area", "采集地理区", "如 浙江·三门湾。可由经纬度反解。"),
    ]),
    ("环境", [
        ("habitat",    "生境",  "生境 / 底质：泥滩 / 沙滩 / 岩相 …"),
        ("tide",       "潮水",  "潮位 / 潮时，如 低潮 14:30。"),
        ("salinity",   "盐度",  "选填。"),
        ("water_temp", "水温",  "选填。"),
        ("weather",    "天气",  "选填。"),
    ]),
    ("人员", [
        ("collector",    "采集人", "自动填充到工作台标本的采集人。"),
        ("photographer", "拍摄人", "自动填充到工作台标本的拍摄人。"),
        ("identifier",   "鉴定人", "自动填充到工作台标本的鉴定人。"),
    ]),
    ("时间 / 拍摄", [
        ("collection_time", "采集时刻", "选填，如 14:30。"),
        ("photo_date",      "拍摄日期", "YYYYMMDD，选填。"),
        ("photo_location",  "拍摄地点", "如 实验室。"),
    ]),
    ("其它", [
        ("method", "采集方法", "如 手拣 / 筛网。"),
        ("remark", "备注",    ""),
    ]),
]

# Flat list of all editable field keys.
_ALL_FIELD_KEYS = [k for _, rows in _SECTIONS for (k, _l, _h) in rows]

# Table columns shown in the list (key, label).
_TABLE_COLS: list[tuple[str, str]] = [
    ("station", "站位"),
    ("collection_date", "采集时间"),
    ("station_label", "说明"),
    ("lon", "经度"),
    ("lat", "纬度"),
    ("habitat", "生境"),
    ("collector", "采集人"),
]

# 新增（步骤 4）批量表格列。地区/样地 不在表里——它们由当前工作区（断面）继承，
# 见 project_settings_service.effective_new_specimen_prefill。
_GRID_COLS: list[tuple[str, str]] = [
    ("station", "站位"),
    ("collection_date", "采集日期"),
    ("habitat", "生境"),
    ("tide", "潮水"),
    ("salinity", "盐度"),
    ("water_temp", "水温"),
    ("collector", "采集人"),
]


def _theme():
    """Return a token getter bound to the live theme (graceful fallback)."""
    try:
        from app.config.theme import TOKENS
        return TOKENS.get
    except Exception:  # pragma: no cover - theme always present in app
        return lambda k, d=None: d


class CollectionRecordsView(BaseView):
    """采集记录簿 — list + editor for per-station field collection records."""

    view_id = "collection_records"
    nav_title = "采集记录"
    nav_icon = "🗂️"

    def __init__(self, ctx: "AppContext") -> None:
        self._fields: dict[str, QLineEdit] = {}
        self._current_id: Optional[int] = None
        super().__init__(ctx)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self._apply_style()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # 两个标签页：新增「批量表格」+ 保留的「逐条精修」（原列表+侧边表单）。
        self._tabs = QTabWidget()

        # Tab 1：批量表格（新增）
        self._tabs.addTab(self._build_grid_pane(), "批量表格")

        # Tab 2：逐条精修（保留原 UI 不变）
        refine = QWidget()
        rl = QHBoxLayout(refine)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(14)
        rl.addWidget(self._build_list_pane(), 3)
        rl.addWidget(self._build_editor_pane(), 2)
        self._tabs.addTab(refine, "逐条精修")

        root.addWidget(self._tabs)

    def _apply_style(self) -> None:
        g = _theme()
        bg, panel, border = g("bg", "#0a1e24"), g("panel_2", "#0e2329"), g("border", "#21424a")
        text, muted, accent = g("text", "#c8dcd6"), g("muted", "#7fa49b"), g("accent", "#4fd1b8")
        accent_fg = g("accent_fg", "#ffffff")
        hdr_bg = g("accent", "#2c5f8a")
        self.setStyleSheet(
            f"#{self.view_id}{{background:{bg};}}"
            f"QLabel{{color:{text};background:transparent;}}"
            f"QLabel#SectionTitle{{color:{muted};font-weight:600;font-size:12px;}}"
            f"QLabel#PaneTitle{{color:{text};font-weight:600;font-size:15px;}}"
            f"QLineEdit{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:4px 8px;font-size:13px;}}"
            f"QPushButton{{background:{panel};color:{text};border:1px solid {border};"
            f"border-radius:5px;padding:5px 12px;font-size:13px;}}"
            f"QPushButton:hover{{background:{border};}}"
            f"QPushButton#Primary{{background:{accent};color:{accent_fg};border:1px solid {accent};}}"
            f"QTableWidget{{background:{bg};color:{text};gridline-color:{border};"
            f"border:1px solid {border};border-radius:6px;"
            f"selection-background-color:{accent};selection-color:{accent_fg};}}"
            f"QHeaderView::section{{background:{hdr_bg};color:{accent_fg};font-weight:600;"
            f"padding:5px 8px;border:none;border-right:1px solid {border};}}"
            f"QScrollArea{{background:transparent;border:none;}}"
            f"QFrame#Sep{{background:{border};max-height:1px;border:none;}}"
        )

    # ── Grid pane (批量表格, 步骤 4) ────────────────────────────────────────────
    def _build_grid_pane(self) -> QWidget:
        pane = QWidget()
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        bar = QHBoxLayout()
        title = QLabel("批量表格")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        # 显示当前工作区继承的 地区/样地（行将套用，不在表里重复输入）。
        self._grid_ps_lbl = QLabel("")
        self._grid_ps_lbl.setObjectName("SectionTitle")
        bar.addWidget(self._grid_ps_lbl)
        bar.addStretch()
        btn_add = QPushButton("＋ 加一行")
        btn_add.clicked.connect(lambda: self._grid_add_row(inherit=True))
        bar.addWidget(btn_add)
        btn_fill = QPushButton("↓ 向下填充")
        btn_fill.setToolTip("把当前格的值填到本列下方所有行")
        btn_fill.clicked.connect(self._grid_fill_down)
        bar.addWidget(btn_fill)
        btn_save = QPushButton("保存表格")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._grid_save)
        bar.addWidget(btn_save)
        # Excel 模板导出 / 导入（步骤 5）
        btn_export = QPushButton("⬇ 导出模板")
        btn_export.setToolTip("导出 Excel 模板（含已有记录），离线填好后再导入")
        btn_export.clicked.connect(self._grid_export_template)
        bar.addWidget(btn_export)
        btn_import = QPushButton("⬆ 导入Excel")
        btn_import.setToolTip("按模板（固定列）导入；配合「导出模板」往返")
        btn_import.clicked.connect(self._grid_import)
        bar.addWidget(btn_import)
        btn_import2 = QPushButton("⬆ 导入(自定义)")
        btn_import2.setToolTip("任意 Excel/CSV/TXT：自定义列映射 + 任意经纬度格式 + 坐标系纠偏，"
                               "用于采集计划批量录入断面/站位")
        btn_import2.clicked.connect(self._grid_import_mapped)
        bar.addWidget(btn_import2)
        v.addLayout(bar)

        # 非模态状态行（不用会阻塞的 QMessageBox，offscreen 测试也安全）。
        self._grid_status_lbl = QLabel("")
        self._grid_status_lbl.setObjectName("SectionTitle")
        v.addWidget(self._grid_status_lbl)

        self._grid = QTableWidget(0, len(_GRID_COLS))
        self._grid.setHorizontalHeaderLabels([lbl for _k, lbl in _GRID_COLS])
        self._grid.verticalHeader().setVisible(True)
        self._grid.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._grid.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        v.addWidget(self._grid, 1)
        return pane

    def _build_list_pane(self) -> QWidget:
        pane = QWidget()
        v = QVBoxLayout(pane)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        bar = QHBoxLayout()
        title = QLabel("采集记录")
        title.setObjectName("PaneTitle")
        bar.addWidget(title)
        bar.addStretch()
        btn_new = QPushButton("＋ 新建")
        btn_new.clicked.connect(self._new_record)
        bar.addWidget(btn_new)
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet("font-size:12px;")
        bar.addWidget(self._count_lbl)
        v.addLayout(bar)

        self._table = QTableWidget(0, len(_TABLE_COLS))
        self._table.setHorizontalHeaderLabels([lbl for _k, lbl in _TABLE_COLS])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self._table, 1)
        return pane

    def _build_editor_pane(self) -> QWidget:
        outer = QWidget()
        ov = QVBoxLayout(outer)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(8)

        title = QLabel("记录详情")
        title.setObjectName("PaneTitle")
        ov.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form = QVBoxLayout(inner)
        form.setContentsMargins(2, 2, 8, 2)
        form.setSpacing(6)

        for section_title, rows in _SECTIONS:
            sec = QLabel(section_title)
            sec.setObjectName("SectionTitle")
            form.addSpacing(4)
            form.addWidget(sec)
            for key, label, help_text in rows:
                edit = QLineEdit()
                self._fields[key] = edit
                required = key in _KEY_FIELDS
                form.addWidget(form_row(label, edit, required=required, help_text=help_text or None))
        form.addStretch()
        scroll.setWidget(inner)
        ov.addWidget(scroll, 1)

        # Action bar
        actions = QHBoxLayout()
        self._btn_delete = QPushButton("删除")
        self._btn_delete.clicked.connect(self._delete_record)
        actions.addWidget(self._btn_delete)
        actions.addStretch()
        btn_save = QPushButton("保存")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save_record)
        actions.addWidget(btn_save)
        ov.addLayout(actions)
        return outer

    # ── BaseView ────────────────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._apply_style()
        self._reload()
        self._grid_load()
        self._consume_pending_filter()

    def _consume_pending_filter(self) -> None:
        """采集地图点击点 → ctx.pending_record_filter，跳来此页时选中匹配行。

        按 (province, site, station) 匹配；上层概括点的 site/station 可能为 None，
        此时匹配第一条该地区/样地的行。匹配后清除句柄。
        """
        flt = getattr(self.ctx, "pending_record_filter", None)
        if not isinstance(flt, dict):
            return
        try:
            self.ctx.pending_record_filter = None
        except Exception:
            pass
        prov, site, station = flt.get("province"), flt.get("site"), flt.get("station")
        db = self.ctx.get_db()
        if db is None:
            return
        records = crs.list_records(db)
        target = None
        for rec in records:
            if rec.get("province") != prov:
                continue
            if site is not None and rec.get("site") != site:
                continue
            if station is not None and rec.get("station") != station:
                continue
            target = rec.get("id")
            break
        if target is not None:
            self._select_row_by_id(target)

    # ── Data ──────────────────────────────────────────────────────────────────

    def _reload(self) -> None:
        self._table.blockSignals(True)
        self._table.setUpdatesEnabled(False)   # paint once after the bulk fill
        try:
            self._table.setRowCount(0)
            db = self.ctx.get_db()
            records = crs.list_records(db) if db is not None else []
            for rec in records:
                row = self._table.rowCount()
                self._table.insertRow(row)
                for col, (key, _lbl) in enumerate(_TABLE_COLS):
                    val = rec.get(key)
                    item = QTableWidgetItem("" if val is None else str(val))
                    if col == 0:
                        item.setData(Qt.ItemDataRole.UserRole, rec.get("id"))
                    self._table.setItem(row, col, item)
        finally:
            self._table.setUpdatesEnabled(True)
            self._table.blockSignals(False)
        self._count_lbl.setText(f"{len(records)} 条")

    def _on_row_selected(self) -> None:
        items = self._table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rid = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        db = self.ctx.get_db()
        if db is None or rid is None:
            return
        rec = next((r for r in crs.list_records(db) if r.get("id") == rid), None)
        if rec is None:
            return
        self._current_id = rid
        for key, edit in self._fields.items():
            val = rec.get(key)
            edit.setText("" if val is None else str(val))

    def _new_record(self) -> None:
        self._current_id = None
        self._table.clearSelection()
        for edit in self._fields.values():
            edit.clear()
        if self._fields:
            self._fields["province"].setFocus()

    def _save_record(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            QMessageBox.information(self, "采集记录", "当前没有打开的项目，无法保存。")
            return
        data = {k: e.text().strip() for k, e in self._fields.items()}
        missing = [lbl for k, lbl in (("province", "地区"), ("site", "样地"),
                                       ("station", "站位"), ("collection_date", "采集时间"))
                   if not data.get(k)]
        if missing:
            QMessageBox.warning(self, "采集记录", "请填写必填项：" + "、".join(missing))
            return
        if self._current_id is not None:
            data["id"] = self._current_id
        rid = crs.upsert_record(db, data)
        self._current_id = rid
        self._reload()
        self._select_row_by_id(rid)

    def _delete_record(self) -> None:
        if self._current_id is None:
            self._new_record()
            return
        db = self.ctx.get_db()
        if db is None:
            return
        crs.delete_record(db, self._current_id)
        self._current_id = None
        self._reload()
        self._new_record()

    def _select_row_by_id(self, rid: int) -> None:
        for row in range(self._table.rowCount()):
            if self._table.item(row, 0).data(Qt.ItemDataRole.UserRole) == rid:
                self._table.selectRow(row)
                return

    # ── Grid (批量表格) data ─────────────────────────────────────────────────────
    def _effective_ps(self) -> tuple[str, str]:
        """Inherited (province, site) for the current workspace, or empties.

        地区/样地 are entered once at the survey root and inherited down the folder
        tree (see project_settings_service.effective_new_specimen_prefill); the grid
        applies them to every row so the user never re-types them.
        """
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            return ("", "")
        try:
            from app.services import project_settings_service as pss
            pf = pss.effective_new_specimen_prefill(
                project_dir, root=self.ctx.current_project_root
            )
            return (pf.get("province", ""), pf.get("site", ""))
        except Exception:
            return ("", "")

    def _grid_load(self) -> None:
        """Fill the batch grid from the project's records + one blank trailing row."""
        prov, site = self._effective_ps()
        if prov or site:
            self._grid_ps_lbl.setText(f"地区 {prov or '—'} · 样地 {site or '—'}（自动套用）")
        else:
            self._grid_ps_lbl.setText("（未设地区/样地：可在项目设置或上层目录填写）")

        self._grid.blockSignals(True)
        self._grid.setUpdatesEnabled(False)   # paint once after the bulk fill
        try:
            self._grid.setRowCount(0)
            db = self.ctx.get_db()
            records = crs.list_records(db) if db is not None else []
            for rec in records:
                self._grid_append_row(rec)
            self._grid_append_row(None)  # trailing blank row for quick add
        finally:
            self._grid.setUpdatesEnabled(True)
            self._grid.blockSignals(False)

    def _grid_append_row(self, rec: Optional[dict]) -> None:
        row = self._grid.rowCount()
        self._grid.insertRow(row)
        for col, (key, _lbl) in enumerate(_GRID_COLS):
            val = (rec or {}).get(key)
            item = QTableWidgetItem("" if val in (None, "") else str(val))
            if col == 0:
                # Stash the originating record (id/province/site) on the row's first
                # cell so re-saving preserves identity and any non-grid fields.
                item.setData(Qt.ItemDataRole.UserRole, rec or None)
            self._grid.setItem(row, col, item)

    def _grid_add_row(self, *, inherit: bool = True) -> None:
        """Append a blank row, optionally inheriting 采集日期/采集人 from the last row."""
        carry: dict = {}
        if inherit and self._grid.rowCount() > 0:
            last = self._grid.rowCount() - 1
            for col, (key, _lbl) in enumerate(_GRID_COLS):
                if key in ("collection_date", "collector"):
                    it = self._grid.item(last, col)
                    if it and it.text().strip():
                        carry[key] = it.text().strip()
        self._grid_append_row(carry or None)

    def _grid_fill_down(self) -> None:
        """Copy the current cell's value down its column to all rows below."""
        cur = self._grid.currentItem()
        if cur is None:
            return
        col = cur.column()
        text = cur.text()
        for row in range(cur.row() + 1, self._grid.rowCount()):
            it = self._grid.item(row, col)
            if it is None:
                it = QTableWidgetItem("")
                self._grid.setItem(row, col, it)
            it.setText(text)

    def _grid_save(self) -> None:
        """Upsert every non-blank grid row. 地区/样地 come from inheritance."""
        db = self.ctx.get_db()
        if db is None:
            self._grid_status_lbl.setText("当前没有打开的项目，无法保存。")
            return
        eff_prov, eff_site = self._effective_ps()

        saved = 0
        skipped_no_ps = 0
        for row in range(self._grid.rowCount()):
            values = {}
            for col, (key, _lbl) in enumerate(_GRID_COLS):
                it = self._grid.item(row, col)
                values[key] = it.text().strip() if it else ""
            # Blank trailing/empty rows: need at least 站位 + 采集日期 to be a record.
            if not values.get("station") or not values.get("collection_date"):
                continue
            station_item = self._grid.item(row, 0)
            orig = station_item.data(Qt.ItemDataRole.UserRole) if station_item else None
            data = dict(orig) if isinstance(orig, dict) else {}
            data["province"] = (data.get("province") or eff_prov)
            data["site"] = (data.get("site") or eff_site)
            data.update(values)
            if not data.get("province") or not data.get("site"):
                skipped_no_ps += 1
                continue
            crs.upsert_record(db, data)
            saved += 1

        self._reload()
        self._grid_load()
        msg = f"已保存 {saved} 条。"
        if skipped_no_ps:
            msg += f"  {skipped_no_ps} 行缺地区/样地未保存（请先在项目设置或上层目录填写）。"
        self._grid_status_lbl.setText(msg)

    # ── Excel 模板导出 / 导入（步骤 5） ──────────────────────────────────────────
    def _grid_export_template(self) -> None:
        from app.utils import ui
        db = self.ctx.get_db()
        if db is None:
            self._grid_status_lbl.setText("当前没有打开的项目，无法导出。")
            return
        path = ui.get_save_file_name(
            self, "导出采集记录模板", "采集记录模板.xlsx", "Excel 文件 (*.xlsx)"
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        prov, site = self._effective_ps()
        try:
            from app.services import collection_record_io as crio
            n = crio.export_template(db, path, province=prov, site=site)
            self._grid_status_lbl.setText(f"已导出模板：{n} 条已有记录 + 空行 → {path}")
        except Exception as exc:  # noqa: BLE001
            self._grid_status_lbl.setText(f"导出失败：{exc}")

    def _grid_import(self) -> None:
        """固定列模板导入（配合「导出模板」往返）。"""
        from app.utils import ui
        db = self.ctx.get_db()
        if db is None:
            self._grid_status_lbl.setText("当前没有打开的项目，无法导入。")
            return
        path = ui.get_open_file_name(
            self, "导入采集记录", "", "表格 (*.xlsx *.xlsm *.csv)"
        )
        if not path:
            return
        from app.services import collection_record_io as crio
        rep = crio.import_file(db, path)
        self._reload()
        self._grid_load()
        if not rep.ok:
            self._grid_status_lbl.setText("导入失败：" + "；".join(rep.errors[:3]))
            return
        msg = f"已导入 {rep.imported} 条。"
        if rep.skipped:
            msg += f"  跳过 {rep.skipped} 行（缺地区/样地/站位/采集日期）。"
        if rep.errors:
            msg += f"  {len(rep.errors)} 行出错。"
        self._grid_status_lbl.setText(msg)

    def _grid_import_mapped(self) -> None:
        """自定义列映射 + 任意格式经纬度导入（采集计划：断面/站位 + 经纬度，日期可空）。"""
        db = self.ctx.get_db()
        if db is None:
            self._grid_status_lbl.setText("当前没有打开的项目，无法导入。请先建/选项目。")
            return
        from app.widgets.coord_import_dialog import CoordImportDialog
        dlg = CoordImportDialog(db, parent=self)
        if dlg.exec():
            self._reload()
            self._grid_load()
            self._grid_status_lbl.setText("导入完成（自定义映射）。")
