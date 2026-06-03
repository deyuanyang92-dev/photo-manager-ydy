"""Tests for app/services/project_service.py — TDD.

Covers:
- create_project: returns dict, creates dirs
- open_project: returns dict, registers root in default_registry
- list_projects: reads user_projects.json
- get_incoming_jpg_dir: modern name / legacy fallback
- get_results_dir
- ensure_project_dirs: creates incoming-jpg / results / _data
"""
import json
import os
import tempfile
from pathlib import Path

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────

def _write_projects_json(path: Path, projects: list) -> None:
    path.write_text(json.dumps({"version": 1, "projects": projects}), encoding="utf-8")


# ── create_project ─────────────────────────────────────────────────────────

class TestCreateProject:
    def test_returns_dict(self, tmp_path):
        from app.services.project_service import create_project
        result = create_project("Test Project", str(tmp_path / "proj"))
        assert isinstance(result, dict)

    def test_has_name_and_dir(self, tmp_path):
        from app.services.project_service import create_project
        proj_dir = str(tmp_path / "proj")
        result = create_project("My Specimens", proj_dir)
        assert result.get("name") == "My Specimens"
        assert "dir" in result or "directory" in result

    def test_creates_subdirs(self, tmp_path):
        from app.services.project_service import create_project
        proj_dir = tmp_path / "proj"
        create_project("Test", str(proj_dir))
        assert (proj_dir / "incoming-jpg").exists()
        assert (proj_dir / "results").exists()
        assert (proj_dir / "_data").exists()

    def test_result_has_id(self, tmp_path):
        from app.services.project_service import create_project
        result = create_project("T", str(tmp_path / "p"))
        assert "id" in result


# ── open_project ───────────────────────────────────────────────────────────

class TestOpenProject:
    def test_returns_dict(self, tmp_path):
        from app.services.project_service import open_project
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        result = open_project(str(proj_dir))
        assert isinstance(result, dict)

    def test_registers_root_in_default_registry(self, tmp_path):
        """Opening a project must register its dir in the default SafePathRegistry."""
        from app.services.project_service import open_project
        from app.utils.path_utils import default_registry
        proj_dir = tmp_path / "proj_open"
        proj_dir.mkdir()
        open_project(str(proj_dir))
        # After open, a child path must pass assert_safe
        child = str(proj_dir / "results" / "file.tif")
        # Should not raise
        default_registry.assert_safe(child)

    def test_creates_subdirs_if_missing(self, tmp_path):
        from app.services.project_service import open_project
        proj_dir = tmp_path / "new_proj"
        proj_dir.mkdir()
        open_project(str(proj_dir))
        assert (proj_dir / "incoming-jpg").exists()
        assert (proj_dir / "results").exists()

    def test_has_dir_key(self, tmp_path):
        from app.services.project_service import open_project
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        result = open_project(str(proj_dir))
        assert "dir" in result or "directory" in result


# ── list_projects ──────────────────────────────────────────────────────────

class TestListProjects:
    def test_returns_list(self, tmp_path):
        from app.services.project_service import list_projects
        json_path = tmp_path / "user_projects.json"
        _write_projects_json(json_path, [])
        result = list_projects(str(json_path))
        assert isinstance(result, list)

    def test_returns_projects(self, tmp_path):
        from app.services.project_service import list_projects
        json_path = tmp_path / "user_projects.json"
        _write_projects_json(json_path, [
            {"id": "1", "name": "Proj A", "directory": "/tmp/a"},
            {"id": "2", "name": "Proj B", "directory": "/tmp/b"},
        ])
        result = list_projects(str(json_path))
        assert len(result) == 2

    def test_missing_file_returns_empty(self, tmp_path):
        from app.services.project_service import list_projects
        result = list_projects(str(tmp_path / "nonexistent.json"))
        assert result == []

    def test_empty_file_returns_empty(self, tmp_path):
        from app.services.project_service import list_projects
        json_path = tmp_path / "user_projects.json"
        _write_projects_json(json_path, [])
        result = list_projects(str(json_path))
        assert result == []


# ── get_incoming_jpg_dir ───────────────────────────────────────────────────

