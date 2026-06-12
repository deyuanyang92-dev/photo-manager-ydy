"""db_manager.py — SQLite project database manager for W0.

Each project directory gets its own _data/project.db file.
Connections are cached per resolved project_dir path.
"""

import os
import sqlite3
from pathlib import Path
from typing import Optional

# Cache open connections by resolved project_dir path
_db_cache: dict[str, sqlite3.Connection] = {}

# Load schema SQL once
_SCHEMA_SQL_PATH = Path(__file__).parent / "schema.sql"
# darwin_core 视图：原 12 术语逐字复刻 db-utils.js:75-97（仅加 s. 前缀消歧），
# 在其后**附加**对齐 Darwin Core / Humboldt / OBIS 的标准术语——采集记录(collection_records)
# 按四键(province/site/station/collection_date) LEFT JOIN 进来，外加导出期常量。
#   - 采样努力: samplingProtocol / sampleSizeValue+Unit / samplingEffort（定量行有值，定性行空）
#   - 位置: habitat / waterBody / min&maxDepthInMeters
#   - 标本: basisOfRecord=PreservedSpecimen / preparations / geodeticDatum=WGS84 / countryCode=CN
#           / occurrenceStatus=present
#   - 环境量(盐度/水温/DO/pH/潮区…) + 采集性质 → dynamicProperties(JSON)，OBIS eMoF 的轻量替代
# json_object 需 SQLite json1（Python 3.9+ 标准内置）。无匹配采集记录的标本：派生术语为 NULL，
# 常量仍输出，dynamicProperties 为 NULL。
_DARWIN_CORE_SQL = """
DROP VIEW IF EXISTS darwin_core;
CREATE VIEW darwin_core AS
SELECT
  s.uid              AS occurrenceID,
  s.scientific_name  AS scientificName,
  s.family           AS family,
  s.genus            AS genus,
  s.order_name       AS "order",
  s.lon              AS decimalLongitude,
  s.lat              AS decimalLatitude,
  s.collection_date  AS eventDate,
  s.collector        AS recordedBy,
  s.identifier       AS identifiedBy,
  CASE
    WHEN s.province IS NOT NULL AND s.province != ''
    THEN s.province
         || CASE WHEN s.site IS NOT NULL AND s.site != '' THEN '·' || s.site ELSE '' END
         || CASE WHEN s.station IS NOT NULL AND s.station != '' THEN '·' || s.station ELSE '' END
    ELSE ''
  END AS locality,
  s.storage          AS verbatimPreservation,
  -- ── 附加标准术语（常量）──
  'PreservedSpecimen' AS basisOfRecord,
  s.storage           AS preparations,
  'WGS84'             AS geodeticDatum,
  'CN'                AS countryCode,
  'present'           AS occurrenceStatus,
  -- ── 附加标准术语（采集记录四键 JOIN）──
  cr.habitat           AS habitat,
  cr.water_body        AS waterBody,
  NULLIF(cr.depth, '') AS minimumDepthInMeters,
  NULLIF(cr.depth, '') AS maximumDepthInMeters,
  NULLIF(cr.sample_no, '') AS recordNumber,
  NULLIF(TRIM(
      COALESCE(cr.method, '')
      || CASE WHEN COALESCE(cr.sampler_model, '') != '' THEN ' · ' || cr.sampler_model ELSE '' END
      || CASE WHEN COALESCE(cr.sampler_spec, '') != '' THEN ' · ' || cr.sampler_spec ELSE '' END
      || CASE WHEN COALESCE(cr.sieve_mesh, '') != '' THEN ' · 网筛' || cr.sieve_mesh || 'mm' ELSE '' END
  ), '') AS samplingProtocol,
  NULLIF(cr.sample_area, '') AS sampleSizeValue,
  CASE WHEN COALESCE(cr.sample_area, '') != '' THEN 'square metre' END AS sampleSizeUnit,
  CASE WHEN COALESCE(cr.replicates, '') != '' THEN cr.replicates || ' 重复' END AS samplingEffort,
  CASE WHEN cr.id IS NOT NULL THEN json_object(
      '采集性质',     cr.sample_type,
      '航次',         cr.cruise,
      '船号',         cr.vessel,
      '采泥器型号',   cr.sampler_model,
      '潮区',         cr.tidal_zone,
      '潮汐',         cr.tide,
      '盐度',         cr.salinity,
      '表层水温',     cr.water_temp,
      '底层水温',     cr.bottom_temp,
      '溶解氧',       cr.dissolved_oxygen,
      'pH',           cr.ph,
      '天气',         cr.weather,
      '网筛mm',       cr.sieve_mesh,
      '记录人',       cr.recorder,
      '核对人',       cr.checker
  ) END AS dynamicProperties
FROM specimens s
LEFT JOIN collection_records cr
  ON s.province = cr.province
 AND s.site = cr.site
 AND s.station = cr.station
 AND s.collection_date = cr.collection_date;
"""


