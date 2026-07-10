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
# UID 组成段（province/site/station/id…）的标签以 naming_field_catalog 为准，
# 见 docs/PROJECT_MEMORY.md「三层概念 / 字段含义」— 勿在此写反：
#   province=省/市(GXFCG) · site=地区/样地(BLW) · id=物种编号(BZC003) · uid=完整标本号
FIELD_META: dict[str, dict[str, str]] = {
    "uid": {"label": "标本编号"},
    "_workspace_label": {"label": "工作区"},
    "scientific_name": {"label": "学名"},
    "scientific_name_cn": {"label": "中名"},
    "taxon_group": {"label": "门类"},
    "taxon_group_cn": {"label": "类群"},
    "order_name": {"label": "目(拉丁)"},
    "order_cn": {"label": "目"},
    "family": {"label": "科(拉丁)"},
    "family_cn": {"label": "科"},
    "genus": {"label": "属(拉丁)"},
    "genus_cn": {"label": "属"},
    "geo_area": {"label": "海区"},
    "lon": {"label": "经度"},
    "lat": {"label": "纬度"},
    "collection_date": {"label": "采集日期"},
    "photo_date": {"label": "拍摄日期"},
    "notes": {"label": "备注"},
    "photo_notes": {"label": "照片备注"},
    "camera_make": {"label": "相机品牌"},
    "camera_model": {"label": "相机型号"},
    "lens_model": {"label": "镜头型号"},
    "exposure_time": {"label": "快门"},
    "f_number": {"label": "光圈"},
    "iso": {"label": "ISO"},
    "focal_length": {"label": "焦距"},
    "exif_datetime": {"label": "EXIF 拍摄时间"},
    "photo_absolute_path": {"label": "照片绝对路径"},
    "result_tif_paths": {"label": "成果TIF路径列表"},
    "image_width": {"label": "照片宽度"},
    "image_height": {"label": "照片高度"},
}


# specimens 表列名 → naming_field_catalog 键（标签与工作台照片编号卡一致）
_NAMING_CATALOG_DB_KEYS: dict[str, str] = {
    "id": "species_id",
    "province": "province",
    "site": "site",
    "station": "station",
    "storage": "storage",
    "collection_date": "collection_date",
    "photo_date": "photo_date",
    "collector": "collector",
    "photographer": "photographer",
    "identifier": "identifier",
    "geo_area": "geo_area",
    "taxon_group": "taxon_group",
    "order_name": "order_name",
    "family": "family",
    "genus": "genus",
    "scientific_name": "scientific_name",
    "scientific_name_cn": "scientific_name_cn",
    "notes": "notes",
    "photo_notes": "photo_notes",
}

# 筛选 UI 不暴露的内部/兜底列（仍可经 PRAGMA 存在，仅对用户隐藏）。
HIDDEN_FILTER_FIELDS: frozenset[str] = frozenset({
    "raw_json",
    "owner_project_dir",
    "collab_updated_at",
    "metadata",
    "pinned",
    "angle",
})

# 下拉顺序：常用字段置顶，其余按中文 label 排序。
COMMON_FILTER_PRIORITY: tuple[str, ...] = (
    "uid",
    "station",
    "storage",
    "storage_is_rna",
    "photographer",
    "collector",
    "scientific_name",
    "scientific_name_cn",
    "province",
    "site",
    "collection_date",
    "photo_date",
    "family_cn",
    "family",
    "genus_cn",
    "genus",
    "taxon_group_cn",
    "taxon_group",
    "order_cn",
    "order_name",
    "geo_area",
    "identifier",
    "notes",
    "photo_notes",
    "lon",
    "lat",
    "id",
)

