"""test_monitor_service.py — Tests for is_grouped marking in scan_project.

Task 1-D: scan_project marks FileEntry.is_grouped=True for JPGs that appear
in the grouping table (specimen_uid IS NOT NULL).
"""

import json
import os
import sqlite3
import tempfile
import shutil
from pathlib import Path

import pytest

from app.services.monitor_service import (
    FileEntry,
    ScanResult,
    scan_project,
    ensure_seen_files_table,
)
from app.services.grouping_service import (
    Group,
    save_grouping,
    _ensure_grouping_table,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestScanMarksGroupedJpg:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ensure_seen_files_table(self.db)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_jpg(self, name: str) -> str:
        d = os.path.join(self.tmpdir, "incoming-jpg")
        os.makedirs(d, exist_ok=True)
        full = os.path.join(d, name)
        with open(full, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        return full

    def test_ungrouped_jpg_is_not_grouped(self):
        """JPG not in grouping table → is_grouped=False."""
        self._make_jpg("solo.jpg")
        result = scan_project(self.tmpdir, self.db)
        found = next(f for f in result.jpg_files if f.name == "solo.jpg")
        assert found.is_grouped is False

    def test_scan_marks_grouped_jpg(self):
        """JPG path in grouping table (uid set) → is_grouped=True."""
        jpg_path = self._make_jpg("grouped.jpg")
        # Insert grouping row with this jpg_path
        _ensure_grouping_table(self.db)
        group = Group(
            group_index=0,
            angle_label="A",
            jpg_paths=[jpg_path],
            status="confirmed",
        )
        save_grouping(self.db, "UID-001", [group], clean_phantoms=False)

        result = scan_project(self.tmpdir, self.db)
        found = next(f for f in result.jpg_files if f.name == "grouped.jpg")
        assert found.is_grouped is True

    def test_only_grouped_jpg_marked_not_others(self):
        """Only the jpg_path in grouping gets is_grouped=True; others remain False."""
        jpg_grouped = self._make_jpg("in_group.jpg")
        self._make_jpg("not_in_group.jpg")

        _ensure_grouping_table(self.db)
        group = Group(
            group_index=0,
            angle_label="A",
            jpg_paths=[jpg_grouped],
            status="confirmed",
        )
        save_grouping(self.db, "UID-002", [group], clean_phantoms=False)

        result = scan_project(self.tmpdir, self.db)
        grouped = next(f for f in result.jpg_files if f.name == "in_group.jpg")
        solo = next(f for f in result.jpg_files if f.name == "not_in_group.jpg")
        assert grouped.is_grouped is True
        assert solo.is_grouped is False

    def test_null_uid_row_does_not_mark_grouped(self):
        """Rows with uid=NULL (empty grouping) should not mark is_grouped."""
        jpg_path = self._make_jpg("null_uid.jpg")
        _ensure_grouping_table(self.db)
        # Insert a row with explicit NULL uid via raw SQL to test the edge case
        self.db.execute(
            "INSERT INTO grouping (uid, group_index, jpg_paths, angle_label) VALUES (NULL, 0, ?, '')",
            (json.dumps([jpg_path]),)
        )
        self.db.commit()

        result = scan_project(self.tmpdir, self.db)
        found = next(f for f in result.jpg_files if f.name == "null_uid.jpg")
        assert found.is_grouped is False

    def test_is_grouped_field_exists_on_file_entry(self):
        """FileEntry must have an is_grouped attribute (default False)."""
        entry = FileEntry(
            name="test.jpg",
            path="/tmp/test.jpg",
            kind="jpg",
            size=100,
            mtime="2026-01-01T00:00:00+00:00",
        )
        assert hasattr(entry, "is_grouped")
        assert entry.is_grouped is False
