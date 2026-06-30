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
import threading
import time
import zipfile
from pathlib import Path

import pytest

from app.services.grouping_service import (
    ADHOC_GROUPING_UID,
    Group,
    SpecimenGrouping,
    add_explicit_unassign,
    archive_jpg_count,
    backfill_archive_zips,
    clear_group_tiff_link,
    clear_uid_mismatched_result_links,
    deduplicate_tiff_links,
    delete_grouping,
    get_explicit_unassigns,
    is_blank_draft_group,
    is_composed_group,
    load_grouping,
    merge_adhoc_groups_for_uid,
    registered_result_paths,
    remove_explicit_unassign,
    resolved_archive_zip,
    result_pair_candidates,
    result_path_key,
    save_grouping,
    uid_filename_mismatch,
    without_blank_draft_groups,
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


class TestDraftGroupFiltering:
    def test_blank_draft_group_detected(self):
        assert is_blank_draft_group(Group(group_index=0))

    def test_group_with_media_is_not_blank(self):
        assert not is_blank_draft_group(Group(group_index=0, jpg_paths=["/x/a.jpg"]))

    def test_group_with_result_state_is_not_blank(self):
        assert not is_blank_draft_group(Group(group_index=0, status="composed"))

    def test_without_blank_draft_groups_preserves_real_groups(self):
        real = Group(group_index=1, output_name="custom")
        assert without_blank_draft_groups([Group(group_index=0), real]) == [real]


class TestResultLinkRules:
    def test_archive_jpg_count_plain_zip(self, tmp_path):
        zip_path = tmp_path / "photos.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("a.jpg", b"a")
            zf.writestr("b.jpeg", b"b")
            zf.writestr("notes.txt", b"n")

        assert archive_jpg_count(str(zip_path)) == 2

    def test_archive_jpg_count_legacy_manifest_zip(self, tmp_path):
        zip_path = tmp_path / "legacy.zip"
        manifest = {"files": [{"archiveName": "a.jxl"}, {"archiveName": "b.jxl"}]}
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))

        assert archive_jpg_count(str(zip_path)) == 2

    def test_resolved_archive_zip_prefers_explicit_path(self, tmp_path):
        explicit = tmp_path / "explicit.zip"
        sibling = tmp_path / "result.zip"
        tiff = tmp_path / "result.tif"
        for path in (explicit, sibling, tiff):
            path.write_bytes(b"x")

        group = Group(
            group_index=0,
            composed_tiff_path=str(tiff),
            archive_zip=str(explicit),
        )

        assert resolved_archive_zip(group) == str(explicit)

    def test_resolved_archive_zip_falls_back_to_same_stem_zip(self, tmp_path):
        tiff = tmp_path / "result.tif"
        zip_path = tmp_path / "result.zip"
        tiff.write_bytes(b"t")
        zip_path.write_bytes(b"z")

        assert resolved_archive_zip(Group(group_index=0, composed_tiff_path=str(tiff))) == str(zip_path)

    def test_result_path_key_normalizes_casefolded_resolved_paths(self, tmp_path):
        path = tmp_path / "A.TIF"
        path.write_bytes(b"x")

        assert result_path_key(str(path)) == str(path.resolve()).casefold()

    def test_clear_group_tiff_link_preserves_jpg_membership(self):
        group = Group(
            group_index=0,
            jpg_paths=["/x/a.jpg"],
            composed_tiff_path="/x/result.tif",
            archive_zip="/x/result.zip",
            status="organized",
            source="external-tif",
            output_name="result",
            retired_tiff_paths=["/x/old.tif"],
        )

        clear_group_tiff_link(group)

        assert group.jpg_paths == ["/x/a.jpg"]
        assert group.composed_tiff_path is None
        assert group.archive_zip is None
        assert group.status == "pending"
        assert group.source is None
        assert group.output_name is None
        assert group.retired_tiff_paths == []

    def test_deduplicate_tiff_links_clears_later_duplicates(self, tmp_path):
        tiff = tmp_path / "same.tif"
        tiff.write_bytes(b"x")
        first = Group(group_index=0, composed_tiff_path=str(tiff), status="organized")
        second = Group(group_index=1, composed_tiff_path=str(tiff), status="organized")

        assert deduplicate_tiff_links([first, second]) is True
        assert first.composed_tiff_path == str(tiff)
        assert second.composed_tiff_path is None
        assert second.status is None

    def test_is_composed_group_uses_paths_or_state(self):
        assert is_composed_group(Group(group_index=0, composed_tiff_path="/x/a.tif"))
        assert is_composed_group(Group(group_index=0, archive_zip="/x/a.zip"))
        assert is_composed_group(Group(group_index=0, status="organized"))
        assert not is_composed_group(Group(group_index=0, jpg_paths=["/x/a.jpg"]))

    def test_result_pair_candidates_mark_registered_pairs(self, tmp_path):
        db = _db()
        results = tmp_path / "results"
        results.mkdir()
        used_tif = results / "used.tif"
        used_zip = results / "used.zip"
        free_tif = results / "free.tif"
        free_zip = results / "free.zip"
        for path in (used_tif, used_zip, free_tif, free_zip):
            path.write_bytes(path.suffix.encode())
        save_grouping(db, "USED-UID", [
            Group(
                group_index=0,
                composed_tiff_path=str(used_tif),
                archive_zip=str(used_zip),
                status="organized",
            )
        ], clean_phantoms=False)

        used = registered_result_paths(db)
        candidates = result_pair_candidates(results, used)

        by_stem = {c["stem"]: c for c in candidates}
        assert by_stem["used"]["associated"] is True
        assert by_stem["used"]["associated_uid"] == "USED-UID"
        assert by_stem["free"]["associated"] is False

    def test_uid_filename_mismatch_detects_sibling_specimen_code(self):
        uid = "FJ-XM-BZC003-T95E-20260601"

        assert uid_filename_mismatch(uid, "FJ-XM-BZC002-T95E-20260601.tif")
        assert not uid_filename_mismatch(uid, "FJ-XM-BZC003-T95E-20260601.tif")
        assert not uid_filename_mismatch(ADHOC_GROUPING_UID, "FJ-XM-BZC002.tif")

    def test_clear_uid_mismatched_result_links_removes_wrong_tiff_or_zip(self):
        uid = "FJ-XM-BZC003-T95E-20260601"
        wrong_tiff = Group(
            group_index=0,
            composed_tiff_path="/x/FJ-XM-BZC002-T95E-20260601.tif",
            archive_zip="/x/FJ-XM-BZC002-T95E-20260601.zip",
            status="organized",
        )
        wrong_zip = Group(
            group_index=1,
            composed_tiff_path="/x/FJ-XM-BZC003-T95E-20260601.tif",
            archive_zip="/x/FJ-XM-BZC002-T95E-20260601.zip",
            status="organized",
        )

        assert clear_uid_mismatched_result_links(uid, [wrong_tiff, wrong_zip]) is True
        assert wrong_tiff.composed_tiff_path is None
        assert wrong_tiff.archive_zip is None
        assert wrong_tiff.status is None
        assert wrong_zip.composed_tiff_path
        assert wrong_zip.archive_zip is None
        assert wrong_zip.status == "composed"

    def test_clear_uid_mismatch_preserves_imported_tif_only_group(self):
        uid = "FJ-XM-BZC003-T95E-20260601"
        imported = Group(
            group_index=0,
            jpg_paths=[],
            composed_tiff_path="/x/GXFCG-BLW-BZC002-R-20260618.tif",
            status="organized",
        )

        assert clear_uid_mismatched_result_links(uid, [imported]) is False
        assert imported.composed_tiff_path
        assert imported.status == "organized"


