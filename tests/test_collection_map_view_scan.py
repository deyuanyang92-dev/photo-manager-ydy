"""test_collection_map_view_scan.py — 采集地图跨项目扫描的性能/锁不变量（offscreen）.

回归卡顿根因：进入采集地图页时，旧实现对「所有已知项目」用 ctx.get_db() 打开库
（→ 缓存连接 + 首次 ensure_schema 建表/迁移/建视图/commit），而且 _populate_projects
与 _reload_collection_map_points 各开一轮。本测试钉死新契约：

  1. 跨项目一律走 open_project_db_private(create=False, ensure=False)：纯读、不建表；
  2. 每个项目库一次 on_activate 只打开一次（不是两轮/三轮）；
  3. 别人的库不进 db_manager._db_cache（CLAUDE.md 锁泄漏红线）；
  4. ctx.get_db() 只用于「当前项目自身」（标识样式），不得带别的项目目录；
  5. 聚合结果不变（跨项目站位点数 / 单项目过滤）。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collection_map_view_scan.py -q
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from app.db import db_manager
from app.services import collection_record_service as crs
from app.views.collection_map_view import CollectionMapView

_APP = QApplication.instance() or QApplication([])


class _Ctx:
    """最小 AppContext 替身：记录 get_db 的每次调用参数。"""

    def __init__(self) -> None:
        self.current_project_dir = None
        self.current_project_root = None
        self.settings = None
        self.get_db_calls: list = []

    def get_db(self, project_dir=None):
        self.get_db_calls.append(project_dir)
        return None          # 无当前项目 → 样式读取直接跳过


def _make_project(root, name: str, records: list[dict]) -> str:
    d = root / name
    d.mkdir()
    conn = db_manager.open_project_db_private(str(d), create=True)
    try:
        for r in records:
            crs.upsert_record(conn, r)
        conn.commit()
    finally:
        conn.close()
    db_manager.close_all()   # create=True 不入缓存，但保险起见
    return str(d)


@pytest.fixture()
def two_projects(tmp_path):
    p1 = _make_project(tmp_path, "P1", [
        {"province": "ZJ", "site": "SMW", "station": "B2",
         "collection_date": "20260518", "lon": "121.0", "lat": "29.0"},
        {"province": "ZJ", "site": "SMW", "station": "H1",
         "collection_date": "20260519", "lon": "123.0", "lat": "31.0"},
    ])
    p2 = _make_project(tmp_path, "P2", [
        {"province": "FJ", "site": "XM", "station": "A1",
         "collection_date": "20260601", "lon": "118.0", "lat": "24.0"},
    ])
    return [p1, p2]


def _view(monkeypatch, dirs, ctx) -> CollectionMapView:
    monkeypatch.setattr(
        "app.views.collection_map_view.list_projects",
        lambda _path: [{"name": os.path.basename(d), "directory": d} for d in dirs],
    )
    v = CollectionMapView(ctx)
    v.resize(900, 600)
    return v


def _spy_private(monkeypatch) -> list:
    calls: list = []
    real = db_manager.open_project_db_private

    def spy(project_dir, **kw):
        calls.append((db_manager.normalize_path(str(project_dir)), dict(kw)))
        return real(project_dir, **kw)

    monkeypatch.setattr(db_manager, "open_project_db_private", spy)
    return calls


class TestActivateScan:
    def test_each_project_opened_once_readonly(self, two_projects, monkeypatch):
        calls = _spy_private(monkeypatch)
        ctx = _Ctx()
        v = _view(monkeypatch, two_projects, ctx)
        calls.clear()          # 只测 on_activate（构造期底图初始化另算）
        v.on_activate()

        opened = [d for d, _kw in calls]
        want = [db_manager.normalize_path(d) for d in two_projects]
        # 每个项目库正好开一次（旧实现是 2~3 轮）
        assert sorted(opened) == sorted(want), opened
        # 纯读：不 create、不 ensure_schema
        assert all(kw == {"create": False, "ensure": False} for _d, kw in calls)

    def test_no_cached_connection_for_other_projects(self, two_projects, monkeypatch):
        ctx = _Ctx()
        v = _view(monkeypatch, two_projects, ctx)
        v.on_activate()

        cached = set(db_manager._db_cache.keys())
        for d in two_projects:
            assert db_manager.normalize_path(d) not in cached
        # ctx.get_db 只能用于「当前项目」（不带目录参数）
        assert all(a is None for a in ctx.get_db_calls), ctx.get_db_calls

    def test_points_aggregated_across_projects(self, two_projects, monkeypatch):
        ctx = _Ctx()
        v = _view(monkeypatch, two_projects, ctx)
        v.on_activate()
        assert len(v._points) == 3          # B2 + H1 + A1
        v._set_level("province")
        assert len(v._points) == 2          # ZJ + FJ

    def test_single_project_filter(self, two_projects, monkeypatch):
        ctx = _Ctx()
        v = _view(monkeypatch, two_projects, ctx)
        v._project_filter = two_projects[1]
        v.on_activate()
        assert len(v._points) == 1          # P2 只有 A1
        assert db_manager.normalize_path(two_projects[1]) not in db_manager._db_cache

    def test_badges_show_station_counts(self, two_projects, monkeypatch):
        ctx = _Ctx()
        v = _view(monkeypatch, two_projects, ctx)
        v.on_activate()
        subs = []
        for i in range(v._proj_list.count()):
            w = v._proj_list.itemWidget(v._proj_list.item(i))
            subs.append(w._sel_labels[-1].text())
        assert subs[0] == "3 站位"          # 全部项目
        assert subs[1] == "2 站位"          # P1
        assert subs[2] == "1 站位"          # P2

    def test_legacy_db_without_collection_records_is_skipped(self, tmp_path, monkeypatch):
        """老库还没有 collection_records 表 → 跳过（不再靠 ensure_schema 现建表写盘）。"""
        import sqlite3
        d = tmp_path / "legacy"
        (d / "_data").mkdir(parents=True)
        conn = sqlite3.connect(str(d / "_data" / "project.db"))
        conn.execute("CREATE TABLE t(x)")
        conn.commit()
        conn.close()

        ctx = _Ctx()
        v = _view(monkeypatch, [str(d)], ctx)
        v.on_activate()                      # 不崩
        assert v._points == []
        with sqlite3.connect(str(d / "_data" / "project.db")) as c:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "collection_records" not in tables   # 没有被写进新表
