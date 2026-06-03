"""coord_utils.py — 坐标格式转换工具（Python 直译自 coord-utils.js）。

支持 DD / DMS / DDM / ISO 6709 格式解析与互转；
WGS-84 ↔ GCJ-02 ↔ BD09 坐标系转换（纯函数，无副作用，可测）。

Known limitations (carried over from JS original):
  P3-5: gcj02_to_wgs84() 迭代 5 次反解，误差 < 1 m；科研精度 < 0.1 m 需更多迭代。
  P3-6: parse_detailed() 的 verbatim_lon 始终为空串（与 JS 版保持一致）。
"""
from __future__ import annotations

import math
import re
from typing import Optional


# ── 地球椭球参数 ──────────────────────────────────────────────────────────────

_PI = math.pi
_A = 6378245.0
_EE = 0.00669342162296594323


# ── 格式解析 ──────────────────────────────────────────────────────────────────

def _valid_lat(v: float) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= 90


def _valid_lon(v: float) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= 180


def _dms_to_dd(d: float, m: float, s: float, direction: str) -> float:
    dd = d + m / 60 + s / 3600
    if re.match(r"^[SW]$", direction, re.I):
        dd = -dd
    return dd


def _ddm_to_dd(d: float, m: float, direction: str) -> float:
    dd = d + m / 60
    if re.match(r"^[SW]$", direction, re.I):
        dd = -dd
    return dd


def _dd_to_dms(dd: float) -> dict:
    """Return dict with keys d, m, s (float)."""
    abs_dd = abs(dd)
    d = int(abs_dd)
    mf = (abs_dd - d) * 60
    m = int(mf)
    s = round((mf - m) * 60, 1)
    return {"d": d, "m": m, "s": float(s)}


def _dd_to_ddm(dd: float) -> dict:
    """Return dict with keys d, m (float)."""
    abs_dd = abs(dd)
    d = int(abs_dd)
    m = round((abs_dd - d) * 60, 3)
    return {"d": d, "m": float(m)}


def infer_lat_lon_order(v1: float, v2: float) -> dict:
    """Infer which value is lat and which is lon based on magnitude."""
    a1, a2 = abs(v1), abs(v2)
    if a1 > 90 and a2 <= 90:
        return {"lat": v2, "lon": v1}
    if a2 > 90 and a1 <= 90:
        return {"lat": v1, "lon": v2}
    return {"lat": v1, "lon": v2, "ambiguous": True}


def _parse_dd(s: str) -> Optional[dict]:
    """DD: 29.11492 N 121.76421 E / 29.11492 121.76421 / -29.11492 -121.76421"""
    # value dir value dir
    m = re.match(
        r"^([+-]?\d+\.?\d*)\s*°?\s*([NS])?\s+([+-]?\d+\.?\d*)\s*°?\s*([EW])?$",
        s, re.I
    )
    if m:
        v1, dir1, v2, dir2 = m.group(1), m.group(2), m.group(3), m.group(4)
        lat, lon = float(v1), float(v2)
        if dir1:
            if re.search(r"S", dir1, re.I):
                lat = -abs(lat)
            if re.search(r"N", dir1, re.I):
                lat = abs(lat)
        if dir2:
            if re.search(r"W", dir2, re.I):
                lon = -abs(lon)
            if re.search(r"E", dir2, re.I):
                lon = abs(lon)
        return infer_lat_lon_order(lat, lon)

    # dir value dir value: N 24.615706 E 118.322613
    m0 = re.match(r"^([NS])\s+([+-]?\d+\.?\d*)\s*°?\s*([EW])\s+([+-]?\d+\.?\d*)\s*°?$", s, re.I)
    if m0:
        dir1, v1, dir2, v2 = m0.group(1), m0.group(2), m0.group(3), m0.group(4)
        lat, lon = float(v1), float(v2)
        if re.search(r"S", dir1, re.I):
            lat = -abs(lat)
        if re.search(r"N", dir1, re.I):
            lat = abs(lat)
        if re.search(r"W", dir2, re.I):
            lon = -abs(lon)
        if re.search(r"E", dir2, re.I):
            lon = abs(lon)
        return {"lat": lat, "lon": lon}

    # single value + direction: 29.11492 N
    m1 = re.match(r"^([+-]?\d+\.?\d*)\s*°?\s*([NSEW])$", s, re.I)
    if m1:
        v = float(m1.group(1))
        d = m1.group(2).upper()
        if d in ("N", "S"):
            return {"lat": -abs(v) if d == "S" else abs(v), "lon": None}
        return {"lat": None, "lon": -abs(v) if d == "W" else abs(v)}

    return None


