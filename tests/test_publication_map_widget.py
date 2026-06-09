"""test_publication_map_widget.py — 出版底图画布（matplotlib 嵌入）冒烟 + 导出.

PublicationMapWidget 用 matplotlib 画「底图 + 校准后散点」，并经 savefig 导出
PNG/PDF/SVG/EPS。本测试用临时小图 + 校准模型验证渲染不崩、导出产物非空。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_publication_map_widget.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.services import geo_calibration as gc

_APP = QApplication.instance() or QApplication([])


def _make_image(tmp_path: Path, w=200, h=120) -> Path:
    from PIL import Image
    img = Image.new("RGB", (w, h), (220, 235, 235))
    p = tmp_path / "base.png"
    img.save(p)
    return p


def _calib_model():
    # 简单仿射：经度 0..100 → px 0..200；纬度 0..100 → py 120..0（纬度大在上）
    pts = [(0, 0, 0, 120), (100, 0, 200, 120), (0, 100, 0, 0), (100, 100, 200, 0)]
    return gc.fit(pts, order=1)


def _widget():
    from app.widgets.publication_map_widget import PublicationMapWidget
    w = PublicationMapWidget()
    w.resize(640, 400)
    return w


def _pts():
    return [{"lon": 50, "lat": 50, "label": "B2", "count": 3},
            {"lon": 20, "lat": 80, "label": "H1", "count": 1}]


class TestRender:
    def test_instantiates(self):
        assert _widget() is not None

    def test_render_image_with_points(self, tmp_path):
        w = _widget()
        entry = {"id": "image:base.png", "name": "base", "kind": "image",
                 "source": str(_make_image(tmp_path)), "ext": ".png"}
        w.set_basemap(entry, calibration={"model": _calib_model()})
        w.set_points(_pts())
        w.render()   # 不崩

    def test_render_without_calibration_no_crash(self, tmp_path):
        w = _widget()
        entry = {"id": "image:base.png", "name": "base", "kind": "image",
                 "source": str(_make_image(tmp_path)), "ext": ".png"}
        w.set_basemap(entry, calibration=None)   # 未校准
        w.set_points(_pts())
        w.render()   # 仅画底图，不崩

    def test_no_basemap_no_crash(self):
        w = _widget()
        w.set_points(_pts())
        w.render()


class TestExport:
    @pytest.mark.parametrize("fmt", ["png", "pdf", "svg"])
    def test_export_writes_nonempty(self, tmp_path, fmt):
        w = _widget()
        entry = {"id": "image:base.png", "name": "base", "kind": "image",
                 "source": str(_make_image(tmp_path)), "ext": ".png"}
        w.set_basemap(entry, calibration={"model": _calib_model()})
        w.set_points(_pts())
        out = tmp_path / f"map.{fmt}"
        w.export(str(out), fmt=fmt, dpi=150)
        assert out.exists() and out.stat().st_size > 0

    def test_export_infers_format_from_suffix(self, tmp_path):
        w = _widget()
        w.set_points(_pts())
        out = tmp_path / "fig.png"
        w.export(str(out))   # 不传 fmt，按后缀
        assert out.exists() and out.stat().st_size > 0


# ── 生成底图（Nature/R 风，Phase C）─────────────────────────────────────────────

class TestGenerated:
    def test_render_generated_draws_coastlines(self):
        w = _widget()
        w.set_basemap({"id": "generated:platecarree", "name": "世界·等距",
                       "kind": "generated", "proj": "EPSG:4326", "extent": None}, None)
        w.set_points(_pts())
        w.render()
        ax = w._fig.axes[0]
        assert len(ax.lines) > 20          # 海岸/国界/经纬网 实际画出
        assert len(ax.collections) >= 1    # 散点

    def test_render_generated_robinson_no_crash(self):
        w = _widget()
        w.set_basemap({"id": "generated:robinson", "name": "世界·Robinson",
                       "kind": "generated", "proj": "+proj=robin +lon_0=150", "extent": None}, None)
        w.set_points(_pts())
        w.render()

    def test_export_generated_png(self, tmp_path):
        w = _widget()
        w.set_basemap({"id": "generated:china_lcc", "name": "中国·兰伯特",
                       "kind": "generated",
                       "proj": "+proj=lcc +lat_1=25 +lat_2=47 +lat_0=35 +lon_0=105",
                       "extent": [70.0, 140.0, 3.0, 55.0]}, None)
        w.set_points(_pts())
        out = tmp_path / "gen.png"
        w.export(str(out), fmt="png", dpi=120)
        assert out.exists() and out.stat().st_size > 0


class TestBasemapOnlyExport:
    def test_export_without_points(self, tmp_path):
        w = _widget()
        w.set_basemap({"id": "generated:platecarree", "name": "世界·等距",
                       "kind": "generated", "proj": "EPSG:4326", "extent": None,
                       "detail": 110}, None)
        w.set_points(_pts())
        out = tmp_path / "base_only.png"
        w.export(str(out), fmt="png", dpi=110, with_points=False)
        assert out.exists() and out.stat().st_size > 0
        # 导出后屏显恢复带点
        ax = w._fig.axes[0]
        assert len(ax.collections) >= 1
