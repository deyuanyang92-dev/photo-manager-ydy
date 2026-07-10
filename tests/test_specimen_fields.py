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
    assert "storage" in keys
    assert "photographer" in keys
    assert "scientific_name" in keys
    assert "storage_is_rna" in keys
    labels = {f["key"]: f["label"] for f in fields}
    assert labels["photographer"] == "拍摄人"
    assert labels["storage_is_rna"] == "已取RNA"
    derived_flag = {f["key"]: f["derived"] for f in fields}
    assert derived_flag["storage_is_rna"] is True
    assert derived_flag["photographer"] is False


def test_filterable_fields_meta_without_pragma(tmp_path) -> None:
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()
    fields = sf.filterable_fields(str(db))
    keys = {f["key"] for f in fields}
    assert "photographer" in keys
    assert "storage_is_rna" in keys


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
    assert sf.field_label("uid") == "标本编号"
    assert sf.field_label("id") == "物种编号"


def test_uid_segment_labels_match_project_memory() -> None:
    """用户语义: province=省/市 · site=地区/样地 · id=物种编号."""
    assert sf.field_label("province") == "省/市"
    assert sf.field_label("site") == "地区/样地"
    assert sf.field_label("station") == "站位"
    assert sf.field_label("id") == "物种编号"
    assert sf.field_label("storage") == "保存方式"
    assert sf.field_label("uid") == "标本编号"
    assert sf.field_label("uid") != sf.field_label("id")
    assert sf.field_label("id") not in ("样地编号", "样品/物种标签")


def test_summary_field_category_matches_workbench_cards() -> None:
    groups = sf.group_summary_columns([
        ("uid", "编号"),
        ("province", "省/市"),
        ("storage", "保存方式"),
        ("collection_date", "采集日期"),
        ("photo_notes", "拍照备注"),
        ("scientific_name", "学名"),
        ("notes", "备注标签"),
        ("collector", "采集人"),
        ("photo_absolute_path", "照片绝对路径"),
        ("iso", "ISO"),
        ("extra_col", "扩展"),
    ])
    assert sf.summary_field_category("storage") == "naming_identity"
    assert sf.summary_field_category("storage_is_rna") == "naming_identity"
    assert sf.summary_field_category("photo_notes") == "naming_notes"
    assert sf.summary_field_category("photographer") == "metadata"
    assert sf.summary_field_category("photo_absolute_path") == "camera"
    assert sf.summary_field_category("iso") == "camera"
    assert sf.summary_field_category("notes") == "taxon"
    labels = [g["label"] for g in groups]
    assert labels[0] == "照片编号 · 采集位置"
    assert "标本唯一编号" in labels
    assert "分类标签" in labels
    assert "其它" in labels
    assert "拍照与相机" in labels
    assert groups[-1]["columns"][0][0] == "extra_col"
