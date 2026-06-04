"""test_retroactive_service.py — Tests for retroactive organize scan logic."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest


def _make_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "project.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS seen_files (name TEXT PRIMARY KEY, first_seen_at TEXT);
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY, is_active INTEGER DEFAULT 0,
            activated_at TEXT, last_organized_at TEXT,
            next_result_sequence_hint INTEGER, raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS grouping (
            uid TEXT, group_index INTEGER,
            angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
            status TEXT, source TEXT, created_at TEXT, updated_at TEXT,
            result_sequence INTEGER, archive_zip TEXT, retired_tiff_paths TEXT, raw_json TEXT,
            PRIMARY KEY (uid, group_index)
        );
    """)
    conn.commit()
    return conn


class TestRetroactiveScan:
    def test_scan_finds_named_tiffs(self, tmp_path):
        """scan_project_retroactive must return groups for each named TIFF."""
        from app.services.retroactive_service import scan_project_retroactive
        project_dir = str(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        incoming_dir = tmp_path / "incoming-jpg"
        incoming_dir.mkdir()
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        tiff_name = "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        (results_dir / tiff_name).write_bytes(b"TIFF")
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db)
        assert result["ok"] is True
        assert any(sp["uid"] == uid for sp in result["specimens"])
        db.close()

    def test_scan_finds_jpgs_before_tiff(self, tmp_path):
        """Scan must associate JPGs with the TIFF that was written after them."""
        from app.services.retroactive_service import scan_project_retroactive
        project_dir = str(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        incoming_dir = tmp_path / "incoming-jpg"
        incoming_dir.mkdir()
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        tiff_name = "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        # Create JPG first (earlier mtime)
        jpg_path = incoming_dir / "IMG_001.jpg"
        jpg_path.write_bytes(b"JFIF")
        time.sleep(0.05)
        # Then create TIFF (later mtime)
        (results_dir / tiff_name).write_bytes(b"TIFF")
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db)
        specimens = result["specimens"]
        assert specimens, "Must find at least one specimen"
        groups = specimens[0]["groups"]
        assert groups, "Must find at least one group"
        assert groups[0]["jpgCount"] >= 1
        db.close()

    def test_scan_empty_project(self, tmp_path):
        """scan_project_retroactive on empty project must return empty specimens."""
        from app.services.retroactive_service import scan_project_retroactive
        project_dir = str(tmp_path)
        (tmp_path / "results").mkdir()
        (tmp_path / "incoming-jpg").mkdir()
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db)
        assert result["ok"] is True
        assert result["specimens"] == []
        db.close()

    def test_scan_unnamed_tiffs(self, tmp_path):
        """TIFFs without valid 7-part naming must be in unnamedTiffs."""
        from app.services.retroactive_service import scan_project_retroactive
        project_dir = str(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (tmp_path / "incoming-jpg").mkdir()
        # Create a TIFF with non-standard name
        (results_dir / "some_random_photo.tif").write_bytes(b"TIFF")
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db)
        assert any(t["name"] == "some_random_photo.tif"
                   for t in result.get("unnamedTiffs", []))
        db.close()

    def test_scan_unassigned_jpgs(self, tmp_path):
        """JPG written AFTER the only TIFF must be in unassignedJpgs."""
        from app.services.retroactive_service import scan_project_retroactive
        import time
        project_dir = str(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        incoming_dir = tmp_path / "incoming-jpg"
        incoming_dir.mkdir()
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        tiff_name = "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
        # Create TIFF first
        (results_dir / tiff_name).write_bytes(b"TIFF")
        time.sleep(0.05)
        # Create JPG AFTER the TIFF — should be unassigned (no later TIFF)
        (incoming_dir / "late.jpg").write_bytes(b"JFIF")
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db)
        assert len(result.get("unassignedJpgs", [])) >= 1
        db.close()


class TestRetroactiveScanSubdir:
    """subdir parameter: scan only results/<subdir>/ when given."""

    def _setup_project(self, tmp_path):
        """Create project with two result subdirs: sub_a and sub_b."""
        project_dir = str(tmp_path)
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        (tmp_path / "incoming-jpg").mkdir()

        sub_a = results_dir / "sub_a"
        sub_b = results_dir / "sub_b"
        sub_a.mkdir()
        sub_b.mkdir()

        uid_a = "FJ-XM-B2-DLC001-T95E-20260601"
        uid_b = "FJ-XM-B2-DLC002-T95E-20260601"
        (sub_a / "FJ-XM-B2-DLC001-1-T95E-20260601.tif").write_bytes(b"TIFF-A")
        (sub_b / "FJ-XM-B2-DLC002-1-T95E-20260601.tif").write_bytes(b"TIFF-B")
        return project_dir, uid_a, uid_b

    def test_retroactive_with_subdir_only_scans_subdir(self, tmp_path):
        """subdir='sub_a' must return only specimens from results/sub_a/."""
        from app.services.retroactive_service import scan_project_retroactive
        project_dir, uid_a, uid_b = self._setup_project(tmp_path)
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db, subdir="sub_a")
        assert result["ok"] is True
        uids = [sp["uid"] for sp in result["specimens"]]
        assert uid_a in uids, "sub_a TIFF must appear"
        assert uid_b not in uids, "sub_b TIFF must NOT appear when subdir=sub_a"
        db.close()

    def test_retroactive_subdir_none_scans_all(self, tmp_path):
        """subdir=None must scan entire results/ including both subdirectories."""
        from app.services.retroactive_service import scan_project_retroactive
        project_dir, uid_a, uid_b = self._setup_project(tmp_path)
        db = _make_db(tmp_path)
        result = scan_project_retroactive(project_dir, db, subdir=None)
        assert result["ok"] is True
        uids = [sp["uid"] for sp in result["specimens"]]
        assert uid_a in uids, "sub_a TIFF must appear when subdir=None"
        assert uid_b in uids, "sub_b TIFF must appear when subdir=None"
        db.close()

    def test_retroactive_nonexistent_subdir_returns_empty(self, tmp_path):
        """subdir pointing to nonexistent dir must return empty specimens list."""
        from app.services.retroactive_service import scan_project_retroactive
        (tmp_path / "results").mkdir()
        (tmp_path / "incoming-jpg").mkdir()
        db = _make_db(tmp_path)
        result = scan_project_retroactive(str(tmp_path), db, subdir="ghost_dir")
        assert result["ok"] is True
        assert result["specimens"] == []
        db.close()