def _parse_dms(s: str) -> Optional[dict]:
    """DMS: 29°06'53.7\"N / N24°29'21.1\" / 29 06 53.7 N"""
    # direction-first: N24°29'21.1"
    re0 = re.compile(r"([NSEW])\s*(\d+)\s*°\s*(\d+)\s*[′']\s*([\d.]+)\s*[″\"]?", re.I)
    pre = re0.findall(s)
    if len(pre) >= 2:
        parts = pre  # each: (dir, d, m, s_sec)
        lats, lons = [], []
        for (direction, d, m, sec) in pre:
            dd = _dms_to_dd(float(d), float(m), float(sec), direction)
            if re.search(r"[NS]", direction, re.I):
                lats.append(dd)
            else:
                lons.append(dd)
        if lats and lons:
            return {"lat": lats[0], "lon": lons[0]}

    # direction-last: 29°06'53.7"N
    re1 = re.compile(r"(\d+)\s*°\s*(\d+)\s*[′']\s*([\d.]+)\s*[″\"]?\s*([NSEW])", re.I)
    parts1 = re1.findall(s)
    if len(parts1) == 2:
        (d1, m1, s1, dir1), (d2, m2, s2, dir2) = parts1
        lat = _dms_to_dd(float(d1), float(m1), float(s1), dir1)
        lon = _dms_to_dd(float(d2), float(m2), float(s2), dir2)
        return {"lat": lat, "lon": lon}
    if len(parts1) == 1:
        d, m, sec, direction = parts1[0]
        dd = _dms_to_dd(float(d), float(m), float(sec), direction)
        if re.search(r"[NS]", direction, re.I):
            return {"lat": dd, "lon": None}
        return {"lat": None, "lon": dd}

    # space-separated: 29 06 53.7 N
    re2 = re.compile(r"(\d+)\s+(\d+)\s+([\d.]+)\s+([NSEW])", re.I)
    parts2 = re2.findall(s)
    if len(parts2) == 2:
        (d1, m1, s1, dir1), (d2, m2, s2, dir2) = parts2
        lat = _dms_to_dd(float(d1), float(m1), float(s1), dir1)
        lon = _dms_to_dd(float(d2), float(m2), float(s2), dir2)
        return {"lat": lat, "lon": lon}
    if len(parts2) == 1:
        d, m, sec, direction = parts2[0]
        dd = _dms_to_dd(float(d), float(m), float(sec), direction)
        if re.search(r"[NS]", direction, re.I):
            return {"lat": dd, "lon": None}
        return {"lat": None, "lon": dd}

    return None


def _parse_ddm(s: str) -> Optional[dict]:
    """DDM: 29°06.895'N / N24°29.352'"""
    # direction-first: N24°29.352'
    re_pre = re.compile(r"([NSEW])\s*(\d+)\s*°\s*([\d.]+)\s*[′']", re.I)
    pre = re_pre.findall(s)
    if len(pre) >= 2:
        lats, lons = [], []
        for (direction, d, m) in pre:
            dd = _ddm_to_dd(float(d), float(m), direction)
            if re.search(r"[NS]", direction, re.I):
                lats.append(dd)
            else:
                lons.append(dd)
        if lats and lons:
            return {"lat": lats[0], "lon": lons[0]}

    # direction-last: 29°06.895'N
    re_post = re.compile(r"(\d+)\s*°\s*([\d.]+)\s*[′']\s*([NSEW])", re.I)
    parts = re_post.findall(s)
    if len(parts) == 2:
        (d1, m1, dir1), (d2, m2, dir2) = parts
        lat = _ddm_to_dd(float(d1), float(m1), dir1)
        lon = _ddm_to_dd(float(d2), float(m2), dir2)
        return {"lat": lat, "lon": lon}
    if len(parts) == 1:
        d, m, direction = parts[0]
        dd = _ddm_to_dd(float(d), float(m), direction)
        if re.search(r"[NS]", direction, re.I):
            return {"lat": dd, "lon": None}
        return {"lat": None, "lon": dd}

    return None


