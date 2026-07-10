"""workspace_index_service.py — 根库 workspace_index_cache 刷新与读取.

设计（对齐 docs/specs/project-data-management-architecture.md M5）：
- 缓存写在**调查根** ``_data/project.db`` 的 ``workspace_index_cache``
- 事实仍在各工作区自己的 ``project.db``
- 刷新是显式/挂钩动作；读路径可回退到现场扫描

后续升级：改 ``compute_workspace_index_stats`` 或加编号 migration 即可，
不必改调用方。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.db.db_manager import open_project_db, open_project_db_private
from app.utils.path_utils import normalize_path

_CACHE_COLS = (
    "specimen_count",
    "station_count",
    "event_count",
    "photo_count",
    "unassigned_photo_count",
    "result_count",
    "missing_coord_count",
    "taxonomy_incomplete_count",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_workspace_index_stats(workspace_dir: str) -> dict[str, int]:
    """Scan one workspace db (private conn) and return cache-shaped counts."""
    stats = {k: 0 for k in _CACHE_COLS}
    try:
        conn = open_project_db_private(workspace_dir)
    except Exception:
        return stats
    try:
        try:
            (n,) = conn.execute("SELECT COUNT(*) FROM specimens").fetchone()
            stats["specimen_count"] = int(n or 0)
        except sqlite3.Error:
            pass
        try:
            (n,) = conn.execute(
                "SELECT COUNT(DISTINCT station) FROM specimens "
                "WHERE station IS NOT NULL AND TRIM(station) != ''"
            ).fetchone()
            stats["station_count"] = int(n or 0)
        except sqlite3.Error:
            pass
        try:
            (n,) = conn.execute(
                "SELECT COUNT(DISTINCT collection_date) FROM specimens "
                "WHERE collection_date IS NOT NULL AND TRIM(collection_date) != ''"
            ).fetchone()
            stats["event_count"] = int(n or 0)
        except sqlite3.Error:
            pass
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN current_assignment_id IS NULL THEN 1 ELSE 0 END) AS unassigned
                  FROM photos
                """
            ).fetchone()
            if row:
                stats["photo_count"] = int(row[0] or 0)
                stats["unassigned_photo_count"] = int(row[1] or 0)
        except sqlite3.Error:
            # photos 表可能尚未使用；回退到 grouping 成片数
            pass
        try:
            cols = {
                r[1] for r in conn.execute("PRAGMA table_info(grouping)").fetchall()
            }
            if "composed_tiff_path" in cols:
                (n,) = conn.execute(
                    "SELECT COUNT(*) FROM grouping "
                    "WHERE composed_tiff_path IS NOT NULL AND TRIM(composed_tiff_path) != ''"
                ).fetchone()
                stats["result_count"] = int(n or 0)
        except sqlite3.Error:
            pass
        try:
            (n,) = conn.execute(
                """
                SELECT COUNT(*) FROM specimens
                 WHERE (lon IS NULL OR TRIM(CAST(lon AS TEXT)) = '')
                    OR (lat IS NULL OR TRIM(CAST(lat AS TEXT)) = '')
                """
            ).fetchone()
            stats["missing_coord_count"] = int(n or 0)
        except sqlite3.Error:
            pass
        try:
            (n,) = conn.execute(
                """
                SELECT COUNT(*) FROM specimens
                 WHERE scientific_name IS NULL OR TRIM(scientific_name) = ''
                """
            ).fetchone()
            stats["taxonomy_incomplete_count"] = int(n or 0)
        except sqlite3.Error:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return stats


