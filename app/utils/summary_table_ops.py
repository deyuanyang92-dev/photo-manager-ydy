"""summary_table_ops.py — 数据汇总编号表：排序 / 列筛选（内存，不重新查库）。"""
from __future__ import annotations

from typing import Any, Callable, Iterable

_NUMERIC_KEYS = frozenset({
    "iso",
    "f_number",
    "focal_length",
    "image_width",
    "image_height",
    "lon",
    "lat",
    "exposure_time",
})

_DATE_KEYS = frozenset({
    "collection_date",
    "photo_date",
    "exif_datetime",
})


def _as_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def sort_key_for_row(
    row: dict[str, Any],
    key: str,
    *,
    cell_value: Callable[[dict[str, Any], str], str] | None = None,
) -> tuple:
    """Type-aware sort key: numbers/dates first, blanks last."""
    if cell_value is not None and key in {"_workspace_label"}:
        text = cell_value(row, key)
    elif key.startswith("storage_is_") or key.endswith("_is_rna"):
        from app.config.specimen_fields import eval_derived, is_derived

        if is_derived(key):
            return (0, 1 if eval_derived(key, row) else 0, "")
    else:
        text = ""
        if cell_value is not None:
            text = cell_value(row, key)
        else:
            val = row.get(key)
            text = "" if val is None else str(val).strip()

    if key in _NUMERIC_KEYS:
        num = _as_float(row.get(key))
        if num is None:
            return (2, 0.0, text.casefold())
        return (0, num, text.casefold())

    if key in _DATE_KEYS:
        raw = str(row.get(key) or text or "").strip()
        if not raw:
            return (2, "", "")
        return (1, raw, raw)

    if not text:
        return (2, "", "")
    return (1, text.casefold(), text)


def sort_specimen_rows(
    rows: Iterable[dict[str, Any]],
    key: str,
    *,
    ascending: bool = True,
    cell_value: Callable[[dict[str, Any], str], str] | None = None,
) -> list[dict[str, Any]]:
    ordered = sorted(
        list(rows or []),
        key=lambda r: sort_key_for_row(r, key, cell_value=cell_value),
    )
    if not ascending:
        ordered.reverse()
    return ordered


def unique_column_values(
    rows: Iterable[dict[str, Any]],
    key: str,
    *,
    cell_value: Callable[[dict[str, Any], str], str],
    limit: int = 500,
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows or []:
        text = cell_value(row, key)
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return sorted(out, key=lambda s: (s == "", s.casefold()))


def apply_column_filters(
    rows: Iterable[dict[str, Any]],
    filters: dict[str, set[str]],
    *,
    cell_value: Callable[[dict[str, Any], str], str],
) -> list[dict[str, Any]]:
    active = {
        k: set(v)
        for k, v in (filters or {}).items()
        if v is not None and len(v) > 0
    }
    if not active:
        return list(rows or [])
    out: list[dict[str, Any]] = []
    for row in rows or []:
        keep = True
        for key, allowed in active.items():
            text = cell_value(row, key)
            if text not in allowed:
                keep = False
                break
        if keep:
            out.append(row)
    return out
