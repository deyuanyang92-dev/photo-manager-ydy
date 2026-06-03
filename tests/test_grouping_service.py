"""test_grouping_service.py — TDD tests for grouping_service.

Tests:
  - load_grouping: returns empty SpecimenGrouping for unknown uid
  - save_grouping: persists groups to DB
  - Phantom path cleanup: jpg_paths that don't exist on disk are removed
  - explicitUnassigns: add / remove / get
  - Round-trip: save → load preserves all fields
"""

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.services.grouping_service import (
    Group,
    SpecimenGrouping,
    add_explicit_unassign,
    get_explicit_unassigns,
    load_grouping,
    remove_explicit_unassign,
    save_grouping,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _make_file(directory: str, name: str) -> str:
    """Create a real file and return its path."""
    os.makedirs(directory, exist_ok=True)
    p = os.path.join(directory, name)
    with open(p, "wb") as f:
        f.write(b"\x00" * 10)
    return p


# ── load_grouping ─────────────────────────────────────────────────────────────

class TestLoadGrouping:
    def test_unknown_uid_returns_empty(self):
        db = _db()
        result = load_grouping(db, "UNKNOWN_UID")
        assert isinstance(result, SpecimenGrouping)
        assert result.uid == "UNKNOWN_UID"
        assert result.groups == []

    def test_creates_table_if_missing(self):
        """Should auto-create table without crashing."""
        db = _db()
        result = load_grouping(db, "SP1")
        assert result is not None


# ── save_grouping ─────────────────────────────────────────────────────────────

class TestSaveGrouping:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_save_and_load_basic(self):
        db = _db()
        uid = "FJ-XM-B1-DLC001-T95E-20260601"
        jpg1 = _make_file(self.tmpdir, "img001.jpg")
        jpg2 = _make_file(self.tmpdir, "img002.jpg")
        groups = [
            Group(
                group_index=1,
                angle_label="正面",
                jpg_paths=[jpg1, jpg2],
            )
        ]
        save_grouping(db, uid, groups)
        loaded = load_grouping(db, uid)
        assert len(loaded.groups) == 1
        assert loaded.groups[0].group_index == 1
        assert loaded.groups[0].angle_label == "正面"

    def test_save_replaces_existing(self):
        """Save twice → second save replaces first."""
        db = _db()
        uid = "SP_REPLACE"
        jpg1 = _make_file(self.tmpdir, "r1.jpg")
        jpg2 = _make_file(self.tmpdir, "r2.jpg")

        g1 = Group(group_index=1, angle_label="第一次", jpg_paths=[jpg1])
        save_grouping(db, uid, [g1])

        g2 = Group(group_index=1, angle_label="第二次", jpg_paths=[jpg2])
        save_grouping(db, uid, [g2])

        loaded = load_grouping(db, uid)
        assert len(loaded.groups) == 1
        assert loaded.groups[0].angle_label == "第二次"

    def test_multiple_groups_persisted(self):
        db = _db()
        uid = "SP_MULTI"
        jpg1 = _make_file(self.tmpdir, "m1.jpg")
        jpg2 = _make_file(self.tmpdir, "m2.jpg")
        jpg3 = _make_file(self.tmpdir, "m3.jpg")
        groups = [
            Group(group_index=1, angle_label="正面", jpg_paths=[jpg1, jpg2]),
            Group(group_index=2, angle_label="侧面", jpg_paths=[jpg3]),
        ]
        save_grouping(db, uid, groups)
        loaded = load_grouping(db, uid)
        assert len(loaded.groups) == 2

    def test_composed_tiff_path_persisted(self):
        db = _db()
        uid = "SP_COMPOSED"
        jpg = _make_file(self.tmpdir, "c1.jpg")
        g = Group(
            group_index=1,
            jpg_paths=[jpg],
            composed_tiff_path="/results/SP_COMPOSED-1.tif",
        )
        save_grouping(db, uid, [g])
        loaded = load_grouping(db, uid)
        assert loaded.groups[0].composed_tiff_path == "/results/SP_COMPOSED-1.tif"


# ── Phantom path cleanup ──────────────────────────────────────────────────────

class TestPhantomPathCleanup:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_phantom_paths_removed_on_save(self):
        """Paths that don't exist on disk are stripped before writing.

        Oracle: server.js grouping-tool save — phantom jpg cleanup.
        """
        db = _db()
        uid = "SP_PHANTOM"
        real_jpg = _make_file(self.tmpdir, "real.jpg")
        phantom_jpg = "/nonexistent/path/phantom.jpg"

        g = Group(group_index=1, jpg_paths=[real_jpg, phantom_jpg])
        save_grouping(db, uid, [g], clean_phantoms=True)

        loaded = load_grouping(db, uid)
        assert real_jpg in loaded.groups[0].jpg_paths
        assert phantom_jpg not in loaded.groups[0].jpg_paths

    def test_all_real_paths_survive(self):
        db = _db()
        uid = "SP_REAL"
        jpg1 = _make_file(self.tmpdir, "real1.jpg")
        jpg2 = _make_file(self.tmpdir, "real2.jpg")
        g = Group(group_index=1, jpg_paths=[jpg1, jpg2])
        save_grouping(db, uid, [g], clean_phantoms=True)

        loaded = load_grouping(db, uid)
        saved_paths = set(loaded.groups[0].jpg_paths)
        assert jpg1 in saved_paths
        assert jpg2 in saved_paths

    def test_all_phantom_paths_results_in_empty_group(self):
        db = _db()
        uid = "SP_ALL_PHANTOM"
        g = Group(group_index=1, jpg_paths=["/gone/a.jpg", "/gone/b.jpg"])
        save_grouping(db, uid, [g], clean_phantoms=True)

        loaded = load_grouping(db, uid)
        assert loaded.groups[0].jpg_paths == []

    def test_clean_phantoms_false_preserves_paths(self):
        """When clean_phantoms=False, phantom paths are preserved."""
        db = _db()
        uid = "SP_KEEP"
        phantom = "/nonexistent/phantom.jpg"
        g = Group(group_index=1, jpg_paths=[phantom])
        save_grouping(db, uid, [g], clean_phantoms=False)

        loaded = load_grouping(db, uid)
        assert phantom in loaded.groups[0].jpg_paths


# ── explicitUnassigns ─────────────────────────────────────────────────────────

class TestExplicitUnassigns:
    def test_add_and_get(self, tmp_path):
        db = _db()
        p = str(tmp_path / "img001.jpg")
        add_explicit_unassign(db, p)
        unassigns = get_explicit_unassigns(db)
        resolved = str(Path(p).resolve())
        assert resolved in unassigns

    def test_remove_unassign(self, tmp_path):
        db = _db()
        p = str(tmp_path / "img002.jpg")
        add_explicit_unassign(db, p)
        remove_explicit_unassign(db, p)
        unassigns = get_explicit_unassigns(db)
        resolved = str(Path(p).resolve())
        assert resolved not in unassigns

    def test_empty_db_returns_empty_set(self):
        db = _db()
        unassigns = get_explicit_unassigns(db)
        assert unassigns == set()

    def test_idempotent_add(self, tmp_path):
        """Adding same path twice → only one record."""
        db = _db()
        p = str(tmp_path / "img003.jpg")
        add_explicit_unassign(db, p)
        add_explicit_unassign(db, p)  # duplicate → ignored
        unassigns = get_explicit_unassigns(db)
        resolved = str(Path(p).resolve())
        matching = [u for u in unassigns if u == resolved]
        assert len(matching) == 1

    def test_paths_stored_as_resolved_absolute(self, tmp_path):
        """Paths are stored resolved/absolute (canonical form)."""
        db = _db()
        p = str(tmp_path / "sub" / ".." / "img.jpg")  # non-canonical
        add_explicit_unassign(db, p)
        unassigns = get_explicit_unassigns(db)
        # Should contain the resolved canonical form
        resolved = str(Path(p).resolve())
        assert resolved in unassigns

    def test_multiple_paths(self, tmp_path):
        db = _db()
        paths = [str(tmp_path / f"img{i:03d}.jpg") for i in range(3)]
        for p in paths:
            add_explicit_unassign(db, p)
        unassigns = get_explicit_unassigns(db)
        for p in paths:
            assert str(Path(p).resolve()) in unassigns
