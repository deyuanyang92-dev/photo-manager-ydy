"""db_manager.py — SQLite project database manager for W0.

Each project directory gets its own _data/project.db file.
Connections are cached per resolved project_dir path.
"""

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Optional

from app.utils.path_utils import normalize_path

# Cache open connections by resolved project_dir path
_db_cache: dict[str, sqlite3.Connection] = {}

# Load schema SQL once
_SCHEMA_SQL_PATH = Path(__file__).parent / "schema.sql"

# §7 旧: ensure_schema 每次调用都 `_SCHEMA_SQL_PATH.read_text(...)` —— 每开一个子库
#      就多一次真实磁盘 IO（drvfs/网络盘上尤其贵）。
# 新: 进程内只读一次并缓存（schema.sql 是打包进程序的静态资源，运行期不会变）。
_SCHEMA_SQL_CACHE: Optional[str] = None
_SCHEMA_FP_CACHE: Optional[str] = None


def _schema_sql() -> str:
    """Return schema.sql text, read from disk at most once per process."""
    global _SCHEMA_SQL_CACHE
    if _SCHEMA_SQL_CACHE is None:
        _SCHEMA_SQL_CACHE = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    return _SCHEMA_SQL_CACHE


def _schema_fingerprint() -> str:
    """sha256(schema.sql + darwin_core 视图 SQL + SCHEMA_VERSION)，进程内只算一次。

    指纹覆盖 ensure_schema 会写进库里的**全部**东西：
      - schema.sql（建表 + 补列的参照形状）
      - ``_DARWIN_CORE_SQL``（视图定义；改视图不改表时指纹也必须变）
      - ``migrations.SCHEMA_VERSION``（编号迁移的目标版本）
    任一变化 → 指纹变化 → 老库下次打开重新跑完整 ensure。
    """
    global _SCHEMA_FP_CACHE
    if _SCHEMA_FP_CACHE is None:
        from app.db.migrations import SCHEMA_VERSION

        h = hashlib.sha256()
        h.update(_schema_sql().encode("utf-8"))
        h.update(_DARWIN_CORE_SQL.encode("utf-8"))
        h.update(f"|v{int(SCHEMA_VERSION)}".encode("utf-8"))
        _SCHEMA_FP_CACHE = h.hexdigest()
    return _SCHEMA_FP_CACHE


def _stored_schema_fp(conn: sqlite3.Connection) -> Optional[str]:
    """读库里记录的 schema 指纹；老库（无 _schema_meta / 无 schema_fp 列）返回 None。

    纯 SELECT，不建表、不写盘 —— 命中即让 ensure_schema 直接返回。
    """
    try:
        row = conn.execute("SELECT schema_fp FROM _schema_meta WHERE id=1").fetchone()
    except sqlite3.Error:
        return None  # 没有 _schema_meta 表，或该表还没有 schema_fp 列
    if row is None:
        return None
    try:
        value = row["schema_fp"]
    except (IndexError, TypeError, KeyError):
        value = row[0]
    return str(value) if value else None


def _persist_schema_fp(conn: sqlite3.Connection, fp: str) -> None:
    """把当前指纹写进 _schema_meta（缺列则先 ALTER 补上）。

    调用点在 run_pending_migrations 之后 —— 那时 ``_schema_meta`` 必然存在。
    Best-effort：只读盘 / 权限受限时静默跳过，退回「每次全量 ensure」的旧行为，
    正确性不受影响（只是没有加速）。
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(_schema_meta)").fetchall()}
        if not cols:
            return  # 理论上不会发生（migrations 已建表）
        if "schema_fp" not in cols:
            conn.execute("ALTER TABLE _schema_meta ADD COLUMN schema_fp TEXT")
        conn.execute("UPDATE _schema_meta SET schema_fp=? WHERE id=1", (fp,))
    except sqlite3.Error:
        pass


def is_database_locked(exc: BaseException) -> bool:
    """Return True for SQLite lock/busy errors."""
    return isinstance(exc, sqlite3.OperationalError) and (
        "database is locked" in str(exc).lower()
        or "database table is locked" in str(exc).lower()
        or "database is busy" in str(exc).lower()
    )


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply common SQLite pragmas, falling back when WAL is unavailable.

    WAL is preferred for the workbench because it keeps reads responsive while
    writes happen. Some Windows/WSL mounted drives can still reject the WAL
    switch with ``disk I/O error`` even though the database itself is readable
    and writable. In that case, keep the workspace usable with SQLite's default
    rollback journal instead of failing project entry.
    """
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError as exc:
        if is_database_locked(exc):
            raise
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
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
 AND COALESCE(s.station, '') = COALESCE(cr.station, '')
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

    resolved = normalize_path(project_dir)
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

    # check_same_thread=False: cached conn is shared, but ONLY the Qt main
    # thread may use it. Any background QThread/worker that needs DB access
    # MUST open its OWN sqlite3.connect() (do not call get_db()/reuse the
    # cache) — sharing one Connection across threads corrupts cursors even
    # under WAL. (Collab already self-stores; no worker currently hits this.)
    conn = sqlite3.connect(str(db_path), timeout=8.0, check_same_thread=False)
    _configure_connection(conn)

    ensure_schema(conn)
    _db_cache[resolved] = conn
    return conn


