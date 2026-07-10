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
import re
from typing import Any, Optional

from app.config.specimen_fields import eval_derived, is_derived
from app.utils.specimen_date_filter import date_in_range, is_date_field

__all__ = ["query_specimens", "field_choices", "operators_for_field"]


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


def operators_for_field(field: str) -> list[str]:
    """Return supported filter operator keys for a field."""
    if is_derived(field):
        return ["eq"]
    if is_date_field(field):
        return ["eq", "gte", "lte", "between", "in", "is_empty", "not_empty"]
    return ["eq", "in", "contains", "is_empty", "not_empty"]


def _split_values(value: object) -> list[str]:
    """Split a user-entered multi-value list."""
    text = str(value or "").strip()
    if not text:
        return []
    return [
        part.strip()
        for part in re.split(r"[|,，;；\n]+", text)
        if part.strip()
    ]


def _expand_date_token(token: object, *, end: bool = False) -> str:
    """Expand partial dates for inclusive lexical comparison.

    Supports ``YYYY``, ``YYYYMM``, ``YYYYMMDD``, ``YYYY-MM`` and ``YYYY-MM-DD``.
    """
    text = str(token or "").strip().replace("/", "-")
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if not digits:
        return text
    if len(digits) == 4:
        return f"{digits}-12-31" if end else f"{digits}-01-01"
    if len(digits) == 6:
        year, month = digits[:4], digits[4:6]
        return f"{year}-{month}-31" if end else f"{year}-{month}-01"
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text


def _parse_between_value(value: object) -> tuple[str, str]:
    """Parse common range notations into inclusive start/end dates."""
    text = str(value or "").strip()
    if not text:
        return "", ""
    for sep in ("|", "~", "～", "至", "到", ".."):
        if sep in text:
            lo, hi = text.split(sep, 1)
            return _expand_date_token(lo), _expand_date_token(hi, end=True)
    compact = re.fullmatch(r"\s*(\d{4}|\d{6}|\d{8})\s*-\s*(\d{4}|\d{6}|\d{8})\s*", text)
    if compact:
        return _expand_date_token(compact.group(1)), _expand_date_token(compact.group(2), end=True)
    return _expand_date_token(text), _expand_date_token(text, end=True)


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

    if is_date_field(field):
        if op == "is_empty":
            return not s.strip()
        if op == "not_empty":
            return bool(s.strip())
        if op == "eq":
            lo = _expand_date_token(val)
            hi = _expand_date_token(val, end=True)
            return date_in_range(s, lo, hi)
        if op == "in":
            return any(
                date_in_range(s, _expand_date_token(v), _expand_date_token(v, end=True))
                for v in _split_values(val)
            )
        if op == "gte":
            return date_in_range(s, _expand_date_token(val), None)
        if op == "lte":
            return date_in_range(s, None, _expand_date_token(val, end=True))
        if op == "between":
            lo, hi = _parse_between_value(val)
            return date_in_range(s, lo, hi)
        return False

    if op == "eq":
        return s == str(val)
    if op == "in":
        return s in set(_split_values(val))
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


def field_choices(
    workspaces: list[str],
    field: str,
    *,
    limit: int = 800,
) -> list[str]:
    """某字段在所有工作区中的非空 distinct 值(升序), 供筛选下拉。

    派生维度(is_derived)固定返回 ``["是", "否"]``。
    ``limit`` 防止超大字段(如 uid)撑爆下拉；超出时仍返回前 N 项。
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
                # DISTINCT + 上限，避免全表扫进内存后再截断
                rows = conn.execute(
                    f'SELECT DISTINCT "{field}" FROM specimens '
                    f'WHERE "{field}" IS NOT NULL AND TRIM("{field}") != "" '
                    f"ORDER BY 1 LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
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
            if len(vals) >= limit:
                break
        if len(vals) >= limit:
            break
    return sorted(vals)[:limit]
