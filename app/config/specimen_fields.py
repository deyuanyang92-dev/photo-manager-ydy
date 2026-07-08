"""specimen_fields.py — 数据筛选字段注册表(方案3 PRAGMA+注册表).

spec: docs/specs/2026-07-08-data-filter-view-design.md

**不存字段列表**。可筛字段 = ``PRAGMA table_info(specimens)`` ∪ ``FIELD_META`` ∪
``DERIVED``。升级 DB 加列 → 自动可筛(零码改);中文科标签 / 派生语义(RNA)放注册表。

- ``FIELD_META``: 只存**增强元数据**(中文 label),不存字段列表。
- ``DERIVED``: 派生维度, 如 ``storage_is_rna``(oracle ``app.js:300``: storage 以
  ``R`` 开头 = 已取 RNA, transcriptome=true)。

Qt-free, 纯数据 + sqlite PRAGMA, 易测。
"""
from __future__ import annotations

import sqlite3
from typing import Any

# 只增强元数据(中文 label), 不存字段列表 — 列表走 PRAGMA。
FIELD_META: dict[str, dict[str, str]] = {
    "storage": {"label": "保存方式"},
    "photographer": {"label": "拍摄人"},
    "collector": {"label": "采集人"},
    "identifier": {"label": "鉴定人"},
    "province": {"label": "省"},
    "site": {"label": "地区"},
    "station": {"label": "站位"},
    "scientific_name": {"label": "学名"},
    "scientific_name_cn": {"label": "中名"},
    "taxon_group": {"label": "门类"},
    "order_name": {"label": "目"},
    "family": {"label": "科"},
    "genus": {"label": "属"},
    "geo_area": {"label": "海区"},
    "collection_date": {"label": "采集日期"},
    "photo_date": {"label": "拍摄日期"},
    "notes": {"label": "备注"},
    "photo_notes": {"label": "照片备注"},
}

# 派生维度: 从现有列解码出的语义筛选项。升级加派生 → 这里加一条。
DERIVED: dict[str, dict[str, Any]] = {
    "storage_is_rna": {
        "label": "已取RNA",
        "from": "storage",
        # oracle app.js:300 — R 前缀 = RNAlater, transcriptome=true
        "match": lambda v: str(v or "").strip().upper().startswith("R"),
    },
}


def is_derived(key: str) -> bool:
    """该字段是否为派生维度(非 db 实列)。"""
    return key in DERIVED


def field_label(key: str) -> str:
    """字段的中文显示标签; FIELD_META/DERIVED 命中返回 label, 否则返回 key 本身。"""
    if key in FIELD_META:
        return FIELD_META[key]["label"]
    if key in DERIVED:
        return DERIVED[key]["label"]
    return key


def _pragma_columns(db_path: str) -> list[str]:
    """读 specimens 表的实列名(PRAGMA table_info)。损坏/无表 → []."""
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("PRAGMA table_info(specimens)").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    # row: (cid, name, type, notnull, dflt_value, pk)
    return [str(r[1]) for r in rows if r[1]]


def filterable_fields(db_path: str) -> list[dict[str, Any]]:
    """可筛字段 = PRAGMA 列 ∪ FIELD_META ∪ DERIVED, 带中文 label。

    返回 ``[{"key": str, "label": str, "derived": bool}]``。顺序: PRAGMA 列优先
    (表实际顺序), 再补 FIELD_META(PRAGMA 没有的, 兼容损坏 db), 再 DERIVED。
    """
    cols = _pragma_columns(db_path)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in cols:
        if c in seen:
            continue
        seen.add(c)
        out.append({"key": c, "label": field_label(c), "derived": False})
    for c in FIELD_META:
        if c in seen:
            continue
        seen.add(c)
        out.append({"key": c, "label": FIELD_META[c]["label"], "derived": False})
    for c in DERIVED:
        if c in seen:
            continue
        seen.add(c)
        out.append({"key": c, "label": DERIVED[c]["label"], "derived": True})
    return out


def eval_derived(key: str, row: dict[str, Any]) -> bool:
    """对 specimen 行(dict)求派生维度布尔值。非派生 key → False。

    用于查询结果的 post-filter(UI 勾选「已取RNA」时过滤)。
    """
    spec = DERIVED.get(key)
    if spec is None:
        return False
    src = spec.get("from")
    val = row.get(src) if isinstance(row, dict) else None
    match = spec.get("match")
    try:
        return bool(match(val)) if match is not None else False
    except Exception:
        return False