# 数据汇总「显示列」分组 — 对齐工作台右栏卡片布局：
#   卡1 naming_panel（照片编号）· 卡2 taxon_card_panel（分类标签）
#   · 卡3 metadata_panel（其它）· 成片 EXIF（拍照与相机，汇总时补全）
# (category_id, 中文标题, 该组字段 key 顺序)
SUMMARY_COLUMN_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "naming_geo",
        "照片编号 · 采集位置",
        ("province", "site", "station"),
    ),
    (
        "naming_identity",
        "照片编号 · 编号与保存",
        ("id", "storage", "storage_is_rna"),
    ),
    (
        "naming_date",
        "照片编号 · 日期",
        ("collection_date", "photo_date"),
    ),
    (
        "naming_notes",
        "照片编号 · 拍照备注",
        ("photo_notes",),
    ),
    (
        "voucher",
        "标本唯一编号",
        ("uid", "_workspace_label"),
    ),
    (
        "taxon",
        "分类标签",
        (
            "taxon_group",
            "taxon_group_cn",
            "order_name",
            "order_cn",
            "family",
            "family_cn",
            "genus",
            "genus_cn",
            "scientific_name",
            "scientific_name_cn",
            "notes",
        ),
    ),
    (
        "metadata",
        "其它",
        (
            "collector",
            "photographer",
            "identifier",
            "lon",
            "lat",
            "geo_area",
            "habitat",
            "angle",
        ),
    ),
    (
        "camera",
        "拍照与相机",
        (
            "photo_absolute_path",
            "camera_make",
            "camera_model",
            "lens_model",
            "exposure_time",
            "f_number",
            "iso",
            "focal_length",
            "exif_datetime",
            "image_width",
            "image_height",
        ),
    ),
)

_SUMMARY_CATEGORY_BY_KEY: dict[str, str] = {
    key: cat_id
    for cat_id, _label, keys in SUMMARY_COLUMN_CATEGORIES
    for key in keys
}

_SUMMARY_PHOTO_FIELD_PREFIXES: tuple[str, ...] = (
    "camera_",
    "lens_",
    "exif_",
    "focal_",
    "exposure_",
    "shutter_",
)

_SUMMARY_PHOTO_FIELD_NAMES: frozenset[str] = frozenset({
    "iso",
    "f_number",
    "fnumber",
    "aperture",
    "focal_length",
    "exposure_time",
    "datetime_original",
    "make",
    "model",
    "photographic_sensitivity",
    "photo_absolute_path",
    "image_width",
    "image_height",
})

_SUMMARY_METADATA_FIELD_NAMES: frozenset[str] = frozenset({
    "collector",
    "photographer",
    "identifier",
    "lon",
    "lat",
    "geo_area",
    "habitat",
    "angle",
})

_SUMMARY_NAMING_GEO_NAMES: frozenset[str] = frozenset({"province", "site", "station"})
_SUMMARY_NAMING_IDENTITY_NAMES: frozenset[str] = frozenset({"id", "storage", "storage_is_rna"})
_SUMMARY_NAMING_DATE_NAMES: frozenset[str] = frozenset({"collection_date", "photo_date"})
_SUMMARY_NAMING_NOTES_NAMES: frozenset[str] = frozenset({"photo_notes"})
_SUMMARY_VOUCHER_NAMES: frozenset[str] = frozenset({"uid", "_workspace_label"})

# 数据汇总查询时从成片 TIF 补全的列（非 specimens 表实列）。
SUMMARY_ENRICHED_SPECIMEN_KEYS: tuple[str, ...] = (
    "photo_absolute_path",
    "result_tif_paths",
    "camera_make",
    "camera_model",
    "lens_model",
    "exposure_time",
    "f_number",
    "iso",
    "focal_length",
    "exif_datetime",
    "image_width",
    "image_height",
)

_SUMMARY_CATEGORY_LABELS: dict[str, str] = {
    cat_id: label for cat_id, label, _keys in SUMMARY_COLUMN_CATEGORIES
}
_SUMMARY_CATEGORY_LABELS["other"] = "其它字段"

_SUMMARY_CATEGORY_ORDER: tuple[str, ...] = tuple(
    cat_id for cat_id, _label, _keys in SUMMARY_COLUMN_CATEGORIES
) + ("other",)

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
    """字段的中文显示标签; UID 组成段对齐 naming_field_catalog + PROJECT_MEMORY."""
    if key in DERIVED:
        return DERIVED[key]["label"]
    catalog_key = _NAMING_CATALOG_DB_KEYS.get(key)
    if catalog_key:
        from app.services.naming_field_catalog import field_label as naming_label

        return naming_label(catalog_key)
    if key in FIELD_META:
        return FIELD_META[key]["label"]
    return key


