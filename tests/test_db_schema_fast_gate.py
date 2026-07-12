"""ensure_schema 快速 gate 的回归测试（纯逻辑，无 GUI）.

背景：ensure_schema 以前在**每个** project.db 首次打开时都跑完整路径
（executescript 全量 schema + 在一次性内存库里物化整份 schema 做列 diff +
DROP/CREATE darwin_core 视图 + commit 写盘）。跨库统计一乘以 N 个项目就是主线程
上的秒级冻结，而且是一条「读页面却写子库」的路径。

本文件锁住新行为：
  1. 首次 ensure（老库/新库）仍跑完整路径，schema 正确、darwin_core 视图存在；
  2. 之后再 ensure 同一个库 → 直接 return：不 executescript、不物化内存库、不写盘；
  3. 指纹随 schema.sql / darwin_core SQL / SCHEMA_VERSION 变化而失效 → 重跑完整路径；
  4. force=True 无条件跑完整路径；
  5. 老库（无 _schema_meta）不会被 gate 误判为「已最新」——补列迁移不能被跳过。
"""
import os
import sqlite3

import pytest

from app.db import db_manager


@pytest.fixture(autouse=True)
def reset_cache():
    db_manager.close_all()
    yield
    db_manager.close_all()


@pytest.fixture
def tmp_project(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return str(p)


def _count_calls(monkeypatch, target_name):
    """Patch a db_manager module attr with a counting wrapper. Returns the counter."""
    calls = {"n": 0}
    original = getattr(db_manager, target_name)

    def wrapper(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(db_manager, target_name, wrapper)
    return calls


class TestFastGate:
    def test_second_ensure_skips_full_path(self, tmp_project, monkeypatch):
        conn = db_manager.open_project_db(tmp_project, create=True)  # 完整路径跑过一次
        calls = _count_calls(monkeypatch, "_migrate_add_missing_columns")

        db_manager.ensure_schema(conn)
        db_manager.ensure_schema(conn)

        assert calls["n"] == 0, "指纹命中后不得再物化内存库做列 diff"

    def test_second_ensure_does_not_write(self, tmp_project):
        """gate 命中 = 纯读：库文件 mtime/size 不变（读路径禁写子库）。"""
        db_path = os.path.join(tmp_project, "_data", "project.db")
        db_manager.open_project_db(tmp_project, create=True)
        db_manager.close_all()

        before = os.stat(db_path)
        conn = db_manager.open_project_db(tmp_project)  # 已是最新 → 应零写入
        try:
            after = os.stat(db_path)
            assert (after.st_size, after.st_mtime_ns) == (
                before.st_size,
                before.st_mtime_ns,
            )
        finally:
            db_manager.close_all()
        assert conn is not None

    def test_fingerprint_persisted(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
        row = conn.execute("SELECT schema_fp FROM _schema_meta WHERE id=1").fetchone()
        assert row["schema_fp"] == db_manager._schema_fingerprint()

    def test_stale_fingerprint_reruns_full_path(self, tmp_project, monkeypatch):
        conn = db_manager.open_project_db(tmp_project, create=True)
        conn.execute("UPDATE _schema_meta SET schema_fp='deadbeef' WHERE id=1")
        conn.commit()

        calls = _count_calls(monkeypatch, "_migrate_add_missing_columns")
        db_manager.ensure_schema(conn)

        assert calls["n"] == 1, "指纹不匹配必须重跑完整路径"
        row = conn.execute("SELECT schema_fp FROM _schema_meta WHERE id=1").fetchone()
        assert row["schema_fp"] == db_manager._schema_fingerprint()

    def test_force_reruns_full_path(self, tmp_project, monkeypatch):
        conn = db_manager.open_project_db(tmp_project, create=True)
        calls = _count_calls(monkeypatch, "_migrate_add_missing_columns")

        db_manager.ensure_schema(conn, force=True)

        assert calls["n"] == 1

    def test_fingerprint_covers_view_and_version(self):
        """指纹必须覆盖 darwin_core 视图 SQL 与 SCHEMA_VERSION，不只是 schema.sql。"""
        base = db_manager._schema_fingerprint()

        db_manager._SCHEMA_FP_CACHE = None
        original_view = db_manager._DARWIN_CORE_SQL
        try:
            db_manager._DARWIN_CORE_SQL = original_view + "\n-- changed\n"
            assert db_manager._schema_fingerprint() != base
        finally:
            db_manager._DARWIN_CORE_SQL = original_view
            db_manager._SCHEMA_FP_CACHE = None

        assert db_manager._schema_fingerprint() == base


class TestGateDoesNotSkipLegacyMigration:
    """老库没有 _schema_meta → 指纹读不到 → 必须走完整路径把缺列补上。"""

    def _make_legacy_db(self, project_dir):
        data_dir = os.path.join(project_dir, "_data")
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "project.db")
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE grouping (
              uid TEXT, group_index INTEGER,
              angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
              PRIMARY KEY (uid, group_index)
            );
            """
        )
        con.commit()
        con.close()
        return db_path

    def test_legacy_db_still_migrated(self, tmp_project):
        self._make_legacy_db(tmp_project)
        conn = db_manager.open_project_db(tmp_project)

        cols = {r[1] for r in conn.execute("PRAGMA table_info(grouping)").fetchall()}
        assert "archive_zip" in cols
        views = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        }
        assert "darwin_core" in views
        row = conn.execute("SELECT schema_fp FROM _schema_meta WHERE id=1").fetchone()
        assert row["schema_fp"] == db_manager._schema_fingerprint()

    def test_stored_fp_none_on_legacy_db(self, tmp_project):
        db_path = self._make_legacy_db(tmp_project)
        raw = sqlite3.connect(db_path)
        try:
            assert db_manager._stored_schema_fp(raw) is None
        finally:
            raw.close()
