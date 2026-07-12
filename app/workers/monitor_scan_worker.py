"""Background monitor scan worker.

Keeps slow directory scans and SQLite reads off the Qt GUI thread.

卡顿根因 (P1) 与本文件的修法
---------------------------------
旧实现在后台线程里用一个**可写**的私有连接跑 ``scan_project``，而 ``scan_project``
是写操作：

* ``monitor_service.ensure_seen_files_table``      → CREATE TABLE + commit
* ``monitor_service._insert_missing_first_seen_times`` → INSERT OR IGNORE 事务
* ``photo_asset_service.upsert_photo_files_lightweight`` → 每张 JPG 无条件
  ``UPDATE photo_files ... last_seen_at`` + ``UPDATE photos SET updated_at``
  （稳态下 = 2N 行写，且相机连拍时每 300ms 就来一轮）

主线程同时也在写同一个库（右栏 autosave / 激活保存，走 db_manager 缓存连接，
``busy_timeout=8000``）。两个 writer 一撞，主线程最长硬等 8 秒；若该盘 WAL 降级成
DELETE 日志，写者持整库 EXCLUSIVE 锁，主线程连读都被挡 ⇒ 边拍边填字段必卡。

新实现（全部收敛在本文件内，不动 monitor_service / photo_asset_service / db_manager）：

1. **扫描阶段绝不拿写锁**：``scan_project`` 拿到的是 ``_RecordingConnection`` —— 读
   直通到一个 ``mode=ro`` 的只读连接，所有写语句只被 *记录* 下来，不执行。
2. **脏检查**（等价于「只有 size/mtime 真变了才写」）：回放前把稳态噪音写剔掉 ——
   已存在的 ``CREATE TABLE IF NOT EXISTS``、以及 size_bytes/mtime 未变的
   ``UPDATE photo_files`` + 紧随其后的 ``UPDATE photos SET updated_at``。
   稳态扫描 ⇒ 剩余写语句为 0 ⇒ **根本不打开可写连接**，writer-vs-writer 窗口消失。
3. 确实有增量（新照片 / 新归属）时，才开一个私有可写连接，**单事务**一次性回放，
   然后再 emit 结果 —— 与旧实现的时序一致（结果送达时写已落盘），不引入并发写窗口。

任何一步在只读路径上出错（老库缺表、mode=ro 打不开…）都会 **回落到旧的整段逻辑**
(``_run_legacy_scan``)，行为与修改前完全一致。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)


# ── SQL 分类 / 归一 ───────────────────────────────────────────────────────────

_WRITE_PREFIXES = (
    "INSERT", "UPDATE", "DELETE", "REPLACE",
    "CREATE", "DROP", "ALTER", "VACUUM", "REINDEX",
)


def _normalize_sql(sql: str) -> str:
    """Collapse whitespace so SQL text can be pattern-matched."""
    return re.sub(r"\s+", " ", str(sql)).strip()


def _is_write_sql(sql: str) -> bool:
    head = _normalize_sql(sql).upper()
    return head.startswith(_WRITE_PREFIXES)


class _RecordingConnection:
    """Read-through / write-record proxy around a sqlite3 connection.

    Reads (SELECT / PRAGMA / …) go straight to the wrapped connection.
    Mutating statements are appended to :attr:`ops` and **never executed here**,
    so the scan thread cannot hold a write lock on project.db.

    Read-after-write inside ``scan_project`` was checked and is safe:
    ``upsert_photo_files_lightweight`` reads ``photo_files`` *before* its write
    block, and the only in-transaction SELECT (current assignment of a photo)
    concerns rows that either already exist (visible to the read connection) or
    were just INSERTed as brand-new photos — for which the real connection would
    also report "no current assignment". Same outcome either way.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self.ops: list[tuple[str, object, bool]] = []  # (sql, params, is_many)

    # -- statement entry points -------------------------------------------
    def execute(self, sql, parameters=(), /):
        if _is_write_sql(sql):
            self.ops.append((str(sql), tuple(parameters), False))
            return _NullCursor()
        return self._conn.execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters, /):
        if _is_write_sql(sql):
            self.ops.append((str(sql), [tuple(p) for p in seq_of_parameters], True))
            return _NullCursor()
        return self._conn.executemany(sql, seq_of_parameters)

    def executescript(self, sql_script, /):  # pragma: no cover - not used by scan
        raise sqlite3.OperationalError(
            "executescript is not allowed on the monitor scan connection"
        )

    def cursor(self):
        # 扫描路径不使用裸游标；真要用了也只会拿到只读连接的游标（写会直接报错，
        # 从而触发 _run_legacy_scan 回落），不会静默丢写。
        return self._conn.cursor()

    # -- transaction API (no-ops: nothing is executed here) ----------------
    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    # -- everything else delegates ----------------------------------------
    def __getattr__(self, name):
        return getattr(self._conn, name)