def _project_db_path(resolved_dir: str) -> Path:
    """Return the _data/project.db path for a project directory."""
    return Path(resolved_dir) / "_data" / "project.db"


def open_project_db(project_dir: str, *, create: bool = False) -> sqlite3.Connection:
    """Open (or retrieve cached) the SQLite connection for *project_dir*.

    ``create=False`` (the DEFAULT, used by every background read via
    ``AppContext.get_db``) is a strict OPEN: the workspace's ``project.db`` must
    already exist. If it does not — because the drive is unmounted, the share is
    offline, or the folder was deleted — this raises
    :class:`ProjectUnavailableError` and creates **nothing**. This is the guard
    that stops an unmounted project from being silently re-fabricated as an
    empty ghost on the local disk (see ``project_paths`` for the full rationale).

    ``create=True`` is the deliberate path used only when a workspace is being
    *established* (new project, or claiming an existing folder). The project
    ROOT must already exist (its parent volume is present); only the ``_data/``
    subfolder and the db file are materialised — never the root tree itself.

    Sets WAL mode, foreign_keys ON, and runs ensure_schema.
    """
    from app.services.project_paths import (
        ProjectUnavailableError,
        require_project_root,
    )

    resolved = str(Path(project_dir).resolve())
    if resolved in _db_cache:
        return _db_cache[resolved]

    db_path = _project_db_path(resolved)
    if create:
        # Root must already exist — never mkdir(parents=True) the whole tree,
        # which is exactly what fabricated ghosts on phantom mountpoints.
        require_project_root(resolved)
        db_path.parent.mkdir(exist_ok=True)  # only the _data/ leaf, inside root
    elif not db_path.exists():
        raise ProjectUnavailableError(
            f"工作区不可用（盘未挂载 / 数据库丢失）：{project_dir}"
        )

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()

    ensure_schema(conn)
    _db_cache[resolved] = conn
    return conn


def get_db(project_dir: str) -> sqlite3.Connection:
    """Return cached connection; opens if not yet open."""
    resolved = str(Path(project_dir).resolve())
    if resolved not in _db_cache:
        return open_project_db(project_dir)
    return _db_cache[resolved]


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently apply schema.sql, then recreate darwin_core view.

    ``CREATE TABLE IF NOT EXISTS`` creates *missing tables* but never adds new
    *columns* to a table that already exists. A project.db created by an older
    schema — notably the web prototype (db-utils.js:64, whose ``grouping`` table
    has only 5 columns) — therefore keeps its stale shape: archive/compression
    state becomes unreadable (shows "尚未压缩") and explicit-column writes crash
    ("no column named archive_zip"). ``_migrate_add_missing_columns`` closes that
    gap additively, before the view is rebuilt.
    """
    schema_sql = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    _migrate_add_missing_columns(conn, schema_sql)
    conn.executescript(_DARWIN_CORE_SQL)
    conn.commit()


def _migrate_add_missing_columns(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Add any columns present in *schema_sql* but missing from existing tables.

    The expected shape is derived by materialising *schema_sql* in a throwaway
    in-memory DB and introspecting it — so this stays in lock-step with
    ``schema.sql`` automatically as columns are added in the future, with no
    hand-maintained column list. Idempotent: only genuinely-missing columns are
    ALTERed in, so repeated calls are no-ops.

    SQLite restriction: ``ALTER TABLE ADD COLUMN`` cannot add a NOT NULL column
    without a default, and the default must be constant. Every additive column
    in this schema is nullable or has a literal default, so we carry the
    reference default through; a NOT NULL column lacking a default is skipped
    (cannot happen in the current schema) rather than raising.
    """
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(schema_sql)
        ref_tables = [
            r[0] for r in ref.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in ref_tables:
            actual = {
                r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
            if not actual:
                continue  # table did not pre-exist — schema_sql just created it fresh
            for cid, name, ctype, notnull, dflt, pk in ref.execute(
                f'PRAGMA table_info("{table}")'
            ).fetchall():
                if name in actual:
                    continue
                col_def = f'"{name}" {ctype}' if ctype else f'"{name}"'
                if dflt is not None:
                    col_def += f" DEFAULT {dflt}"
                    if notnull:
                        col_def += " NOT NULL"
                elif notnull:
                    # Can't safely add NOT NULL without a default — skip.
                    continue
                conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {col_def}')
        conn.commit()
    finally:
        ref.close()


def close_all() -> None:
    """Close and evict all cached connections. Used in tests and on exit."""
    for conn in list(_db_cache.values()):
        try:
            conn.close()
        except Exception:
            pass
    _db_cache.clear()


def close_project_db(project_dir: str) -> None:
    """Close and evict a single project's connection."""
    resolved = str(Path(project_dir).resolve())
    conn = _db_cache.pop(resolved, None)
    if conn:
        try:
            conn.close()
        except Exception:
            pass
