"""test_geo_basemap.py — Natural Earth GeoJSON 加载 + 生成底图投影预设.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_geo_basemap.py -v
"""
from __future__ import annotations

import pytest

from app.services import geo_basemap as gb


class TestLoadGeometries:
    def test_load_land_rings(self):
        rings = gb.load_geometries("ne_110m_land")
        assert len(rings) > 50                       # 全球陆块若干
        ring = rings[0]
        assert len(ring) >= 3
        lon, lat = ring[0]
        assert -180 <= lon <= 180 and -90 <= lat <= 90

    def test_missing_returns_empty(self):
        assert gb.load_geometries("不存在的数据集") == []


class TestPresets:
    def test_presets_nonempty_and_shaped(self):
        ps = gb.generated_presets()
        assert len(ps) >= 3
        for p in ps:
            assert p["kind"] == "generated"
            assert p["id"].startswith("generated:")
            assert "proj" in p and p["name"]

    def test_has_robinson_and_china(self):
        names = " ".join(p["name"] for p in gb.generated_presets())
        assert "Robinson" in names or "罗宾森" in names
        assert "中国" in names


class TestProjection:
    def test_project_lonlat_platecarree_identity(self):
        # PlateCarree(等距圆柱) 下投影≈原经纬度
        xs, ys = gb.project_points("EPSG:4326", [120.0], [30.0])
        assert xs[0] == pytest.approx(120.0)
        assert ys[0] == pytest.approx(30.0)

    def test_project_robinson_changes_coords(self):
        xs, ys = gb.project_points("+proj=robin", [120.0], [30.0])
        # 罗宾森投影单位为米，量级远大于度
        assert abs(xs[0]) > 1000


class TestMorePresets:
    def test_has_journal_projections(self):
        names = " ".join(p["name"] for p in gb.generated_presets())
        for kw in ("Equal Earth", "Winkel", "近海", "北极"):
            assert kw in names, kw

    def test_regional_uses_50m_detail(self):
        china = next(p for p in gb.generated_presets() if p["id"] == "generated:china_seas")
        assert china["detail"] == 50
        assert gb.load_geometries("ne_50m_coastline")   # 50m 数据随包
