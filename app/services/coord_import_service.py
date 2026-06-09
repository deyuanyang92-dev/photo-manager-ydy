"""coord_import_service.py — 采集计划站位批量导入（Excel/CSV/TXT）.

支持把整理好的站位表（地区/断面/站位 + 经纬度）导入采集计划（写当前项目
collection_records）。流程：
  read_table(path) → (表头, 行 dict 列表)
  normalize_rows(rows, mapping, coord_system, default_date) → 规范化记录列表
    - 列映射用户自定义（哪列=地区/断面/站位/站位说明/经度/纬度/经纬合一）；
    - 经纬度任意格式经 coord_utils.parse（DD/DMS/DDM/ISO6709）→ 十进制度；
    - 坐标系 GCJ02/BD09 → 统一 WGS84；
    - 每行带 ok/error 供向导预览；输出可直接喂 collection_record_service.upsert_record。

纯逻辑，无 Qt，可单测。CSV/TXT 用 stdlib csv（嗅探分隔符），XLSX 用 openpyxl。
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

from app.utils import coord_utils as cu

# 列映射可指派的目标字段。
TARGET_FIELDS = ("province", "site", "station", "station_label", "lon", "lat", "lonlat")
TARGET_LABELS = {
    "province": "地区", "site": "断面/采集地", "station": "站位",
    "station_label": "站位说明", "lon": "经度", "lat": "纬度",
    "lonlat": "经纬度(合一列)",
}

COORD_SYSTEMS = ("WGS84", "GCJ02", "BD09")

# 示例文件表头 + 演示行（列名与 _guess_header 的命中词一致，导入时自动识别）。
SAMPLE_HEADERS = ["地区", "断面", "站位", "站位说明", "经度", "纬度"]
SAMPLE_ROWS = [
    ["浙江", "三门湾", "B2", "潮间带泥滩", "121.6543", "29.1234"],
    ["浙江", "三门湾", "B3", "低潮区礁石", "121°39'42\"E", "29°07'18\"N"],
    ["福建", "罗源湾", "L1", "养殖区", "119.7500", "26.4500"],
]
SAMPLE_MAPPING = {
    "province": "地区", "site": "断面", "station": "站位",
    "station_label": "站位说明", "lon": "经度", "lat": "纬度",
}


def sample_table() -> tuple[list[str], list[dict]]:
    """返回内置示例表，供 UI 直接预览，不必先保存 CSV 再打开。"""
    return list(SAMPLE_HEADERS), [
        {SAMPLE_HEADERS[i]: (row[i] if i < len(row) else "") for i in range(len(SAMPLE_HEADERS))}
        for row in SAMPLE_ROWS
    ]


def sample_preview_rows(coord_system: str = "WGS84") -> list[dict]:
    """示例表按默认列映射解析后的结果；用于展示经纬度解析预览。"""
    _headers, rows = sample_table()
    return normalize_rows(rows, SAMPLE_MAPPING, coord_system=coord_system)


def write_sample_file(path: str) -> None:
    """写一个示例站位表（CSV）供用户参照填写。UTF-8 BOM，Excel 直接打开不乱码。"""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(SAMPLE_HEADERS)
        w.writerows(SAMPLE_ROWS)


# ── 读表 ───────────────────────────────────────────────────────────────────────

def read_table(path: str) -> tuple[list[str], list[dict]]:
    """读 CSV/TXT/XLSX → (表头, 行 dict 列表)。首行为表头。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(p)
    return _read_delimited(p)


