"""跨断面调查概览聚合（只读）。

给定多个工作区目录, 汇总标本数 / 成片数 / RNA / 物种数, 以及采集区、站位、
拍摄人分布。纯 Python, 无 Qt; db 缺失或损坏时跳过, 不抛。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Optional

import sqlite3

from app.config.specimen_fields import eval_derived
from app.services.specimen_filter_service import query_specimens
from app.services.taxon_inventory_service import aggregate_taxon_inventory

__all__ = [
    "aggregate_survey_overview",
    "map_points_for_workspaces",
    "map_points_from_specimen_rows",
]


def _site_label(workspace: str, label: Optional[str]) -> str:
    if label and str(label).strip():
        return str(label).strip()
    return Path(workspace).name or str(workspace)


def _photo_count(workspace: str) -> int:
    from app.services import project_service as ps

    try:
        res = ps.get_project_results(workspace)
    except Exception:
        return 0
    try:
        return int(res.get("total") or 0)
    except (TypeError, ValueError):
        return 0


def _counter_rows(counter: Counter[str], *, limit: int = 12) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, count in counter.most_common(limit):
        if not str(name).strip():
            continue
        rows.append({"name": str(name), "count": int(count)})
    return rows


def map_points_from_specimen_rows(
    specimens: list[dict[str, Any]],
    *,
    level: str = "station",
) -> list[dict[str, Any]]:
    """Aggregate lon/lat from specimen rows (filtered or full scope)."""
    key_cols = {
        "station": ("province", "site", "station"),
        "site": ("province", "site"),
        "province": ("province",),
    }.get(level)
    if not key_cols:
        return []
    acc: dict[tuple, dict[str, Any]] = {}
    for row in specimens or []:
        lon, lat = row.get("lon"), row.get("lat")
        if lon in (None, "") or lat in (None, ""):
            continue
        try:
            flon, flat = float(lon), float(lat)
        except (TypeError, ValueError):
            continue
        key = tuple(str(row.get(c) or "").strip() for c in key_cols)
        if not any(key):
            continue
        slot = acc.get(key)
        if slot is None:
            slot = {
                "sum_lon": 0.0,
                "sum_lat": 0.0,
                "count": 0,
                "province": row.get("province"),
                "site": row.get("site"),
                "station": row.get("station"),
            }
            acc[key] = slot
        slot["sum_lon"] += flon
        slot["sum_lat"] += flat
        slot["count"] += 1
    out: list[dict[str, Any]] = []
    for key in sorted(acc.keys()):
        s = acc[key]
        n = s["count"]
        if level == "station":
            label = str(s.get("station") or key[-1] or "")
        elif level == "site":
            label = str(s.get("site") or key[-1] or "")
        else:
            label = str(s.get("province") or key[0] or "")
        out.append({
            "lon": s["sum_lon"] / n,
            "lat": s["sum_lat"] / n,
            "label": label,
            "count": n,
            "level": level,
            "province": s.get("province"),
            "site": s.get("site"),
            "station": s.get("station"),
        })
    return out


def _open_workspace_dbs(workspaces: list[str]) -> list[sqlite3.Connection]:
    conns: list[sqlite3.Connection] = []
    for ws in workspaces:
        db_path = Path(ws) / "_data" / "project.db"
        if not db_path.exists():
            continue
        conn: Optional[sqlite3.Connection] = None  # 每轮重置, 防跨迭代残留
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='collection_records'"
            ).fetchone()
            if row:
                conns.append(conn)
            else:
                conn.close()
        except sqlite3.Error:
            # §7 旧: try: conn.close() —— connect 本身抛错时 conn 还是上一轮
            # 的连接(可能已入列), 误关后续查询直接 ProgrammingError。
            if conn is not None and conn not in conns:
                try:
                    conn.close()
                except Exception:
                    pass
    return conns


def _map_points_from_specimens(
    workspaces: list[str],
    level: str,
) -> list[dict[str, Any]]:
    """Fallback: aggregate specimen lon/lat when collection_records empty."""
    rows = query_specimens(workspaces, [])
    return map_points_from_specimen_rows(rows, level=level)


def map_points_for_workspaces(
    workspaces: list[str],
    *,
    level: str = "station",
) -> list[dict[str, Any]]:
    """跨工作区站位/采集区地图点 (collection_records 优先, specimens 兜底)."""
    ws_list = [str(w) for w in (workspaces or []) if w]
    if not ws_list:
        return []
    from app.services import collection_record_service as crs

    conns = _open_workspace_dbs(ws_list)
    try:
        if conns:
            pts = crs.map_points_across(conns, level)
            if pts:
                return pts
    finally:
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
    return _map_points_from_specimens(ws_list, level)


def aggregate_survey_overview(
    workspaces: list[str],
    *,
    labels: Optional[list[str]] = None,
) -> dict[str, Any]:
    """跨断面调查概览。

    返回::
        {
            "workspace_count": int,
            "specimen_count": int,
            "photo_count": int,
            "rna_count": int,
            "species_count": int,
            "site_rows": [{"name", "count"}, ...],
            "station_rows": [...],
            "province_rows": [...],
            "photographer_rows": [...],
            "collector_rows": [...],
            "identifier_rows": [...],
            "per_workspace": [
                {"path", "label", "specimens", "photos", "rna", "species"},
                ...
            ],
        }
    """
    ws_list = [str(w) for w in (workspaces or []) if w]
    if labels is not None and len(labels) != len(ws_list):
        raise ValueError(
            f"labels 长度({len(labels)})与 workspaces 长度({len(ws_list)})不一致"
        )

    if not ws_list:
        return {
            "workspace_count": 0,
            "specimen_count": 0,
            "photo_count": 0,
            "rna_count": 0,
            "species_count": 0,
            "site_rows": [],
            "station_rows": [],
            "province_rows": [],
            "photographer_rows": [],
            "collector_rows": [],
            "identifier_rows": [],
            "per_workspace": [],
            "map_points": [],
        }

    label_list = [
        _site_label(ws, labels[i] if labels else None) for i, ws in enumerate(ws_list)
    ]
    rows = query_specimens(ws_list, [], labels=label_list)
    inventory = aggregate_taxon_inventory(ws_list, labels=label_list)

    site_ctr: Counter[str] = Counter()
    station_ctr: Counter[str] = Counter()
    province_ctr: Counter[str] = Counter()
    photographer_ctr: Counter[str] = Counter()
    collector_ctr: Counter[str] = Counter()
    identifier_ctr: Counter[str] = Counter()
    rna_count = 0
    per_ws: dict[str, dict[str, Any]] = {}

    for i, ws in enumerate(ws_list):
        per_ws[ws] = {
            "path": ws,
            "label": label_list[i],
            "specimens": 0,
            "photos": _photo_count(ws),
            "rna": 0,
            "species": 0,
        }

    for entry in inventory:
        for site, _n in (entry.get("count_per_site") or {}).items():
            for meta in per_ws.values():
                if meta["label"] == site:
                    meta["species"] += 1
                    break

    for row in rows:
        ws = str(row.get("_workspace") or "")
        if ws in per_ws:
            per_ws[ws]["specimens"] += 1
            if eval_derived("storage_is_rna", row):
                per_ws[ws]["rna"] += 1
                rna_count += 1
        site = str(row.get("site") or "").strip()
        if site:
            site_ctr[site] += 1
        station = str(row.get("station") or "").strip()
        if station:
            station_ctr[station] += 1
        province = str(row.get("province") or "").strip()
        if province:
            province_ctr[province] += 1
        photographer = str(row.get("photographer") or "").strip()
        if photographer:
            photographer_ctr[photographer] += 1
        collector = str(row.get("collector") or "").strip()
        if collector:
            collector_ctr[collector] += 1
        identifier = str(row.get("identifier") or "").strip()
        if identifier:
            identifier_ctr[identifier] += 1

    photo_total = sum(int(m["photos"]) for m in per_ws.values())
    map_pts = map_points_for_workspaces(ws_list, level="station")

    return {
        "workspace_count": len(ws_list),
        "specimen_count": len(rows),
        "photo_count": photo_total,
        "rna_count": rna_count,
        "species_count": len(inventory),
        "site_rows": _counter_rows(site_ctr),
        "station_rows": _counter_rows(station_ctr),
        "province_rows": _counter_rows(province_ctr),
        "photographer_rows": _counter_rows(photographer_ctr),
        "collector_rows": _counter_rows(collector_ctr),
        "identifier_rows": _counter_rows(identifier_ctr),
        "per_workspace": list(per_ws.values()),
        "map_points": map_pts,
    }