def refresh_workspace_index(
    root_dir: str,
    workspace_dir: str,
    *,
    workspace_id: Optional[str] = None,
) -> dict[str, Any]:
    """Compute stats for *workspace_dir* and UPSERT into root catalog cache.

    Returns the upserted row dict (plus ``workspace_id``).
    """
    from app.services.project_catalog_service import ensure_workspace_meta

    root = normalize_path(root_dir)
    ws = normalize_path(workspace_dir)
    meta = ensure_workspace_meta(ws, root_dir=root)
    wid = workspace_id or str(meta.get("workspace_id") or "")
    if not wid:
        raise ValueError("无法解析 workspace_id")

    stats = compute_workspace_index_stats(ws)
    ts = _utc_now_iso()
    conn = open_project_db(root, create=True)
    conn.execute(
        """
        INSERT INTO workspace_index_cache (
          workspace_id, specimen_count, station_count, event_count,
          photo_count, unassigned_photo_count, result_count,
          missing_coord_count, taxonomy_incomplete_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id) DO UPDATE SET
          specimen_count=excluded.specimen_count,
          station_count=excluded.station_count,
          event_count=excluded.event_count,
          photo_count=excluded.photo_count,
          unassigned_photo_count=excluded.unassigned_photo_count,
          result_count=excluded.result_count,
          missing_coord_count=excluded.missing_coord_count,
          taxonomy_incomplete_count=excluded.taxonomy_incomplete_count,
          updated_at=excluded.updated_at
        """,
        (
            wid,
            stats["specimen_count"],
            stats["station_count"],
            stats["event_count"],
            stats["photo_count"],
            stats["unassigned_photo_count"],
            stats["result_count"],
            stats["missing_coord_count"],
            stats["taxonomy_incomplete_count"],
            ts,
        ),
    )
    # 同步 workspaces.last_indexed_at（若已登记）
    try:
        conn.execute(
            "UPDATE workspaces SET last_indexed_at=? WHERE workspace_id=?",
            (ts, wid),
        )
    except sqlite3.Error:
        pass
    conn.commit()
    row = dict(stats)
    row["workspace_id"] = wid
    row["updated_at"] = ts
    return row


def read_cached_index(
    root_dir: str,
    workspace_id: str,
) -> Optional[dict[str, Any]]:
    """Return one cache row or None."""
    try:
        conn = open_project_db(normalize_path(root_dir), create=False)
    except Exception:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM workspace_index_cache WHERE workspace_id=?",
            (workspace_id,),
        ).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None


def read_all_cached_indexes(root_dir: str) -> list[dict[str, Any]]:
    """Return all cache rows for a survey root."""
    try:
        conn = open_project_db(normalize_path(root_dir), create=False)
    except Exception:
        return []
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM workspace_index_cache ORDER BY workspace_id"
            ).fetchall()
        ]
    except sqlite3.Error:
        return []


def refresh_registered_workspaces(root_dir: str) -> list[dict[str, Any]]:
    """Refresh cache for every active workspace registered under *root_dir*."""
    from app.services.project_catalog_service import list_registered_workspaces

    root = Path(normalize_path(root_dir))
    out: list[dict[str, Any]] = []
    for row in list_registered_workspaces(str(root)):
        rel = str(row.get("relative_path") or "")
        wid = str(row.get("workspace_id") or "")
        if not rel or not wid:
            continue
        ws_path = str((root / rel).resolve()) if not Path(rel).is_absolute() else rel
        try:
            out.append(refresh_workspace_index(str(root), ws_path, workspace_id=wid))
        except Exception:
            continue
    return out


def cached_kpi_for_workspaces(
    root_dir: Optional[str],
    workspace_dirs: list[str],
) -> Optional[dict[str, int]]:
    """Sum KPI from root cache when every workspace has a fresh-enough row.

    Returns None if root missing or any workspace lacks cache → caller should
    fall back to live scan.
    """
    if not root_dir or not workspace_dirs:
        return None
    root = normalize_path(root_dir)
    try:
        from app.services.project_catalog_service import ensure_workspace_meta
    except Exception:
        return None

    totals = {k: 0 for k in _CACHE_COLS}
    for ws in workspace_dirs:
        try:
            meta = ensure_workspace_meta(ws, root_dir=root)
            wid = str(meta.get("workspace_id") or "")
        except Exception:
            return None
        cached = read_cached_index(root, wid) if wid else None
        if not cached:
            return None
        for k in _CACHE_COLS:
            totals[k] += int(cached.get(k) or 0)
    return totals
