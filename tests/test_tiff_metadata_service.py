from __future__ import annotations

import sqlite3

from PIL import Image

from app.db.db_manager import ensure_schema


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _seed_specimen(conn: sqlite3.Connection) -> str:
    uid = "GXFCG-BLW-SUOSC001-DLC001-D79-20260618"
    conn.execute(
        """
        INSERT INTO specimens (
          uid, id, province, site, station, storage, collection_date,
          photo_date, scientific_name, scientific_name_cn, lon, lat,
          geo_area, collector, photographer, identifier, notes, photo_notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            "DLC001",
            "GXFCG",
            "BLW",
            "SUOSC001",
            "D79",
            "20260618",
            "20260618",
            "Marphysa sp.",
            "岩虫属一种",
            108.1,
            21.5,
            "广西北部湾",
            "张三",
            "李四",
            "王五",
            "备注",
            "背面",
        ),
    )
    from app.services.project_settings_service import save_setting

    save_setting(conn, "project_meta", {
        "project_code": "GXFCG",
        "name": "广西北部湾项目",
        "year": "2026",
        "date_range": "20260618",
        "location": "广西",
        "photo_location": "实验室",
    })
    return uid


def _tiff(path):
    Image.new("RGB", (16, 12), (20, 40, 60)).save(path, "TIFF")


def test_write_result_tiff_metadata_fill_empty(tmp_path):
    from app.services.tiff_metadata_service import read_app_metadata, write_result_tiff_metadata

    conn = _db()
    try:
        uid = _seed_specimen(conn)
        tif = tmp_path / "result.tif"
        _tiff(tif)

        result = write_result_tiff_metadata(conn, uid, str(tif), project_dir="")
        payload = read_app_metadata(str(tif))

        assert result["written"] is True
        assert payload["specimen_uid"] == uid
        assert payload["project"]["name"] == "广西北部湾项目"
        assert payload["fields"]["collector"] == "张三"
        assert payload["fields"]["scientific_name"] == "Marphysa sp."
    finally:
        conn.close()


def test_fill_empty_preserves_existing_app_values(tmp_path):
    from app.services.tiff_metadata_service import (
        MODE_FILL_EMPTY,
        read_app_metadata,
        write_tiff_metadata,
    )

    tif = tmp_path / "result.tif"
    _tiff(tif)
    original = {
        "source": "SpecimenPhotoWorkbench",
        "schema_version": 1,
        "specimen_uid": "OLD",
        "project": {"name": "旧项目"},
        "fields": {"collector": "旧采集人"},
    }
    write_tiff_metadata(str(tif), original, mode=MODE_FILL_EMPTY)

    incoming = {
        "source": "SpecimenPhotoWorkbench",
        "schema_version": 1,
        "specimen_uid": "NEW",
        "project": {"name": "新项目", "year": "2026"},
        "fields": {"collector": "新采集人", "photographer": "新拍摄人"},
    }
    write_tiff_metadata(str(tif), incoming, mode=MODE_FILL_EMPTY)
    payload = read_app_metadata(str(tif))

    assert payload["specimen_uid"] == "OLD"
    assert payload["project"]["name"] == "旧项目"
    assert payload["project"]["year"] == "2026"
    assert payload["fields"]["collector"] == "旧采集人"
    assert payload["fields"]["photographer"] == "新拍摄人"


def test_force_replaces_app_values(tmp_path):
    from app.services.tiff_metadata_service import (
        MODE_FILL_EMPTY,
        MODE_FORCE,
        read_app_metadata,
        write_tiff_metadata,
    )

    tif = tmp_path / "result.tif"
    _tiff(tif)
    write_tiff_metadata(str(tif), {
        "source": "SpecimenPhotoWorkbench",
        "schema_version": 1,
        "specimen_uid": "OLD",
        "project": {"name": "旧项目"},
        "fields": {"collector": "旧采集人"},
    }, mode=MODE_FILL_EMPTY)

    write_tiff_metadata(str(tif), {
        "source": "SpecimenPhotoWorkbench",
        "schema_version": 1,
        "specimen_uid": "NEW",
        "project": {"name": "新项目"},
        "fields": {"collector": "新采集人"},
    }, mode=MODE_FORCE)
    payload = read_app_metadata(str(tif))

    assert payload["specimen_uid"] == "NEW"
    assert payload["project"]["name"] == "新项目"
    assert payload["fields"]["collector"] == "新采集人"