def _parse_iso6709(s: str) -> Optional[dict]:
    """ISO 6709: +29.11492+121.76421/"""
    m = re.match(r"^([+-]\d+\.?\d*)([+-]\d+\.?\d*)/?$", s)
    if not m:
        return None
    return {"lat": float(m.group(1)), "lon": float(m.group(2))}


def _normalize(input_str: str) -> str:
    """Normalize input string (translate Chinese direction words, collapse whitespace)."""
    s = input_str.strip()
    s = s.replace(",", " ")
    s = re.sub(r"\s+", " ", s)
    s = s.replace("北纬", "N").replace("南纬", "S")
    s = s.replace("东经", "E").replace("西经", "W")
    return s


def parse(input_str: str, *, is_latitude: Optional[bool] = None) -> Optional[dict]:
    """Parse coordinate string in any supported format.

    Parameters
    ----------
    input_str : str
        Raw coordinate input (DD, DMS, DDM, or ISO 6709).
    is_latitude : bool or None
        Hint for single-value inputs. True → treat as lat, False → lon.

    Returns
    -------
    dict with keys ``lat`` and ``lon`` (floats), or None if unparseable.
    """
    if not input_str or not isinstance(input_str, str):
        return None
    s = _normalize(input_str)

    r = _parse_dms(s) or _parse_ddm(s) or _parse_dd(s) or _parse_iso6709(s)
    if not r:
        return None

    # Handle single-value + isLatitude hint
    if r.get("lat") is None and r.get("lon") is not None and is_latitude is True:
        r = {"lat": r["lon"], "lon": None}
    elif r.get("lon") is None and r.get("lat") is not None and is_latitude is False:
        r = {"lat": None, "lon": r["lat"]}

    if r.get("lat") is not None and not _valid_lat(r["lat"]):
        return None
    if r.get("lon") is not None and not _valid_lon(r["lon"]):
        return None
    return r


# ── 格式化输出 ─────────────────────────────────────────────────────────────────

def to_dd(lat: float, lon: float) -> str:
    """Format as DD with cardinal directions: 29.11492°N, 121.76421°E"""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.5f}°{ns}, {abs(lon):.5f}°{ew}"


def to_dms(lat: float, lon: float) -> str:
    """Format as DMS: 29°6'53.7"N 121°45'51.2"E"""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    ld, lo = _dd_to_dms(lat), _dd_to_dms(lon)
    return f"{ld['d']}°{ld['m']}'{ld['s']}\"{ns} {lo['d']}°{lo['m']}'{lo['s']}\"{ew}"


def to_ddm(lat: float, lon: float) -> str:
    """Format as DDM: 29°6.895'N 121°45.854'E"""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    ld, lo = _dd_to_ddm(lat), _dd_to_ddm(lon)
    return f"{ld['d']}°{ld['m']:.3f}'{ns} {lo['d']}°{lo['m']:.3f}'{ew}"


def to_dd_zh(lat: float, lon: float) -> str:
    """Format with Chinese direction words: 北纬 29.114920  东经 121.764210"""
    ns = "北纬" if lat >= 0 else "南纬"
    ew = "东经" if lon >= 0 else "西经"
    return f"{ns} {abs(lat):.6f}  {ew} {abs(lon):.6f}"


def to_dms_zh(lat: float, lon: float) -> str:
    """Format DMS with Chinese direction words."""
    ns = "北纬" if lat >= 0 else "南纬"
    ew = "东经" if lon >= 0 else "西经"
    ld, lo = _dd_to_dms(lat), _dd_to_dms(lon)
    return (f"{ns} {ld['d']}°{ld['m']}'{ld['s']}\"  "
            f"{ew} {lo['d']}°{lo['m']}'{lo['s']}\"")