def open_project_db_private(
    project_dir: str, *, create: bool = False, ensure: bool = False
) -> sqlite3.Connection:
    """Open an uncached connection for background workers / short-lived reads.

    The GUI thread owns the cached connection returned by ``open_project_db``.
    Workers must use a private connection so cursors and transactions cannot
    cross thread boundaries. 跨工作区汇总/索引等「碰一下就走」的路径也必须用
    私有连接并在 finally 里 close —— 缓存连接会把每个子工作区的文件锁一直
    扣到退出 (Windows 上目录因此不能移动/删除, v0.56 锁泄漏治理)。

    ``create=True`` mirrors ``open_project_db(create=True)``: materialise only
    the ``_data/`` leaf + db file inside an EXISTING root (never fabricates the
    tree), and run ensure_schema. ``ensure=True`` runs ensure_schema on an
    existing db (legacy dbs may lack newer tables). Plain open (both False)
    skips ensure_schema for speed and NEVER writes.
    """
    from app.services.project_paths import (
        ProjectUnavailableError,
        require_project_root,
    )

    resolved = normalize_path(project_dir)
    db_path = _project_db_path(resolved)
    if create:
        require_project_root(resolved)
        db_path.parent.mkdir(exist_ok=True)  # only the _data/ leaf, inside root
    elif not db_path.exists():
        raise ProjectUnavailableError(
            f"工作区不可用（盘未挂载 / 数据库丢失）：{project_dir}"
        )

    conn = sqlite3.connect(str(db_path), timeout=8.0)
    _configure_connection(conn)
    if create or ensure:
        ensure_schema(conn)
    return conn


def get_db(project_dir: str) -> sqlite3.Connection:
    """Return cached connection; opens if not yet open."""
    resolved = normalize_path(project_dir)
    if resolved not in _db_cache:
        return open_project_db(project_dir)
    return _db_cache[resolved]


def ensure_schema(conn: sqlite3.Connection, *, force: bool = False) -> None:
    """Idempotently apply schema.sql, then recreate darwin_core view.

    ``CREATE TABLE IF NOT EXISTS`` creates *missing tables* but never adds new
    *columns* to a table that already exists. A project.db created by an older
    schema — notably the web prototype (db-utils.js:64, whose ``grouping`` table
    has only 5 columns) — therefore keeps its stale shape: archive/compression
    state becomes unreadable (shows "尚未压缩") and explicit-column writes crash
    ("no column named archive_zip"). ``_migrate_add_missing_columns`` closes that
    gap additively, before the view is rebuilt.

    After structural sync, numbered data migrations in ``app.db.migrations`` run
    so future upgrades (backfill / index init) have a stable version hook.

    **快速 gate（性能红线）**：完整路径 = executescript 全量 schema + 在一次性内存库里
    物化整份 schema 做列 diff + DROP/CREATE 视图 + commit(写盘)，单库本地 10–30 ms，
    drvfs/网络盘 50–150 ms。它以前在**每个** project.db 首次打开时都跑（open_project_db
    → get_db 的每条新路径），N 个项目的跨库统计因此在主线程上叠成秒级冻结，而且这是一条
    「读页面却写子库」的路径。现在开头先做一条 SELECT 读 ``_schema_meta.schema_fp``：
    指纹与当前代码一致 → 直接 return，全程零写入（~0.5 ms）。指纹缺失/不匹配（老库、
    schema 升级后首次打开）才走完整路径，跑完把新指纹与版本号在同一个 commit 里落库。
    ``force=True`` 无条件跑完整路径（供测试/修复用）。
    """
    fp = _schema_fingerprint()
    if not force and _stored_schema_fp(conn) == fp:
        return  # 已是当前 schema：不 executescript、不物化内存库、不重建视图、不 commit

    # §7 旧: schema_sql = _SCHEMA_SQL_PATH.read_text(encoding="utf-8")  # 每次调用都读盘
    schema_sql = _schema_sql()
    conn.executescript(schema_sql)
    _migrate_add_missing_columns(conn, schema_sql)
    from app.db.migrations import run_pending_migrations

    run_pending_migrations(conn)
    conn.executescript(_DARWIN_CORE_SQL)
    # 指纹最后写，与 set_schema_version 落在同一个 commit 里：中途崩溃 → 指纹不落库
    # → 下次打开重跑完整 ensure（宁可慢一次，不可跳过必要迁移）。
    _persist_schema_fp(conn, fp)
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
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
    _db_cache.clear()


def close_project_db(project_dir: str) -> None:
    """Close and evict a single project's connection."""
    resolved = normalize_path(project_dir)
    conn = _db_cache.pop(resolved, None)
    if conn:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
