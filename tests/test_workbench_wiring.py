"""test_workbench_wiring.py — Tests for WorkbenchView logic that requires
real filesystem and DB (no Qt window needed for service-layer tests).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


def _make_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS specimens (
            uid TEXT PRIMARY KEY, id TEXT, province TEXT, site TEXT, station TEXT,
            storage TEXT, collection_date TEXT, photo_date TEXT,
            scientific_name TEXT, scientific_name_cn TEXT,
            taxon_group TEXT, taxon_group_cn TEXT, order_name TEXT, order_cn TEXT,
            family TEXT, family_cn TEXT, genus TEXT, genus_cn TEXT,
            lon REAL, lat REAL, geo_area TEXT, collector TEXT, photographer TEXT,
            identifier TEXT, notes TEXT, photo_notes TEXT, angle TEXT,
            metadata INTEGER DEFAULT 0, pinned INTEGER DEFAULT 0,
            owner_project_dir TEXT, raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0, activated_at TEXT,
            last_organized_at TEXT, next_result_sequence_hint INTEGER, raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
            status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            result_sequence INTEGER, archive_zip TEXT, retired_tiff_paths TEXT, raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        );
        CREATE TABLE IF NOT EXISTS explicit_unassigns (
            path TEXT PRIMARY KEY, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS seen_files (
            name TEXT PRIMARY KEY, first_seen_at TEXT
        );
    """)
    conn.commit()
    return conn


class TestSequenceNamingOnCompose:
    def test_organize_preview_names_first_tiff(self, tmp_path):
        """organize_preview must return seq=1 for a fresh uid with no existing TIFFs."""
        from app.services.organize_service import organize_preview
        db_path = str(tmp_path / "project.db")
        db = _make_db(db_path)
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        prev = organize_preview(db, uid, results_dir=results_dir)
        assert prev.next_seq == 1
        assert prev.suggested_tiff_name == "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        db.close()

    def test_organize_preview_increments_seq(self, tmp_path):
        """organize_preview must return seq=2 when seq-1 TIFF already exists."""
        from app.services.organize_service import organize_preview
        db_path = str(tmp_path / "project.db")
        db = _make_db(db_path)
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        # Create the seq-1 TIFF
        tiff1 = os.path.join(results_dir, "FJ-XM-B2-DLC001-1-T95E-20260601.tif")
        Path(tiff1).write_bytes(b"TIFF")
        prev = organize_preview(db, uid, results_dir=results_dir)
        assert prev.next_seq == 2
        assert prev.suggested_tiff_name == "FJ-XM-B2-DLC001-2-T95E-20260601.tif"
        db.close()


class TestSeqHintBump:
    def test_bump_seq_hint_updates_db(self, tmp_path):
        """_bump_seq_hint must advance next_result_sequence_hint."""
        from app.services.organize_service import _bump_seq_hint
        db_path = str(tmp_path / "project.db")
        db = _make_db(db_path)
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        _bump_seq_hint(db, uid, 1)
        row = db.execute(
            "SELECT next_result_sequence_hint FROM tasks WHERE uid = ?", (uid,)
        ).fetchone()
        assert row is not None
        assert row[0] == 2  # 1 + 1
        db.close()


class TestFreeCompose:
    def test_free_compose_names_output_in_incoming(self, tmp_path):
        """Free compose output basename must start with '自由合成-' if no name given."""
        incoming_dir = str(tmp_path / "incoming-jpg")
        os.makedirs(incoming_dir)
        from app.views.workbench_view import _free_compose_output_name
        name1 = _free_compose_output_name(incoming_dir, None)
        assert name1.startswith("自由合成-")
        assert name1.endswith(".tif")
        # Create first file to test increment
        Path(os.path.join(incoming_dir, name1)).write_bytes(b"X")
        name2 = _free_compose_output_name(incoming_dir, None)
        assert name2 != name1

    def test_free_compose_user_name(self, tmp_path):
        """User-provided name must be used (sanitized)."""
        incoming_dir = str(tmp_path / "incoming-jpg")
        os.makedirs(incoming_dir)
        from app.views.workbench_view import _free_compose_output_name
        name = _free_compose_output_name(incoming_dir, "my output")
        assert name == "my output.tif"

    def test_free_compose_user_name_conflict_falls_back(self, tmp_path):
        """If user name already exists, fall back to auto-naming."""
        incoming_dir = str(tmp_path / "incoming-jpg")
        os.makedirs(incoming_dir)
        Path(os.path.join(incoming_dir, "my output.tif")).write_bytes(b"X")
        from app.views.workbench_view import _free_compose_output_name
        # When user name conflicts, try the same name again → auto
        name = _free_compose_output_name(incoming_dir, "my output")
        # Falls back to 自由合成-1.tif since user_name conflicts
        assert name.startswith("自由合成-")


