"""tests/test_collection_record_io.py — 采集记录 Excel/CSV 导出导入（步骤 5）."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services import collection_record_io as io
from app.services import collection_record_service as crs


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE collection_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            province TEXT, site TEXT, station TEXT, collection_date TEXT,
            station_label TEXT, lon REAL, lat REAL, geo_area TEXT,
            habitat TEXT, tide TEXT, salinity TEXT, water_temp TEXT, weather TEXT,
            collector TEXT, photographer TEXT, identifier TEXT,
            collection_time TEXT, photo_date TEXT, photo_location TEXT,
            method TEXT, remark TEXT, raw_json TEXT,
            UNIQUE(province, site, station, collection_date))"""
    )
    conn.commit()
    yield conn
    conn.close()


def test_export_template_writes_records_and_blank_rows(db, tmp_path):
    crs.upsert_record(db, {"province": "GD", "site": "雷州", "station": "S01",
                           "collection_date": "20260518", "habitat": "泥滩"})
    out = tmp_path / "模板.xlsx"
    written = io.export_template(db, str(out), province="GD", site="雷州", blank_rows=5)
    assert written == 1
    assert out.exists()
    # read it back via the importer's reader
    header, rows = io._read_xlsx(str(out))
    assert "站位" in header and "采集日期" in header
    assert rows[0][header.index("站位")] == "S01"
    # blank rows pre-seed province/site
    assert rows[1][header.index("地区")] == "GD"


def test_roundtrip_export_then_import(db, tmp_path):
    crs.upsert_record(db, {"province": "GD", "site": "雷州", "station": "S01",
                           "collection_date": "20260518", "habitat": "泥滩",
                           "collector": "张三"})
    out = tmp_path / "rt.xlsx"
    io.export_template(db, str(out), blank_rows=0)
    # wipe table, re-import
    db.execute("DELETE FROM collection_records")
    db.commit()
    rep = io.import_file(db, str(out))
    assert rep.ok and rep.imported == 1
    rec = crs.lookup_record(db, "GD", "雷州", "S01", "20260518")
    assert rec is not None and rec["habitat"] == "泥滩" and rec["collector"] == "张三"


def test_import_csv_with_chinese_headers(db, tmp_path):
    csv_path = tmp_path / "in.csv"
    csv_path.write_text(
        "地区,样地,站位,采集日期,生境,采集人\n"
        "FJ,XM,B2,20260601,岩相,李四\n",
        encoding="utf-8-sig",
    )
    rep = io.import_file(db, str(csv_path))
    assert rep.imported == 1
    rec = crs.lookup_record(db, "FJ", "XM", "B2", "20260601")
    assert rec["habitat"] == "岩相" and rec["collector"] == "李四"


def test_import_skips_rows_missing_key_fields(db, tmp_path):
    csv_path = tmp_path / "in.csv"
    csv_path.write_text(
        "地区,样地,站位,采集日期,生境\n"
        "FJ,XM,,20260601,泥滩\n"      # missing 站位 → skip
        "FJ,XM,B2,20260601,岩相\n",   # ok
        encoding="utf-8-sig",
    )
    rep = io.import_file(db, str(csv_path))
    assert rep.imported == 1
    assert rep.skipped == 1


def test_import_unrecognized_header_fails_gracefully(db, tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("foo,bar,baz\n1,2,3\n", encoding="utf-8-sig")
    rep = io.import_file(db, str(csv_path))
    assert rep.ok is False
    assert rep.errors


def test_import_english_keys_also_work(db, tmp_path):
    csv_path = tmp_path / "en.csv"
    csv_path.write_text(
        "province,site,station,collection_date,habitat\n"
        "GD,雷州,S09,20260519,沙滩\n",
        encoding="utf-8-sig",
    )
    rep = io.import_file(db, str(csv_path))
    assert rep.imported == 1
    assert crs.lookup_record(db, "GD", "雷州", "S09", "20260519")["habitat"] == "沙滩"
