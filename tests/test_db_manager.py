"""Tests for db_manager.py.

Covers: open/create db, schema idempotency, WAL mode, darwin_core view,
caching, close_all cleanup.
"""
import sqlite3
import tempfile
import os
import pytest

from app.db import db_manager


@pytest.fixture(autouse=True)
def reset_cache():
    """Close all connections before and after each test for isolation."""
    db_manager.close_all()
    yield
    db_manager.close_all()


@pytest.fixture
def tmp_project(tmp_path):
    """Return a temporary project directory path (str)."""
    return str(tmp_path / "test_project")


class TestOpenProjectDb:
    def test_creates_data_dir_and_file(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        assert conn is not None
        db_path = os.path.join(tmp_project, "_data", "project.db")
        assert os.path.exists(db_path)

    def test_returns_cached_connection(self, tmp_project):
        conn1 = db_manager.open_project_db(tmp_project)
        conn2 = db_manager.open_project_db(tmp_project)
        assert conn1 is conn2

    def test_wal_mode(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

    def test_row_factory(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        assert conn.row_factory is sqlite3.Row


class TestEnsureSchema:
    def test_tables_created(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "specimens" in tables
        assert "tasks" in tables
        assert "grouping" in tables
        assert "seen_files" in tables
        assert "_import_manifest" in tables

    def test_darwin_core_view_created(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        views = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()}
        assert "darwin_core" in views

    def test_idempotent_second_call(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        # Should not raise
        db_manager.ensure_schema(conn)
        db_manager.ensure_schema(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "specimens" in tables

    def test_specimens_columns(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(specimens)").fetchall()}
        required = {
            "uid", "id", "province", "site", "station", "storage",
            "collection_date", "photo_date", "scientific_name", "scientific_name_cn",
            "taxon_group", "taxon_group_cn", "order_name", "order_cn",
            "family", "family_cn", "genus", "genus_cn",
            "lon", "lat", "geo_area", "collector", "photographer", "identifier",
            "notes", "photo_notes", "angle", "metadata", "pinned",
            "owner_project_dir", "raw_json",
        }
        assert required.issubset(cols)
        # Spec: no species/species_cn columns
        assert "species" not in cols
        assert "species_cn" not in cols

    def test_tasks_has_raw_json(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        assert "raw_json" in cols

    def test_grouping_extended_columns(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(grouping)").fetchall()}
        extended = {"status", "source", "created_at", "updated_at",
                    "result_sequence", "archive_zip", "retired_tiff_paths", "raw_json"}
        assert extended.issubset(cols)

    def test_darwin_core_columns(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project)
        # Insert a specimen and query darwin_core
        conn.execute("""
            INSERT INTO specimens (uid, scientific_name, family, genus, order_name,
                lon, lat, collection_date, collector, identifier,
                province, site, station, storage)
            VALUES ('test-uid','Homo sapiens','Hominidae','Homo','Primates',
                119.5, 26.1, '20260601', 'Zhang', 'Li',
                'FJ', 'XM', 'B2', 'T95E')
        """)
        conn.commit()
        row = conn.execute("SELECT * FROM darwin_core WHERE occurrenceID='test-uid'").fetchone()
        assert row is not None
        assert row["occurrenceID"] == "test-uid"
        assert row["scientificName"] == "Homo sapiens"
        assert row["locality"] == "FJ·XM·B2"
        assert row["verbatimPreservation"] == "T95E"


class TestGetDb:
    def test_get_db_opens_if_not_cached(self, tmp_project):
        conn = db_manager.get_db(tmp_project)
        assert conn is not None

    def test_get_db_returns_same_as_open(self, tmp_project):
        conn1 = db_manager.open_project_db(tmp_project)
        conn2 = db_manager.get_db(tmp_project)
        assert conn1 is conn2


class TestCloseAll:
    def test_close_all_clears_cache(self, tmp_project):
        db_manager.open_project_db(tmp_project)
        db_manager.close_all()
        # After close_all, a new open should create a new connection
        conn_new = db_manager.open_project_db(tmp_project)
        assert conn_new is not None
