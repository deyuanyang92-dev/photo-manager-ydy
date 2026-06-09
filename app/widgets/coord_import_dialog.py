"""coord_import_dialog.py — 站位批量导入向导（采集计划）.

把整理好的 Excel/CSV/TXT 站位表导入当前项目采集计划（collection_records）：
  选文件 → 自定义列映射（哪列=地区/断面/站位/说明/经度/纬度/经纬合一）
  → 选源坐标系（WGS84/GCJ02/BD09，自动纠偏）+ 缺省采集日期（计划阶段可空）
  → 预览（红标解析失败行）→ 导入。

经纬度任意格式经 coord_utils 解析（见 coord_import_service）。逻辑方法
load_file / set_mapping / preview / do_import 与 UI 解耦，便于单测。
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services import coord_import_service as cis
from app.services import collection_record_service as crs
from app.utils import ui

_NONE = "—无—"


class CoordImportDialog(QDialog):
    """站位批量导入向导。导入写入构造时传入的项目 DB。"""

    def __init__(self, db: sqlite3.Connection, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._headers: list[str] = []
        self._rows: list[dict] = []
        self._field_combos: dict[str, QComboBox] = {}
        self.setWindowTitle("批量导入站位（采集计划）")
        self.resize(820, 600)
        self._build_ui()
        ui.center_on(self, parent)

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)

        top = QHBoxLayout()
        self._file_lbl = QLabel("未选择文件")
        btn_file = QPushButton("选择文件 (Excel/CSV/TXT)…")
        btn_file.clicked.connect(self._pick_file)
        btn_sample = QPushButton("下载示例")
        btn_sample.setToolTip("导出一个示例站位表（CSV），按其列填好后再导入。")
        btn_sample.clicked.connect(self._save_sample)
        top.addWidget(btn_file)
        top.addWidget(btn_sample)
        top.addWidget(self._file_lbl, 1)
        v.addLayout(top)

        form = QFormLayout()
        for key in cis.TARGET_FIELDS:
            combo = QComboBox()
            combo.addItem(_NONE)
            self._field_combos[key] = combo
            form.addRow(cis.TARGET_LABELS[key], combo)
        self._coord_sys = QComboBox()
        self._coord_sys.addItems(cis.COORD_SYSTEMS)
        form.addRow("源坐标系", self._coord_sys)
        self._date = QLineEdit()
        self._date.setPlaceholderText("计划阶段可留空；实采可填 YYYYMMDD")
        form.addRow("缺省采集日期", self._date)
        v.addLayout(form)

        bar = QHBoxLayout()
        btn_prev = QPushButton("预览")
        btn_prev.clicked.connect(self._refresh_preview)
        bar.addWidget(btn_prev)
        self._summary = QLabel("")
        bar.addWidget(self._summary, 1)
        v.addLayout(bar)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["状态", "地区", "断面", "站位", "经度", "纬度"])
        self._table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self._table, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self._btn_import = QPushButton("导入")
        self._btn_import.setDefault(True)
        self._btn_import.clicked.connect(self._on_import)
        actions.addWidget(cancel)
        actions.addWidget(self._btn_import)
        v.addLayout(actions)

    # ── 逻辑（可单测）────────────────────────────────────────────────────────────

    def load_file(self, path: str) -> None:
        self._headers, self._rows = cis.read_table(path)
        self._file_lbl.setText(path)
        # 刷新列下拉 + 智能猜测
        for key, combo in self._field_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(_NONE)
            combo.addItems(self._headers)
            guess = self._guess_header(key)
            if guess:
                combo.setCurrentText(guess)
            combo.blockSignals(False)

    def _guess_header(self, key: str) -> Optional[str]:
        hints = {
            "province": ["地区", "省", "province", "prov"],
            "site": ["断面", "样地", "采集地", "site"],
            "station": ["站位", "站点", "station"],
            "station_label": ["说明", "名称", "label", "name"],
            "lon": ["经度", "经", "lon", "lng", "longitude", "x"],
            "lat": ["纬度", "纬", "lat", "latitude", "y"],
            "lonlat": ["坐标", "经纬", "coord"],
        }.get(key, [])
        for h in self._headers:
            hl = h.lower()
            if any(hint.lower() in hl for hint in hints):
                return h
        return None

    def current_mapping(self) -> dict:
        out = {}
        for key, combo in self._field_combos.items():
            txt = combo.currentText()
            if txt and txt != _NONE:
                out[key] = txt
        return out

    def set_mapping(self, mapping: dict) -> None:
        for key, col in mapping.items():
            combo = self._field_combos.get(key)
            if combo is not None:
                if combo.count() <= 1:           # 还没载列 → 补上
                    combo.addItem(col)
                combo.setCurrentText(col)

    def preview(self) -> list[dict]:
        return cis.normalize_rows(
            self._rows, self.current_mapping(),
            coord_system=self._coord_sys.currentText(),
            default_date=self._date.text().strip(),
        )

    def do_import(self) -> tuple[int, int]:
        recs = self.preview()
        n_ok = n_err = 0
        for r in recs:
            if not r.get("ok"):
                n_err += 1
                continue
            crs.upsert_record(self._db, {
                "province": r["province"], "site": r["site"], "station": r["station"],
                "station_label": r["station_label"], "collection_date": r["collection_date"],
                "lon": r["lon"], "lat": r["lat"],
            })
            n_ok += 1
        return n_ok, n_err

    # ── UI 事件 ───────────────────────────────────────────────────────────────

    def _save_sample(self) -> None:
        path = ui.get_save_file_name(
            self, "保存示例站位表", "站位导入示例.csv", "CSV (*.csv)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        cis.write_sample_file(path)
        ui.info(self, "示例已保存", f"示例站位表已保存到：\n{path}\n按其列填好后即可导入。")

    def _pick_file(self) -> None:
        path = ui.get_open_file_name(
            self, "选择站位表", "",
            "表格 (*.xlsx *.xlsm *.csv *.txt);;所有文件 (*)",
        )
        if path:
            self.load_file(path)
            self._refresh_preview()

    def _refresh_preview(self) -> None:
        recs = self.preview()
        self._table.setRowCount(0)
        ok = 0
        for r in recs:
            row = self._table.rowCount()
            self._table.insertRow(row)
            status = "✓" if r["ok"] else f"✗ {r['error']}"
            cells = [status, r["province"], r["site"], r["station"],
                     "" if r["lon"] is None else f"{r['lon']:.5f}",
                     "" if r["lat"] is None else f"{r['lat']:.5f}"]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                if not r["ok"]:
                    item.setForeground(QColor("#b42318"))
                self._table.setItem(row, c, item)
            ok += 1 if r["ok"] else 0
        self._summary.setText(f"可导入 {ok} / 共 {len(recs)} 行")

    def _on_import(self) -> None:
        if self._db is None:
            ui.warn(self, "导入", "当前没有打开的项目，无法导入。请先建/选项目。")
            return
        if not self.current_mapping():
            ui.warn(self, "导入", "请先设置列映射。")
            return
        n_ok, n_err = self.do_import()
        ui.info(self, "导入完成", f"成功导入 {n_ok} 个站位，跳过 {n_err} 个错误行。")
        if n_ok:
            self.accept()
