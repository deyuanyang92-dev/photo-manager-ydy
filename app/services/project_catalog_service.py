"""Project catalog service for survey roots and child workspaces.

The catalog lives in the survey root's ``_data/project.db`` and records which
child directories are managed workspaces. Each workspace still keeps its own
``_data/project.db`` for specimens, photos, grouping, collaboration tasks, and
collection records.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.db.db_manager import open_project_db
from app.utils.path_utils import normalize_path


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_one_row(conn, sql: str, params: tuple = ()):
    return conn.execute(sql, params).fetchone()


def ensure_survey_project(
    root_dir: str,
    *,
    name: Optional[str] = None,
    code: Optional[str] = None,
    location: Optional[str] = None,
    date_range: Optional[str] = None,
    raw: Optional[dict] = None,
) -> dict:
    """Ensure the survey root has a catalog row and return it as a dict."""
    root = Path(normalize_path(root_dir))
    conn = open_project_db(str(root), create=True)
    row = _fetch_one_row(conn, "SELECT * FROM survey_project LIMIT 1")
    ts = _utc_now_iso()
    if row is None:
        project_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO survey_project (
              project_id, name, code, root_relative_path, location, date_range,
              created_at, updated_at, raw_json
            ) VALUES (?, ?, ?, '.', ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                name or root.name,
                code,
                location,
                date_range,
                ts,
                ts,
                json.dumps(raw or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        row = _fetch_one_row(conn, "SELECT * FROM survey_project WHERE project_id=?", (project_id,))
    else:
        updates = {
            "name": name,
            "code": code,
            "location": location,
            "date_range": date_range,
        }
        non_empty = {k: v for k, v in updates.items() if v}
        if non_empty or raw:
            sets = [f"{k}=?" for k in non_empty]
            vals = list(non_empty.values())
            if raw:
                sets.append("raw_json=?")
                vals.append(json.dumps(raw, ensure_ascii=False))
            sets.append("updated_at=?")
            vals.append(ts)
            vals.append(row["project_id"])
            conn.execute(
                f"UPDATE survey_project SET {', '.join(sets)} WHERE project_id=?",
                tuple(vals),
            )
            conn.commit()
            row = _fetch_one_row(conn, "SELECT * FROM survey_project WHERE project_id=?", (row["project_id"],))
    return dict(row)


def ensure_workspace_meta(
    workspace_dir: str,
    *,
    project_id: Optional[str] = None,
    root_dir: Optional[str] = None,
    role: str = "workspace",
    display_name: Optional[str] = None,
    raw: Optional[dict] = None,
) -> dict:
    """Ensure a workspace has stable ``workspace_meta`` in its own DB.

    v0.56: 用私有连接并关闭(不再把子工作区文件锁扣到退出); 已有记录且调用方
    没带任何新信息时=纯读, 不再每次重写 updated_at。
    """
    from app.db.db_manager import open_project_db_private

    workspace = Path(normalize_path(workspace_dir))
    # §7 旧: conn = open_project_db(str(workspace), create=True)  # 缓存连接, 锁泄漏
    conn = open_project_db_private(str(workspace), create=True)
    try:
        return _ensure_workspace_meta_on(
            conn,
            workspace,
            project_id=project_id,
            root_dir=root_dir,
            role=role,
            display_name=display_name,
            raw=raw,
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ensure_workspace_meta_on(
    conn,
    workspace: Path,
    *,
    project_id: Optional[str],
    root_dir: Optional[str],
    role: str,
    display_name: Optional[str],
    raw: Optional[dict],
) -> dict:
    row = _fetch_one_row(conn, "SELECT * FROM workspace_meta LIMIT 1")
    ts = _utc_now_iso()
    root_hint = normalize_path(root_dir) if root_dir else None
    if row is not None and all(
        v is None for v in (project_id, root_hint, display_name, raw)
    ) and role == "workspace":
        # 纯读: 无任何新信息, 不写 (v0.55 每次调用都 UPDATE updated_at)
        return dict(row)
    if row is None:
        workspace_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO workspace_meta (
              workspace_id, project_id, role, display_name, root_project_hint,
              created_at, updated_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                project_id,
                role,
                display_name or workspace.name,
                root_hint,
                ts,
                ts,
                json.dumps(raw or {}, ensure_ascii=False),
            ),
        )
        conn.commit()
        row = _fetch_one_row(conn, "SELECT * FROM workspace_meta WHERE workspace_id=?", (workspace_id,))
    else:
        conn.execute(
            """
            UPDATE workspace_meta
               SET project_id=COALESCE(?, project_id),
                   role=COALESCE(?, role),
                   display_name=COALESCE(?, display_name),
                   root_project_hint=COALESCE(?, root_project_hint),
                   updated_at=?,
                   raw_json=CASE WHEN ? IS NULL THEN raw_json ELSE ? END
             WHERE workspace_id=?
            """,
            (
                project_id,
                role,
                display_name,
                root_hint,
                ts,
                json.dumps(raw, ensure_ascii=False) if raw is not None else None,
                json.dumps(raw, ensure_ascii=False) if raw is not None else None,
                row["workspace_id"],
            ),
        )
        conn.commit()
        row = _fetch_one_row(conn, "SELECT * FROM workspace_meta WHERE workspace_id=?", (row["workspace_id"],))
    return dict(row)


def read_workspace_meta(workspace_dir: str) -> Optional[dict]:
    """Pure-read ``workspace_meta`` from a workspace's own DB.

    私有连接 + 立即关闭, 零写入(不建库/不迁移/不碰 updated_at)。
    库缺失、表缺失(legacy db)或读失败 → None, 调用方走活扫描兜底。
    """
    from app.db.db_manager import open_project_db_private

    try:
        conn = open_project_db_private(normalize_path(workspace_dir))
    except Exception:
        return None
    try:
        row = _fetch_one_row(conn, "SELECT * FROM workspace_meta LIMIT 1")
        return dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def register_workspace(
    root_dir: str,
    workspace_dir: str,
    *,
    role: str = "workspace",
    name: Optional[str] = None,
    project_meta: Optional[dict] = None,
    raw: Optional[dict] = None,
) -> dict:
    """Register *workspace_dir* in the survey root catalog.

    Returns a dict with ``project`` and ``workspace`` rows. Paths stored in the
    root catalog are relative to ``root_dir`` so a whole survey folder can move.
    """
    root = Path(normalize_path(root_dir))
    workspace = Path(normalize_path(workspace_dir))
    project = ensure_survey_project(
        str(root),
        name=(project_meta or {}).get("name"),
        code=(project_meta or {}).get("code"),
        location=(project_meta or {}).get("location"),
        date_range=(project_meta or {}).get("date_range"),
        raw=project_meta,
    )
    meta = ensure_workspace_meta(
        str(workspace),
        project_id=project["project_id"],
        root_dir=str(root),
        role=role,
        display_name=name or workspace.name,
        raw=raw,
    )

    try:
        # Catalog paths are stored POSIX-style so a survey folder can move
        # across OSes (Windows relpath yields backslashes).
        rel = os.path.relpath(workspace, root).replace(os.sep, "/")
    except ValueError:
        rel = workspace.name
    ts = _utc_now_iso()
    conn = open_project_db(str(root), create=True)
    conn.execute(
        """
        INSERT INTO workspaces (
          workspace_id, project_id, parent_workspace_id, role, name,
          relative_path, active, last_seen_at, raw_json
        ) VALUES (?, ?, NULL, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(workspace_id) DO UPDATE SET
          project_id=excluded.project_id,
          role=excluded.role,
          name=excluded.name,
          relative_path=excluded.relative_path,
          active=1,
          last_seen_at=excluded.last_seen_at,
          raw_json=excluded.raw_json
        """,
        (
            meta["workspace_id"],
            project["project_id"],
            role,
            name or workspace.name,
            rel,
            ts,
            json.dumps(raw or {}, ensure_ascii=False),
        ),
    )
    conn.commit()
    row = _fetch_one_row(conn, "SELECT * FROM workspaces WHERE workspace_id=?", (meta["workspace_id"],))
    result = {"project": project, "workspace": dict(row), "workspace_meta": meta}
    # 登记后刷新根库索引缓存（失败不阻断进入工作区）
    try:
        from app.services.workspace_index_service import refresh_workspace_index

        result["index_cache"] = refresh_workspace_index(
            str(root), str(workspace), workspace_id=meta["workspace_id"],
        )
    except Exception:
        pass
    return result


def list_registered_workspaces(root_dir: str) -> list[dict]:
    """Return active workspace catalog rows for a survey root."""
    # §7 旧: conn = open_project_db(..., create=True) —— 纯读却会替缺库的根目录建库。
    try:
        conn = open_project_db(normalize_path(root_dir), create=False)
    except Exception:
        return []
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT * FROM workspaces
             WHERE active=1
             ORDER BY COALESCE(display_order, 999999), relative_path
            """
        ).fetchall()
    ]
