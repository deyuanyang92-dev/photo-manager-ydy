"""geo_basemap.py — Natural Earth 矢量加载 + 生成底图投影预设.

为「程序生成」的 Nature/R(ggplot2+rnaturalearth) 风格底图提供：
  - 随包 Natural Earth 1:110m GeoJSON（陆地/海岸/国界）的几何加载，展平成经纬度环列表；
  - 一组投影预设（世界 Robinson/等距、中国及周边 兰伯特…）；
  - pyproj 经纬度→投影坐标的批量变换。

纯逻辑，无 Qt。pyproj 为可选依赖：缺失时 project_points 退回恒等（仅 PlateCarree 可用）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_GEO_DIR = Path(__file__).resolve().parents[2] / "resources" / "geo"


# ── GeoJSON 加载 ───────────────────────────────────────────────────────────────

def _rings_from_geometry(geom: dict) -> list[list[tuple]]:
    """把一个 GeoJSON geometry 展平成若干 (lon,lat) 环。"""
    t = geom.get("type")
    coords = geom.get("coordinates")
    rings: list[list[tuple]] = []
    if t == "Polygon":
        for ring in coords:
            rings.append([(float(x), float(y)) for x, y in ring])
    elif t == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                rings.append([(float(x), float(y)) for x, y in ring])
    elif t == "LineString":
        rings.append([(float(x), float(y)) for x, y in coords])
    elif t == "MultiLineString":
        for line in coords:
            rings.append([(float(x), float(y)) for x, y in line])
    return rings


def load_geometries(name: str) -> list[list[tuple]]:
    """读 `resources/geo/<name>.geojson`，返回 (lon,lat) 环列表。缺失/损坏 → []。"""
    p = _GEO_DIR / f"{name}.geojson"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    out: list[list[tuple]] = []
    for feat in data.get("features", []):
        geom = feat.get("geometry")
        if geom:
            out.extend(_rings_from_geometry(geom))
    return out


# ── 投影预设 ───────────────────────────────────────────────────────────────────
# extent = 经纬度裁剪框 [lon_min, lon_max, lat_min, lat_max]；None = 全球。

# detail = Natural Earth 比例尺（110 全球速绘 / 50 区域细节）。
_PRESETS: list[dict] = [
    {"id": "generated:robinson", "name": "世界 · Robinson（Nature 风）",
     "kind": "generated", "proj": "+proj=robin +lon_0=150", "extent": None, "detail": 110},
    {"id": "generated:eqearth", "name": "世界 · Equal Earth（等积，期刊常用）",
     "kind": "generated", "proj": "+proj=eqearth +lon_0=150", "extent": None, "detail": 110},
    {"id": "generated:wintri", "name": "世界 · Winkel Tripel（NatGeo 标准）",
     "kind": "generated", "proj": "+proj=wintri +lon_0=105", "extent": None, "detail": 110},
    {"id": "generated:mollweide", "name": "世界 · Mollweide",
     "kind": "generated", "proj": "+proj=moll +lon_0=150", "extent": None, "detail": 110},
    {"id": "generated:platecarree", "name": "世界 · 等距圆柱",
     "kind": "generated", "proj": "EPSG:4326", "extent": None, "detail": 110},
    {"id": "generated:npolar", "name": "北极 · 方位等积",
     "kind": "generated", "proj": "+proj=laea +lat_0=90 +lon_0=0",
     "extent": None, "detail": 110},
    {"id": "generated:china_lcc", "name": "中国及周边 · 兰伯特",
     "kind": "generated", "proj": "+proj=lcc +lat_1=25 +lat_2=47 +lat_0=35 +lon_0=105",
     "extent": [70.0, 140.0, 3.0, 55.0], "detail": 50},
    {"id": "generated:china_seas", "name": "中国近海 · 墨卡托",
     "kind": "generated", "proj": "+proj=merc +lon_0=120",
     "extent": [105.0, 127.0, 17.0, 42.0], "detail": 50},
    {"id": "generated:east_asia_seas", "name": "东亚海域 · 墨卡托",
     "kind": "generated", "proj": "+proj=merc +lon_0=125",
     "extent": [100.0, 150.0, 0.0, 45.0], "detail": 50},
]


def generated_presets() -> list[dict]:
    """生成底图预设（拷贝，避免调用方改到内部状态）。"""
    return [dict(p) for p in _PRESETS]


# ── 投影变换 ───────────────────────────────────────────────────────────────────

def project_points(proj: str, lons, lats):
    """经纬度(WGS84) → 目标投影坐标。返回 (xs, ys) 列表。

    proj = pyproj 可识别的 CRS（EPSG / proj4 串）。EPSG:4326 等距时为恒等。
    pyproj 缺失 → 恒等返回（仅等距底图可用）。
    """
    lons = list(lons)
    lats = list(lats)
    if proj in ("EPSG:4326", "epsg:4326", "+proj=longlat"):
        return list(lons), list(lats)
    try:
        from pyproj import Transformer
        tr = Transformer.from_crs("EPSG:4326", proj, always_xy=True)
        xs, ys = tr.transform(lons, lats)
        return list(xs), list(ys)
    except Exception as exc:
        logger.warning("project_points failed for proj=%r: %s", proj, exc)
        return list(lons), list(lats)
