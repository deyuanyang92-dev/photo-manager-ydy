"""Regression tests for MonitorScanWorker (P1 卡顿根因: 扫描 worker 与主线程抢写锁).

这些用例**直接调用 worker 的扫描体** (`MonitorScanWorker.scan_once`)，故意绕开
`workbench_monitor_workflow._refresh_monitor` 里的 offscreen 同步短路 —— 否则测试
走的是主线程同步路径，线上的 worker 路径根本没被覆盖。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from app.db.db_manager import open_project_db_private
from app.workers.monitor_scan_worker import (
    MonitorScanWorker,
    _open_readonly,
    _RecordingConnection,
    prune_noop_ops,
)


def _make_project(tmp_path: Path, jpgs=("a.jpg", "b.jpg")) -> Path:
    root = tmp_path / "proj"
    (root / "incoming-jpg").mkdir(parents=True)
    (root / "results").mkdir(parents=True)
    for name in jpgs:
        (root / "incoming-jpg" / name).write_bytes(b"\xff\xd8\xff" + name.encode())
    db = open_project_db_private(str(root), create=True)
    db.close()
    return root


def _worker(root: Path) -> MonitorScanWorker:
    return MonitorScanWorker(1, str(root), "incoming-jpg", "results")


def _rows(root: Path, sql: str):
    db = open_project_db_private(str(root))
    try:
        return [tuple(r) for r in db.execute(sql).fetchall()]
    finally:
        db.close()


# ── 核心回归：稳态扫描必须一条写语句都不发 ────────────────────────────────────

def test_steady_state_scan_performs_zero_writes(tmp_path):
    root = _make_project(tmp_path)
    w = _worker(root)

    first = w.scan_once()
    assert len(first.jpg_files) == 2
    assert w.last_replayed_ops > 0  # 首扫要把 seen_files / photo_files 落盘

    before = _rows(root, "SELECT file_id, size_bytes, mtime, last_seen_at FROM photo_files")
    photos_before = _rows(root, "SELECT photo_id, updated_at FROM photos")
    assert len(before) == 2

    time.sleep(0.01)
    second = w.scan_once()

    # 关键断言：文件系统没变 ⇒ worker 一条写语句都不回放 ⇒ 不开可写连接 ⇒
    # 主线程 autosave 不会被 SQLite 写锁挡住。
    assert w.last_replayed_ops == 0
    assert len(second.jpg_files) == 2
    # last_seen_at / updated_at 不再被每轮扫描无条件重写
    assert _rows(root, "SELECT file_id, size_bytes, mtime, last_seen_at FROM photo_files") == before
    assert _rows(root, "SELECT photo_id, updated_at FROM photos") == photos_before


def test_first_seen_at_is_still_persisted_and_stable(tmp_path):
    root = _make_project(tmp_path)
    w = _worker(root)

    first = w.scan_once()
    seen = dict(_rows(root, "SELECT name, first_seen_at FROM seen_files"))
    assert set(seen) == {"a.jpg", "b.jpg"}
    assert all(v for v in seen.values())

    second = w.scan_once()
    assert dict(_rows(root, "SELECT name, first_seen_at FROM seen_files")) == seen
    assert {f.name: f.first_seen_at for f in second.jpg_files} == \
           {f.name: f.first_seen_at for f in first.jpg_files}


def test_new_file_still_gets_written(tmp_path):
    root = _make_project(tmp_path)
    w = _worker(root)
    w.scan_once()
    assert w.last_replayed_ops > 0

    (root / "incoming-jpg" / "c.jpg").write_bytes(b"\xff\xd8\xffc")
    result = w.scan_once()

    assert w.last_replayed_ops > 0  # 真有增量时照常写
    assert len(result.jpg_files) == 3
    rels = {r[0] for r in _rows(root, "SELECT relative_path FROM photo_files")}
    assert any(r.endswith("c.jpg") for r in rels)
    assert len(rels) == 3


def test_changed_file_size_triggers_update(tmp_path):
    root = _make_project(tmp_path)
    w = _worker(root)
    w.scan_once()

    (root / "incoming-jpg" / "a.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 500)
    w.scan_once()

    sizes = dict(_rows(root, "SELECT relative_path, size_bytes FROM photo_files"))
    changed = [v for k, v in sizes.items() if k.endswith("a.jpg")]
    assert changed and changed[0] == 503


# ── 只读连接：物理上写不进去 ──────────────────────────────────────────────────

def test_scan_connection_is_physically_readonly(tmp_path):
    root = _make_project(tmp_path)
    ro = _open_readonly(str(root))
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO seen_files (name, first_seen_at) VALUES ('x','y')")
    finally:
        ro.close()


def test_recording_connection_records_writes_and_passes_reads(tmp_path):
    root = _make_project(tmp_path)
    ro = _open_readonly(str(root))
    try:
        proxy = _RecordingConnection(ro)
        with proxy:
            proxy.execute(
                "INSERT INTO seen_files (name, first_seen_at) VALUES (?, ?)",
                ("z.jpg", "t0"),
            )
        proxy.commit()
        assert len(proxy.ops) == 1
        # 写没有真的落到只读连接上
        assert proxy.execute("SELECT COUNT(*) FROM seen_files").fetchone()[0] == 0
    finally:
        ro.close()


# ── prune_noop_ops 单元测试 ───────────────────────────────────────────────────

def _mem_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE photo_files (file_id TEXT PRIMARY KEY, size_bytes INT, "
        "mtime TEXT, exists_on_disk INT, last_seen_at TEXT)"
    )
    db.execute(
        "INSERT INTO photo_files VALUES ('F1', 100, '2026-01-01T00:00:00+00:00', 1, 'old')"
    )
    db.commit()
    return db


_UPD_FILE = (
    "\n                    UPDATE photo_files\n"
    "                       SET size_bytes=?, mtime=?, exists_on_disk=1, last_seen_at=?\n"
    "                     WHERE file_id=?\n                    "
)
_TOUCH = "UPDATE photos SET updated_at=? WHERE photo_id=?"


def test_prune_drops_unchanged_row_update_and_its_touch():
    db = _mem_db()
    ops = [
        (_UPD_FILE, (100, "2026-01-01T00:00:00+00:00", "now", "F1"), False),
        (_TOUCH, ("now", "P1"), False),
    ]
    assert prune_noop_ops(db, ops) == []


def test_prune_keeps_changed_row_update_and_its_touch():
    db = _mem_db()
    ops = [
        (_UPD_FILE, (999, "2026-01-01T00:00:00+00:00", "now", "F1"), False),
        (_TOUCH, ("now", "P1"), False),
    ]
    assert len(prune_noop_ops(db, ops)) == 2


def test_prune_keeps_unknown_statements_verbatim():
    db = _mem_db()
    ops = [
        ("INSERT OR IGNORE INTO seen_files (name, first_seen_at) VALUES (?, ?)",
         [("a", "t")], True),
        ("UPDATE photos SET updated_at=? WHERE photo_id=?", ("now", "P9"), False),
    ]
    # 未被证明冗余的写一律保留（前面没有被丢弃的 photo_files 更新）
    assert prune_noop_ops(db, ops) == ops


def test_prune_drops_create_table_when_table_exists():
    db = _mem_db()
    ops = [
        ("CREATE TABLE IF NOT EXISTS photo_files (x INT)", (), False),
        ("CREATE TABLE IF NOT EXISTS brand_new (x INT)", (), False),
    ]
    kept = prune_noop_ops(db, ops)
    assert len(kept) == 1 and "brand_new" in kept[0][0]
