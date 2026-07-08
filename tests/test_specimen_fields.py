"""test_specimen_fields.py — 数据筛选字段注册表(方案3 PRAGMA+注册表, spec 2026-07-08)."""
from __future__ import annotations

import sqlite3

from app.config import specimen_fields as sf


def _make_db(p) -> str:
    (p / "_data").mkdir(parents=True)
    db = p / "_data" / "project.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE specimens ("
        "uid TEXT PRIMARY KEY, storage TEXT, photographer TEXT, scientific_name TEXT)"
    )
    conn.execute("INSERT INTO specimens VALUES ('u1','R95E','张三','Aa sp')")
    conn.commit()
    conn.close()
    return str(db)


def test_filterable_fields_includes_pragma_columns(tmp_path) -> None:
    db = _make_db(tmp_path)
    fields = sf.filterable_fields(db)
    keys = {f["key"] for f in fields}
    # PRAGMA 列
    assert "storage" in keys
    assert "photographer" in keys
    assert "scientific_name" in keys
    # 派生维度
    assert "storage_is_rna" in keys
    labels = {f["key"]: f["label"] for f in fields}
    assert labels["photographer"] == "拍摄人"
    assert labels["storage_is_rna"] == "已取RNA"
    # 派生标记
    derived_flag = {f["key"]: f["derived"] for f in fields}
    assert derived_flag["storage_is_rna"] is True
    assert derived_flag["photographer"] is False


def test_filterable_fields_meta_without_pragma(tmp_path) -> None:
    """损坏/空 db(无 specimens 表): 退化为仅 FIELD_META ∪ DERIVED, 不抛."""
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()  # 无表
    fields = sf.filterable_fields(str(db))
    keys = {f["key"] for f in fields}
    assert "photographer" in keys  # 来自 FIELD_META
    assert "storage_is_rna" in keys  # 来自 DERIVED


def test_storage_is_rna_matches_r_prefix() -> None:
    assert sf.eval_derived("storage_is_rna", {"storage": "R95E"}) is True
    assert sf.eval_derived("storage_is_rna", {"storage": "r75e"}) is True
    assert sf.eval_derived("storage_is_rna", {"storage": "T95E"}) is False
    assert sf.eval_derived("storage_is_rna", {"storage": None}) is False
    assert sf.eval_derived("storage_is_rna", {}) is False
    assert sf.eval_derived("storage_is_rna", {"storage": ""}) is False


def test_is_derived_and_label() -> None:
    assert sf.is_derived("storage_is_rna") is True
    assert sf.is_derived("photographer") is False
    assert sf.field_label("photographer") == "拍摄人"
    assert sf.field_label("storage_is_rna") == "已取RNA"
    # 无 meta 的列(如 uid)返回 key 本身 — 仍可筛, 仅无中文标签
    assert sf.field_label("uid") == "uid"
