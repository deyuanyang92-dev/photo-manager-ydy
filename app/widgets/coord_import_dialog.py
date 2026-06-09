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

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
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
        self._btn_screenshot = QPushButton("截图")
        self._btn_screenshot.setToolTip("打开截图工具，可截当前导入窗口或任意屏幕区域。")
        self._btn_screenshot.clicked.connect(self._capture_screenshot)
        self._btn_sample_preview = QPushButton("查看示例")
        self._btn_sample_preview.setToolTip("直接查看示例表和经纬度解析结果。")
        self._btn_sample_preview.clicked.connect(self._show_sample_preview)
        btn_sample = QPushButton("下载示例")
        btn_sample.setToolTip("导出一个示例站位表（CSV），按其列填好后再导入。")
        btn_sample.clicked.connect(self._save_sample)
        top.addWidget(btn_file)
        top.addWidget(self._btn_screenshot)
        top.addWidget(self._btn_sample_preview)
        top.addWidget(btn_sample)
        top.addWidget(self._file_lbl, 1)
        v.addLayout(top)

        hint = QLabel(
            "必填：经度 + 纬度（或经纬度合一列）；地区/断面/站位 至少填一项。"
            "其余（站位说明、源坐标系、缺省采集日期）均可选。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#667085;")
        v.addWidget(hint)

        # 列映射每行标注必填/可选，便于用户对照表头。
        _REQ = {
            "province": "（地区/断面/站位 至少一项）",
            "site": "（地区/断面/站位 至少一项）",
            "station": "（地区/断面/站位 至少一项）",
            "station_label": "（可选）",
            "lon": "（必填，或用经纬合一列）",
            "lat": "（必填，或用经纬合一列）",
            "lonlat": "（可选，替代经度/纬度两列）",
        }
        form = QFormLayout()
        for key in cis.TARGET_FIELDS:
            combo = QComboBox()
            combo.addItem(_NONE)
            self._field_combos[key] = combo
            form.addRow(cis.TARGET_LABELS[key] + _REQ.get(key, ""), combo)
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
        ui.info(
            self, "示例已保存",
            f"示例站位表已保存到：\n{path}\n按其列填好后即可导入。\n\n"
            "必填：经度、纬度（或经纬度合一列）；地区/断面/站位 至少填一项。"
            "站位说明为可选列。",
        )

    def _build_sample_preview_dialog(self) -> QDialog:
        dlg = QDialog(self)
        dlg.setWindowTitle("示例站位表预览")
        dlg.resize(760, 360)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        head = QLabel("示例站位表")
        head.setStyleSheet("font-size:15px;font-weight:600;")
        v.addWidget(head)

        legend = QLabel(
            "必填列：经度、纬度（或经纬度合一列）；地区/断面/站位 至少填一项。"
            "站位说明为可选列。"
        )
        legend.setWordWrap(True)
        legend.setStyleSheet("color:#667085;")
        v.addWidget(legend)

        table = QTableWidget(0, 8)
        table.setObjectName("SamplePreviewTable")
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setHorizontalHeaderLabels([
            "地区", "断面", "站位", "说明",
            "原始经度", "原始纬度", "解析经度", "解析纬度",
        ])
        _headers, rows = cis.sample_table()
        parsed = cis.sample_preview_rows(coord_system="WGS84")
        for raw, rec in zip(rows, parsed):
            row = table.rowCount()
            table.insertRow(row)
            cells = [
                raw.get("地区", ""), raw.get("断面", ""), raw.get("站位", ""),
                raw.get("站位说明", ""), raw.get("经度", ""), raw.get("纬度", ""),
                "" if rec.get("lon") is None else f"{rec['lon']:.5f}",
                "" if rec.get("lat") is None else f"{rec['lat']:.5f}",
            ]
            for col, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                if not rec.get("ok"):
                    item.setForeground(QColor("#b42318"))
                table.setItem(row, col, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(table, 1)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(line)

        actions = QHBoxLayout()
        save = QPushButton("保存 CSV")
        save.clicked.connect(self._save_sample)
        close = QPushButton("关闭")
        close.clicked.connect(dlg.accept)
        actions.addWidget(save)
        actions.addStretch()
        actions.addWidget(close)
        v.addLayout(actions)

        dlg._sample_table = table
        return dlg

    def _show_sample_preview(self) -> None:
        dlg = self._build_sample_preview_dialog()
        ui.center_on(dlg, self)
        dlg.exec()

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

    def _find_screenshot_controller(self):
        """Find MainWindow._shot_ctrl from inside this modal dialog."""
        w: Optional[QWidget] = self
        while w is not None:
            ctrl = getattr(w, "_shot_ctrl", None)
            if ctrl is not None:
                return ctrl
            w = w.parentWidget()
        for top in QApplication.topLevelWidgets():
            ctrl = getattr(top, "_shot_ctrl", None)
            if ctrl is not None:
                return ctrl
        return None

    def _capture_screenshot(self) -> None:
        ctrl = self._find_screenshot_controller()
        if ctrl is None or not hasattr(ctrl, "capture_region"):
            ui.warn(self, "截图", "截图工具尚未初始化。")
            return
        QTimer.singleShot(0, ctrl.capture_region)

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
