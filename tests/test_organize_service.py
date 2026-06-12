"""test_organize_service.py — TDD tests for organize_service.

Tests:
  - next_result_sequence returns ≥ 1
  - Monotonic sequence allocation
  - organize_preview: sequence + suggested TIFF name
  - Gate: uid not active → OrganizeGateError
  - Gate: no groups (or groups < 2 jpg) → OrganizeGateError
  - build_result_basename: sequence inserted at position 4 (Oracle: server.js:3493-3497)
"""

import sqlite3
import os
import pytest

from app.services.organize_service import (
    next_result_sequence,
    organize_preview,
    build_result_basename,
    rename_tiff,
    OrganizeGateError,
    _check_organize_gate,
    _bump_seq_hint,
)


# ── rename_tiff：外部 TIFF 按编号成果名改名（同目录、冲突加序号、不覆盖） ─────────

class TestRenameTiff:
    def test_renames_in_same_dir(self, tmp_path):
        src = tmp_path / "HeliconFocus.tif"
        src.write_bytes(b"II*\x00")
        new = rename_tiff(str(src), "FJ-XM-B2-DLC001-1-T95E-20260601.tif")
        assert os.path.basename(new) == "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        assert os.path.isfile(new)
        assert not src.exists()

    def test_collision_appends_suffix(self, tmp_path):
        src = tmp_path / "ext.tif"; src.write_bytes(b"II*\x00")
        occupied = tmp_path / "T.tif"; occupied.write_bytes(b"xx")  # 别的文件占名
        new = rename_tiff(str(src), "T.tif")
        assert os.path.basename(new) == "T_1.tif"      # 不覆盖, 加序号
        assert occupied.read_bytes() == b"xx"          # 原占名文件没被动

    def test_same_name_noop(self, tmp_path):
        src = tmp_path / "keep.tif"; src.write_bytes(b"II*\x00")
        new = rename_tiff(str(src), "keep.tif")
        assert new == str(src) and src.exists()

    def test_missing_source_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rename_tiff(str(tmp_path / "nope.tif"), "x.tif")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0,
            activated_at TEXT,
            last_organized_at TEXT,
            next_result_sequence_hint INTEGER,
            raw_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT,
            composed_tiff_path TEXT,
            status TEXT, source TEXT,
            created_at TEXT, updated_at TEXT,
            result_sequence INTEGER,
            archive_zip TEXT, retired_tiff_paths TEXT,
            raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        )
    """)
    conn.commit()
    return conn


def _activate(db, uid):
    db.execute(
        "INSERT OR REPLACE INTO tasks (uid, is_active) VALUES (?, 1)",
        (uid,),
    )
    db.commit()


def _add_group(db, uid, group_index, jpg_paths):
    import json
    db.execute(
        """
        INSERT OR REPLACE INTO grouping
          (uid, group_index, angle_label, jpg_paths)
        VALUES (?, ?, ?, ?)
        """,
        (uid, group_index, "测试组", json.dumps(jpg_paths, ensure_ascii=False)),
    )
    db.commit()


# ── build_result_basename ─────────────────────────────────────────────────────

class TestBuildResultBasename:
    def test_inserts_seq_at_position_4(self):
        """Oracle: server.js:3493-3497 — splice(4, 0, seq)."""
        uid = "FJ-YGLZ-B2-DLC001-RD75E-20260506-0508"
        result = build_result_basename(uid, 1)
        assert result == "FJ-YGLZ-B2-DLC001-1-RD75E-20260506-0508"

    def test_seq_2(self):
        uid = "FJ-YGLZ-B2-DLC001-RD75E-20260506-0508"
        result = build_result_basename(uid, 2)
        assert result == "FJ-YGLZ-B2-DLC001-2-RD75E-20260506-0508"

    def test_different_uid(self):
        uid = "ZJ-NB-C1-HXB002-T95E-20260601"
        result = build_result_basename(uid, 3)
        parts = result.split("-")
        assert parts[4] == "3"

    def test_seq_always_at_index_4(self):
        uid = "A-B-C-D-E-F"  # 6 parts
        result = build_result_basename(uid, 5)
        parts = result.split("-")
        assert parts[4] == "5"
        assert parts[0] == "A"
        assert parts[1] == "B"
        assert parts[2] == "C"
        assert parts[3] == "D"


# ── next_result_sequence ──────────────────────────────────────────────────────

class TestNextResultSequence:
    def test_minimum_is_1(self):
        db = _db()
        seq = next_result_sequence(db, "SP_NEW")
        assert seq >= 1

    def test_uses_db_hint(self):
        db = _db()
        _bump_seq_hint(db, "SP1", 4)
        seq = next_result_sequence(db, "SP1")
        assert seq >= 5

    def test_bump_then_get(self):
        db = _db()
        _bump_seq_hint(db, "SP2", 2)
        seq = next_result_sequence(db, "SP2")
        assert seq >= 3

    def test_monotonically_increases_with_bumps(self):
        db = _db()
        uid = "SP_MONO"
        _bump_seq_hint(db, uid, 0)
        s1 = next_result_sequence(db, uid)
        _bump_seq_hint(db, uid, s1)
        s2 = next_result_sequence(db, uid)
        assert s2 > s1

    def test_hint_not_decremented_by_bump(self):
        """_bump_seq_hint only advances, never retreats."""
        db = _db()
        uid = "SP_NODEC"
        _bump_seq_hint(db, uid, 10)
        _bump_seq_hint(db, uid, 2)  # should be ignored (lower)
        seq = next_result_sequence(db, uid)
        assert seq >= 11


# ── organize_preview ──────────────────────────────────────────────────────────

class TestOrganizePreview:
    def test_returns_seq_and_tiff_name(self, tmp_path):
        db = _db()
        uid = "FJ-YGLZ-B2-DLC001-RD75E-20260506-0508"
        preview = organize_preview(db, uid,
                                   results_dir=str(tmp_path),
                                   incoming_dir=str(tmp_path))
        assert preview.next_seq >= 1
        assert preview.suggested_tiff_name.endswith(".tif")
        assert str(preview.next_seq) in preview.suggested_tiff_name

    def test_suggested_name_uses_build_result_basename(self, tmp_path):
        db = _db()
        uid = "FJ-YGLZ-B2-DLC001-RD75E-20260506-0508"
        _bump_seq_hint(db, uid, 2)
        preview = organize_preview(db, uid,
                                   results_dir=str(tmp_path),
                                   incoming_dir=str(tmp_path))
        expected_basename = build_result_basename(uid, preview.next_seq)
        assert preview.suggested_tiff_name == expected_basename + ".tif"

    def test_empty_uid_raises(self, tmp_path):
        db = _db()
        with pytest.raises((ValueError, Exception)):
            organize_preview(db, "", results_dir=str(tmp_path))

    def test_includes_groups_from_db(self, tmp_path):
        db = _db()
        uid = "FJ-YGLZ-B2-DLC001-RD75E-20260506"
        _add_group(db, uid, 1, ["/p/a.jpg", "/p/b.jpg"])
        preview = organize_preview(db, uid)
        assert len(preview.groups) == 1

    def test_warns_when_no_groups(self, tmp_path):
        db = _db()
        uid = "FJ-YGLZ-B2-DLC001-RD75E-20260506"
        preview = organize_preview(db, uid)
        assert any("分组" in w or "隐式" in w or "无" in w for w in preview.warnings)


# ── _check_organize_gate ──────────────────────────────────────────────────────

class TestOrganizeGate:
    def test_inactive_uid_raises(self):
        db = _db()
        uid = "SP_INACTIVE"
        groups = [{"jpgPaths": ["/a.jpg", "/b.jpg"]}]
        with pytest.raises(OrganizeGateError, match="激活"):
            _check_organize_gate(db, uid, groups, allow_inactive=False)

    def test_active_uid_passes(self):
        db = _db()
        uid = "SP_ACTIVE"
        _activate(db, uid)
        groups = [{"jpgPaths": ["/a.jpg", "/b.jpg"]}]
        # Should not raise
        _check_organize_gate(db, uid, groups, allow_inactive=False)

    def test_allow_inactive_bypasses_active_check(self):
        db = _db()
        uid = "SP_INACTIVE2"
        groups = [{"jpgPaths": ["/a.jpg", "/b.jpg"]}]
        # Should not raise when allow_inactive=True
        _check_organize_gate(db, uid, groups, allow_inactive=True)

    def test_no_groups_raises(self):
        db = _db()
        uid = "SP_NOGROUPS"
        _activate(db, uid)
        with pytest.raises(OrganizeGateError, match="分组|照片"):
            _check_organize_gate(db, uid, [], allow_inactive=False)

    def test_group_with_only_one_jpg_raises(self):
        """Groups with < 2 JPGs must fail the gate."""
        db = _db()
        uid = "SP_ONEJPG"
        _activate(db, uid)
        groups = [{"jpgPaths": ["/only_one.jpg"]}]
        with pytest.raises(OrganizeGateError):
            _check_organize_gate(db, uid, groups, allow_inactive=False)

    def test_group_with_two_jpgs_passes(self):
        db = _db()
        uid = "SP_TWOJPG"
        _activate(db, uid)
        groups = [{"jpgPaths": ["/a.jpg", "/b.jpg"]}]
        # Should not raise
        _check_organize_gate(db, uid, groups, allow_inactive=False)

    def test_empty_uid_raises(self):
        db = _db()
        with pytest.raises((OrganizeGateError, ValueError)):
            _check_organize_gate(db, "", [], allow_inactive=True)
