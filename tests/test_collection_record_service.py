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


# ── zone 采区分带（潮间带 H.39 / 潮下带 H.30）──────────────────────────────────

class TestZone:
    def test_zone_columns_has_both_zones(self):
        assert set(crs.ZONE_COLUMNS) == {"intertidal", "subtidal"}

    def test_intertidal_columns_match_H39(self):
        keys = {k for k, _zh in crs.ZONE_COLUMNS["intertidal"]}
        # H.39 潮间带专属
        for must in ("quadrate_no", "air_temp", "quant_bottles", "qual_bottles",
                     "sample_thickness", "tidal_zone", "tide", "sample_area"):
            assert must in keys, f"潮间带缺 {must}"
        # 不应含潮下带专属
        for nope in ("wire_out", "net_type", "trawl_distance", "cruise"):
            assert nope not in keys, f"潮间带不应含 {nope}"

    def test_subtidal_columns_match_H30(self):
        keys = {k for k, _zh in crs.ZONE_COLUMNS["subtidal"]}
        # H.30 潮下带专属
        for must in ("cruise", "vessel", "wire_out", "sampler_area", "net_type",
                     "net_width", "trawl_distance", "trawl_start", "trawl_end",
                     "grab_sample_total", "trawl_sample_total", "sample_thickness"):
            assert must in keys, f"潮下带缺 {must}"
        # 不应含潮间带专属
        for nope in ("quadrate_no", "air_temp", "quant_bottles", "qual_bottles"):
            assert nope not in keys, f"潮下带不应含 {nope}"

    def test_zone_columns_subset_of_db_columns(self):
        """两 zone 列序里的每个 key 都必须是真 DB 列。"""
        dbcols = set(crs._COLUMNS)
        for zcols in crs.ZONE_COLUMNS.values():
            for k, _zh in zcols:
                assert k in dbcols, f"{k} 不在 _COLUMNS（schema 缺列）"

    def test_zone_columns_headers_unique_per_zone(self):
        for z, zcols in crs.ZONE_COLUMNS.items():
            hdrs = [zh for _k, zh in zcols]
            assert len(hdrs) == len(set(hdrs)), f"{z} 表头重复"

    def test_columns_for_zone(self):
        assert crs.columns_for_zone("intertidal")
        assert crs.columns_for_zone("subtidal")
        assert crs.columns_for_zone(None) == []
        assert crs.columns_for_zone("bogus") == []

    def test_infer_zone_from_headers(self):
        assert crs.infer_zone_from_headers(["地区", "站位", "样方号", "气温(℃)"]) == "intertidal"
        assert crs.infer_zone_from_headers(["地区", "站位", "航次", "放绳长度(m)"]) == "subtidal"
        assert crs.infer_zone_from_headers(["地区", "站位", "采集日期"]) is None
        # H.30 专属优先
        assert crs.infer_zone_from_headers(["站位", "样方号", "网型"]) == "subtidal"

    def test_upsert_writes_and_reads_zone(self, db):
        rid = crs.upsert_record(db, _sample(zone="intertidal"))
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["zone"] == "intertidal"
        assert isinstance(rid, int)

    def test_upsert_preserves_zone_when_incoming_empty(self, db):
        """已分类记录再被无 zone 的 upsert 命中（如站位导入）→ 不冲掉 zone。"""
        crs.upsert_record(db, _sample(zone="subtidal"))
        # ON CONFLICT 路径：同 4 键、不带 zone
        crs.upsert_record(db, _sample())  # 无 zone 键
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["zone"] == "subtidal"

    def test_upsert_preserves_zone_by_id_when_incoming_empty(self, db):
        rid = crs.upsert_record(db, _sample(zone="intertidal"))
        crs.upsert_record(db, _sample(id=rid))  # 按 id 更新、无 zone
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["zone"] == "intertidal"

    def test_intertidal_fields_roundtrip(self, db):
        extra = {"zone": "intertidal", "quadrate_no": "B2-Q3", "air_temp": "26",
                 "quant_bottles": "2", "qual_bottles": "1", "sample_thickness": "15"}
        crs.upsert_record(db, _sample(**extra))
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        for k, v in extra.items():
            assert rec[k] == v, f"{k} 未持久化: {rec.get(k)!r}"

    def test_subtidal_fields_roundtrip(self, db):
        extra = {"zone": "subtidal", "wire_out": "32", "sampler_area": "0.1",
                 "net_type": "阿氏网", "net_width": "2", "trawl_distance": "500",
                 "trawl_start": "09:10", "trawl_end": "09:20",
                 "grab_sample_total": "2", "trawl_sample_total": "1"}
        crs.upsert_record(db, _sample(**extra))
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        for k, v in extra.items():
            assert rec[k] == v, f"{k} 未持久化: {rec.get(k)!r}"


# ── set_station_coords（采集地图：拖点/绑点 → 整站坐标刷新）─────────────────────

