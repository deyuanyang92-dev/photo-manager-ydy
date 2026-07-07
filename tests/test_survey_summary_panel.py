"""tests/test_survey_summary_panel.py — 物种名录面板 (spec survey-summary-view T4).

覆盖:
- set_workspaces → 表格行数 / 列内容 / inventory() getter
- 多断面聚合 + labels dict
- 空工作区 (不崩)
- 数值列排序正确 (10 > 2,非字典序)
- 三个导出按钮:仅 mock 文件选择器 (UI) + 信息弹窗 (UI),走真实
  export_inventory_excel/csv/darwin_core 写盘逻辑,断言文件存在 + 内容。
- export_inventory_darwin_core 跨真实 workspace db (db_manager.ensure_schema
  建 darwin_core 视图) 合并行。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QTableWidgetItem

from app.services import export_service
from app.utils import ui
from app.widgets.survey_summary_panel import SurveySummaryPanel, _HEADERS


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _make_workspace(dir_path: Path) -> Path:
    """Create a workspace skeleton (``_data/project.db``) and return the db path."""
    (dir_path / "_data").mkdir(parents=True, exist_ok=True)
    db_path = dir_path / "_data" / "project.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS specimens (
            uid TEXT PRIMARY KEY,
            scientific_name TEXT,
            scientific_name_cn TEXT,
            family TEXT,
            genus TEXT,
            order_name TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def _insert(db_path: Path, rows: list[tuple]) -> None:
    """rows = (uid, scientific_name, scientific_name_cn, family, genus, order_name)."""
    conn = sqlite3.connect(str(db_path))
    conn.executemany(
        "INSERT OR REPLACE INTO specimens"
        " (uid, scientific_name, scientific_name_cn, family, genus, order_name)"
        " VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _col_index(header_text: str) -> int:
    return _HEADERS.index(header_text)


# ── 基础填表 ────────────────────────────────────────────────────────────────────

def test_set_workspaces_populates_table(qapp, tmp_path):
    ws = tmp_path / "A"
    db = _make_workspace(ws)
    _insert(db, [
        # A-1 的 cn 留空 → 验证「首个非空」合并逻辑 (用 A-3 的值补齐)
        ("A-1", "Macoma balthica", "", "Tellinidae", "Macoma", "Venerida"),
        ("A-2", "Neanthes succinea", "刺沙蚕", "Nereididae", "Neanthes", "Phyllodocida"),
        ("A-3", "Macoma balthica", "Baltic 红樱蛤", "Tellinidae", "Macoma", "Venerida"),
    ])

    panel = SurveySummaryPanel()
    panel.set_workspaces([str(ws)])

    # 学名去重 → 2 物种
    assert panel._table.rowCount() == 2
    inv = panel.inventory()
    assert {r["scientific_name"] for r in inv} == {"Macoma balthica", "Neanthes succinea"}

    # Macoma 出现 2 编号
    macoma = next(r for r in inv if r["scientific_name"] == "Macoma balthica")
    assert macoma["total_count"] == 2
    assert "A" in macoma["sites"]

    # 中文名取首个非空: A-1 空白 → 用 A-3 的「Baltic 红樱蛤」补齐
    assert macoma["scientific_name_cn"] == "Baltic 红樱蛤"


def test_table_columns_match_spec(qapp, tmp_path):
    ws = tmp_path / "S1"
    _make_workspace(ws)
    panel = SurveySummaryPanel()
    panel.set_workspaces([str(ws)])
    headers = [
        panel._table.horizontalHeaderItem(c).text()
        for c in range(panel._table.columnCount())
    ]
    assert headers == ["学名", "中文名", "目", "科", "属", "出现断面", "各断面数量", "合计编号数"]


def test_cell_contents(qapp, tmp_path):
    ws = tmp_path / "B"
    db = _make_workspace(ws)
    _insert(db, [
        ("B-1", "Cerastoderma edule", "鸟蛤", "Cardiidae", "Cerastoderma", "Cardiida"),
    ])
    panel = SurveySummaryPanel()
    panel.set_workspaces([str(ws)])

    r = 0  # 单行
    assert panel._table.item(r, _col_index("学名")).text() == "Cerastoderma edule"
    assert panel._table.item(r, _col_index("中文名")).text() == "鸟蛤"
    assert panel._table.item(r, _col_index("目")).text() == "Cardiida"
    assert panel._table.item(r, _col_index("科")).text() == "Cardiidae"
    assert panel._table.item(r, _col_index("属")).text() == "Cerastoderma"
    assert panel._table.item(r, _col_index("出现断面")).text() == "B"
    assert panel._table.item(r, _col_index("各断面数量")).text() == "B=1"
    assert panel._table.item(r, _col_index("合计编号数")).text() == "1"


def test_multi_workspace_aggregation(qapp, tmp_path):
    wa = tmp_path / "A"
    wb = tmp_path / "B"
    da = _make_workspace(wa)
    db = _make_workspace(wb)
    _insert(da, [
        ("A-1", "Hydrobia ulvae", "河口螺", "Hydrobiidae", "Hydrobia", "Littorinimorpha"),
    ])
    _insert(db, [
        ("B-1", "Hydrobia ulvae", "河口螺", "Hydrobiidae", "Hydrobia", "Littorinimorpha"),
        ("B-2", "Peringia ulvae", "", "Hydrobiidae", "Peringia", "Littorinimorpha"),
    ])

    panel = SurveySummaryPanel()
    panel.set_workspaces([str(wa), str(wb)])

    inv = {r["scientific_name"]: r for r in panel.inventory()}
    assert set(inv) == {"Hydrobia ulvae", "Peringia ulvae"}
    # 同种跨断面合并
    h = inv["Hydrobia ulvae"]
    assert sorted(h["sites"]) == ["A", "B"]
    assert h["count_per_site"] == {"A": 1, "B": 1}
    assert h["total_count"] == 2


def test_labels_dict_overrides_basename(qapp, tmp_path):
    ws = tmp_path / "folder_x"
    db = _make_workspace(ws)
    _insert(db, [
        ("X-1", "Abra alba", "白樱蛤", "Semelidae", "Abra", "Venerida"),
    ])
    panel = SurveySummaryPanel()
    panel.set_workspaces([str(ws)], labels={str(ws): "断面甲"})

    row = panel.inventory()[0]
    assert row["sites"] == ["断面甲"]
    assert panel._table.item(0, _col_index("出现断面")).text() == "断面甲"


def test_empty_workspaces_no_crash(qapp):
    panel = SurveySummaryPanel()
    panel.set_workspaces([])
    assert panel._table.rowCount() == 0
    assert panel.inventory() == []
    # 导出按钮应禁用
    assert panel._btn_excel.isEnabled() is False
    assert panel._btn_csv.isEnabled() is False
    assert panel._btn_dwc.isEnabled() is False


# ── 排序:数值列按整数序 ─────────────────────────────────────────────────────────

def test_numeric_column_sorts_as_integer(qapp, tmp_path):
    ws = tmp_path / "N"
    db = _make_workspace(ws)
    rows = []
    # 构造 total_count 分别为 2 与 11 的两种 (验证 11 不被排到 2 之前)
    rows.append(("N-1", "Species Low", "低", "Fam", "Gen", "Ord"))
    rows.append(("N-2", "Species Low", "低", "Fam", "Gen", "Ord"))  # Species Low → count 2
    for i in range(11):
        rows.append((f"N-H{i:02d}", "Species High", "高", "Fam", "Gen", "Ord"))
    _insert(db, rows)

    panel = SurveySummaryPanel()
    panel.set_workspaces([str(ws)])

    col = _col_index("合计编号数")
    panel._table.sortByColumn(col, Qt.SortOrder.AscendingOrder)

    # 第一行应是 count=2 的物种
    top_name = panel._table.item(panel._table.verticalHeader().logicalIndex(0), 0).text()
    assert top_name == "Species Low"
    # 倒序后第一行应是 count=11
    panel._table.sortByColumn(col, Qt.SortOrder.DescendingOrder)
    top_name = panel._table.item(panel._table.verticalHeader().logicalIndex(0), 0).text()
    assert top_name == "Species High"


# ── 导出按钮 (真实写盘,仅 mock 文件选择器 / 信息弹窗) ──────────────────────────

def _stub_dialogs(monkeypatch, path: str) -> None:
    """Replace the native file picker + success/info boxes with no-ops."""
    monkeypatch.setattr(ui, "get_save_file_name", lambda *a, **kw: path)
    monkeypatch.setattr(ui, "info", lambda *a, **kw: None)
    monkeypatch.setattr(ui, "warn", lambda *a, **kw: None)
    monkeypatch.setattr(ui, "exception", lambda *a, **kw: None)


def _seed_two_species(tmp_path: Path):
    ws = tmp_path / "WS"
    db = _make_workspace(ws)
    _insert(db, [
        ("WS-1", "Sabella spallanzanii", "羽穗虫", "Sabellidae", "Sabella", "Sabellida"),
        ("WS-2", "Sabella spallanzanii", "羽穗虫", "Sabellidae", "Sabella", "Sabellida"),
        ("WS-3", "Amphitrite ornata", " ornata", "Terebellidae", "Amphitrite", "Terebellida"),
    ])
    return str(ws)


def test_export_excel_button_writes_file(qapp, tmp_path, monkeypatch):
    ws = _seed_two_species(tmp_path)
    panel = SurveySummaryPanel()
    panel.set_workspaces([ws])

    out = tmp_path / "inv.xlsx"
    _stub_dialogs(monkeypatch, str(out))
    panel._btn_excel.click()

    assert out.exists()
    import openpyxl
    wb = openpyxl.load_workbook(str(out))
    ws_sheet = wb.active
    # 表头 + 2 物种行
    assert ws_sheet.max_row == 1 + 2
    headers = [c.value for c in ws_sheet[1]]
    assert headers == export_service.INVENTORY_HEADERS


def test_export_csv_button_writes_file(qapp, tmp_path, monkeypatch):
    ws = _seed_two_species(tmp_path)
    panel = SurveySummaryPanel()
    panel.set_workspaces([ws])

    out = tmp_path / "inv.csv"
    _stub_dialogs(monkeypatch, str(out))
    panel._btn_csv.click()

    assert out.exists()
    # UTF-8 BOM
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0] == ",".join(export_service.INVENTORY_HEADERS)
    # 2 数据行
    assert len(lines) == 1 + 2
    assert "Sabella spallanzanii" in text


def test_export_cancel_does_nothing(qapp, tmp_path, monkeypatch):
    ws = _seed_two_species(tmp_path)
    panel = SurveySummaryPanel()
    panel.set_workspaces([ws])
    # 用户取消 → get_save_file_name 返回空
    monkeypatch.setattr(ui, "get_save_file_name", lambda *a, **kw: "")
    monkeypatch.setattr(ui, "info", lambda *a, **kw: None)
    out = tmp_path / "none.xlsx"
    panel._export_excel.__self__  # sanity: bound
    panel._btn_excel.click()
    assert not out.exists()


# ── Darwin Core 跨断面合并 (真实 db_manager.ensure_schema 建视图) ─────────────

def test_export_darwin_core_button_writes_file(qapp, tmp_path, monkeypatch):
    # 用 db_manager 建真实 workspace db (ensure_schema 创建 darwin_core 视图)
    from app.db import db_manager
    db_manager.close_all()
    ws_dir = tmp_path / "DWC"
    ws_dir.mkdir(parents=True)
    conn = db_manager.open_project_db(str(ws_dir), create=True)
    conn.execute(
        "INSERT INTO specimens (uid, scientific_name, family, genus, order_name, collector)"
        " VALUES (?,?,?,?,?,?)",
        ("DWC-1", "Nereis virens", "Nereididae", "Nereis", "Phyllodocida", "采集人甲"),
    )
    conn.commit()
    db_manager.close_all()  # 释放缓存连接,让导出开自己的连接

    panel = SurveySummaryPanel()
    panel.set_workspaces([str(ws_dir)])

    out = tmp_path / "dwc.csv"
    _stub_dialogs(monkeypatch, str(out))
    panel._btn_dwc.click()

    assert out.exists()
    text = out.read_bytes().decode("utf-8-sig")
    lines = text.strip().splitlines()
    header = lines[0].split(",")
    assert "occurrenceID" in header
    assert "scientificName" in header
    # 数据行含刚插入的标本
    assert any("Nereis virens" in line for line in lines[1:])
    assert any("DWC-1" in line for line in lines[1:])
    db_manager.close_all()


def test_export_inventory_darwin_core_missing_db_skipped(tmp_path):
    # 一个存在 + 一个不存在的目录 → 不抛,产出存在的 db 的行
    from app.db import db_manager
    db_manager.close_all()
    ws_dir = tmp_path / "real"
    ws_dir.mkdir(parents=True)
    conn = db_manager.open_project_db(str(ws_dir), create=True)
    conn.execute(
        "INSERT INTO specimens (uid, scientific_name, family, genus)"
        " VALUES (?,?,?,?)",
        ("R-1", "Gammarus locusta", "Gammaridae", "Gammarus"),
    )
    conn.commit()
    db_manager.close_all()

    out = tmp_path / "combined.csv"
    res = export_service.export_inventory_darwin_core(
        [str(ws_dir), str(tmp_path / "ghost")], out
    )
    assert Path(res).exists()
    text = out.read_bytes().decode("utf-8-sig")
    assert "Gammarus locusta" in text
    db_manager.close_all()


def test_export_inventory_darwin_core_no_workspaces(tmp_path):
    # 无任何可用 db → 仍写出一份带 DwC 核心表头、无数据行的合法 CSV
    out = tmp_path / "empty.csv"
    res = export_service.export_inventory_darwin_core([str(tmp_path / "ghost")], out)
    assert Path(res).exists()
    text = out.read_bytes().decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].split(",")[0] == "occurrenceID"
    assert len(lines) == 1  # 仅表头
