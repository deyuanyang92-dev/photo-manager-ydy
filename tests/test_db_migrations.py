"""tests/test_db_migrations.py — schema_version + 编号 migration."""
from __future__ import annotations

import sqlite3

import pytest

from app.db import db_manager
from app.db import migrations as mig


@pytest.fixture(autouse=True)
def reset_cache():
    db_manager.close_all()
    yield
    db_manager.close_all()


def test_open_project_sets_schema_version(tmp_path):
    p = tmp_path / "ws"
    p.mkdir()
    conn = db_manager.open_project_db(str(p), create=True)
    assert mig.get_schema_version(conn) == mig.SCHEMA_VERSION
    # 幂等
    db_manager.ensure_schema(conn)
    assert mig.get_schema_version(conn) == mig.SCHEMA_VERSION


def test_old_db_without_meta_upgrades(tmp_path):
    p = tmp_path / "ws"
    (p / "_data").mkdir(parents=True)
    db = p / "_data" / "project.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE specimens (uid TEXT)")
    conn.commit()
    conn.close()

    opened = db_manager.open_project_db(str(p), create=False)
    assert mig.get_schema_version(opened) == mig.SCHEMA_VERSION
    # _schema_meta 表存在
    row = opened.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_meta'"
    ).fetchone()
    assert row is not None


def test_run_pending_migrations_idempotent(tmp_path):
    p = tmp_path / "ws"
    p.mkdir()
    conn = db_manager.open_project_db(str(p), create=True)
    v1 = mig.run_pending_migrations(conn)
    v2 = mig.run_pending_migrations(conn)
    assert v1 == v2 == mig.SCHEMA_VERSION