def to_ddm_zh(lat: float, lon: float) -> str:
    """Format DDM with Chinese direction words."""
    ns = "北纬" if lat >= 0 else "南纬"
    ew = "东经" if lon >= 0 else "西经"
    ld, lo = _dd_to_ddm(lat), _dd_to_ddm(lon)
    return f"{ns} {ld['d']}°{ld['m']:.3f}'  {ew} {lo['d']}°{lo['m']:.3f}'"


# ── 增强解析 ──────────────────────────────────────────────────────────────────

def parse_detailed(input_str: str) -> Optional[dict]:
    """Parse with rich result: format name, formatted strings, DMS/DDM components.

    Returns None if input cannot be fully parsed (both lat and lon required).

    Note: verbatim_lon is always "" (P3-6 known limitation, mirrors JS).
    """
    if not input_str or not isinstance(input_str, str):
        return None
    s = _normalize(input_str)

    fmt = None
    r = None

    r = _parse_dms(s)
    if r and r.get("lat") is not None and r.get("lon") is not None:
        fmt = "DMS"

    if not fmt:
        r = _parse_ddm(s)
        if r and r.get("lat") is not None and r.get("lon") is not None:
            fmt = "DDM"

    if not fmt:
        r = _parse_iso6709(s)
        if r and r.get("lat") is not None and r.get("lon") is not None:
            fmt = "ISO6709"

    if not fmt:
        r = _parse_dd(s)
        if r and r.get("lat") is not None and r.get("lon") is not None:
            fmt = "DD"

    if not fmt or not r or r.get("lat") is None or r.get("lon") is None:
        return None
    if not _valid_lat(r["lat"]) or not _valid_lon(r["lon"]):
        return None

    lat, lon = r["lat"], r["lon"]
    format_labels = {
        "DD": "十进制度 (DD)",
        "DMS": "度分秒 (DMS)",
        "DDM": "度分 (DDM)",
        "ISO6709": "ISO 6709",
    }
    dms_lat = _dd_to_dms(lat)
    dms_lon = _dd_to_dms(lon)
    ddm_lat = _dd_to_ddm(lat)
    ddm_lon = _dd_to_ddm(lon)

    return {
        "lat": lat,
        "lon": lon,
        "format": fmt,
        "format_label": format_labels.get(fmt, fmt),
        "lat_direction": "N" if lat >= 0 else "S",
        "lon_direction": "E" if lon >= 0 else "W",
        "verbatim_lat": next(
            (p for p in s.split() if re.search(r"[NS]", p, re.I)
             or _try_float(p) in (lat, -lat)),
            ""
        ),
        "verbatim_lon": "",  # P3-6: known limitation
        "dms": {"lat": dms_lat, "lon": dms_lon},
        "ddm": {"lat": ddm_lat, "lon": ddm_lon},
        "dd": {"lat": abs(lat), "lon": abs(lon)},
        "formatted": {
            "dd": to_dd(lat, lon),
            "dms": to_dms(lat, lon),
            "ddm": to_ddm(lat, lon),
            "ddzh": to_dd_zh(lat, lon),
            "dmszh": to_dms_zh(lat, lon),
            "ddmzh": to_ddm_zh(lat, lon),
        },
    }


def _try_float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return float("nan")


# ── 字段构造函数 ──────────────────────────────────────────────────────────────

def from_dms_fields(d: float, m: float, s: float, direction: str) -> float:
    """Convert structured DMS fields to decimal degrees."""
    dd = abs(float(d)) + float(m) / 60 + float(s) / 3600
    if re.match(r"^[SW]$", str(direction), re.I):
        dd = -dd
    return dd


def from_ddm_fields(d: float, m: float, direction: str) -> float:
    """Convert structured DDM fields to decimal degrees."""
    dd = abs(float(d)) + float(m) / 60
    if re.match(r"^[SW]$", str(direction), re.I):
        dd = -dd
    return dd


# ── 坐标有效性 ────────────────────────────────────────────────────────────────

def is_valid(lat: float, lon: float) -> bool:
    """Return True if both lat and lon are within valid ranges."""
    return _valid_lat(lat) and _valid_lon(lon)