class _NullCursor:
    """Stand-in cursor returned for recorded (not executed) writes."""

    lastrowid = None
    rowcount = -1
    description = None

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def fetchmany(self, size=None):
        return []

    def __iter__(self):
        return iter(())

    def close(self) -> None:
        return None


# ── 脏检查：把稳态噪音写剔掉 ─────────────────────────────────────────────────

# photo_asset_service.upsert_photo_files_lightweight 的既有行更新（原文）
_SQL_UPDATE_PHOTO_FILE = _normalize_sql(
    "UPDATE photo_files SET size_bytes=?, mtime=?, exists_on_disk=1, last_seen_at=? "
    "WHERE file_id=?"
).upper()
_SQL_TOUCH_PHOTO = _normalize_sql(
    "UPDATE photos SET updated_at=? WHERE photo_id=?"
).upper()


def _existing_table_names(conn: sqlite3.Connection) -> set[str]:
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except sqlite3.Error:
        return set()
    return {str(r[0]).lower() for r in rows}


def _create_table_target(norm_upper: str) -> str | None:
    m = re.match(
        r"CREATE TABLE (?:IF NOT EXISTS )?[\"'`\[]?([A-Z0-9_]+)", norm_upper
    )
    return m.group(1).lower() if m else None


def _photo_file_row_unchanged(
    conn: sqlite3.Connection, params: tuple
) -> bool:
    """True when the recorded photo_files UPDATE would not change anything.

    params = (size_bytes, mtime, last_seen_at, file_id).  ``last_seen_at`` is
    deliberately ignored — refreshing it on every scan is exactly the redundant
    write we are eliminating.
    """
    if len(params) != 4:
        return False
    size_bytes, mtime, _last_seen_at, file_id = params
    try:
        row = conn.execute(
            "SELECT size_bytes, mtime, exists_on_disk FROM photo_files "
            "WHERE file_id=?",
            (file_id,),
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    try:
        cur_size, cur_mtime, cur_exists = row[0], row[1], row[2]
    except Exception:  # noqa: BLE001
        return False
    return (
        cur_size == size_bytes
        and cur_mtime == mtime
        and int(cur_exists or 0) == 1
    )


def prune_noop_ops(
    conn: sqlite3.Connection,
    ops: list[tuple[str, object, bool]],
) -> list[tuple[str, object, bool]]:
    """Drop recorded statements that would write without changing anything.

    * ``CREATE TABLE IF NOT EXISTS x`` when ``x`` already exists (DDL still
      takes a write lock).
    * ``UPDATE photo_files SET size_bytes/mtime/last_seen_at`` when size+mtime
      are unchanged, together with the ``UPDATE photos SET updated_at`` that
      ``upsert_photo_files_lightweight`` always emits right after it.

    Anything unrecognised is kept verbatim — an unknown statement is always
    replayed, so this can only remove writes it positively proved redundant.
    """
    if not ops:
        return []

    tables = _existing_table_names(conn)
    kept: list[tuple[str, object, bool]] = []
    dropped_photo_file_update = False

    for sql, params, is_many in ops:
        norm = _normalize_sql(sql).upper()

        if norm.startswith("CREATE TABLE IF NOT EXISTS"):
            target = _create_table_target(norm)
            if target and target in tables:
                dropped_photo_file_update = False
                continue
            kept.append((sql, params, is_many))
            dropped_photo_file_update = False
            continue

        if (
            not is_many
            and norm == _SQL_UPDATE_PHOTO_FILE
            and isinstance(params, tuple)
            and _photo_file_row_unchanged(conn, params)
        ):
            dropped_photo_file_update = True
            continue

        if (
            not is_many
            and norm == _SQL_TOUCH_PHOTO
            and dropped_photo_file_update
        ):
            # 仅当紧邻的 photo_files 更新被判定为无变化时，才连带丢弃这条
            # updated_at 触碰（二者由同一循环体成对发出）。
            dropped_photo_file_update = False
            continue

        dropped_photo_file_update = False
        kept.append((sql, params, is_many))

    return kept


# ── 连接打开 / 回放 ───────────────────────────────────────────────────────────

def _project_db_file(project_dir: str) -> Path:
    from app.db.db_manager import _project_db_path
    from app.utils.path_utils import normalize_path

    return _project_db_path(normalize_path(project_dir))


def _open_readonly(project_dir: str) -> sqlite3.Connection:
    """Open the workspace db in SQLite read-only mode (physically write-proof)."""
    db_path = _project_db_file(project_dir)
    if not db_path.exists():
        raise sqlite3.OperationalError(f"project db not found: {db_path}")
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=8.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def replay_ops(project_dir: str, ops: list[tuple[str, object, bool]]) -> int:
    """Replay pruned writes on a private RW connection, in ONE transaction.

    Returns the number of statements applied (0 when nothing had to be written —
    the steady-state case, in which no writable connection is opened at all).
    """
    if not ops:
        return 0

    from app.db.db_manager import open_project_db_private

    rw = open_project_db_private(project_dir)
    try:
        with rw:
            for sql, params, is_many in ops:
                if is_many:
                    rw.executemany(sql, params)
                else:
                    rw.execute(sql, params)
    finally:
        try:
            rw.close()
        except Exception:  # noqa: BLE001
            pass
    return len(ops)


class MonitorScanWorker(QThread):
    """Scan one project workspace without ever holding a write lock during the scan."""

    finished_scan = pyqtSignal(int, object)
    failed = pyqtSignal(int, object)

    def __init__(
        self,
        request_id: int,
        project_dir: str,
        incoming_subdir: str,
        results_subdir: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.request_id = request_id
        self.project_dir = project_dir
        self.incoming_subdir = incoming_subdir
        self.results_subdir = results_subdir
        # 诊断用：最近一次扫描实际回放了多少条写语句（稳态应为 0）。
        self.last_replayed_ops = 0

    # ── §7 旧实现（保留备查）──────────────────────────────────────────────
    # def run(self) -> None:
    #     db = None
    #     try:
    #         from app.db.db_manager import open_project_db_private
    #         from app.services.monitor_service import (
    #             build_attribution_context,
    #             scan_project,
    #         )
    #
    #         db = open_project_db_private(self.project_dir)
    #         attr = build_attribution_context(self.project_dir, db)
    #         result = scan_project(
    #             self.project_dir,
    #             db,
    #             attr=attr,
    #             incoming_subdir=self.incoming_subdir,
    #             results_subdir=self.results_subdir,
    #         )
    #         self.finished_scan.emit(self.request_id, result)
    #     except Exception as exc:  # noqa: BLE001
    #         self.failed.emit(self.request_id, exc)
    #     finally:
    #         if db is not None:
    #             try:
    #                 db.close()
    #             except Exception:
    #                 pass

    def run(self) -> None:
        try:
            result = self.scan_once()
            self.finished_scan.emit(self.request_id, result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.request_id, exc)

    # ── scan (thread body, kept Qt-free so it is unit-testable) ───────────
    def scan_once(self):
        """Run one scan: read-only scan → prune → single-transaction replay."""
        try:
            return self._run_readonly_scan()
        except sqlite3.Error as exc:
            # 老库缺表 / mode=ro 打不开 / 只读连接被拒 …… → 回落到旧逻辑，
            # 行为与修改前完全一致（并让降级可观测，而不是静默）。
            log.warning(
                "monitor scan read-only path unavailable (%s); "
                "falling back to the legacy read-write scan", exc,
            )
            return self._run_legacy_scan()

    def _run_readonly_scan(self):
        from app.services.monitor_service import (
            build_attribution_context,
            scan_project,
        )

        ro = _open_readonly(self.project_dir)
        try:
            proxy = _RecordingConnection(ro)
            attr = build_attribution_context(self.project_dir, proxy)
            result = scan_project(
                self.project_dir,
                proxy,
                attr=attr,
                incoming_subdir=self.incoming_subdir,
                results_subdir=self.results_subdir,
            )
            pending = prune_noop_ops(ro, proxy.ops)
        finally:
            try:
                ro.close()
            except Exception:  # noqa: BLE001
                pass

        # 稳态：pending 为空 ⇒ 不开可写连接 ⇒ 主线程的 autosave 完全不会被挡。
        self.last_replayed_ops = replay_ops(self.project_dir, pending)
        return result

    def _run_legacy_scan(self):
        """Old behaviour verbatim: private RW connection, writes inside scan_project."""
        from app.db.db_manager import open_project_db_private
        from app.services.monitor_service import (
            build_attribution_context,
            scan_project,
        )

        db = None
        try:
            db = open_project_db_private(self.project_dir)
            attr = build_attribution_context(self.project_dir, db)
            return scan_project(
                self.project_dir,
                db,
                attr=attr,
                incoming_subdir=self.incoming_subdir,
                results_subdir=self.results_subdir,
            )
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:  # noqa: BLE001
                    pass
