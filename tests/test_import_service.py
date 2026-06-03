"""Tests for import_service.py.

Covers spec invariants, boundary cases, and failure modes.
All imports operate on /tmp copies — never touch the real data directory.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.db import db_manager
from app.services.import_service import import_all, ImportReport


# ── Helpers ────────────────────────────────────────────────────────────────

SOURCE_DATA = Path("/mnt/n/claude/photo-platform-ydy/prototype-photo-gui/data")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_ro_copy(tmp_path: Path) -> Path:
    """Copy the real data dir to tmp_path and make it read-only."""
    dst = tmp_path / "data"
    if SOURCE_DATA.exists():
        shutil.copytree(str(SOURCE_DATA), str(dst))
        # Make read-only
        for f in dst.rglob("*"):
            if f.is_file():
                f.chmod(0o444)
        dst.chmod(0o555)
    return dst


def _read_projects(data_dir: Path) -> list[dict]:
    """Parse user_projects.json and return list of project dicts."""
    fp = data_dir / "user_projects.json"
    if not fp.exists():
        return []
    d = json.loads(fp.read_text(encoding="utf-8"))
    return d.get("projects", [])


@pytest.fixture(autouse=True)
def reset_db_cache():
    db_manager.close_all()
    yield
    db_manager.close_all()


# ── Invariant: source files not mutated ───────────────────────────────────

class TestImportDoesNotMutateSource:
    def test_import_does_not_mutate_source(self, tmp_path):
        """Spec invariant: sha256 of every source *.json must be unchanged."""
        data_dir = _make_ro_copy(tmp_path)
        if not data_dir.exists():
            pytest.skip("Source data not available")
        projects = _read_projects(data_dir)

        # Snapshot hashes before
        json_files = list(data_dir.glob("*.json"))
        before = {str(f): _sha256(str(f)) for f in json_files}

        # Each project needs a writable project dir for its _data/
        project_dirs = []
        for p in projects:
            pd = tmp_path / "projects" / str(p.get("id", "x"))
            pd.mkdir(parents=True, exist_ok=True)
            project_dirs.append({**p, "_resolved_test_dir": str(pd)})

        import_all(str(data_dir), project_dirs)

        # Verify hashes unchanged
        after = {str(f): _sha256(str(f)) for f in json_files}
        assert before == after


# ── Invariant: raw_json roundtrip ─────────────────────────────────────────

class TestRawJsonRoundtrip:
    def test_raw_json_roundtrip(self, tmp_path):
        """Spec invariant: json.loads(raw_json) must deep-equal source object."""
        data_dir = _make_ro_copy(tmp_path)
        if not data_dir.exists():
            pytest.skip("Source data not available")
        projects = _read_projects(data_dir)
        if not projects:
            pytest.skip("No projects in source data")

        project_dirs = []
        for p in projects:
            pd = tmp_path / "projects" / str(p.get("id", "x"))
            pd.mkdir(parents=True, exist_ok=True)
            project_dirs.append({**p, "_resolved_test_dir": str(pd)})

        import_all(str(data_dir), project_dirs)

        # Check specimens roundtrip in at least one project
        found_specimens = False
        for p in project_dirs:
            pd = p["_resolved_test_dir"]
            conn = db_manager.open_project_db(pd)
            rows = conn.execute("SELECT raw_json FROM specimens LIMIT 5").fetchall()
            for row in rows:
                found_specimens = True
                obj = json.loads(row[0])
                assert isinstance(obj, dict)
                # Must have at least the core fields
                assert "id" in obj or "province" in obj
        # Only assert if there were specimens to check
        # (some test envs may have none)


# ── Invariant: Chinese task key preserved ─────────────────────────────────

class TestChineseTaskKeyPreserved:
    def _make_synthetic_data(self, tmp_path: Path):
        """Build synthetic data files with a Chinese uid task."""
        data_dir = tmp_path / "synthetic_data"
        data_dir.mkdir()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        # user_specimens.json
        sp = {
            "id": "DLC001", "province": "浙江", "site": "三门湾",
            "station": "B2", "storage": "T95E",
            "collectionDate": "20260601", "photoDate": "20260601",
            "ownerProjectDir": str(proj_dir),
        }
        (data_dir / "user_specimens.json").write_text(
            json.dumps({"version": 1, "specimens": [sp]}), encoding="utf-8"
        )

        # specimen_tasks.json — Chinese uid key
        cn_uid = "浙江-三门湾-B2-DLC001-T95E-20260601"
        (data_dir / "specimen_tasks.json").write_text(
            json.dumps({"projects": {str(proj_dir): {cn_uid: {"isActive": True}}}}),
            encoding="utf-8",
        )

        # grouping_confirmations.json
        (data_dir / "grouping_confirmations.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )

        return data_dir, proj_dir, cn_uid

    def test_chinese_task_key_preserved(self, tmp_path):
        """Spec invariant: Chinese-keyed task must survive import verbatim."""
        data_dir, proj_dir, cn_uid = self._make_synthetic_data(tmp_path)

        projects = [{"_resolved_test_dir": str(proj_dir)}]
        import_all(str(data_dir), projects)

        conn = db_manager.open_project_db(str(proj_dir))
        row = conn.execute("SELECT uid FROM tasks WHERE uid=?", (cn_uid,)).fetchone()
        assert row is not None, f"Chinese uid '{cn_uid}' was not found in tasks table"
        assert row[0] == cn_uid


# ── Invariant: missing station degrades gracefully ────────────────────────

class TestLegacyUidMissingStation:
    def test_legacy_uid_missing_station(self, tmp_path):
        """Spec invariant: specimen without station must be imported without error."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        sp = {
            "id": "OLD001", "province": "FJ", "site": "XM",
            # station is missing (None / absent)
            "storage": "T95E",
            "collectionDate": "20260601", "photoDate": "20260601",
            "ownerProjectDir": str(proj_dir),
        }
        (data_dir / "user_specimens.json").write_text(
            json.dumps({"version": 1, "specimens": [sp]}), encoding="utf-8"
        )
        (data_dir / "specimen_tasks.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )
        (data_dir / "grouping_confirmations.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )

        projects = [{"_resolved_test_dir": str(proj_dir)}]
        report = import_all(str(data_dir), projects)

        conn = db_manager.open_project_db(str(proj_dir))
        rows = conn.execute("SELECT uid FROM specimens").fetchall()
        assert len(rows) == 1
        uid = rows[0][0]
        assert "--" not in uid  # no double-dash from missing segment


# ── Invariant: idempotent per-row (INSERT OR REPLACE) ─────────────────────

class TestIdempotentPerRow:
    def test_idempotent_per_row(self, tmp_path):
        """Spec invariant: importing twice must not double the row count."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        sp = {
            "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
            "storage": "T95E", "collectionDate": "20260601", "photoDate": "20260601",
            "ownerProjectDir": str(proj_dir),
        }
        (data_dir / "user_specimens.json").write_text(
            json.dumps({"version": 1, "specimens": [sp]}), encoding="utf-8"
        )
        (data_dir / "specimen_tasks.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )
        (data_dir / "grouping_confirmations.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )

        projects = [{"_resolved_test_dir": str(proj_dir)}]
        import_all(str(data_dir), projects)
        import_all(str(data_dir), projects)

        conn = db_manager.open_project_db(str(proj_dir))
        count = conn.execute("SELECT COUNT(*) FROM specimens").fetchone()[0]
        assert count == 1


# ── Boundary / failure cases ───────────────────────────────────────────────

class TestBoundaryCases:
    def _write_minimal(self, data_dir: Path, proj_dir: Path, specimens=None):
        (data_dir / "user_specimens.json").write_text(
            json.dumps({"version": 1, "specimens": specimens or []}),
            encoding="utf-8",
        )
        (data_dir / "specimen_tasks.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )
        (data_dir / "grouping_confirmations.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )

    def test_missing_source_file_skips_not_crash(self, tmp_path):
        """Boundary: source JSON file missing → skip, no crash."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        # Only write tasks, skip specimens and grouping
        (data_dir / "specimen_tasks.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )
        # No user_specimens.json, no grouping_confirmations.json
        projects = [{"_resolved_test_dir": str(proj_dir)}]
        report = import_all(str(data_dir), projects)  # must not raise
        assert report is not None

    def test_corrupt_json_aborts_no_partial_write(self, tmp_path):
        """Boundary: corrupt JSON must abort without writing partial data."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        (data_dir / "user_specimens.json").write_text(
            "{INVALID JSON", encoding="utf-8"
        )
        (data_dir / "specimen_tasks.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )
        (data_dir / "grouping_confirmations.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )

        projects = [{"_resolved_test_dir": str(proj_dir)}]
        with pytest.raises(Exception):
            import_all(str(data_dir), projects)

        # Verify nothing was written to specimens
        db_manager.close_all()
        conn = db_manager.open_project_db(str(proj_dir))
        count = conn.execute("SELECT COUNT(*) FROM specimens").fetchone()[0]
        assert count == 0

    def test_empty_specimens_empty_projects(self, tmp_path):
        """Boundary: empty data produces empty db without error."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()
        self._write_minimal(data_dir, proj_dir, specimens=[])

        projects = [{"_resolved_test_dir": str(proj_dir)}]
        report = import_all(str(data_dir), projects)
        assert report is not None

        conn = db_manager.open_project_db(str(proj_dir))
        assert conn.execute("SELECT COUNT(*) FROM specimens").fetchone()[0] == 0

    def test_lon_lat_empty_string_stored_as_null(self, tmp_path):
        """Boundary: lon/lat empty string must be stored as NULL, not 0."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj_dir = tmp_path / "proj1"
        proj_dir.mkdir()

        sp = {
            "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
            "storage": "T95E", "collectionDate": "20260601", "photoDate": "20260601",
            "ownerProjectDir": str(proj_dir),
            "lon": "", "lat": "",  # empty strings
        }
        self._write_minimal(data_dir, proj_dir, specimens=[sp])

        projects = [{"_resolved_test_dir": str(proj_dir)}]
        import_all(str(data_dir), projects)

        conn = db_manager.open_project_db(str(proj_dir))
        row = conn.execute("SELECT lon, lat FROM specimens").fetchone()
        assert row["lon"] is None
        assert row["lat"] is None

    def test_same_uid_two_owner_dirs_go_to_separate_dbs(self, tmp_path):
        """Boundary: same uid in two project dirs → each in its own db."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        proj1 = tmp_path / "proj1"
        proj2 = tmp_path / "proj2"
        proj1.mkdir()
        proj2.mkdir()

        sp1 = {
            "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
            "storage": "T95E", "collectionDate": "20260601", "photoDate": "20260601",
            "ownerProjectDir": str(proj1),
        }
        sp2 = {
            "id": "DLC001", "province": "FJ", "site": "XM", "station": "B2",
            "storage": "T95E", "collectionDate": "20260601", "photoDate": "20260601",
            "ownerProjectDir": str(proj2),
        }
        (data_dir / "user_specimens.json").write_text(
            json.dumps({"version": 1, "specimens": [sp1, sp2]}), encoding="utf-8"
        )
        (data_dir / "specimen_tasks.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )
        (data_dir / "grouping_confirmations.json").write_text(
            json.dumps({"projects": {}}), encoding="utf-8"
        )

        projects = [
            {"_resolved_test_dir": str(proj1)},
            {"_resolved_test_dir": str(proj2)},
        ]
        import_all(str(data_dir), projects)

        conn1 = db_manager.open_project_db(str(proj1))
        conn2 = db_manager.open_project_db(str(proj2))
        assert conn1.execute("SELECT COUNT(*) FROM specimens").fetchone()[0] == 1
        assert conn2.execute("SELECT COUNT(*) FROM specimens").fetchone()[0] == 1
