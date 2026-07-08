"""test_specimen_filter_service.py — 跨断面筛选服务(spec 2026-07-08 §4.2/§7)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app.services import specimen_filter_service as svc


def _make_ws(p: Path, rows: list[tuple]) -> Path:
    (p / "_data").mkdir(parents=True)
    db = p / "_data" / "project.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE specimens ("
        "uid TEXT, storage TEXT, photographer TEXT, province TEXT, scientific_name TEXT)"
    )
    for r in rows:
        conn.execute("INSERT INTO specimens VALUES (?,?,?,?,?)", r)
    conn.commit()
    conn.close()
    return p


def test_query_cross_workspace_and(tmp_path) -> None:
    a = _make_ws(tmp_path / "a", [("u1", "R95E", "张三", "浙江", "Aa"),
                                  ("u2", "T95E", "李四", "浙江", "Bb")])
    b = _make_ws(tmp_path / "b", [("u3", "R75E", "张三", "福建", "Cc")])
    res = svc.query_specimens(
        [str(a), str(b)],
        [
            {"field": "photographer", "op": "eq", "value": "张三"},
            {"field": "storage_is_rna", "op": "eq", "value": "是"},
        ],
    )
    uids = {r["uid"] for r in res}
    assert uids == {"u1", "u3"}, "张三 + 已取RNA → u1/u3; u2 非 RNA 排除"


def test_query_contains(tmp_path) -> None:
    a = _make_ws(tmp_path / "a", [("u1", "R95E", "张三", "浙江", "Aa bb"),
                                  ("u2", None, "李四", "浙江", "Cc")])
    res = svc.query_specimens(
        [str(a)], [{"field": "scientific_name", "op": "contains", "value": "Aa"}]
    )
    assert {r["uid"] for r in res} == {"u1"}


def test_query_is_empty_and_not_empty(tmp_path) -> None:
    a = _make_ws(tmp_path / "a", [("u1", "R95E", "张三", "浙江", "Aa"),
                                  ("u2", None, "李四", "浙江", "Bb")])
    empty = svc.query_specimens([str(a)], [{"field": "storage", "op": "is_empty"}])
    assert {r["uid"] for r in empty} == {"u2"}
    not_empty = svc.query_specimens([str(a)], [{"field": "storage", "op": "not_empty"}])
    assert {r["uid"] for r in not_empty} == {"u1"}


def test_query_missing_db_skipped(tmp_path) -> None:
    res = svc.query_specimens([str(tmp_path / "nope")], [])
    assert res == []


def test_query_workspace_label_attached(tmp_path) -> None:
    a = _make_ws(tmp_path / "断面a", [("u1", "R95E", "张三", "浙江", "Aa")])
    res = svc.query_specimens([str(a)], [], labels=["断面甲"])
    assert res[0]["_workspace_label"] == "断面甲"
    assert res[0]["_workspace"] == str(a)


def test_query_readonly_does_not_write(tmp_path) -> None:
    a = _make_ws(tmp_path / "a", [("u1", "R95E", "张三", "浙江", "Aa")])
    db = a / "_data" / "project.db"
    mtime_before = db.stat().st_mtime_ns
    svc.query_specimens([str(a)], [{"field": "photographer", "op": "eq", "value": "张三"}])
    assert db.stat().st_mtime_ns == mtime_before, "筛选纯读, 不应改 db mtime"
    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM specimens").fetchone()[0]
    conn.close()
    assert count == 1, "筛选不应增删行"


def test_field_choices_distinct(tmp_path) -> None:
    a = _make_ws(tmp_path / "a", [("u1", "R95E", "张三", "浙江", "Aa"),
                                  ("u2", None, "张三", "福建", "Bb"),
                                  ("u3", "T95E", "李四", "", "Cc")])
    assert svc.field_choices([str(a)], "photographer") == ["张三", "李四"]
    assert svc.field_choices([str(a)], "province") == ["浙江", "福建"]
    # 派生维度值固定 是/否
    assert svc.field_choices([str(a)], "storage_is_rna") == ["是", "否"]