class TestGetIncomingJpgDir:
    def test_modern_dir_when_present(self, tmp_path):
        """Returns 'incoming-jpg' dir when it exists."""
        from app.services.project_service import get_incoming_jpg_dir
        proj_dir = tmp_path / "proj"
        modern = proj_dir / "incoming-jpg"
        modern.mkdir(parents=True)
        result = get_incoming_jpg_dir(str(proj_dir))
        assert Path(result).name == "incoming-jpg"

    def test_legacy_fallback_when_modern_missing(self, tmp_path):
        """Falls back to '新拍JPG' when 'incoming-jpg' does not exist but legacy does."""
        from app.services.project_service import get_incoming_jpg_dir
        proj_dir = tmp_path / "proj"
        legacy = proj_dir / "新拍JPG"
        legacy.mkdir(parents=True)
        result = get_incoming_jpg_dir(str(proj_dir))
        assert Path(result).name == "新拍JPG"

    def test_modern_preferred_over_legacy(self, tmp_path):
        """When both exist, modern 'incoming-jpg' is preferred."""
        from app.services.project_service import get_incoming_jpg_dir
        proj_dir = tmp_path / "proj"
        (proj_dir / "incoming-jpg").mkdir(parents=True)
        (proj_dir / "新拍JPG").mkdir(parents=True)
        result = get_incoming_jpg_dir(str(proj_dir))
        assert Path(result).name == "incoming-jpg"

    def test_returns_modern_path_when_neither_exists(self, tmp_path):
        """When neither dir exists, return the modern path (not yet created)."""
        from app.services.project_service import get_incoming_jpg_dir
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        result = get_incoming_jpg_dir(str(proj_dir))
        assert "incoming-jpg" in result or "incoming" in result.lower()


# ── get_results_dir ────────────────────────────────────────────────────────

class TestGetResultsDir:
    def test_returns_results_subdir(self, tmp_path):
        from app.services.project_service import get_results_dir
        proj_dir = str(tmp_path / "proj")
        result = get_results_dir(proj_dir)
        assert Path(result).name == "results"

    def test_is_inside_project_dir(self, tmp_path):
        from app.services.project_service import get_results_dir
        proj_dir = str(tmp_path / "proj")
        result = get_results_dir(proj_dir)
        assert result.startswith(str(tmp_path))


# ── ensure_project_dirs ────────────────────────────────────────────────────

class TestEnsureProjectDirs:
    def test_creates_incoming_results_data(self, tmp_path):
        from app.services.project_service import ensure_project_dirs
        proj_dir = tmp_path / "proj"
        ensure_project_dirs(str(proj_dir))
        assert (proj_dir / "incoming-jpg").is_dir()
        assert (proj_dir / "results").is_dir()
        assert (proj_dir / "_data").is_dir()

    def test_idempotent(self, tmp_path):
        from app.services.project_service import ensure_project_dirs
        proj_dir = tmp_path / "proj"
        ensure_project_dirs(str(proj_dir))
        ensure_project_dirs(str(proj_dir))  # should not raise
        assert (proj_dir / "incoming-jpg").is_dir()

    def test_returns_dict_with_paths(self, tmp_path):
        from app.services.project_service import ensure_project_dirs
        proj_dir = tmp_path / "proj"
        result = ensure_project_dirs(str(proj_dir))
        assert isinstance(result, dict)
        assert "projectDir" in result or "project_dir" in result


# ── get_project_summary ────────────────────────────────────────────────────

