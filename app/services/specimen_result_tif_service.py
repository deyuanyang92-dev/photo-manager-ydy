"""specimen_result_tif_service.py — 成果母版 TIF 绝对路径索引（工作区库）.

数据汇总、选片、导出 TIF 都依赖这里记录的**原始 .tif 路径**。
界面缩略图可走 JPEG 代理（见 image_thumbnail），但 path 永远是母版 TIF。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.db.db_manager import open_project_db
from app.db.result_tif_schema import ensure_result_tif_index_table
from app.utils.path_utils import normalize_path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_absolute_tif(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).resolve())
    except OSError:
        return text


def sync_workspace_result_tifs(workspace_dir: str) -> int:
    """Scan ``results/`` and persist absolute master TIF paths into workspace db."""
    from app.services import project_service as ps

    root = normalize_path(workspace_dir)
    if not root:
        return 0
    try:
        conn = open_project_db(root)
    except Exception:
        return 0

    ensure_result_tif_index_table(conn)
    res = ps.get_project_results(root)
    now = _utc_now_iso()
    rows: list[tuple[Any, ...]] = []

    for group in res.get("groups") or []:
        uid = str(group.get("uid") or "").strip()
        if not uid:
            continue
        for item in group.get("items") or []:
            abs_path = _resolve_absolute_tif(str(item.get("path") or ""))
            if not abs_path.lower().endswith((".tif", ".tiff")):
                continue
            if not Path(abs_path).is_file():
                continue
            seq = item.get("seq")
            try:
                seq_val = int(seq) if seq is not None else None
            except (TypeError, ValueError):
                seq_val = None
            rows.append((
                uid,
                seq_val,
                abs_path,
                str(item.get("name") or Path(abs_path).name),
                str(item.get("mtime") or ""),
                now,
            ))

    with conn:
        conn.execute("DELETE FROM specimen_result_tif_index")
        if rows:
            conn.executemany(
                """
                INSERT INTO specimen_result_tif_index (
                  uid, seq, absolute_path, file_name, mtime_iso, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
    return len(rows)


def list_result_tif_paths(conn: sqlite3.Connection, uid: str) -> list[str]:
    """All recorded master TIF absolute paths for *uid*, ordered by seq."""
    ensure_result_tif_index_table(conn)
    text = str(uid or "").strip()
    if not text:
        return []
    try:
        cur = conn.execute(
            """
            SELECT absolute_path FROM specimen_result_tif_index
             WHERE uid=?
             ORDER BY COALESCE(seq, 999999), absolute_path
            """,
            (text,),
        )
        out: list[str] = []
        seen: set[str] = set()
        for (path,) in cur.fetchall():
            p = str(path or "").strip()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out
    except sqlite3.Error:
        return []


def primary_result_tif_path(conn: sqlite3.Connection, uid: str) -> Optional[str]:
    paths = list_result_tif_paths(conn, uid)
    return paths[0] if paths else None


def lookup_result_tifs_for_workspace(workspace_dir: str, uid: str) -> list[str]:
    root = normalize_path(workspace_dir)
    if not root or not uid:
        return []
    try:
        conn = open_project_db(root)
        return list_result_tif_paths(conn, uid)
    except Exception:
        return []
