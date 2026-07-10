"""tests/test_workspace_index_service.py — 根库索引缓存刷新/读取."""
from __future__ import annotations

import sqlite3

import pytest

from app.db import db_manager
from app.services import project_catalog_service as catalog
from app.services import workspace_index_service as wis


@pytest.fixture(autouse=True)
def reset_cache():
    db_manager.close_all()
    yield
    db_manager.close_all()


def _make_ws(path, *, specimens=0):
    path.mkdir(parents=True, exist_ok=True)
    (path / "_data").mkdir(exist_ok=True)
    conn = sqlite3.connect(str(path / "_data" / "project.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS specimens ("
        "uid TEXT, station TEXT, collection_date TEXT, lon TEXT, lat TEXT, "
        "scientific_name TEXT, storage TEXT)"
    )
    for i in range(specimens):
        conn.execute(
            "INSERT INTO specimens VALUES (?,?,?,?,?,?,?)",
            (f"u{i}", "A01", "2026-06-01", "110", "21", "Aa", "R95E"),
        )
    conn.commit()
    conn.close()


def test_refresh_and_read_cache(tmp_path):
    root = tmp_path / "survey"
    ws = root / "断面A"
    root.mkdir()
    _make_ws(ws, specimens=3)

    catalog.register_workspace(str(root), str(ws), name="断面A")
    # register_workspace 已挂钩 refresh
    rows = wis.read_all_cached_indexes(str(root))
    assert len(rows) == 1
    assert rows[0]["specimen_count"] == 3

    # 再刷一次仍幂等
    again = wis.refresh_workspace_index(str(root), str(ws))
    assert again["specimen_count"] == 3


def test_cached_kpi_for_workspaces(tmp_path):
    root = tmp_path / "survey"
    a = root / "a"
    b = root / "b"
    root.mkdir()
    _make_ws(a, specimens=2)
    _make_ws(b, specimens=5)
    catalog.register_workspace(str(root), str(a), name="a")
    catalog.register_workspace(str(root), str(b), name="b")

    totals = wis.cached_kpi_for_workspaces(str(root), [str(a), str(b)])
    assert totals is not None
    assert totals["specimen_count"] == 7


def test_compute_stats_missing_db(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    stats = wis.compute_workspace_index_stats(str(empty))
    assert stats["specimen_count"] == 0


def test_cached_kpi_read_leaves_no_cached_child_conn_and_no_writes(tmp_path):
    """v0.56 锁泄漏治理: KPI 读路径不得缓存子工作区连接(Windows 文件锁扣到退出),
    也不得改动子库(v0.55 顺手 UPDATE workspace_meta.updated_at + 跑迁移)."""
    from app.utils.path_utils import normalize_path

    root = tmp_path / "survey"
    a = root / "a"
    root.mkdir()
    _make_ws(a, specimens=2)
    catalog.register_workspace(str(root), str(a), name="a")

    db_manager.close_all()

    before = sqlite3.connect(str(a / "_data" / "project.db"))
    row = before.execute("SELECT updated_at FROM workspace_meta").fetchone()
    before.close()
    assert row is not None
    ts_before = row[0]

    totals = wis.cached_kpi_for_workspaces(str(root), [str(a)])
    assert totals is not None and totals["specimen_count"] == 2

    assert normalize_path(str(a)) not in db_manager._db_cache, (
        "KPI 读路径不应留下子工作区缓存连接"
    )

    after = sqlite3.connect(str(a / "_data" / "project.db"))
    ts_after = after.execute("SELECT updated_at FROM workspace_meta").fetchone()[0]
    after.close()
    assert ts_after == ts_before, "KPI 读路径不应改写子库 workspace_meta"


def test_ensure_workspace_meta_pure_read_keeps_updated_at_and_no_cache(tmp_path):
    """v0.56: ensure_workspace_meta 无新信息时=纯读, 不重写 updated_at, 不缓存连接."""
    from app.utils.path_utils import normalize_path

    ws = tmp_path / "ws"
    _make_ws(ws)

    meta1 = catalog.ensure_workspace_meta(str(ws))
    db_manager.close_all()
    meta2 = catalog.ensure_workspace_meta(str(ws))

    assert meta2["workspace_id"] == meta1["workspace_id"]
    assert meta2["updated_at"] == meta1["updated_at"], "纯读不应重写 updated_at"
    assert normalize_path(str(ws)) not in db_manager._db_cache


class TestCacheStaleness:
    """docstring 承诺 "fresh-enough row" —— 子库在缓存刷新后又被写过,
    缓存必须判 stale 返回 None(调用方回退现场扫描),不得无限期喂陈旧 KPI。"""

    def test_stale_cache_returns_none_after_workspace_write(self, tmp_path):
        import os
        import time

        root = tmp_path / "survey"
        a = root / "a"
        root.mkdir()
        _make_ws(a, specimens=2)
        catalog.register_workspace(str(root), str(a), name="a")

        assert wis.cached_kpi_for_workspaces(str(root), [str(a)]) is not None

        # 模拟刷新之后子库又被写: 把 db mtime 推到缓存 updated_at 之后
        db_path = a / "_data" / "project.db"
        future = time.time() + 30
        os.utime(db_path, (future, future))

        assert wis.cached_kpi_for_workspaces(str(root), [str(a)]) is None, (
            "子库比缓存新 → 必须判 stale 回退现场扫描"
        )

    def test_fresh_cache_still_served(self, tmp_path):
        root = tmp_path / "survey"
        a = root / "a"
        root.mkdir()
        _make_ws(a, specimens=4)
        catalog.register_workspace(str(root), str(a), name="a")
        totals = wis.cached_kpi_for_workspaces(str(root), [str(a)])
        assert totals is not None and totals["specimen_count"] == 4