def _read_delimited(p: Path) -> tuple[list[str], list[dict]]:
    text = p.read_text(encoding="utf-8-sig")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delim = dialect.delimiter
    except csv.Error:
        delim = "\t" if "\t" in sample else ","
    reader = csv.reader(text.splitlines(), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return [], []
    headers = [h.strip() for h in rows[0]]
    out: list[dict] = []
    for r in rows[1:]:
        d = {headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
        out.append(d)
    return headers, out


def _read_xlsx(p: Path) -> tuple[list[str], list[dict]]:
    import openpyxl
    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return [], []
    headers = [str(h).strip() if h is not None else "" for h in header_row]
    out: list[dict] = []
    for row in rows_iter:
        if row is None or all(c is None for c in row):
            continue
        d = {}
        for i, h in enumerate(headers):
            v = row[i] if i < len(row) else None
            d[h] = "" if v is None else (str(v) if not isinstance(v, str) else v)
        out.append(d)
    wb.close()
    return headers, out


# ── 规范化 ─────────────────────────────────────────────────────────────────────

def _cell(row: dict, col: Optional[str]) -> str:
    if not col:
        return ""
    return str(row.get(col, "") or "").strip()


def _to_wgs84(lon: float, lat: float, coord_system: str) -> tuple[float, float]:
    cs = (coord_system or "WGS84").upper()
    if cs == "GCJ02":
        r = cu.gcj02_to_wgs84(lon, lat)
        return r["lon"], r["lat"]
    if cs == "BD09":
        r = cu.bd09_to_wgs84(lon, lat)
        return r["lon"], r["lat"]
    return lon, lat


def normalize_rows(rows: list[dict], mapping: dict,
                   coord_system: str = "WGS84", default_date: str = "") -> list[dict]:
    """按列映射规范化为采集记录 dict 列表（每行带 ok/error）。

    mapping: {target_field: 列名}；经纬度用 lon+lat 两列，或 lonlat 合一列。
    coord_system: 源坐标系，GCJ02/BD09 自动纠偏到 WGS84。
    default_date: 规划阶段一般留空（''），实采再补。
    """
    out: list[dict] = []
    for row in rows:
        rec = {
            "province": _cell(row, mapping.get("province")),
            "site": _cell(row, mapping.get("site")),
            "station": _cell(row, mapping.get("station")),
            "station_label": _cell(row, mapping.get("station_label")),
            "collection_date": default_date or "",
            "lon": None, "lat": None,
            "ok": False, "error": "",
            "_raw": row,
        }
        lat, lon = _parse_coords(row, mapping)
        if lat is None or lon is None:
            rec["error"] = "经纬度无法解析"
            out.append(rec)
            continue
        lon, lat = _to_wgs84(lon, lat, coord_system)
        if not cu.is_valid(lat, lon):
            rec["error"] = "经纬度超范围"
            out.append(rec)
            continue
        rec["lon"] = round(lon, 7)
        rec["lat"] = round(lat, 7)
        if not rec["station"] and not rec["site"] and not rec["province"]:
            rec["error"] = "缺少 地区/断面/站位"
            out.append(rec)
            continue
        rec["ok"] = True
        out.append(rec)
    return out


def _parse_coords(row: dict, mapping: dict):
    """返回 (lat, lon)，解析失败为 (None, None)。"""
    combined = _cell(row, mapping.get("lonlat"))
    if combined:
        r = cu.parse(combined)
        if r and r.get("lat") is not None and r.get("lon") is not None:
            return r["lat"], r["lon"]
        return None, None
    lon_s = _cell(row, mapping.get("lon"))
    lat_s = _cell(row, mapping.get("lat"))
    if not lon_s or not lat_s:
        return None, None
    return _parse_one(lat_s, is_lat=True), _parse_one(lon_s, is_lat=False)


def _parse_one(s: str, *, is_lat: bool) -> Optional[float]:
    """单列经/纬度：先认裸小数，再认 DMS/DDM 等格式（coord_utils）。"""
    try:
        return float(s)
    except ValueError:
        pass
    r = cu.parse(s, is_latitude=is_lat)
    if not r:
        return None
    # 取 lat/lon 中非空者（单值 DMS 带方向时只填一侧）
    return r.get("lat") if is_lat else r.get("lon")
