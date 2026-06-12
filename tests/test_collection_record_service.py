"""test_collection_record_service.py — unit tests for the 采集记录簿 service.

The collection-record registry is the desktop's "field collection log": one
row per (province, site, station, collection_date) carrying the full set of
field metadata (coords / habitat / tide / collector / …). The workbench later
looks a row up by those four keys and auto-fills the subset of fields it owns.

Pure logic, no Qt. DB is an in-memory SQLite seeded via ensure_schema().

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collection_record_service.py -v
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db.db_manager import ensure_schema
from app.services import collection_record_service as crs


@pytest.fixture()
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    yield conn
    conn.close()


def _sample(**over) -> dict:
    base = {
        "province": "ZJ",
        "site": "SMW",
        "station": "B2",
        "collection_date": "20260518",
        "station_label": "北滩二区",
        "lon": "121.76421",
        "lat": "29.11492",
        "geo_area": "三门湾",
        "habitat": "泥滩",
        "tide": "低潮 14:30",
        "collector": "杨德援",
        "photographer": "钟珅",
        "identifier": "",
        "photo_date": "20260519",
        "photo_location": "实验室",
        "method": "手拣",
        "remark": "test",
    }
    base.update(over)
    return base


# ── upsert + lookup ──────────────────────────────────────────────────────────

class TestUpsertLookup:
    def test_insert_then_lookup_by_four_keys(self, db):
        crs.upsert_record(db, _sample())
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec is not None
        assert rec["habitat"] == "泥滩"
        assert rec["tide"] == "低潮 14:30"
        assert rec["collector"] == "杨德援"
        # lon/lat round-trip as floats
        assert rec["lon"] == pytest.approx(121.76421)
        assert rec["lat"] == pytest.approx(29.11492)

    def test_lookup_missing_returns_none(self, db):
        crs.upsert_record(db, _sample())
        assert crs.lookup_record(db, "ZJ", "SMW", "B9", "20260518") is None
        # different date → different record key
        assert crs.lookup_record(db, "ZJ", "SMW", "B2", "20260101") is None

    def test_upsert_is_idempotent_on_four_keys(self, db):
        crs.upsert_record(db, _sample(collector="A"))
        crs.upsert_record(db, _sample(collector="B"))  # same 4-key, new value
        assert len(crs.list_records(db)) == 1
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["collector"] == "B"

    def test_different_date_makes_new_record(self, db):
        crs.upsert_record(db, _sample(collection_date="20260518"))
        crs.upsert_record(db, _sample(collection_date="20260601"))
        assert len(crs.list_records(db)) == 2


# ── list + delete ────────────────────────────────────────────────────────────

class TestListDelete:
    def test_list_records_returns_all(self, db):
        crs.upsert_record(db, _sample(station="B2"))
        crs.upsert_record(db, _sample(station="H1"))
        recs = crs.list_records(db)
        assert {r["station"] for r in recs} == {"B2", "H1"}

    def test_delete_record(self, db):
        rid = crs.upsert_record(db, _sample())
        crs.delete_record(db, rid)
        assert crs.list_records(db) == []
        assert crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518") is None


# ── domain invariants ────────────────────────────────────────────────────────

class TestInvariants:
    def test_empty_lonlat_stored_as_null_not_zero(self, db):
        """Empty lon/lat strings → NULL, never 0 (mirrors specimens gotcha)."""
        crs.upsert_record(db, _sample(lon="", lat=""))
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["lon"] is None
        assert rec["lat"] is None

    def test_raw_json_preserves_unknown_fields(self, db):
        """Fields with no column survive via the raw_json fallback."""
        crs.upsert_record(db, _sample(salinity_extra="30‰", weird_field="x"))
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["salinity_extra"] == "30‰"
        assert rec["weird_field"] == "x"

    def test_macrobenthos_quant_fields_roundtrip(self, db):
        """大型底栖定量调查新字段作为真列持久化 + 读回（非 raw_json 兜底）。"""
        extra = {
            "sample_type": "定量", "water_body": "东海·三门湾",
            "cruise": "2026春季三门湾航次", "vessel": "科学三号",
            "sampler_model": "大洋50型", "sample_no": "B2-2026-007",
            "recorder": "李四", "checker": "王五",
            "tidal_zone": "中潮区", "depth": "5", "bottom_temp": "14",
            "dissolved_oxygen": "7.2", "ph": "8.1", "sampler_spec": "0.1m²采泥器",
            "sample_area": "0.2", "replicates": "4", "sieve_mesh": "1.0",
        }
        crs.upsert_record(db, _sample(**extra))
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        for k, v in extra.items():
            assert rec[k] == v, f"{k} 未持久化: {rec.get(k)!r}"

    def test_upsert_returns_stable_id_on_update(self, db):
        """Re-upserting the same 4-key keeps the same row id."""
        rid1 = crs.upsert_record(db, _sample(collector="A"))
        rid2 = crs.upsert_record(db, _sample(collector="B"))
        assert rid1 == rid2


# ── autofill_values (non-destructive subset) ──────────────────────────────────

class TestAutofillValues:
    def test_fills_only_empty_fields(self):
        record = _sample(collector="杨德援", lon="121.5", geo_area="三门湾")
        current = {"collector": "已填", "lon": "", "geo_area": None}
        out = crs.autofill_values(record, current)
        assert "collector" not in out          # user value preserved
        assert out["lon"] == "121.5"           # empty → filled
        assert out["geo_area"] == "三门湾"      # None → filled

    def test_skips_empty_record_values(self):
        record = _sample(identifier="", photographer="钟珅")
        current = {"identifier": "", "photographer": ""}
        out = crs.autofill_values(record, current)
        assert "identifier" not in out         # record blank → nothing to fill
        assert out["photographer"] == "钟珅"

    def test_only_capture_card_fields_returned(self):
        """habitat / tide have no capture slot → never auto-filled."""
        record = _sample(habitat="泥滩", tide="低潮")
        out = crs.autofill_values(record, {})
        assert "habitat" not in out
        assert "tide" not in out
        assert set(out).issubset(set(crs.AUTOFILL_FIELDS))


# ── map_points 聚合（采集地图数据源）─────────────────────────────────────────

class TestMapPoints:
    """map_points(db, level) 聚合站位经纬度供采集地图分级绘制。"""

    def _seed(self, db):
        # 两条同站位 B2（不同日期）→ 站位级聚合成 1 点、count=2、坐标取均值
        crs.upsert_record(db, _sample(station="B2", collection_date="20260518",
                                      lon="121.0", lat="29.0", station_label="北滩二区"))
        crs.upsert_record(db, _sample(station="B2", collection_date="20260519",
                                      lon="123.0", lat="31.0", station_label="北滩二区"))
        # 同样地 SMW 下另一站位 H1（无 station_label）
        crs.upsert_record(db, _sample(station="H1", collection_date="20260520",
                                      lon="125.0", lat="33.0", station_label=""))
        # 另一地区 FJ / 样地 XM
        crs.upsert_record(db, _sample(province="FJ", site="XM", station="A1",
                                      collection_date="20260601",
                                      lon="118.0", lat="24.0", station_label="厦门湾"))

    def test_station_level_groups_and_averages(self, db):
        self._seed(db)
        pts = crs.map_points(db, "station")
        # B2, H1 (ZJ/SMW) + A1 (FJ/XM) = 3 个站位点
        assert len(pts) == 3
        b2 = next(p for p in pts if p["station"] == "B2")
        assert b2["count"] == 2
        assert b2["lon"] == pytest.approx(122.0)   # (121+123)/2
        assert b2["lat"] == pytest.approx(30.0)    # (29+31)/2
        assert b2["label"] == "北滩二区"
        assert b2["level"] == "station"
        assert b2["province"] == "ZJ" and b2["site"] == "SMW"

    def test_station_label_falls_back_to_code(self, db):
        self._seed(db)
        pts = crs.map_points(db, "station")
        h1 = next(p for p in pts if p["station"] == "H1")
        assert h1["label"] == "H1"   # station_label 空 → 用 station 码

    def test_site_level_aggregates_stations(self, db):
        self._seed(db)
        pts = crs.map_points(db, "site")
        # ZJ/SMW（3 行）+ FJ/XM（1 行）= 2 个样地点
        assert len(pts) == 2
        smw = next(p for p in pts if p["site"] == "SMW")
        assert smw["count"] == 3                    # B2×2 + H1×1
        assert smw["lon"] == pytest.approx((121.0 + 123.0 + 125.0) / 3)
        assert smw["label"] == "SMW"
        assert smw["station"] is None               # 上层无 station
        assert smw["level"] == "site"

    def test_province_level_aggregates_all(self, db):
        self._seed(db)
        pts = crs.map_points(db, "province")
        assert len(pts) == 2                         # ZJ + FJ
        zj = next(p for p in pts if p["province"] == "ZJ")
        assert zj["count"] == 3
        assert zj["label"] == "ZJ"
        assert zj["site"] is None and zj["station"] is None

    def test_filters_null_coords(self, db):
        # 空经纬度（存 NULL）不应进地图
        crs.upsert_record(db, _sample(station="NX", collection_date="20260701",
                                      lon="", lat=""))
        crs.upsert_record(db, _sample(station="OK", collection_date="20260702",
                                      lon="120.0", lat="28.0"))
        pts = crs.map_points(db, "station")
        stations = {p["station"] for p in pts}
        assert "NX" not in stations
        assert "OK" in stations

    def test_all_null_returns_empty(self, db):
        crs.upsert_record(db, _sample(lon="", lat=""))
        assert crs.map_points(db, "station") == []

    def test_invalid_level_raises(self, db):
        with pytest.raises(ValueError):
            crs.map_points(db, "galaxy")


# ── map_points_across 跨项目聚合 ───────────────────────────────────────────────

class TestMapPointsAcross:
    def _db(self):
        import sqlite3
        from app.db.db_manager import ensure_schema
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        ensure_schema(c)
        return c

    def test_merges_centroid_weighted_across_dbs(self):
        a = self._db(); b = self._db()
        # 同一站位 ZJ/SMW/B2 跨两库：a 两行(121,123)，b 一行(125)
        crs.upsert_record(a, _sample(station="B2", collection_date="20260518", lon="121.0", lat="29.0"))
        crs.upsert_record(a, _sample(station="B2", collection_date="20260519", lon="123.0", lat="31.0"))
        crs.upsert_record(b, _sample(station="B2", collection_date="20260520", lon="125.0", lat="33.0"))
        pts = crs.map_points_across([a, b], "station")
        b2 = next(p for p in pts if p["station"] == "B2")
        assert b2["count"] == 3
        assert b2["lon"] == pytest.approx((121.0 + 123.0 + 125.0) / 3)
        assert b2["lat"] == pytest.approx((29.0 + 31.0 + 33.0) / 3)

    def test_province_level_across(self):
        a = self._db(); b = self._db()
        crs.upsert_record(a, _sample(province="ZJ", site="SMW", station="B2",
                                     collection_date="20260518", lon="121.0", lat="29.0"))
        crs.upsert_record(b, _sample(province="FJ", site="XM", station="A1",
                                     collection_date="20260601", lon="118.0", lat="24.0"))
        pts = crs.map_points_across([a, b], "province")
        assert {p["province"] for p in pts} == {"ZJ", "FJ"}

    def test_filters_null_and_empty(self):
        a = self._db()
        crs.upsert_record(a, _sample(station="NX", collection_date="20260701", lon="", lat=""))
        assert crs.map_points_across([a], "station") == []
        assert crs.map_points_across([], "station") == []
