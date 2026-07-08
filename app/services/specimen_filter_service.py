"""specimen_filter_service.py — 跨断面 specimen 筛选(只读).

spec: docs/specs/2026-07-08-data-filter-view-design.md §4.2

给定多个工作区目录, 各读 ``_data/project.db`` 的 ``specimens`` 表, 内存合并 +
按条件 AND 过滤(派生维度 post-filter)。**纯 SELECT, 永不写 db**(红线)。

容错同 ``taxon_inventory_service``: db 缺失/损坏/锁定 → 跳过, 不抛。

字段名来自 ``specimen_fields`` 注册表(PRAGMA ∪ META ∪ DERIVED), 非硬编码。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.config.specimen_fields import eval_derived, is_derived

__all__ = ["query_specimens", "field_choices"]


def _workspace_rows(workspace: str) -> list[dict[str, Any]]:
    """读单工作区 specimens 全行(列名取自 PRAGMA, 避 SELECT * 碰 raw_json 兜底大字段)。"""
    db_path = Path(workspace) / "_data" / "project.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(specimens)").fetchall()]
            if "uid" not in cols:
                return []
            col_sql = ", ".join(f'"{c}"' for c in cols)
            rows = conn.execute(f"SELECT {col_sql} FROM specimens").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [dict(zip(cols, r)) for r in rows]


def _match_one(row: dict[str, Any], cond: dict[str, Any]) -> bool:
    field = str(cond.get("field") or "")
    op = str(cond.get("op") or "eq")
    val = cond.get("value", "")

    # 派生维度(RNA 等): op=eq, value 是/否
    if is_derived(field):
        b = eval_derived(field, row)
        want_true = str(val).strip() not in ("否", "false", "0", "no", "")
        return b == want_true

    cell = row.get(field)
    s = "" if cell is None else str(cell)
    if op == "eq":
        return s == str(val)
    if op == "contains":
        return (str(val) in s) if str(val).strip() else True
    if op == "is_empty":
        return not s.strip()
    if op == "not_empty":
        return bool(s.strip())
    return False


def _matches(row: dict[str, Any], conditions: list[dict[str, Any]]) -> bool:
    """AND — 全部条件满足。空 conditions = 全通过(仅列出, 不过滤)。"""
    return all(_match_one(row, c) for c in conditions)


def query_specimens(
    workspaces: list[str],
    conditions: list[dict[str, Any]],
    *,
    labels: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """跨断面查询 specimen, 按 conditions(AND)过滤。

    返回行 = specimens 各列 + ``_workspace``(目录) + ``_workspace_label``(断面名)。
    conditions: ``[{"field": str, "op": "eq|contains|is_empty|not_empty", "value": str}]``;
    派生维度用 ``op="eq"`` + value ``是/否``。空 conditions 返回全部行。
    """
    out: list[dict[str, Any]] = []
    for i, ws in enumerate(workspaces):
        label = labels[i] if labels and i < len(labels) else (Path(ws).name or str(ws))
        for row in _workspace_rows(ws):
            row["_workspace"] = str(ws)
            row["_workspace_label"] = label
            if _matches(row, conditions):
                out.append(row)
    return out


def field_choices(workspaces: list[str], field: str) -> list[str]:
    """某字段在所有工作区中的非空 distinct 值(升序), 供筛选下拉。

    派生维度(is_derived)固定返回 ``["是", "否"]``。
    """
    if is_derived(field):
        return ["是", "否"]
    vals: set[str] = set()
    for ws in workspaces:
        db_path = Path(ws) / "_data" / "project.db"
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute(f'SELECT "{field}" FROM specimens').fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        for (v,) in rows:
            if v is None:
                continue
            s = str(v).strip()
            if s:
                vals.add(s)
    return sorted(vals)
