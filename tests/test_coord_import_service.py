"""test_coord_import_service.py — 采集计划站位批量导入（Excel/CSV/TXT）.

读表 + 用户列映射 + coord_utils 任意格式经纬度解析 + 坐标系统一 WGS84。
纯逻辑，无 Qt。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_coord_import_service.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services import coord_import_service as cis


# ── 读表 ───────────────────────────────────────────────────────────────────────

class TestReadTable:
    def test_read_csv(self, tmp_path: Path):
        p = tmp_path / "s.csv"
        p.write_text("地区,断面,站位,经度,纬度\nZJ,SMW,B2,121.76,29.11\n", encoding="utf-8")
        headers, rows = cis.read_table(str(p))
        assert headers == ["地区", "断面", "站位", "经度", "纬度"]
        assert rows[0]["站位"] == "B2"

    def test_read_txt_tab(self, tmp_path: Path):
        p = tmp_path / "s.txt"
        p.write_text("prov\tstation\tlon\tlat\nZJ\tB2\t121.0\t29.0\n", encoding="utf-8")
        headers, rows = cis.read_table(str(p))
        assert "station" in headers
        assert rows[0]["lon"] == "121.0"

    def test_read_xlsx(self, tmp_path: Path):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["地区", "站位", "经度", "纬度"])
        ws.append(["ZJ", "B2", 121.76, 29.11])
        p = tmp_path / "s.xlsx"
        wb.save(p)
        headers, rows = cis.read_table(str(p))
        assert headers == ["地区", "站位", "经度", "纬度"]
        assert rows[0]["站位"] == "B2"

    def test_read_csv_no_header(self, tmp_path: Path):
        """has_header=False → synthesise column names, keep row 0 as data."""
        p = tmp_path / "n.csv"
        p.write_text("Abra alba\nCancer pagurus\n", encoding="utf-8")
        headers, rows = cis.read_table(str(p), has_header=False)
        assert len(rows) == 2                       # first row NOT consumed as header
        assert rows[0][headers[0]] == "Abra alba"
        assert rows[1][headers[0]] == "Cancer pagurus"

    def test_read_xlsx_no_header(self, tmp_path: Path):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Abra alba"])
        ws.append(["Cancer pagurus"])
        p = tmp_path / "n.xlsx"
        wb.save(p)
        headers, rows = cis.read_table(str(p), has_header=False)
        assert len(rows) == 2
        assert rows[0][headers[0]] == "Abra alba"


class TestSampleTable:
    def test_sample_table_is_previewable(self):
        headers, rows = cis.sample_table()
        assert headers == cis.SAMPLE_HEADERS
        assert rows and rows[0]["经度"]

    def test_sample_preview_rows_parse_lon_lat(self):
        rows = cis.sample_preview_rows()
        assert len(rows) == len(cis.SAMPLE_ROWS)
        assert all(r["ok"] for r in rows)
        assert rows[0]["lon"] == pytest.approx(121.6543)
        assert rows[1]["lat"] == pytest.approx(29.1217, abs=1e-3)


# ── 规范化 ─────────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_separate_lon_lat_dd(self):
        rows = [{"地区": "ZJ", "断面": "SMW", "站位": "B2", "经度": "121.76", "纬度": "29.11"}]
        mapping = {"province": "地区", "site": "断面", "station": "站位",
                   "lon": "经度", "lat": "纬度"}
        out = cis.normalize_rows(rows, mapping)
        r = out[0]
        assert r["ok"] is True
        assert r["province"] == "ZJ" and r["station"] == "B2"
        assert r["lon"] == pytest.approx(121.76)
        assert r["lat"] == pytest.approx(29.11)
        assert r["collection_date"] == ""          # 计划阶段无日期

    def test_combined_lonlat_dms(self):
        rows = [{"站位": "B2", "坐标": "29°06'53\"N 121°45'51\"E"}]
        mapping = {"station": "站位", "lonlat": "坐标"}
        out = cis.normalize_rows(rows, mapping)
        r = out[0]
        assert r["ok"] is True
        assert r["lat"] == pytest.approx(29.1147, abs=1e-3)
        assert r["lon"] == pytest.approx(121.7642, abs=1e-3)

    def test_gcj02_shifted_to_wgs84(self):
        # 上海附近：GCJ02 与 WGS84 有数十米偏移
        rows = [{"站位": "X", "经度": "121.500000", "纬度": "31.230000"}]
        mapping = {"station": "站位", "lon": "经度", "lat": "纬度"}
        wgs = cis.normalize_rows(rows, mapping, coord_system="WGS84")[0]
        gcj = cis.normalize_rows(rows, mapping, coord_system="GCJ02")[0]
        assert gcj["ok"] and wgs["ok"]
        # GCJ02→WGS84 后与原值不同（被纠偏）
        assert abs(gcj["lon"] - wgs["lon"]) > 1e-4

    def test_bad_coord_marks_error(self):
        rows = [{"站位": "B2", "经度": "乱码", "纬度": ""}]
        mapping = {"station": "站位", "lon": "经度", "lat": "纬度"}
        r = cis.normalize_rows(rows, mapping)[0]
        assert r["ok"] is False
        assert r["error"]

    def test_default_date_applied(self):
        rows = [{"站位": "B2", "经度": "121.0", "纬度": "29.0"}]
        mapping = {"station": "站位", "lon": "经度", "lat": "纬度"}
        r = cis.normalize_rows(rows, mapping, default_date="20260601")[0]
        assert r["collection_date"] == "20260601"

    def test_output_upsertable(self):
        # 输出可直接喂 crs.upsert_record（内存库验证）
        import sqlite3
        from app.db.db_manager import ensure_schema
        from app.services import collection_record_service as crs
        rows = [{"地区": "ZJ", "断面": "SMW", "站位": "B2", "经度": "121.0", "纬度": "29.0"}]
        mapping = {"province": "地区", "site": "断面", "station": "站位",
                   "lon": "经度", "lat": "纬度"}
        out = [r for r in cis.normalize_rows(rows, mapping) if r["ok"]]
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        ensure_schema(db)
        for r in out:
            crs.upsert_record(db, r)
        rec = crs.lookup_record(db, "ZJ", "SMW", "B2", "")
        assert rec is not None and rec["lon"] == pytest.approx(121.0)