class TestMergeAdhocGroupsForUid:
    def test_empty_uid_returns_empty_plan(self):
        db = _db()
        assert merge_adhoc_groups_for_uid(db, "", [Group(group_index=0)]) == []

    def test_blank_adhoc_groups_are_ignored(self):
        db = _db()
        assert merge_adhoc_groups_for_uid(db, "SP1", [Group(group_index=0)]) == []

    def test_new_uid_uses_nonblank_groups(self):
        db = _db()
        group = Group(group_index=0, output_name="1")
        merged = merge_adhoc_groups_for_uid(db, "SP1", [Group(group_index=99), group])
        assert merged == [group]

    def test_existing_uid_appends_after_existing_groups(self):
        db = _db()
        existing = Group(group_index=2, angle_label="existing")
        save_grouping(db, "SP1", [existing], clean_phantoms=False)

        incoming = Group(group_index=0, output_name="new")
        merged = merge_adhoc_groups_for_uid(db, "SP1", [incoming])

        assert [g.group_index for g in merged] == [2, 3]
        assert merged[0].angle_label == "existing"
        assert merged[1] is incoming

    def test_delete_grouping_removes_rows(self):
        db = _db()
        save_grouping(
            db,
            ADHOC_GROUPING_UID,
            [Group(group_index=0, output_name="temp")],
            clean_phantoms=False,
        )
        delete_grouping(db, ADHOC_GROUPING_UID)
        assert load_grouping(db, ADHOC_GROUPING_UID).groups == []


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

    def test_save_retries_transient_database_lock(self):
        """A short-lived SQLite writer lock should not lose a grouping save."""
        db_path = Path(self.tmpdir) / "project.db"
        db = sqlite3.connect(str(db_path), timeout=0.05)
        db.row_factory = sqlite3.Row
        locker = sqlite3.connect(
            str(db_path),
            timeout=0.05,
            check_same_thread=False,
        )
        save_grouping(db, "INIT", [], clean_phantoms=False)
        locker.execute("BEGIN IMMEDIATE")

        def release_lock() -> None:
            time.sleep(0.2)
            locker.commit()
            locker.close()

        thread = threading.Thread(target=release_lock)
        thread.start()
        try:
            save_grouping(
                db,
                "SP_LOCK",
                [Group(group_index=0, angle_label="角度1")],
                clean_phantoms=False,
            )
        finally:
            thread.join(timeout=2.0)

        loaded = load_grouping(db, "SP_LOCK")
        assert [g.angle_label for g in loaded.groups] == ["角度1"]

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


