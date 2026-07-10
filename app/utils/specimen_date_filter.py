"""specimen_date_filter.py — 标本日期字段比较（Qt-free）.

与 ``summary_view`` 中逻辑一致，供 ``specimen_filter_service`` 与汇总查询复用。
"""
from __future__ import annotations

DATE_FIELD_KEYS = frozenset({
    "collection_date",
    "photo_date",
    "collection_time",
})


def norm_date(value: object) -> str:
    """Normalise to ISO ``yyyy-mm-dd`` (slashes → dashes). Empty → ""."""
    if not value:
        return ""
    return str(value).strip().replace("/", "-")


def date_in_range(value: object, lo: object, hi: object) -> bool:
    """Inclusive range on ISO date strings. Empty value never matches."""
    v = norm_date(value)
    if not v:
        return False
    lo_s = norm_date(lo)
    hi_s = norm_date(hi)
    if lo_s and v < lo_s:
        return False
    if hi_s and v > hi_s:
        return False
    return True


def is_date_field(field: str) -> bool:
    return str(field or "") in DATE_FIELD_KEYS
