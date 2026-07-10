"""migrations.py — 编号数据迁移（在 ensure_schema 补列之后运行）.

用途：以后升级/回填/建索引时，往 ``MIGRATIONS`` 追加 ``(version, fn)`` 即可。
老库首次打开会从当前 version 顺序执行到最新。

与 ``_migrate_add_missing_columns`` 的分工：
- 补列 / 建表 → 仍靠 ``schema.sql`` + 自动 ALTER（无需编号）
- 数据回填、索引初始化、一次性修复 → 放这里的编号 migration
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable

# 当前代码期望的 schema 逻辑版本（递增整数）
SCHEMA_VERSION = 2

MigrationFn = Callable[[sqlite3.Connection], None]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_meta_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _schema_meta (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          version INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT
        )
        """
    )
    row = conn.execute("SELECT version FROM _schema_meta WHERE id=1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO _schema_meta (id, version, updated_at) VALUES (1, 0, ?)",
            (_utc_now_iso(),),
        )


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return stored schema version (0 if meta missing)."""
    try:
        row = conn.execute("SELECT version FROM _schema_meta WHERE id=1").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    _ensure_meta_table(conn)
    conn.execute(
        """
        INSERT INTO _schema_meta (id, version, updated_at) VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          version=excluded.version,
          updated_at=excluded.updated_at
        """,
        (int(version), _utc_now_iso()),
    )


def _migration_001_bootstrap(conn: sqlite3.Connection) -> None:
    """v1：建立版本表基线；后续升级从这里往上加."""
    _ensure_meta_table(conn)


def _migration_002_specimen_result_tif_index(conn: sqlite3.Connection) -> None:
    """v2：成果母版 TIF 绝对路径索引（数据汇总 / 导出 TIF 用）."""
    from app.db.result_tif_schema import ensure_result_tif_index_table

    ensure_result_tif_index_table(conn)


# (target_version, migration_fn) — 按 version 升序
MIGRATIONS: list[tuple[int, MigrationFn]] = [
    (1, _migration_001_bootstrap),
    (2, _migration_002_specimen_result_tif_index),
]


def run_pending_migrations(conn: sqlite3.Connection) -> int:
    """Run numbered migrations until SCHEMA_VERSION. Returns final version."""
    _ensure_meta_table(conn)
    current = get_schema_version(conn)
    for version, fn in MIGRATIONS:
        if version <= current:
            continue
        if version > SCHEMA_VERSION:
            break
        fn(conn)
        set_schema_version(conn, version)
        current = version
    # 若 MIGRATIONS 未覆盖到 SCHEMA_VERSION（仅抬版本号），仍对齐
    if current < SCHEMA_VERSION:
        set_schema_version(conn, SCHEMA_VERSION)
        current = SCHEMA_VERSION
    return current
