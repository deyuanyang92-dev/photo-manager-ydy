"""test_backup_service.py — local metadata snapshots (the recover-from-anything net).

Per-project model keeps the ONLY copy of project.db on whatever disk the user
chose (often a removable drive). backup_service silently snapshots that small
db (tens of KB) to the local user data dir so a dead/lost drive never means
lost collection records.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.services import backup_service as bs


@pytest.fixture
def project(tmp_path):
    """A real little workspace with 2 collection_records rows."""
    proj = tmp_path / "proj"
    (proj / "_data").mkdir(parents=True)
    db = sqlite3.connect(str(proj / "_data" / "project.db"))
    db.execute("CREATE TABLE collection_records (id INTEGER PRIMARY KEY, station TEXT)")
    db.execute("INSERT INTO collection_records (station) VALUES ('B1'),('B2')")
    db.commit()
    db.close()
    return proj


@pytest.fixture
def backup_root(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    monkeypatch.setattr(bs, "user_backup_root", lambda: root)
    return root


def test_snapshot_creates_copy_with_equal_rows(project, backup_root):
    out = bs.snapshot_project(str(project), now_tag="20260610-120000")
    assert out is not None and out.exists()
    con = sqlite3.connect(str(out))
    (n,) = con.execute("SELECT COUNT(*) FROM collection_records").fetchone()
    con.close()
    assert n == 2


def test_snapshot_offline_or_missing_returns_none(tmp_path, backup_root):
    assert bs.snapshot_project(str(tmp_path / "gone" / "proj"),
                               now_tag="20260610-120001") is None
    # Existing dir but no db → also None, and fabricates nothing.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert bs.snapshot_project(str(empty), now_tag="20260610-120002") is None
    assert not (empty / "_data").exists()


def test_prune_keeps_newest_n(project, backup_root):
    for i in range(13):
        bs.snapshot_project(str(project), keep=10, now_tag=f"20260610-1200{i:02d}")
    snaps = bs.list_snapshots(str(project))
    assert len(snaps) == 10
    # Newest survives, oldest pruned.
    names = [p.name for p in snaps]
    assert any("120012" in n for n in names)
    assert not any("120000" in n for n in names)


def test_snapshots_segregated_per_project(tmp_path, backup_root):
    for name in ("p1", "p2"):
        proj = tmp_path / name
        (proj / "_data").mkdir(parents=True)
        con = sqlite3.connect(str(proj / "_data" / "project.db"))
        con.execute("CREATE TABLE t (x)")
        con.commit(); con.close()
        bs.snapshot_project(str(proj), now_tag="20260610-130000")
    assert len(bs.list_snapshots(str(tmp_path / "p1"))) == 1
    assert len(bs.list_snapshots(str(tmp_path / "p2"))) == 1


def test_snapshot_projects_json(tmp_path, backup_root):
    src = tmp_path / "user_projects.json"
    src.write_text('{"version":1,"projects":[]}', encoding="utf-8")
    out = bs.snapshot_projects_json(str(src), now_tag="20260610-140000")
    assert out is not None and out.exists()
    assert out.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    # Missing source → None, no crash.
    assert bs.snapshot_projects_json(str(tmp_path / "nope.json"),
                                     now_tag="20260610-140001") is None