class TestGetProjectSummary:
    """Oracle: server.js GET /api/project/summary (lines 2831-2870)."""

    def test_returns_dict_with_required_keys(self, tmp_path):
        from app.services.project_service import get_project_summary
        result = get_project_summary(str(tmp_path / "empty_proj"))
        assert isinstance(result, dict)
        for key in ("specimenCount", "resultCount", "pendingJpgCount", "projectDir"):
            assert key in result, f"missing key: {key}"

    def test_empty_dir_returns_zeros(self, tmp_path):
        from app.services.project_service import get_project_summary
        result = get_project_summary(str(tmp_path / "noproj"))
        assert result["specimenCount"] == 0
        assert result["resultCount"] == 0
        assert result["pendingJpgCount"] == 0

    def test_counts_tifs_in_results(self, tmp_path):
        """TIF files in results/ are counted as resultCount."""
        import sqlite3 as _sqlite3
        from app.services.project_service import ensure_project_dirs, get_project_summary
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        results = proj / "results"
        (results / "A001-1.tif").write_bytes(b"")
        (results / "A002-1.tiff").write_bytes(b"")
        (results / "other.jpg").write_bytes(b"")   # should NOT count
        result = get_project_summary(str(proj))
        assert result["resultCount"] == 2

    def test_counts_tifs_in_freeform(self, tmp_path):
        """TIF files in results/freeform/ are also counted."""
        from app.services.project_service import ensure_project_dirs, get_project_summary
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        freeform = proj / "results" / "freeform"
        freeform.mkdir(parents=True, exist_ok=True)
        (freeform / "free001.tif").write_bytes(b"")
        result = get_project_summary(str(proj))
        assert result["resultCount"] == 1

    def test_counts_jpgs_in_incoming(self, tmp_path):
        """JPG files in incoming-jpg/ are counted as pendingJpgCount."""
        from app.services.project_service import ensure_project_dirs, get_project_summary
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        incoming = proj / "incoming-jpg"
        (incoming / "shot001.jpg").write_bytes(b"")
        (incoming / "shot002.jpeg").write_bytes(b"")
        (incoming / "readme.txt").write_bytes(b"")   # should NOT count
        result = get_project_summary(str(proj))
        assert result["pendingJpgCount"] == 2

    def test_reads_specimen_count_from_sqlite(self, tmp_path):
        """specimenCount reflects rows in project.db specimens table."""
        import sqlite3 as _sqlite3
        from app.services.project_service import ensure_project_dirs, get_project_summary
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        db_path = proj / "_data" / "project.db"
        conn = _sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE specimens (uid TEXT PRIMARY KEY, scientific_name TEXT)"
        )
        conn.execute("INSERT INTO specimens VALUES ('SP001', 'Aplysia californica')")
        conn.execute("INSERT INTO specimens VALUES ('SP002', 'Octopus vulgaris')")
        conn.commit()
        conn.close()
        result = get_project_summary(str(proj))
        assert result["specimenCount"] == 2

    def test_no_db_returns_zero_specimens(self, tmp_path):
        from app.services.project_service import ensure_project_dirs, get_project_summary
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        # _data/ exists but no project.db
        result = get_project_summary(str(proj))
        assert result["specimenCount"] == 0

    def test_never_raises_on_nonexistent_dir(self, tmp_path):
        from app.services.project_service import get_project_summary
        # Should not raise even when the project dir doesn't exist at all
        result = get_project_summary(str(tmp_path / "ghost_project"))
        assert result["specimenCount"] == 0
        assert result["resultCount"] == 0
        assert result["pendingJpgCount"] == 0


# ── get_project_results ────────────────────────────────────────────────────

class TestGetProjectResults:
    """Oracle: server.js GET /api/project/results (lines 2874-2922)."""

    def test_returns_dict_with_required_keys(self, tmp_path):
        from app.services.project_service import get_project_results
        result = get_project_results(str(tmp_path / "empty_proj"))
        assert isinstance(result, dict)
        for key in ("projectDir", "total", "groups", "ungrouped"):
            assert key in result, f"missing key: {key}"

    def test_empty_dir_returns_zero_total(self, tmp_path):
        from app.services.project_service import get_project_results
        result = get_project_results(str(tmp_path / "noproj"))
        assert result["total"] == 0
        assert result["groups"] == []
        assert result["ungrouped"] == []

    def test_never_raises_on_nonexistent_dir(self, tmp_path):
        from app.services.project_service import get_project_results
        result = get_project_results(str(tmp_path / "ghost"))
        assert result["total"] == 0

    def test_counts_tifs_in_results(self, tmp_path):
        """7-segment TIF files appear as items with uid and seq parsed."""
        from app.services.project_service import ensure_project_dirs, get_project_results
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        results = proj / "results"
        # valid 7-segment name: province-site-station-speciesId-seq-storage-dateSeg.tif
        (results / "FJ-YGLZ-B2-DLC001-1-RD75E-20260506.tif").write_bytes(b"")
        (results / "FJ-YGLZ-B2-DLC001-2-RD75E-20260506.tif").write_bytes(b"")
        (results / "other.jpg").write_bytes(b"")   # should NOT count
        result = get_project_results(str(proj))
        assert result["total"] == 2

    def test_groups_by_uid(self, tmp_path):
        """Two TIFs with the same UID should appear in the same group."""
        from app.services.project_service import ensure_project_dirs, get_project_results
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        results = proj / "results"
        (results / "FJ-YGLZ-B2-DLC001-1-RD75E-20260506.tif").write_bytes(b"")
        (results / "FJ-YGLZ-B2-DLC001-2-RD75E-20260506.tif").write_bytes(b"")
        result = get_project_results(str(proj))
        assert len(result["groups"]) == 1
        assert result["groups"][0]["uid"] == "FJ-YGLZ-B2-DLC001-RD75E-20260506"
        assert len(result["groups"][0]["items"]) == 2

    def test_different_uids_produce_separate_groups(self, tmp_path):
        """TIFs with different UIDs produce separate groups."""
        from app.services.project_service import ensure_project_dirs, get_project_results
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        results = proj / "results"
        (results / "FJ-YGLZ-B2-DLC001-1-RD75E-20260506.tif").write_bytes(b"")
        (results / "FJ-YGLZ-B2-DLC002-1-RD75E-20260506.tif").write_bytes(b"")
        result = get_project_results(str(proj))
        assert len(result["groups"]) == 2

    def test_unparseable_tif_goes_to_ungrouped(self, tmp_path):
        """TIFs with non-standard names go into ungrouped."""
        from app.services.project_service import ensure_project_dirs, get_project_results
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        (proj / "results" / "freeform_result.tif").write_bytes(b"")
        result = get_project_results(str(proj))
        assert result["total"] == 1
        assert len(result["ungrouped"]) == 1
        assert result["ungrouped"][0]["uid"] is None

    def test_freeform_dir_tifs_included(self, tmp_path):
        """TIFs in results/freeform/ are counted in total."""
        from app.services.project_service import ensure_project_dirs, get_project_results
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        freeform = proj / "results" / "freeform"
        freeform.mkdir(parents=True, exist_ok=True)
        (freeform / "free001.tif").write_bytes(b"")
        result = get_project_results(str(proj))
        assert result["total"] == 1

    def test_item_has_path_name_seq_fields(self, tmp_path):
        """Each item dict must contain path, name, uid, seq, mtime fields."""
        from app.services.project_service import ensure_project_dirs, get_project_results
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        results = proj / "results"
        (results / "FJ-YGLZ-B2-DLC001-3-RD75E-20260506.tif").write_bytes(b"")
        result = get_project_results(str(proj))
        items = result["groups"][0]["items"]
        assert len(items) == 1
        item = items[0]
        assert "path" in item
        assert "name" in item
        assert "uid" in item
        assert item["seq"] == 3

    def test_groups_sorted_by_uid(self, tmp_path):
        """Groups are returned sorted by uid string."""
        from app.services.project_service import ensure_project_dirs, get_project_results
        proj = tmp_path / "proj"
        ensure_project_dirs(str(proj))
        results = proj / "results"
        (results / "ZZ-SITE-S1-ZZZ001-1-D75E-20260101.tif").write_bytes(b"")
        (results / "AA-SITE-S1-AAA001-1-D75E-20260101.tif").write_bytes(b"")
        result = get_project_results(str(proj))
        uids = [g["uid"] for g in result["groups"]]
        assert uids == sorted(uids)


