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


class TestParseResultTiffName:
    """权威解析器接管 (PROJECT_MEMORY: 禁止 split('-') 猜段):
    标准 7 段与旧手搓逐字节一致; legacy 无站位 6 段新增支持。"""

    def test_standard_seven_segments_matches_legacy_behavior(self):
        from app.services.organize_service import _parse_result_tiff_name
        assert _parse_result_tiff_name("FJ-XM-B2-DLC001-1-T95E-20260601.tif") == (
            "FJ-XM-B2-DLC001-T95E-20260601", 1,
        )

    def test_legacy_six_segments_no_station(self):
        from app.services.organize_service import _parse_result_tiff_name
        assert _parse_result_tiff_name("GXFCG-BLW-BZC003-R-10-20260618.tif") == (
            "GXFCG-BLW-BZC003-R-20260618", 10,
        )

    def test_bare_uid_without_sequence_is_none(self):
        from app.services.organize_service import _parse_result_tiff_name
        assert _parse_result_tiff_name("FJ-XM-B2-DLC001-T95E-20260601.tif") is None
        assert _parse_result_tiff_name("GXFCG-BLW-BZC003-R-20260618.tif") is None

    def test_unrelated_name_is_none(self):
        from app.services.organize_service import _parse_result_tiff_name
        assert _parse_result_tiff_name("IMG_1234.tif") is None

    def test_uid_wrapper_keeps_signature(self):
        from app.services.organize_service import _parse_uid_from_tiff_name
        assert _parse_uid_from_tiff_name("GXFCG-BLW-BZC003-R-1-20260618.tif") == (
            "GXFCG-BLW-BZC003-R-20260618"
        )
        assert _parse_uid_from_tiff_name("random.tif") is None


class TestMaxSeqLegacyUid:
    """老式无站位编号的成果序号必须被磁盘扫描认出, 否则序号从 0 重算 → 覆盖已有成片。

    场景(用户 2026-07-11 确认修): 老编号 GXFCG-BLW-BZC003-R-20260618(5 段, 无站位),
    已拍过 -1-/-2-/-3- 三张成片(文件名 6 段)。再合成整理时:
    §7 旧 _max_seq_for_uid_on_disk 用 split('-') 要求 >=7 段 → 6 段全被跳过 →
    disk_max=0 → 新成片命名 -1- → **直接覆盖已有第 1 张**, 用户数据静默丢失。
    """

    def _write(self, d, name):
        (d / name).write_bytes(b"tif")

    def test_legacy_no_station_uid_sees_existing_results(self, tmp_path):
        from app.services.organize_service import _max_seq_for_uid_on_disk

        uid = "GXFCG-BLW-BZC003-R-20260618"          # 5 段, 无站位
        for seq in (1, 2, 3):
            self._write(tmp_path, f"GXFCG-BLW-BZC003-R-{seq}-20260618.tif")

        assert _max_seq_for_uid_on_disk(uid, str(tmp_path)) == 3, (
            "老式无站位编号的已有成片必须被扫到, 否则新成片会覆盖第 1 张"
        )

    def test_standard_7_segment_uid_still_works(self, tmp_path):
        """回归: 标准 7 段(有站位)编号的扫描行为不变。"""
        from app.services.organize_service import _max_seq_for_uid_on_disk

        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        for seq in (1, 2):
            self._write(tmp_path, f"FJ-XM-B2-DLC001-{seq}-T95E-20260601.tif")

        assert _max_seq_for_uid_on_disk(uid, str(tmp_path)) == 2

    def test_other_uids_results_are_not_counted(self, tmp_path):
        """别的编号的成片不得算进本编号的序号。"""
        from app.services.organize_service import _max_seq_for_uid_on_disk

        uid = "GXFCG-BLW-BZC003-R-20260618"
        self._write(tmp_path, "GXFCG-BLW-BZC003-R-1-20260618.tif")     # 本编号
        self._write(tmp_path, "GXFCG-BLW-OTHER1-R-9-20260618.tif")     # 别的编号
        assert _max_seq_for_uid_on_disk(uid, str(tmp_path)) == 1

    def test_multi_digit_sequence(self, tmp_path):
        from app.services.organize_service import _max_seq_for_uid_on_disk

        uid = "GXFCG-BLW-BZC003-R-20260618"
        self._write(tmp_path, "GXFCG-BLW-BZC003-R-10-20260618.tif")
        assert _max_seq_for_uid_on_disk(uid, str(tmp_path)) == 10


class TestListUnnumberedResultTiffs:
    """列出「命名不规范、算不进序号」的成片(用户 2026-07-11 要求主动提醒)。

    这些文件不参与序号计算, 是撞号覆盖的风险源, 但用户看不见它们。
    """

    def test_lists_only_unparsable_names(self, tmp_path):
        from app.services.organize_service import list_unnumbered_result_tiffs

        # 规范成片(标准 7 段 + legacy 6 段) → 不算不规范
        (tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif").write_bytes(b"a")
        (tmp_path / "GXFCG-BLW-BZC003-R-2-20260618.tif").write_bytes(b"b")
        # 不规范:外部软件随手命名 / 手改过名 / 裸 uid 无序号
        (tmp_path / "IMG_1234.tif").write_bytes(b"c")
        (tmp_path / "扫描件-最终版.tiff").write_bytes(b"d")
        (tmp_path / "FJ-XM-B2-DLC001-T95E-20260601.tif").write_bytes(b"e")  # 无序号
        # 非 TIFF 不算
        (tmp_path / "note.txt").write_text("x")

        bad = list_unnumbered_result_tiffs(str(tmp_path))
        assert sorted(bad) == sorted([
            "FJ-XM-B2-DLC001-T95E-20260601.tif",
            "IMG_1234.tif",
            "扫描件-最终版.tiff",
        ])

    def test_all_standard_names_returns_empty(self, tmp_path):
        from app.services.organize_service import list_unnumbered_result_tiffs

        (tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif").write_bytes(b"a")
        assert list_unnumbered_result_tiffs(str(tmp_path)) == []

    def test_missing_dir_is_tolerated(self, tmp_path):
        from app.services.organize_service import list_unnumbered_result_tiffs

        assert list_unnumbered_result_tiffs(str(tmp_path / "nope")) == []
