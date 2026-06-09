"""test_geo_calibration.py — 控制点校准（经纬度 → 底图像素）拟合.

采集地图出版底图是投影图（经纬网弯曲）。用户点几个已知经纬网交点 + 输入其经纬度，
本模块用最小二乘拟合 经纬度→像素 的变换（仿射 / 二次多项式），并报残差 RMS（像素）
让用户判断落点精度。纯 numpy，无 Qt。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_geo_calibration.py -v
"""
from __future__ import annotations

import json

import pytest

from app.services import geo_calibration as gc


# ── 仿射（order=1）─────────────────────────────────────────────────────────────

class TestAffine:
    def _affine_pts(self):
        # 真值仿射：px = 10 + 2*lon ; py = 5 + 3*lat
        out = []
        for lon, lat in [(0, 0), (10, 0), (0, 10), (10, 10), (-5, 5)]:
            out.append((lon, lat, 10 + 2 * lon, 5 + 3 * lat))
        return out

    def test_exact_fit_rms_zero(self):
        m = gc.fit(self._affine_pts(), order=1)
        assert m["order"] == 1
        assert m["rms_px"] == pytest.approx(0.0, abs=1e-6)

    def test_project_reproduces(self):
        m = gc.fit(self._affine_pts(), order=1)
        px, py = gc.project(m, 7, 4)
        assert px == pytest.approx(10 + 2 * 7)
        assert py == pytest.approx(5 + 3 * 4)

    def test_too_few_points_raises(self):
        with pytest.raises(ValueError):
            gc.fit([(0, 0, 0, 0), (1, 1, 1, 1)], order=1)   # 仅 2 点 < 3


# ── 二次多项式（order=2，吸收投影弯曲）─────────────────────────────────────────

class TestQuadratic:
    def _curved_pts(self):
        # 非线性真值：px = 1 + lon + 0.05*lon^2 ; py = 2 + lat + 0.03*lat*lon
        pts = []
        coords = [(0, 0), (10, 0), (0, 10), (10, 10), (-10, 5),
                  (5, -10), (20, 20), (-15, -5)]
        for lon, lat in coords:
            px = 1 + lon + 0.05 * lon * lon
            py = 2 + lat + 0.03 * lat * lon
            pts.append((lon, lat, px, py))
        return pts

    def test_quadratic_fits_curved(self):
        m = gc.fit(self._curved_pts(), order=2)
        assert m["order"] == 2
        assert m["rms_px"] == pytest.approx(0.0, abs=1e-6)
        px, py = gc.project(m, 8, 8)
        assert px == pytest.approx(1 + 8 + 0.05 * 64)
        assert py == pytest.approx(2 + 8 + 0.03 * 8 * 8)

    def test_affine_worse_on_curved(self):
        pts = self._curved_pts()
        m1 = gc.fit(pts, order=1)
        m2 = gc.fit(pts, order=2)
        assert m2["rms_px"] < m1["rms_px"]   # 二次更贴合弯曲

    def test_quadratic_too_few_raises(self):
        pts = [(i, i, i, i) for i in range(5)]   # 仅 5 点 < 6
        with pytest.raises(ValueError):
            gc.fit(pts, order=2)


# ── 序列化 + 批量 ──────────────────────────────────────────────────────────────

class TestSerialize:
    def test_model_json_round_trip(self):
        pts = [(lon, lat, 10 + 2 * lon, 5 + 3 * lat)
               for lon, lat in [(0, 0), (10, 0), (0, 10), (5, 5)]]
        m = gc.fit(pts, order=1)
        s = json.dumps(m)                 # 必须可 JSON 序列化（存 sidecar）
        m2 = json.loads(s)
        assert gc.project(m2, 3, 3) == pytest.approx(gc.project(m, 3, 3))

    def test_project_many_matches_scalar(self):
        pts = [(lon, lat, 10 + 2 * lon, 5 + 3 * lat)
               for lon, lat in [(0, 0), (10, 0), (0, 10), (5, 5)]]
        m = gc.fit(pts, order=1)
        xs, ys = gc.project_many(m, [1, 2, 3], [4, 5, 6])
        assert list(xs) == pytest.approx([gc.project(m, l, a)[0] for l, a in [(1, 4), (2, 5), (3, 6)]])
        assert list(ys) == pytest.approx([gc.project(m, l, a)[1] for l, a in [(1, 4), (2, 5), (3, 6)]])