# ── 坐标系转换 (WGS-84 ↔ GCJ-02 ↔ BD09) ─────────────────────────────────────

def is_in_mainland_china(lon: float, lat: float) -> bool:
    """Rough bounding-box check for mainland China."""
    return 73.66 <= lon <= 135.05 and 3.86 <= lat <= 53.55


def _transform_lat(x: float, y: float) -> float:
    r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    r += ((20 * math.sin(6 * x * _PI) + 20 * math.sin(2 * x * _PI)) * 2) / 3
    r += ((20 * math.sin(y * _PI) + 40 * math.sin((y / 3) * _PI)) * 2) / 3
    r += ((160 * math.sin((y / 12) * _PI) + 320 * math.sin((y * _PI) / 30)) * 2) / 3
    return r


def _transform_lon(x: float, y: float) -> float:
    r = 300 + x + 2 * y + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    r += ((20 * math.sin(6 * x * _PI) + 20 * math.sin(2 * x * _PI)) * 2) / 3
    r += ((20 * math.sin(x * _PI) + 40 * math.sin((x / 3) * _PI)) * 2) / 3
    r += ((150 * math.sin((x / 12) * _PI) + 300 * math.sin((x / 30) * _PI)) * 2) / 3
    return r


def wgs84_to_gcj02(lon: float, lat: float) -> dict:
    """Convert WGS-84 to GCJ-02 (Mars Coordinates).

    Returns unchanged coords if outside mainland China.
    """
    if not is_in_mainland_china(lon, lat):
        return {"lon": lon, "lat": lat}
    d_lat = _transform_lat(lon - 105.0, lat - 35.0)
    d_lon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = (lat / 180.0) * _PI
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sq = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / (((_A * (1 - _EE)) / (magic * sq)) * _PI)
    d_lon = (d_lon * 180.0) / ((_A / sq) / math.cos(rad_lat) * _PI)
    return {
        "lon": round(lon + d_lon, 6),
        "lat": round(lat + d_lat, 6),
    }


def gcj02_to_wgs84(lon: float, lat: float) -> dict:
    """Convert GCJ-02 back to WGS-84 via 5-iteration approximation (P3-5).

    Typical residual error < 1 m; use more iterations for < 0.1 m.
    """
    if not is_in_mainland_china(lon, lat):
        return {"lon": lon, "lat": lat}
    w_lon, w_lat = lon, lat
    for _ in range(5):
        g = wgs84_to_gcj02(w_lon, w_lat)
        w_lon += (lon - g["lon"])
        w_lat += (lat - g["lat"])
    return {"lon": round(w_lon, 7), "lat": round(w_lat, 7)}


_X_PI = _PI * 3000.0 / 180.0


def bd09_to_gcj02(bd_lon: float, bd_lat: float) -> dict:
    """Convert BD09 (Baidu) to GCJ-02."""
    x = bd_lon - 0.0065
    y = bd_lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    return {
        "lon": round(z * math.cos(theta), 6),
        "lat": round(z * math.sin(theta), 6),
    }


def gcj02_to_bd09(lon: float, lat: float) -> dict:
    """Convert GCJ-02 to BD09 (Baidu)."""
    z = math.sqrt(lon * lon + lat * lat) + 0.00002 * math.sin(lat * _X_PI)
    theta = math.atan2(lat, lon) + 0.000003 * math.cos(lon * _X_PI)
    return {
        "lon": round(z * math.cos(theta) + 0.0065, 6),
        "lat": round(z * math.sin(theta) + 0.006, 6),
    }


def wgs84_to_bd09(lon: float, lat: float) -> dict:
    """Convert WGS-84 → GCJ-02 → BD09."""
    gcj = wgs84_to_gcj02(lon, lat)
    return gcj02_to_bd09(gcj["lon"], gcj["lat"])


def bd09_to_wgs84(lon: float, lat: float) -> dict:
    """Convert BD09 → GCJ-02 → WGS-84."""
    gcj = bd09_to_gcj02(lon, lat)
    return gcj02_to_wgs84(gcj["lon"], gcj["lat"])