class TestSetStationCoords:
    """set_station_coords 更新一站全部行的 lon/lat。

    采集地图按 (province,site,station) 聚合到质心；只改一行点不动，必须整站刷。
    """

    def test_updates_all_rows_of_station(self, db):
        crs.upsert_record(db, _sample(station="B2", collection_date="20260518",
                                      lon="121.0", lat="29.0"))
        crs.upsert_record(db, _sample(station="B2", collection_date="20260519",
                                      lon="123.0", lat="31.0"))
        n = crs.set_station_coords(db, "ZJ", "SMW", "B2", 122.5, 30.5)
        assert n == 2
        for d in ("20260518", "20260519"):
            rec = crs.lookup_record(db, "ZJ", "SMW", "B2", d)
            assert rec["lon"] == pytest.approx(122.5)
            assert rec["lat"] == pytest.approx(30.5)

    def test_leaves_other_stations_untouched(self, db):
        crs.upsert_record(db, _sample(station="B2", collection_date="20260518",
                                      lon="121.0", lat="29.0"))
        crs.upsert_record(db, _sample(station="H1", collection_date="20260518",
                                      lon="125.0", lat="33.0"))
        crs.set_station_coords(db, "ZJ", "SMW", "B2", 122.5, 30.5)
        h1 = crs.lookup_record(db, "ZJ", "SMW", "H1", "20260518")
        assert h1["lon"] == pytest.approx(125.0)
        assert h1["lat"] == pytest.approx(33.0)

    def test_returns_zero_for_unknown_station(self, db):
        crs.upsert_record(db, _sample())
        assert crs.set_station_coords(db, "ZJ", "SMW", "ZZZ", 1.0, 2.0) == 0

    def test_empty_lon_clears_coords(self, db):
        """传空 → 该站行 lon/lat 置 NULL（与 upsert 的 _coerce 语义一致）。"""
        crs.upsert_record(db, _sample(station="B2", lon="121.0", lat="29.0"))
        crs.set_station_coords(db, "ZJ", "SMW", "B2", "", "")
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["lon"] is None
        assert rec["lat"] is None


# ── sync_coords_from_capture（拍照界面 → 采集记录）────────────────────────────

class TestSyncCoordsFromCapture:
    def test_creates_record_when_missing(self, db):
        action = crs.sync_coords_from_capture(
            db,
            province="ZJ",
            site="SMW",
            station="B2",
            collection_date="20260518",
            lon="121.5",
            lat="29.2",
            extra={"collector": "张三", "geo_area": "三门湾"},
        )
        assert action == "created"
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec is not None
        assert rec["lon"] == pytest.approx(121.5)
        assert rec["lat"] == pytest.approx(29.2)
        assert rec["collector"] == "张三"
        assert rec["geo_area"] == "三门湾"

    def test_updates_coords_without_wiping_habitat(self, db):
        crs.upsert_record(db, _sample(habitat="泥滩", tide="低潮", lon="1", lat="2"))
        action = crs.sync_coords_from_capture(
            db,
            province="ZJ",
            site="SMW",
            station="B2",
            collection_date="20260518",
            lon="122.1",
            lat="30.1",
        )
        assert action == "updated"
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec["lon"] == pytest.approx(122.1)
        assert rec["lat"] == pytest.approx(30.1)
        assert rec["habitat"] == "泥滩"
        assert rec["tide"] == "低潮"

    def test_skips_when_keys_incomplete(self, db):
        assert crs.sync_coords_from_capture(
            db,
            province="ZJ",
            site="SMW",
            station="",
            collection_date="20260518",
            lon="1",
            lat="2",
        ) == "skipped"
        assert crs.list_records(db) == []

    def test_skips_create_when_coords_empty(self, db):
        assert crs.sync_coords_from_capture(
            db,
            province="ZJ",
            site="SMW",
            station="B2",
            collection_date="20260518",
            lon="",
            lat="",
        ) == "skipped"
        assert crs.list_records(db) == []


class TestFourKeyNormalization:
    """采集记录四键归一化(2026-07-11 用户报障根因): Excel 导入把日期读成
    '2026-05-18 00:00:00'、位置不转大写, 而工作台查记录时日期是 8 位、位置
    已 .upper() → 精确四键匹配恒不中 → 自动填充静默失效。写入和读取两侧都
    归一化(日期剥非数字取 8 位、位置转大写)后, 无论输入格式都能命中。"""

    def test_excel_datetime_date_is_normalized_on_write(self, db):
        crs.upsert_record(db, _sample(collection_date="2026-05-18 00:00:00"))
        # 工作台用规范 8 位日期查 → 必须命中
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec is not None
        assert rec["collector"] == "杨德援"

    def test_lowercase_location_is_normalized_on_write(self, db):
        crs.upsert_record(db, _sample(site="smw", station="b2", province="zj"))
        # 工作台位置键已 .upper() → 大写查必须命中
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "20260518")
        assert rec is not None

    def test_lookup_normalizes_query_side_too(self, db):
        """反向:记录规范存储, 查询侧给了脏格式也应归一化后命中(防御)。"""
        crs.upsert_record(db, _sample())
        rec = crs.lookup_record(db, "zj", "smw", "b2", "2026/05/18")
        assert rec is not None

    def test_upsert_is_idempotent_across_date_formats(self, db):
        """同一逻辑记录用不同日期格式导入两次 → 归一化后是同一条, 不重复。"""
        crs.upsert_record(db, _sample(collection_date="20260518"))
        crs.upsert_record(db, _sample(collection_date="2026-05-18 00:00:00", collector="改名"))
        rows = crs.list_records(db)
        same = [r for r in rows if r["station"] == "B2" and r["collection_date"] == "20260518"]
        assert len(same) == 1, "两种日期格式应归一化为同一条记录"
        assert same[0]["collector"] == "改名"