# ── default_to_recent_real_project ─────────────────────────────────────────

class TestDefaultToRecentRealProject:
    """Oracle: app.js:2670 (defaultToRecentRealProject)."""

    def test_returns_none_for_empty_list(self, tmp_path):
        import json
        from app.services.project_service import default_to_recent_real_project
        json_path = tmp_path / "user_projects.json"
        json_path.write_text(json.dumps({"version": 1, "projects": []}), encoding="utf-8")
        assert default_to_recent_real_project(str(json_path)) is None

    def test_returns_directory_of_last_real_project(self, tmp_path):
        import json
        from app.services.project_service import default_to_recent_real_project
        json_path = tmp_path / "user_projects.json"
        projs = [
            {"id": "1", "name": "A", "directory": "/tmp/projA"},
            {"id": "2", "name": "B", "directory": "/tmp/projB"},
        ]
        json_path.write_text(json.dumps({"version": 1, "projects": projs}), encoding="utf-8")
        result = default_to_recent_real_project(str(json_path))
        assert result == "/tmp/projB"

    def test_skips_demo_projects(self, tmp_path):
        import json
        from app.services.project_service import default_to_recent_real_project
        json_path = tmp_path / "user_projects.json"
        projs = [
            {"id": "1", "name": "Real", "directory": "/tmp/real"},
            {"id": "2", "name": "Demo", "directory": "/tmp/demo", "isDemo": True},
        ]
        json_path.write_text(json.dumps({"version": 1, "projects": projs}), encoding="utf-8")
        result = default_to_recent_real_project(str(json_path))
        assert result == "/tmp/real"

    def test_returns_none_for_missing_file(self, tmp_path):
        from app.services.project_service import default_to_recent_real_project
        result = default_to_recent_real_project(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_skips_projects_without_directory(self, tmp_path):
        import json
        from app.services.project_service import default_to_recent_real_project
        json_path = tmp_path / "user_projects.json"
        projs = [
            {"id": "1", "name": "NoDir"},
            {"id": "2", "name": "HasDir", "directory": "/tmp/hasdir"},
        ]
        json_path.write_text(json.dumps({"version": 1, "projects": projs}), encoding="utf-8")
        result = default_to_recent_real_project(str(json_path))
        assert result == "/tmp/hasdir"
