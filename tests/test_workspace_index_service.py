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
