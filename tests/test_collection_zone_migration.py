"""test_collection_zone_migration.py — 旧库（无 zone 列）迁移到新 schema。

`ensure_schema` → `_migrate_add_missing_columns` 按 schema.sql 反查 ALTER 加列。
本测试模拟一张「优化前」的 collection_records（无 zone 及 14 个 zone 专属列），
确认：
  1. 迁移后新列存在且可读写；
  2. 旧数据（含 raw_json）零丢失；
  3. 旧行 zone=NULL（历史未分类）；
  4. 4 键唯一约束与 autofill/map 读取点不破。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collection_zone_migration.py -v
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db import db_manager
from app.services import collection_record_service as crs

# 优化前的列集（无 zone / quadrate_no / air_temp / wire_out / ... 14 项）
_LEGACY_COLS = [
    "id INTEGER PRIMARY KEY AUTOINCREMENT",
    "province TEXT", "site TEXT", "station TEXT", "collection_date TEXT",
    "station_label TEXT", "lon REAL", "lat REAL", "geo_area TEXT",
    "water_body TEXT", "cruise TEXT", "vessel TEXT", "habitat TEXT",
    "tidal_zone TEXT", "depth TEXT", "tide TEXT", "salinity TEXT",
    "water_temp TEXT", "bottom_temp TEXT", "dissolved_oxygen TEXT",
    "ph TEXT", "weather TEXT", "sample_type TEXT", "sampler_model TEXT",
    "sampler_spec TEXT", "sample_area TEXT", "replicates TEXT",
    "sieve_mesh TEXT", "sample_no TEXT", "collector TEXT", "recorder TEXT",
    "checker TEXT", "photographer TEXT", "identifier TEXT",
    "collection_time TEXT", "photo_date TEXT", "photo_location TEXT",
    "method TEXT", "remark TEXT", "raw_json TEXT",
    "UNIQUE(province, site, station, collection_date)",
]


def _legacy_db() -> sqlite3.Connection:
    """A project.db whose collection_records has the OLD (pre-zone) shape."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        f"CREATE TABLE collection_records ({', '.join(_LEGACY_COLS)})"
    )
    # 一条历史记录（无 zone）
    conn.execute(
        """INSERT INTO collection_records
           (province, site, station, collection_date, station_label, lon, lat,
            habitat, collector, remark, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("ZJ", "SMW", "B2", "20260518", "北滩二区", 121.76, 29.11,
         "泥滩", "杨德援", "legacy", "{}"),
    )
    conn.commit()
    return conn


def test_migration_adds_zone_and_new_columns():
    conn = _legacy_db()
    # 迁移前：无 zone
    before = {r[1] for r in conn.execute("PRAGMA table_info(collection_records)")}
    assert "zone" not in before
    assert "quadrate_no" not in before
    assert "wire_out" not in before

    db_manager._migrate_add_missing_columns(
        conn, db_manager._SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    )
    after = {r[1] for r in conn.execute("PRAGMA table_info(collection_records)")}
    for newcol in ("zone", "quadrate_no", "air_temp", "quant_bottles", "qual_bottles",
                   "sample_thickness", "wire_out", "sampler_area", "net_type",
                   "net_width", "trawl_distance", "trawl_start", "trawl_end",
                   "grab_sample_total", "trawl_sample_total"):
        assert newcol in after, f"迁移未加列 {newcol}"


def test_legacy_data_survives_and_zone_null():
    conn = _legacy_db()
    db_manager._migrate_add_missing_columns(
        conn, db_manager._SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    )
    row = conn.execute(
        "SELECT * FROM collection_records WHERE station='B2'"
    ).fetchone()
    assert row["collector"] == "杨德援"          # 旧数据不丢
    assert row["remark"] == "legacy"
    assert row["lon"] == pytest.approx(121.76)
    assert row["zone"] is None                    # 历史行 zone=NULL
    assert row["quadrate_no"] is None


def test_legacy_record_still_lookup_and_classifiable():
    """迁移后旧记录可被 4 键 lookup（workbench autofill 不破），并可补分类。"""
    conn = _legacy_db()
    db_manager.ensure_schema(conn)   # 完整路径：executescript + migrate + view
    rec = crs.lookup_record(conn, "ZJ", "SMW", "B2", "20260518")
    assert rec is not None
    assert rec["collector"] == "杨德援"
    # 给历史记录补 zone（按 id）
    crs.upsert_record(conn, {**rec, "id": rec["id"], "zone": "intertidal"})
    rec2 = crs.lookup_record(conn, "ZJ", "SMW", "B2", "20260518")
    assert rec2["zone"] == "intertidal"


def test_new_record_after_migration_writes_zone_fields():
    conn = _legacy_db()
    db_manager.ensure_schema(conn)
    crs.upsert_record(conn, {
        "province": "ZJ", "site": "SMW", "station": "H1",
        "collection_date": "20260601", "zone": "subtidal",
        "wire_out": "30", "net_type": "阿氏网", "trawl_distance": "400",
    })
    rec = crs.lookup_record(conn, "ZJ", "SMW", "H1", "20260601")
    assert rec["zone"] == "subtidal"
    assert rec["wire_out"] == "30"
    assert rec["net_type"] == "阿氏网"
