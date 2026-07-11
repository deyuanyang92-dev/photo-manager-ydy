"""collection_record_service.py — 采集记录簿 (field collection log) CRUD.

A collection record is the desktop's pre-entered field metadata for one
sampling event, uniquely keyed by (province, site, station, collection_date)
— the same location segment the UID derives from (app/utils/naming.py:42-60).

The workbench looks a record up by those four keys and auto-fills the subset
of fields it owns (collector / photographer / lon / lat / geo_area / dates).
Fields the capture UI has no slot for (habitat / tide / …) live only here and
are joined back at export time.

No Qt — pure functions over a sqlite3 connection, kept importable for tests.
This module is a NEW capability beyond the web oracle (its `code_labels.stations`
is only {code: label}); see docs/specs and CLAUDE.md.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Optional


def _norm_key_date(value: Any) -> str:
    """采集日期归一化为 8 位 YYYYMMDD(与 naming.specimen_date_seg 同规则):
    剥去所有非数字后取前 8 位。'2026-05-18 00:00:00' / '2026/05/18' → '20260518'。"""
    return re.sub(r"\D", "", str(value or ""))[:8]


def _norm_key_loc(value: Any) -> str:
    """位置键(省/市、地区/样地、站位)归一化 = 去空格 + 大写, 与工作台
    naming_panel.set_location_keys 的 .upper() 对齐, 保证四键匹配一致。"""
    return str(value or "").strip().upper()


def _normalize_record_keys(data: dict) -> dict:
    """把一条记录的四个匹配键就地归一化(写入前调用), 让存储成规范形。"""
    out = dict(data)
    if "collection_date" in out:
        out["collection_date"] = _norm_key_date(out.get("collection_date"))
    for k in ("province", "site", "station"):
        if k in out:
            out[k] = _norm_key_loc(out.get(k))
    return out

# Real columns on collection_records (id / raw_json handled separately).
_COLUMNS: tuple[str, ...] = (
    "province", "site", "station", "collection_date", "zone",
    "station_label", "lon", "lat", "geo_area", "water_body",
    "cruise", "vessel",
    "habitat", "tidal_zone", "depth",
    "tide", "salinity", "water_temp", "bottom_temp",
    "dissolved_oxygen", "ph", "weather",
    "sample_type", "sampler_model", "sampler_spec", "sample_area",
    "replicates", "sieve_mesh", "sample_no",
    # 潮间带专属 (H.39)
    "quadrate_no", "air_temp", "quant_bottles", "qual_bottles",
    # 两带通用
    "sample_thickness",
    # 潮下带专属 (H.30)
    "wire_out", "sampler_area", "net_type", "net_width",
    "trawl_distance", "trawl_start", "trawl_end",
    "grab_sample_total", "trawl_sample_total",
    "collector", "recorder", "checker", "photographer", "identifier",
    "collection_time", "photo_date", "photo_location",
    "method", "remark",
)

# 采区：潮间带(H.39) / 潮下带(H.30)。'intertidal'|'subtidal'|None(历史未分类)。
ZONES: tuple[Optional[str], ...] = ("intertidal", "subtidal")

# Columns stored as REAL — empty string must become NULL, never 0
# (mirrors the specimens lon/lat gotcha in CLAUDE.md).
_REAL_COLUMNS: frozenset[str] = frozenset({"lon", "lat"})

# ── 两套国标表样的导出/表头列序（单一真相源，io + 视图共用）──────────────────────
# 每条 (字段 key, 中文表头)。潮间带=H.39，潮下带=H.30。共享列两集都含。
# DB 列名不变；表头语义对齐国标（habitat→「底质」、weather→「气象」、
# water_temp→「表层水温(℃)」、replicates 潮间带「取样次数」/潮下带「采泥次数」）。
ZONE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "intertidal": [
        ("province", "地区"), ("site", "样地"), ("station", "站位"),
        ("quadrate_no", "样方号"), ("collection_date", "采集日期"),
        ("station_label", "站位说明"), ("lon", "经度"), ("lat", "纬度"),
        ("geo_area", "采集地理区"),
        ("tidal_zone", "潮区"), ("habitat", "底质"), ("depth", "水深(m)"),
        ("air_temp", "气温(℃)"), ("water_temp", "表层水温(℃)"),
        ("bottom_temp", "底层水温(℃)"), ("salinity", "盐度"),
        ("dissolved_oxygen", "溶解氧"), ("ph", "pH"), ("weather", "气象"),
        ("tide", "潮水"),
        ("sample_type", "采集性质"), ("method", "采样方法"),
        ("sampler_model", "采泥器型号"), ("sampler_spec", "采样器规格"),
        ("sample_area", "取样面积(m²)"), ("replicates", "取样次数"),
        ("sample_thickness", "样品厚度(cm)"), ("sieve_mesh", "网筛孔径(mm)"),
        ("sample_no", "样品编号"),
        ("quant_bottles", "定量瓶数"), ("qual_bottles", "定性瓶数"),
        ("collector", "采集人"), ("recorder", "记录人"), ("checker", "核对人"),
        ("photographer", "拍摄人"), ("identifier", "鉴定人"),
        ("collection_time", "采集时刻"), ("photo_date", "拍摄日期"),
        ("photo_location", "拍摄地点"), ("remark", "备注"),
    ],
    "subtidal": [
        ("province", "地区"), ("site", "样地"), ("station", "站位"),
        ("collection_date", "采集日期"), ("station_label", "站位说明"),
        ("sample_no", "样品编号"),
        ("lon", "经度"), ("lat", "纬度"), ("geo_area", "采集地理区"),
        ("water_body", "海区"),
        ("cruise", "航次"), ("vessel", "船号"),
        ("depth", "水深(m)"), ("wire_out", "放绳长度(m)"),
        ("habitat", "底质"), ("bottom_temp", "底层水温(℃)"),
        ("salinity", "盐度(底层)"), ("weather", "气象"),
        ("sample_type", "采集性质"), ("method", "采样方法"),
        ("sampler_model", "采泥器型号"), ("sampler_area", "采泥器面积(m²)"),
        ("replicates", "采泥次数"), ("sample_thickness", "样品厚度(cm)"),
        ("grab_sample_total", "采泥样品总数"), ("collection_time", "采泥时刻"),
        ("net_type", "网型"), ("net_width", "网宽(m)"),
        ("trawl_distance", "拖网距离(m)"), ("trawl_start", "拖网起始"),
        ("trawl_end", "拖网结束"), ("trawl_sample_total", "拖网样品总数"),
        ("collector", "采集人"), ("recorder", "记录人"), ("checker", "核对人"),
        ("photographer", "拍摄人"), ("identifier", "鉴定人"),
        ("photo_date", "拍摄日期"), ("photo_location", "拍摄地点"),
        ("remark", "备注"),
    ],
}

# 表头 → 字段 key 反查（含两 zone 全部表头），io 导入按表头判 zone/列。
_HEADER_TO_KEY: dict[str, str] = {}
for _zcols in ZONE_COLUMNS.values():
    for _k, _zh in _zcols:
        _HEADER_TO_KEY.setdefault(_zh, _k)


def columns_for_zone(zone: Optional[str]) -> list[tuple[str, str]]:
    """Return the (key, 中文表头) column list for *zone*; [] for unknown/None."""
    return ZONE_COLUMNS.get(zone, [])


def infer_zone_from_headers(headers: list[str]) -> Optional[str]:
    """判别一组表头属于哪个 zone：含 H.39 专属表头→intertidal，
    含 H.30 专属表头→subtidal，否则 None。H.30 专属优先于通用判别。"""
    hset = {h.strip() for h in headers}
    # H.30 潮下带专属表头（船基/拖网/采泥器面积）
    subtidal_only = {"航次", "船号", "放绳长度(m)", "采泥器面积(m²)",
                     "网型", "网宽(m)", "拖网距离(m)", "拖网起始", "拖网结束",
                     "采泥样品总数", "拖网样品总数", "海区"}
    # H.39 潮间带专属表头（样方/气温/瓶数/潮区/潮水）
    intertidal_only = {"样方号", "气温(℃)", "定量瓶数", "定性瓶数",
                       "潮区", "潮水", "表层水温(℃)"}
    if hset & subtidal_only:
        return "subtidal"
    if hset & intertidal_only:
        return "intertidal"
    return None


def _coerce_record_column_value(col: str, value: Any) -> Any:
    """Coerce an incoming value for *col* to its stored form."""
    if col in _REAL_COLUMNS:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    return value


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Flatten a row into a plain dict, merging the raw_json fallback.

    Known columns are authoritative — including when NULL (an empty lon column
    must read back as None, not the "" that raw_json preserved). raw_json only
    supplies extra/unknown fields that have no column.
    """
    d = {k: row[k] for k in row.keys()}
    raw = d.get("raw_json")
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                column_keys = set(d.keys())
                merged = {k: v for k, v in extra.items() if k not in column_keys}
                merged.update(d)  # columns win, even NULL
                return merged
        except (ValueError, TypeError):
            pass
    return d


