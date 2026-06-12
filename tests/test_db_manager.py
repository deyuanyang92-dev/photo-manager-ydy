"""Tests for db_manager.py.

Covers: open/create db, schema idempotency, WAL mode, darwin_core view,
caching, close_all cleanup.

NOTE on ``create=True``: establishing a NEW workspace db is an explicit act
(``open_project_db(dir, create=True)``); the default is a strict open that
refuses to fabricate (see tests/test_project_availability.py for the red-line
contract). Tests below that build a fresh db therefore pass ``create=True``;
tests that open a pre-existing db use the strict default.
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
    """Return a temporary project directory (str). The root EXISTS (as a real
    project folder would) but holds no _data/project.db yet."""
    p = tmp_path / "test_project"
    p.mkdir()
    return str(p)


class TestOpenProjectDb:
    def test_creates_data_dir_and_file(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
        assert conn is not None
        db_path = os.path.join(tmp_project, "_data", "project.db")
        assert os.path.exists(db_path)

    def test_returns_cached_connection(self, tmp_project):
        conn1 = db_manager.open_project_db(tmp_project, create=True)
        conn2 = db_manager.open_project_db(tmp_project)
        assert conn1 is conn2

    def test_wal_mode(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

    def test_row_factory(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
        assert conn.row_factory is sqlite3.Row


class TestEnsureSchema:
    def test_tables_created(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "specimens" in tables
        assert "tasks" in tables
        assert "grouping" in tables
        assert "seen_files" in tables
        assert "_import_manifest" in tables

    def test_darwin_core_view_created(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
        views = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()}
        assert "darwin_core" in views

    def test_idempotent_second_call(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
        # Should not raise
        db_manager.ensure_schema(conn)
        db_manager.ensure_schema(conn)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "specimens" in tables

    def test_specimens_columns(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
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
        conn = db_manager.open_project_db(tmp_project, create=True)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        assert "raw_json" in cols

    def test_grouping_extended_columns(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(grouping)").fetchall()}
        extended = {"status", "source", "created_at", "updated_at",
                    "result_sequence", "archive_zip", "retired_tiff_paths", "raw_json"}
        assert extended.issubset(cols)

    def test_darwin_core_columns(self, tmp_project):
        conn = db_manager.open_project_db(tmp_project, create=True)
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

    def test_darwin_core_constants_present_without_record(self, tmp_project):
        """标本无匹配采集记录：4 个常量术语仍输出，采集记录派生术语为 NULL。"""
        conn = db_manager.open_project_db(tmp_project, create=True)
        conn.execute("""
            INSERT INTO specimens (uid, scientific_name, collection_date,
                province, site, station, storage)
            VALUES ('u-nocr','Aaa','20260601','FJ','XM','B2','T95E')
        """)
        conn.commit()
        row = conn.execute("SELECT * FROM darwin_core WHERE occurrenceID='u-nocr'").fetchone()
        assert row["basisOfRecord"] == "PreservedSpecimen"
        assert row["occurrenceStatus"] == "present"
        assert row["geodeticDatum"] == "WGS84"
        assert row["countryCode"] == "CN"
        assert row["preparations"] == "T95E"
        assert row["habitat"] is None
        assert row["sampleSizeValue"] is None
        assert row["dynamicProperties"] is None

    def test_darwin_core_joins_quantitative_collection_record(self, tmp_project):
        """定量采集记录经四键 JOIN → 标准术语 + dynamicProperties(采集性质=定量)。"""
        conn = db_manager.open_project_db(tmp_project, create=True)
        conn.execute("""
            INSERT INTO specimens (uid, scientific_name, collection_date,
                province, site, station, storage)
            VALUES ('u-quant','Bbb','20260601','FJ','XM','B2','T95E')
        """)
        from app.services import collection_record_service as crs
        crs.upsert_record(conn, {
            "province": "FJ", "site": "XM", "station": "B2",
            "collection_date": "20260601",
            "sample_type": "定量", "habitat": "泥滩", "water_body": "东海·三门湾",
            "depth": "5", "method": "采泥器", "sampler_model": "大洋50型",
            "sampler_spec": "0.1m²采泥器",
            "sieve_mesh": "1.0", "sample_area": "0.2", "replicates": "4",
            "salinity": "30", "sample_no": "B2-2026-007",
            "cruise": "2026春季三门湾航次", "vessel": "科学三号",
            "recorder": "李四", "checker": "王五",
        })
        row = conn.execute("SELECT * FROM darwin_core WHERE occurrenceID='u-quant'").fetchone()
        assert row["habitat"] == "泥滩"
        assert row["waterBody"] == "东海·三门湾"
        assert row["minimumDepthInMeters"] == "5"
        assert row["maximumDepthInMeters"] == "5"
        assert row["sampleSizeValue"] == "0.2"
        assert row["sampleSizeUnit"] == "square metre"
        assert "采泥器" in row["samplingProtocol"]
        assert "大洋50型" in row["samplingProtocol"]   # 采泥器型号 并入 samplingProtocol
        assert "1.0" in row["samplingProtocol"]
        assert "4" in row["samplingEffort"]
        assert row["recordNumber"] == "B2-2026-007"    # 样品编号 → DwC recordNumber
        assert row["basisOfRecord"] == "PreservedSpecimen"
        assert row["occurrenceStatus"] == "present"
        dp = row["dynamicProperties"]
        assert '"采集性质":"定量"' in dp
        assert '"盐度":"30"' in dp
        assert '"航次":"2026春季三门湾航次"' in dp
        assert '"船号":"科学三号"' in dp
        assert '"记录人":"李四"' in dp
        assert '"核对人":"王五"' in dp

    def test_darwin_core_qualitative_has_no_sample_size(self, tmp_project):
        """定性采集记录：无取样面积 → sampleSizeValue 空、性质=定性。"""
        conn = db_manager.open_project_db(tmp_project, create=True)
        conn.execute("""
            INSERT INTO specimens (uid, scientific_name, collection_date,
                province, site, station, storage)
            VALUES ('u-qual','Ccc','20260602','FJ','XM','B3','T95E')
        """)
        from app.services import collection_record_service as crs
        crs.upsert_record(conn, {
            "province": "FJ", "site": "XM", "station": "B3",
            "collection_date": "20260602",
            "sample_type": "定性", "habitat": "岩礁", "method": "手拣定性",
        })
        row = conn.execute("SELECT * FROM darwin_core WHERE occurrenceID='u-qual'").fetchone()
        assert row["sampleSizeValue"] is None
        assert row["sampleSizeUnit"] is None
        assert "手拣定性" in row["samplingProtocol"]
        assert '"采集性质":"定性"' in row["dynamicProperties"]


class TestSchemaMigrationAddsColumns:
    """Opening a project.db created by an OLDER schema (e.g. the web prototype's
    5-column ``grouping`` table, db-utils.js:64) must additively migrate the
    pre-existing tables — CREATE TABLE IF NOT EXISTS alone never adds columns,
    which left imported web-prototype projects unable to read/write archive
    state and crashing on organize/compress writes.
    """

    def _make_legacy_db(self, project_dir):
        """Create a project.db with the legacy 5-column grouping table + a row,
        mimicking a DB created by the web prototype."""
        data_dir = os.path.join(project_dir, "_data")
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "project.db")
        con = sqlite3.connect(db_path)
        con.executescript(
            """
            CREATE TABLE grouping (
              uid TEXT, group_index INTEGER,
              angle_label TEXT, jpg_paths TEXT, composed_tiff_path TEXT,
              PRIMARY KEY (uid, group_index)
            );
            CREATE TABLE tasks (
              uid TEXT PRIMARY KEY, is_active INTEGER DEFAULT 0,
              activated_at TEXT, last_organized_at TEXT,
              next_result_sequence_hint INTEGER
            );
            """
        )
        con.execute(
            "INSERT INTO grouping (uid, group_index, angle_label, jpg_paths, "
            "composed_tiff_path) VALUES (?,?,?,?,?)",
            ("FJ-XM-B2-DLC004-T95E-20260602", 1, "手动整理 1", "[]",
             "/x/results/FJ-XM-B2-DLC004-1-T95E-20260602.tif"),
        )
        con.commit()
        con.close()
        return db_path

    # The legacy db EXISTS before open → these use the strict default open,
    # which doubles as a regression test that opening existing dbs still works.

    def test_grouping_missing_columns_added(self, tmp_project):
        self._make_legacy_db(tmp_project)
        conn = db_manager.open_project_db(tmp_project)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(grouping)").fetchall()}
        for c in ("status", "source", "created_at", "updated_at",
                  "result_sequence", "archive_zip", "retired_tiff_paths", "raw_json"):
            assert c in cols, f"missing migrated column: {c}"

    def test_legacy_grouping_row_preserved(self, tmp_project):
        self._make_legacy_db(tmp_project)
        conn = db_manager.open_project_db(tmp_project)
        row = conn.execute(
            "SELECT * FROM grouping WHERE uid=?",
            ("FJ-XM-B2-DLC004-T95E-20260602",),
        ).fetchone()
        assert row is not None
        assert row["composed_tiff_path"] == "/x/results/FJ-XM-B2-DLC004-1-T95E-20260602.tif"
        assert row["angle_label"] == "手动整理 1"
        # Newly-added column reads as NULL on a pre-existing row.
        assert row["archive_zip"] is None

    def test_tasks_raw_json_added(self, tmp_project):
        self._make_legacy_db(tmp_project)
        conn = db_manager.open_project_db(tmp_project)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        assert "raw_json" in cols

    def test_migration_idempotent(self, tmp_project):
        self._make_legacy_db(tmp_project)
        conn = db_manager.open_project_db(tmp_project)
        db_manager.ensure_schema(conn)
        db_manager.ensure_schema(conn)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(grouping)").fetchall()]
        # No duplicate columns after repeated migration.
        assert len(cols) == len(set(cols))
        assert "archive_zip" in cols

    def test_save_grouping_works_after_migration(self, tmp_project):
        """The explicit-column INSERT in grouping_service.save_grouping would
        crash on the legacy 5-col table; after migration it must succeed."""
        from app.services import grouping_service as gs

        self._make_legacy_db(tmp_project)
        conn = db_manager.open_project_db(tmp_project)
        grouping = gs.SpecimenGrouping(
            uid="NEW-UID",
            groups=[gs.Group(group_index=1, angle_label="g", jpg_paths=[],
                             composed_tiff_path=None, archive_zip="/x/a.zip")],
        )
        gs.save_grouping(conn, "NEW-UID", grouping.groups)  # must not raise
        loaded = gs.load_grouping(conn, "NEW-UID")
        assert loaded.groups[0].archive_zip == "/x/a.zip"


class TestGetDb:
    def test_get_db_opens_existing_if_not_cached(self, tmp_project):
        db_manager.open_project_db(tmp_project, create=True)
        db_manager.close_all()
        conn = db_manager.get_db(tmp_project)
        assert conn is not None

    def test_get_db_returns_same_as_open(self, tmp_project):
        conn1 = db_manager.open_project_db(tmp_project, create=True)
        conn2 = db_manager.get_db(tmp_project)
        assert conn1 is conn2


class TestCloseAll:
    def test_close_all_clears_cache(self, tmp_project):
        db_manager.open_project_db(tmp_project, create=True)
        db_manager.close_all()
        # After close_all, a new open of the EXISTING db should succeed
        conn_new = db_manager.open_project_db(tmp_project)
        assert conn_new is not None