# ── backfill_archive_zips ───────────────────────────────────────────────────

def _write(path: str, size: int) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * size)
    return path


def _save_group_with_tiff(db, uid, tiff_path, archive_zip=None):
    save_grouping(db, uid, [Group(
        group_index=1, angle_label="g", jpg_paths=[],
        composed_tiff_path=tiff_path, archive_zip=archive_zip,
    )])


class TestBackfillArchiveZips:
    """A result composed + compressed by an older build has its zip on disk
    (``results/<stem>.zip``) but, on a DB whose ``archive_zip`` was never
    recorded, shows '尚未压缩'. backfill recovers the pointer from disk —
    without recompressing, deleting, or fabricating anything.
    """

    def test_sets_archive_zip_from_sibling_zip(self, tmp_path):
        db = _db()
        results = tmp_path / "results"
        tiff = _write(str(results / "FJ-XM-B2-DLC004-1-T95E-20260602.tif"), 100)
        _write(str(results / "FJ-XM-B2-DLC004-1-T95E-20260602.zip"), 5000)
        _save_group_with_tiff(db, "U1", tiff)

        n = backfill_archive_zips(db)

        assert n == 1
        g = load_grouping(db, "U1").groups[0]
        assert g.archive_zip == str(results / "FJ-XM-B2-DLC004-1-T95E-20260602.zip")

    def test_no_sibling_zip_leaves_null(self, tmp_path):
        db = _db()
        tiff = _write(str(tmp_path / "results" / "X-1-T-20260602.tif"), 100)
        _save_group_with_tiff(db, "U1", tiff)

        n = backfill_archive_zips(db)

        assert n == 0
        assert load_grouping(db, "U1").groups[0].archive_zip is None

    def test_tiny_zip_ignored(self, tmp_path):
        # A <=32 byte zip is treated as absent (mirrors archive_service's gate).
        db = _db()
        results = tmp_path / "results"
        tiff = _write(str(results / "X-1-T-20260602.tif"), 100)
        _write(str(results / "X-1-T-20260602.zip"), 20)
        _save_group_with_tiff(db, "U1", tiff)

        assert backfill_archive_zips(db) == 0
        assert load_grouping(db, "U1").groups[0].archive_zip is None

    def test_existing_archive_zip_untouched(self, tmp_path):
        db = _db()
        results = tmp_path / "results"
        tiff = _write(str(results / "X-1-T-20260602.tif"), 100)
        _write(str(results / "X-1-T-20260602.zip"), 5000)
        _save_group_with_tiff(db, "U1", tiff, archive_zip="/already/set.zip")

        assert backfill_archive_zips(db) == 0
        assert load_grouping(db, "U1").groups[0].archive_zip == "/already/set.zip"

    def test_missing_tiff_on_disk_skipped(self, tmp_path):
        # composed_tiff_path points nowhere → never fabricate an archive pointer.
        db = _db()
        _save_group_with_tiff(db, "U1", str(tmp_path / "gone" / "X-1-T-20260602.tif"))

        assert backfill_archive_zips(db) == 0
        assert load_grouping(db, "U1").groups[0].archive_zip is None