def summary_field_category(key: str) -> str:
    """数据汇总列所属分组 id（对齐工作台卡片；未知列按命名启发式归类）。"""
    if key in _SUMMARY_CATEGORY_BY_KEY:
        return _SUMMARY_CATEGORY_BY_KEY[key]
    low = str(key or "").strip().lower()
    if not low:
        return "other"
    if key in _SUMMARY_VOUCHER_NAMES:
        return "voucher"
    if key in _SUMMARY_NAMING_GEO_NAMES:
        return "naming_geo"
    if key in _SUMMARY_NAMING_IDENTITY_NAMES:
        return "naming_identity"
    if key in _SUMMARY_NAMING_DATE_NAMES:
        return "naming_date"
    if key in _SUMMARY_NAMING_NOTES_NAMES:
        return "naming_notes"
    if key in _SUMMARY_METADATA_FIELD_NAMES:
        return "metadata"
    if any(low.startswith(prefix) for prefix in _SUMMARY_PHOTO_FIELD_PREFIXES):
        return "camera"
    if low in _SUMMARY_PHOTO_FIELD_NAMES:
        return "camera"
    if low.endswith("_path") and any(token in low for token in ("photo", "tif", "tiff", "result")):
        return "camera"
    if low.startswith(("taxon_", "order_", "family", "genus", "scientific_")):
        return "taxon"
    return "other"


def group_summary_columns(
    columns: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """将汇总列按 SUMMARY_COLUMN_CATEGORIES 分组，供显示列对话框使用。"""
    if not columns:
        return []

    order_index: dict[str, tuple[str, int]] = {}
    for cat_id, _label, keys in SUMMARY_COLUMN_CATEGORIES:
        for index, key in enumerate(keys):
            order_index[key] = (cat_id, index)

    buckets: dict[str, list[tuple[str, str]]] = {
        cat_id: [] for cat_id, _label, _keys in SUMMARY_COLUMN_CATEGORIES
    }
    buckets["other"] = []

    for key, label in columns:
        cat_id = summary_field_category(key)
        if cat_id not in buckets:
            cat_id = "other"
        buckets[cat_id].append((key, label))

    def _sort_key(item: tuple[str, str]) -> tuple[int, int, str]:
        field_key, field_label_text = item
        cat_id = summary_field_category(field_key)
        spec = order_index.get(field_key)
        if spec is not None and spec[0] == cat_id:
            return (0, spec[1], field_label_text)
        return (1, 0, field_label_text)

    groups: list[dict[str, Any]] = []
    for cat_id in _SUMMARY_CATEGORY_ORDER:
        items = buckets.get(cat_id) or []
        if not items:
            continue
        items.sort(key=_sort_key)
        groups.append({
            "id": cat_id,
            "label": _SUMMARY_CATEGORY_LABELS.get(cat_id, cat_id),
            "columns": items,
        })
    return groups


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


def _filter_field_sort_key(field: dict[str, Any]) -> tuple[int, int, str]:
    key = str(field.get("key") or "")
    if key in COMMON_FILTER_PRIORITY:
        return (0, COMMON_FILTER_PRIORITY.index(key), "")
    return (1, 0, str(field.get("label") or key))


def filterable_fields(db_path: str) -> list[dict[str, Any]]:
    """可筛字段 = PRAGMA 列 ∪ FIELD_META ∪ DERIVED, 带中文 label。

    返回 ``[{"key": str, "label": str, "derived": bool}]``。内部列见
    ``HIDDEN_FILTER_FIELDS``；顺序为常用优先，其余按 label 升序。
    """
    cols = _pragma_columns(db_path)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for c in cols:
        if c in seen or c in HIDDEN_FILTER_FIELDS:
            continue
        seen.add(c)
        out.append({"key": c, "label": field_label(c), "derived": False})
    for c in FIELD_META:
        if c in seen or c in HIDDEN_FILTER_FIELDS:
            continue
        seen.add(c)
        out.append({"key": c, "label": field_label(c), "derived": False})
    for c in _NAMING_CATALOG_DB_KEYS:
        if c in seen or c in HIDDEN_FILTER_FIELDS:
            continue
        seen.add(c)
        out.append({"key": c, "label": field_label(c), "derived": False})
    for c in DERIVED:
        if c in seen:
            continue
        seen.add(c)
        out.append({"key": c, "label": DERIVED[c]["label"], "derived": True})
    out.sort(key=_filter_field_sort_key)
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