def lookup_record(
    db: sqlite3.Connection,
    province: Optional[str],
    site: Optional[str],
    station: Optional[str],
    collection_date: Optional[str],
) -> Optional[dict]:
    """Return the record matching all four keys exactly, or None.

    This is the auto-fill entry point: the workbench calls it once the four
    location keys of a specimen are all known.
    """
    # 查询侧同样归一化(防御:手输脏格式也能命中已规范存储的记录)。
    row = db.execute(
        """SELECT * FROM collection_records
            WHERE province=? AND site=? AND station=? AND collection_date=?""",
        (
            _norm_key_loc(province),
            _norm_key_loc(site),
            _norm_key_loc(station),
            _norm_key_date(collection_date),
        ),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def list_records(db: sqlite3.Connection) -> list[dict]:
    """Return every collection record (for the 采集记录 table)."""
    rows = db.execute(
        "SELECT * FROM collection_records ORDER BY province, site, station, collection_date"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── 采集地图聚合 ───────────────────────────────────────────────────────────────
# 按层级把站位经纬度聚合成地图点：站位 → 断面(site) → 地区(province)。
# 上层坐标取下层各采集行经纬度的均值（质心），不单独录入、不改 schema。
# 仅纳入经纬度非空的行（空串存 NULL，见 CLAUDE.md）。

# level → (GROUP BY 列, label 取值表达式)
_MAP_LEVELS: dict[str, tuple[tuple[str, ...], str]] = {
    "station": (("province", "site", "station"),
                "COALESCE(NULLIF(station_label, ''), station)"),
    "site": (("province", "site"), "site"),
    "province": (("province",), "province"),
}


def map_points(db: sqlite3.Connection, level: str) -> list[dict]:
    """聚合采集记录经纬度为地图点。

    *level* ∈ {"station", "site", "province"}。返回每点一个 dict：
    ``{lon, lat, label, count, level, province, site, station}``，上层不存在的
    键置 None（供视图点击后回填筛选）。经纬度为 NULL 的行被排除。
    """
    spec = _MAP_LEVELS.get(level)
    if spec is None:
        raise ValueError(f"未知的地图层级 level={level!r}，应为 station/site/province")
    group_cols, label_expr = spec
    group_sql = ", ".join(group_cols)
    rows = db.execute(
        f"""SELECT {group_sql},
                   {label_expr} AS label,
                   AVG(lon) AS lon, AVG(lat) AS lat,
                   COUNT(*) AS cnt
              FROM collection_records
             WHERE lon IS NOT NULL AND lat IS NOT NULL
             GROUP BY {group_sql}
             ORDER BY {group_sql}"""
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        out.append({
            "lon": r["lon"],
            "lat": r["lat"],
            "label": r["label"],
            "count": int(r["cnt"]),
            "level": level,
            "province": r["province"] if "province" in group_cols else None,
            "site": r["site"] if "site" in group_cols else None,
            "station": r["station"] if "station" in group_cols else None,
        })
    return out


# 站位标识可作为标签来源的字段（marker_style_panel 的「标签」下拉与之对齐）。
MARKER_LABEL_SOURCES = (
    ("label", "名称/站位"), ("station", "站位"), ("site", "断面/采集地"),
    ("province", "地区"), ("lonlat", "经纬度"), ("count", "记录数"), ("none", "无"),
)


def marker_label(point: dict, src: str) -> str:
    """按标签来源 *src* 取地图点 *point* 的显示文本。未知/无 → 空串。"""
    if src in (None, "none"):
        return ""
    if src == "count":
        c = point.get("count")
        return "" if c is None else str(c)
    if src == "lonlat":
        lon, lat = point.get("lon"), point.get("lat")
        if lon is None or lat is None:
            return ""
        return f"{lon:.4f},{lat:.4f}"
    if src == "label":
        return str(point.get("label") or "")
    return str(point.get(src) or "")   # station / site / province


def map_points_across(dbs, level: str) -> list[dict]:
    """跨多个项目库聚合地图点（采集地图「全部项目」用）。

    *dbs* = 已打开的项目库连接列表。逐库取有经纬度的 raw 行，按 *level* 累加
    经纬度之和与计数（**非"平均的平均"**），合并后算质心。返回结构同 map_points。
    """
    spec = _MAP_LEVELS.get(level)
    if spec is None:
        raise ValueError(f"未知的地图层级 level={level!r}，应为 station/site/province")
    group_cols, _label_expr = spec

    acc: dict[tuple, dict] = {}
    for db in dbs:
        if db is None:
            continue
        rows = db.execute(
            """SELECT province, site, station, station_label, lon, lat
                 FROM collection_records
                WHERE lon IS NOT NULL AND lat IS NOT NULL"""
        ).fetchall()
        for r in rows:
            key = tuple(r[c] for c in group_cols)
            slot = acc.get(key)
            if slot is None:
                slot = {"sum_lon": 0.0, "sum_lat": 0.0, "count": 0,
                        "province": r["province"] if "province" in group_cols else None,
                        "site": r["site"] if "site" in group_cols else None,
                        "station": r["station"] if "station" in group_cols else None,
                        "station_label": r["station_label"]}
                acc[key] = slot
            slot["sum_lon"] += float(r["lon"])
            slot["sum_lat"] += float(r["lat"])
            slot["count"] += 1

    out: list[dict] = []
    for key in sorted(acc.keys(), key=lambda k: tuple("" if v is None else str(v) for v in k)):
        s = acc[key]
        n = s["count"]
        if level == "station":
            label = s["station_label"] or s["station"]
        elif level == "site":
            label = s["site"]
        else:
            label = s["province"]
        out.append({
            "lon": s["sum_lon"] / n, "lat": s["sum_lat"] / n,
            "label": label, "count": n, "level": level,
            "province": s["province"], "site": s["site"], "station": s["station"],
        })
    return out


def upsert_record(db: sqlite3.Connection, data: dict) -> int:
    """Insert or update a record; return its row id.

    Idempotent on the (province, site, station, collection_date) unique key:
    re-upserting the same four keys updates the existing row in place and keeps
    its id stable. The full incoming object is also stored in raw_json for
    zero-field-loss. If *data* carries a truthy ``id``, that row is updated by
    id instead (lets the editor change key fields without orphaning the row).
    """
    # 四键归一化(2026-07-11): Excel 日期读成 datetime、位置未转大写会导致
    # 工作台四键精确匹配不中、自动填充静默失效。存储前统一成规范形。
    data = _normalize_record_keys(data)
    values = [_coerce_record_column_value(c, data.get(c)) for c in _COLUMNS]
    raw_json = json.dumps(data, ensure_ascii=False)

    rid = data.get("id")
    if rid:
        # zone：incoming 空时保留既有分类（COALESCE 跳过空串），不冲掉已设 zone。
        assignments = ", ".join(
            f"{c}=COALESCE(NULLIF(?, ''), {c})" if c == "zone" else f"{c}=?"
            for c in _COLUMNS
        )
        db.execute(
            f"UPDATE collection_records SET {assignments}, raw_json=? WHERE id=?",
            (*values, raw_json, rid),
        )
        db.commit()
        return int(rid)

    existing = db.execute(
        """SELECT id FROM collection_records
            WHERE province=? AND site=?
              AND COALESCE(station, '')=COALESCE(?, '')
              AND collection_date=?""",
        (
            data.get("province"),
            data.get("site"),
            data.get("station"),
            data.get("collection_date"),
        ),
    ).fetchone()
    if existing is not None:
        # zone：incoming 空时保留既有分类（COALESCE 跳过空串），不冲掉已设 zone。
        assignments = ", ".join(
            f"{c}=COALESCE(NULLIF(?, ''), {c})" if c == "zone" else f"{c}=?"
            for c in _COLUMNS
        )
        row_id = int(existing["id"])
        db.execute(
            f"UPDATE collection_records SET {assignments}, raw_json=? WHERE id=?",
            (*values, raw_json, row_id),
        )
        db.commit()
        return row_id

    placeholders = ", ".join("?" for _ in _COLUMNS)
    cur = db.execute(
        f"""INSERT INTO collection_records ({", ".join(_COLUMNS)}, raw_json)
             VALUES ({placeholders}, ?)""",
        (*values, raw_json),
    )
    db.commit()
    return int(cur.lastrowid)


def delete_record(db: sqlite3.Connection, record_id: int) -> None:
    """Delete the record with the given id."""
    db.execute("DELETE FROM collection_records WHERE id=?", (record_id,))
    db.commit()


def set_station_coords(
    db: sqlite3.Connection,
    province: Optional[str],
    site: Optional[str],
    station: Optional[str],
    lon: Any,
    lat: Any,
) -> int:
    """Update lon/lat on every record of one station (province, site, station).

    The 采集地图 aggregates a station's records to their coordinate centroid, so
    moving / binding a station pin must update ALL its rows — updating one would
    leave the centroid unmoved. Empty lon/lat → NULL (same coercion as upsert).
    Returns the number of rows updated.
    """
    cur = db.execute(
        "UPDATE collection_records SET lon=?, lat=? "
        "WHERE province=? AND site=? AND station=?",
        (
            _coerce_record_column_value("lon", lon),
            _coerce_record_column_value("lat", lat),
            province, site, station,
        ),
    )
    db.commit()
    return int(cur.rowcount or 0)


def sync_coords_from_capture(
    db: sqlite3.Connection,
    *,
    province: Optional[str],
    site: Optional[str],
    station: Optional[str],
    collection_date: Optional[str],
    lon: Any,
    lat: Any,
    extra: Optional[dict] = None,
) -> str:
    """把拍照界面填的经纬度回写到采集记录（有则更新，无则新建）.

    关联键与 lookup 相同：``province + site + station + collection_date``。
    - 四键不全 → ``"skipped"``（不写库）
    - 已有记录 → 只改 ``lon``/``lat``，不碰生境/潮水等其它字段 → ``"updated"``
    - 无记录 → 新建一行（四键 + 坐标；``extra`` 可带人员/海区等）→ ``"created"``

    空经纬度按 NULL 存（与 upsert 一致）。返回动作标签便于测试/日志。
    """
    p = str(province or "").strip()
    s = str(site or "").strip()
    st = str(station or "").strip()
    d = str(collection_date or "").strip()
    if not (p and s and st and d):
        return "skipped"

    lon_v = _coerce_record_column_value("lon", lon)
    lat_v = _coerce_record_column_value("lat", lat)
    # 两边都空：不新建空壳记录；已有记录则允许清空坐标
    both_empty = lon_v is None and lat_v is None

    existing = lookup_record(db, p, s, st, d)
    if existing is not None:
        db.execute(
            "UPDATE collection_records SET lon=?, lat=? WHERE id=?",
            (lon_v, lat_v, existing["id"]),
        )
        db.commit()
        return "updated"

    if both_empty:
        return "skipped"

    row: dict[str, Any] = {
        "province": p,
        "site": s,
        "station": st,
        "collection_date": d,
        "lon": lon_v,
        "lat": lat_v,
    }
    if extra:
        for key in (
            "geo_area", "collector", "photographer", "identifier",
            "photo_date", "station_label", "habitat",
        ):
            val = extra.get(key)
            if val not in (None, ""):
                row[key] = val
    upsert_record(db, row)
    return "created"


# ── Auto-fill ─────────────────────────────────────────────────────────────────
# The subset of record fields the fixed workbench capture cards can hold.
# Dynamic naming fields (for example habitat) are filled separately by the
# naming panel only when a project rule exposes a matching input.
AUTOFILL_FIELDS: tuple[str, ...] = (
    "collector", "photographer", "identifier",
    "lon", "lat", "geo_area", "photo_date",
)


def autofill_values(record: dict, current: dict) -> dict:
    """Return the {field: value} pairs to fill into the capture cards.

    Non-destructive: only fields whose *current* value is empty AND whose
    *record* value is non-empty are returned. The caller (workbench) applies
    them to the naming / metadata widgets. Never overwrites a value the user
    already typed.
    """
    out: dict = {}
    for f in AUTOFILL_FIELDS:
        cur = current.get(f)
        if cur not in (None, ""):
            continue
        val = record.get(f)
        if val in (None, ""):
            continue
        out[f] = val
    return out
