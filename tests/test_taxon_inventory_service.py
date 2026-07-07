"""tests/test_taxon_inventory_service.py — 物种名录聚合 service (spec T1).

Pure, no Qt. Scans each workspace's ``_data/project.db`` ``specimens`` table,
dedups by ``scientific_name`` across 断面, and reports 出现断面 + 各断面数量.

参考用法见 ``project_settings_service.get_effective`` 的 sqlite3.connect 模式。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.taxon_inventory_service import aggregate_taxon_inventory


# ── helpers ────────────────────────────────────────────────────────────────────

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
    """Insert specimen rows (uid, scientific_name, scientific_name_cn, family, genus, order_name)."""
    conn = sqlite3.connect(str(db_path))
    conn.executemany(
        "INSERT OR REPLACE INTO specimens"
        " (uid, scientific_name, scientific_name_cn, family, genus, order_name)"
        " VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _by_name(inv: list[dict]) -> dict[str, dict]:
    return {row["scientific_name"]: row for row in inv}


# ── 单工作区 ────────────────────────────────────────────────────────────────────

def test_single_workspace_distinct_species(tmp_path):
    ws = tmp_path / "断面A"
    db = _make_workspace(ws)
    _insert(
        db,
        [
            ("A-1", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida"),
            ("A-2", "Neanthes succinea", "刺沙蚕", "Nereididae", "Neanthes", "Phyllodocida"),
        ],
    )
    inv = aggregate_taxon_inventory([str(ws)])

    assert isinstance(inv, list)
    assert len(inv) == 2
    by = _by_name(inv)

    cap = by["Capitella capitata"]
    assert cap["scientific_name_cn"] == "小头虫"
    assert cap["family"] == "Capitellidae"
    assert cap["genus"] == "Capitella"
    assert cap["order_name"] == "Sabellida"
    assert cap["sites"] == ["断面A"]
    assert cap["count_per_site"] == {"断面A": 1}
    assert cap["total_count"] == 1


def test_same_species_multiple_specimens_in_one_workspace(tmp_path):
    ws = tmp_path / "断面A"
    db = _make_workspace(ws)
    _insert(
        db,
        [
            ("A-1", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida"),
            ("A-2", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida"),
            ("A-3", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida"),
        ],
    )
    inv = aggregate_taxon_inventory([str(ws)])
    assert len(inv) == 1
    row = inv[0]
    assert row["count_per_site"] == {"断面A": 3}
    assert row["total_count"] == 3
    assert row["sites"] == ["断面A"]


# ── 跨断面合并 ──────────────────────────────────────────────────────────────────

def test_cross_workspace_same_species_merged(tmp_path):
    ws_a = tmp_path / "断面A"
    ws_b = tmp_path / "断面B"
    db_a = _make_workspace(ws_a)
    db_b = _make_workspace(ws_b)
    _insert(db_a, [("A-1", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida")])
    _insert(
        db_b,
        [
            ("B-1", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida"),
            ("B-2", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida"),
            ("B-3", "Neanthes succinea", "刺沙蚕", "Nereididae", "Neanthes", "Phyllodocida"),
        ],
    )
    inv = aggregate_taxon_inventory([str(ws_a), str(ws_b)])
    by = _by_name(inv)

    cap = by["Capitella capitata"]
    # sites 累积，去重排序
    assert cap["sites"] == ["断面A", "断面B"]
    assert cap["count_per_site"] == {"断面A": 1, "断面B": 2}
    assert cap["total_count"] == 3

    nea = by["Neanthes succinea"]
    assert nea["sites"] == ["断面B"]
    assert nea["count_per_site"] == {"断面B": 1}
    assert nea["total_count"] == 1


def test_cross_workspace_distinct_species_stay_separate(tmp_path):
    ws_a = tmp_path / "断面A"
    ws_b = tmp_path / "断面B"
    db_a = _make_workspace(ws_a)
    db_b = _make_workspace(ws_b)
    _insert(db_a, [("A-1", "Species alpha", "", "FamA", "GenA", "OrdA")])
    _insert(db_b, [("B-1", "Species beta", "", "FamB", "GenB", "OrdB")])
    inv = aggregate_taxon_inventory([str(ws_a), str(ws_b)])
    assert {row["scientific_name"] for row in inv} == {"Species alpha", "Species beta"}


# ── 空学名跳过 ──────────────────────────────────────────────────────────────────

def test_empty_scientific_name_skipped(tmp_path):
    ws = tmp_path / "断面A"
    db = _make_workspace(ws)
    _insert(
        db,
        [
            ("A-1", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida"),
            ("A-2", None, "未命名", "", "", ""),
            ("A-3", "", "空学名", "", "", ""),
            ("A-4", "   ", "空白学名", "", "", ""),
        ],
    )
    inv = aggregate_taxon_inventory([str(ws)])
    assert [row["scientific_name"] for row in inv] == ["Capitella capitata"]


# ── 鲁棒性 ──────────────────────────────────────────────────────────────────────

def test_workspace_without_db_skipped(tmp_path):
    # 断面目录存在但没有 _data/project.db —— 不应崩溃,贡献为空
    ws_no_db = tmp_path / "断面空"
    ws_no_db.mkdir(parents=True)
    ws = tmp_path / "断面A"
    db = _make_workspace(ws)
    _insert(db, [("A-1", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida")])

    inv = aggregate_taxon_inventory([str(ws_no_db), str(ws)])
    assert len(inv) == 1
    assert inv[0]["sites"] == ["断面A"]


def test_nonexistent_workspace_skipped(tmp_path):
    ws = tmp_path / "断面A"
    db = _make_workspace(ws)
    _insert(db, [("A-1", "Capitella capitata", "小头虫", "", "", "")])
    # 传入一个根本不存在的路径 + 一个有效工作区
    inv = aggregate_taxon_inventory([str(tmp_path / "不存在"), str(ws)])
    assert len(inv) == 1
    assert inv[0]["sites"] == ["断面A"]


def test_corrupt_db_skipped(tmp_path):
    ws_bad = tmp_path / "断面坏"
    (ws_bad / "_data").mkdir(parents=True)
    # 写一段非 SQLite 内容冒充 project.db
    (ws_bad / "_data" / "project.db").write_text("NOT A DATABASE")
    ws = tmp_path / "断面A"
    db = _make_workspace(ws)
    _insert(db, [("A-1", "Capitella capita", "小头虫", "", "", "")])

    # 损坏的工作区被跳过,有效工作区照常聚合
    inv = aggregate_taxon_inventory([str(ws_bad), str(ws)])
    assert len(inv) == 1
    assert inv[0]["sites"] == ["断面A"]


# ── 学名空白裁剪 ────────────────────────────────────────────────────────────────

def test_scientific_name_whitespace_stripped(tmp_path):
    ws = tmp_path / "断面A"
    db = _make_workspace(ws)
    _insert(
        db,
        [
            ("A-1", "  Capitella capitata  ", "小头虫", "", "", ""),
            ("A-2", "Capitella capitata", "小头虫", "", "", ""),
        ],
    )
    inv = aggregate_taxon_inventory([str(ws)])
    # 带空白的学名与不带空白的应合并为同一条
    assert len(inv) == 1
    assert inv[0]["scientific_name"] == "Capitella capitata"
    assert inv[0]["total_count"] == 2


# ── 显示字段:取首个非空 ────────────────────────────────────────────────────────

def test_display_fields_keep_first_non_empty(tmp_path):
    # 断面A 的标本缺中名/科;断面B 同种有完整分类 → 合并后应带上 B 的非空字段
    ws_a = tmp_path / "断面A"
    ws_b = tmp_path / "断面B"
    db_a = _make_workspace(ws_a)
    db_b = _make_workspace(ws_b)
    _insert(db_a, [("A-1", "Capitella capitata", "", "", "", "")])
    _insert(
        db_b,
        [("B-1", "Capitella capitata", "小头虫", "Capitellidae", "Capitella", "Sabellida")],
    )
    inv = aggregate_taxon_inventory([str(ws_a), str(ws_b)])
    assert len(inv) == 1
    row = inv[0]
    assert row["scientific_name_cn"] == "小头虫"
    assert row["family"] == "Capitellidae"
    assert row["genus"] == "Capitella"
    assert row["order_name"] == "Sabellida"


# ── 自定义断面标签 ──────────────────────────────────────────────────────────────

def test_custom_labels_override_basename(tmp_path):
    ws_a = tmp_path / "断面A"
    ws_b = tmp_path / "断面B"
    db_a = _make_workspace(ws_a)
    db_b = _make_workspace(ws_b)
    _insert(db_a, [("A-1", "Capitella capitata", "", "", "", "")])
    _insert(db_b, [("B-1", "Capitella capitata", "", "", "", "")])

    inv = aggregate_taxon_inventory(
        [str(ws_a), str(ws_b)], labels=["区域1/断面A", "区域2/断面B"]
    )
    assert len(inv) == 1
    row = inv[0]
    assert row["sites"] == ["区域1/断面A", "区域2/断面B"]
    assert row["count_per_site"] == {"区域1/断面A": 1, "区域2/断面B": 1}


def test_labels_length_mismatch_raises(tmp_path):
    ws_a = tmp_path / "断面A"
    ws_b = tmp_path / "断面B"
    _make_workspace(ws_a)
    _make_workspace(ws_b)
    # 2 个工作区但只给 1 个标签 → 长度不一致,必须 ValueError
    with pytest.raises(ValueError):
        aggregate_taxon_inventory([str(ws_a), str(ws_b)], labels=["only-one"])


# ── 排序 ────────────────────────────────────────────────────────────────────────

def test_result_sorted_by_scientific_name(tmp_path):
    ws = tmp_path / "断面A"
    db = _make_workspace(ws)
    _insert(
        db,
        [
            ("A-1", "Zebra", "", "", "", ""),
            ("A-2", "Apple", "", "", "", ""),
            ("A-3", "Mango", "", "", "", ""),
        ],
    )
    inv = aggregate_taxon_inventory([str(ws)])
    assert [row["scientific_name"] for row in inv] == ["Apple", "Mango", "Zebra"]


def test_empty_input_returns_empty_list():
    assert aggregate_taxon_inventory([]) == []
