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
import sqlite3
from typing import Any, Optional

# Real columns on collection_records (id / raw_json handled separately).
_COLUMNS: tuple[str, ...] = (
    "province", "site", "station", "collection_date",
    "station_label", "lon", "lat", "geo_area",
    "habitat", "tide", "salinity", "water_temp", "weather",
    "collector", "photographer", "identifier",
    "collection_time", "photo_date", "photo_location",
    "method", "remark",
)

# Columns stored as REAL — empty string must become NULL, never 0
# (mirrors the specimens lon/lat gotcha in CLAUDE.md).
_REAL_COLUMNS: frozenset[str] = frozenset({"lon", "lat"})


def _coerce(col: str, value: Any) -> Any:
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
    row = db.execute(
        """SELECT * FROM collection_records
            WHERE province=? AND site=? AND station=? AND collection_date=?""",
        (province, site, station, collection_date),
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
    values = [_coerce(c, data.get(c)) for c in _COLUMNS]
    raw_json = json.dumps(data, ensure_ascii=False)

    rid = data.get("id")
    if rid:
        assignments = ", ".join(f"{c}=?" for c in _COLUMNS)
        db.execute(
            f"UPDATE collection_records SET {assignments}, raw_json=? WHERE id=?",
            (*values, raw_json, rid),
        )
        db.commit()
        return int(rid)

    placeholders = ", ".join("?" for _ in _COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS)
    cur = db.execute(
        f"""INSERT INTO collection_records ({", ".join(_COLUMNS)}, raw_json)
             VALUES ({placeholders}, ?)
             ON CONFLICT(province, site, station, collection_date)
             DO UPDATE SET {updates}, raw_json=excluded.raw_json""",
        (*values, raw_json),
    )
    db.commit()
    if cur.lastrowid:
        # On a plain INSERT lastrowid is the new id; on ON CONFLICT update it
        # may not reflect the existing row — re-resolve by the four keys.
        row = db.execute(
            """SELECT id FROM collection_records
                WHERE province=? AND site=? AND station=? AND collection_date=?""",
            (data.get("province"), data.get("site"),
             data.get("station"), data.get("collection_date")),
        ).fetchone()
        if row is not None:
            return int(row["id"])
        return int(cur.lastrowid)
    return int(cur.lastrowid)


def delete_record(db: sqlite3.Connection, record_id: int) -> None:
    """Delete the record with the given id."""
    db.execute("DELETE FROM collection_records WHERE id=?", (record_id,))
    db.commit()


# ── Auto-fill ─────────────────────────────────────────────────────────────────
# The subset of record fields the workbench capture cards can hold. Habitat /
# tide / salinity / … have no capture slot — they live only in the record and
# are NOT auto-filled (joined at export instead).
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