# ── Collision guard on organise  #cursor ─────────────────────────────────────

class TestOrganiseCollisionGuard:
    """archive_service does not overwrite silently — workbench checks for ZIP collision."""

    def test_archive_group_creates_zip(self, tmp_path):
        """archive_group must create a ZIP next to the TIFF (cjxl fallback path)."""
        from unittest.mock import patch
        from app.services.archive_service import archive_group
        jpg1 = tmp_path / "IMG_001.jpg"
        jpg2 = tmp_path / "IMG_002.jpg"
        jpg1.write_bytes(b"\xff\xd8\xff" * 100)
        jpg2.write_bytes(b"\xff\xd8\xff" * 100)
        tiff = tmp_path / "result.tif"
        tiff.write_bytes(b"IIX" * 1000)
        # Patch cjxl as unavailable so archive falls back to storing original JPG
        with patch("app.services.archive_service._cjxl_available", False):
            result = archive_group(
                jpg_paths=[str(jpg1), str(jpg2)],
                tiff_path=str(tiff),
                project_dir=str(tmp_path),
                delete_jpg=False,
            )
        assert result.ok
        assert os.path.isfile(result.zip_path)
        zip_name = Path(result.zip_path).name
        assert zip_name == "result.zip"

    def test_organize_preview_second_seq_avoids_collision(self, tmp_path):
        """organize_preview must increment seq when seq-1 TIFF already present."""
        from app.services.organize_service import organize_preview
        db = _make_db(str(tmp_path / "project.db"))
        results_dir = str(tmp_path / "results")
        os.makedirs(results_dir)
        uid = "FJ-XM-B2-TST001-T95E-20260601"
        # Create existing seq-1 TIFF
        (Path(results_dir) / "FJ-XM-B2-TST001-1-T95E-20260601.tif").write_bytes(b"T")
        prev = organize_preview(db, uid, results_dir=results_dir)
        assert prev.next_seq == 2
        db.close()


# ── GroupingPanel delete / clear from wiring perspective  #cursor ────────────

class TestGroupingDeleteClearWiring:
    """Service-layer persistence after delete / clear group."""

    def test_clear_group_persists_via_save_grouping(self, tmp_path):
        from app.services.grouping_service import (
            Group, SpecimenGrouping, save_grouping, load_grouping,
        )
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-TST001-T95E-20260601"
        groups = [
            Group(group_index=0, jpg_paths=["/a.jpg", "/b.jpg"]),
        ]
        save_grouping(db, uid, groups, clean_phantoms=False)
        # Simulate clear_group
        loaded = load_grouping(db, uid)
        loaded.groups[0].jpg_paths = []
        save_grouping(db, uid, loaded.groups, clean_phantoms=False)
        reloaded = load_grouping(db, uid)
        assert reloaded.groups[0].jpg_paths == []
        db.close()

    def test_delete_group_persists_via_save_grouping(self, tmp_path):
        from app.services.grouping_service import (
            Group, save_grouping, load_grouping,
        )
        db = _make_db(str(tmp_path / "project.db"))
        uid = "FJ-XM-B2-TST001-T95E-20260601"
        groups = [
            Group(group_index=0, jpg_paths=["/a.jpg"]),
            Group(group_index=1, jpg_paths=["/c.jpg"]),
        ]
        save_grouping(db, uid, groups, clean_phantoms=False)
        # Simulate delete group 0
        loaded = load_grouping(db, uid)
        loaded.groups = [g for g in loaded.groups if g.group_index != 0]
        save_grouping(db, uid, loaded.groups, clean_phantoms=False)
        reloaded = load_grouping(db, uid)
        assert len(reloaded.groups) == 1
        assert reloaded.groups[0].group_index == 1
        db.close()
