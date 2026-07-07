"""Central field catalog for project-configured naming rules.

The UI, validators, and filename builders all need the same list of fields.
Keeping it here avoids hard-coded copies when a new naming segment is added.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class NamingField:
    key: str
    label: str
    short_label: str
    component: bool = True
    required: bool = True
    default_component: bool = False
    default_required: bool = False
    panel_input: bool = True


NAMING_FIELDS: tuple[NamingField, ...] = (
    NamingField("province", "地区", "地区", default_component=True, default_required=True),
    NamingField("site", "样地", "样地", default_component=True, default_required=True),
    NamingField("station", "站位", "站位", default_component=True),
    NamingField("species_id", "样品/物种标签", "编号", default_component=True, default_required=True),
    NamingField("storage", "保存方式", "保存", default_component=True, default_required=True),
    NamingField("date_seg", "日期段", "日期", required=False, default_component=True, panel_input=False),
    NamingField("collection_date", "采集日期", "采集日期", component=False, default_required=True),
    NamingField("photo_date", "拍摄日期", "拍摄日期", component=False, default_required=True),
    NamingField("habitat", "生境", "生境"),
    NamingField("collector", "采集人", "采集人"),
    NamingField("photographer", "拍摄人", "拍摄人"),
    NamingField("identifier", "鉴定人", "鉴定人"),
    NamingField("geo_area", "地理区", "地理区"),
    NamingField("taxon_group", "类群", "类群"),
    NamingField("order_name", "目", "目"),
    NamingField("family", "科", "科"),
    NamingField("genus", "属", "属"),
    NamingField("scientific_name", "物种学名", "学名"),
    NamingField("scientific_name_cn", "物种中文名", "中文名"),
    NamingField("notes", "备注标签", "备注"),
    NamingField("photo_notes", "拍照备注", "拍照备注"),
)

_FIELD_BY_KEY = {field.key: field for field in NAMING_FIELDS}


def _safe_custom_key(raw: object) -> str:
    text = str(raw or "").strip().lower()
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or not re.match(r"^[a-z_]", text):
        return ""
    return text


def _auto_custom_key(label: str, used: set[str]) -> str:
    base = _safe_custom_key(label) or "custom_field"
    candidate = base
    idx = 1
    while candidate in used or candidate in _FIELD_BY_KEY:
        idx += 1
        candidate = f"{base}_{idx}"
    return candidate


def normalize_custom_fields(raw: object) -> list[dict[str, str]]:
    """Return stable project custom field records with safe unique keys."""
    if isinstance(raw, dict):
        items = [
            {"key": key, "label": value}
            for key, value in raw.items()
        ]
    elif isinstance(raw, list):
        items = list(raw)
    else:
        items = []

    out: list[dict[str, str]] = []
    used: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        key = _safe_custom_key(item.get("key"))
        if not label and key:
            label = key
        if not label:
            continue
        if not key:
            key = _auto_custom_key(label, used)
        if key in _FIELD_BY_KEY:
            continue
        base = key
        idx = 1
        while key in used:
            idx += 1
            key = f"{base}_{idx}"
        used.add(key)
        out.append({"key": key, "label": label})
    return out


def custom_field_defs(raw: object) -> tuple[NamingField, ...]:
    return tuple(
        NamingField(
            str(item["key"]),
            str(item["label"]),
            str(item["label"]),
        )
        for item in normalize_custom_fields(raw)
    )


def all_fields(custom_fields: object = None) -> tuple[NamingField, ...]:
    return NAMING_FIELDS + custom_field_defs(custom_fields)


def field_by_key(key: str) -> NamingField | None:
    return _FIELD_BY_KEY.get(str(key or ""))


def field_by_key_with_custom(key: str, custom_fields: object = None) -> NamingField | None:
    text = str(key or "")
    field = _FIELD_BY_KEY.get(text)
    if field is not None:
        return field
    for custom in custom_field_defs(custom_fields):
        if custom.key == text:
            return custom
    return None


def field_label(key: str, *, short: bool = False, custom_fields: object = None) -> str:
    field = field_by_key_with_custom(key, custom_fields)
    if field is None:
        return str(key or "")
    return field.short_label if short else field.label


def component_fields(custom_fields: object = None) -> tuple[NamingField, ...]:
    return tuple(field for field in all_fields(custom_fields) if field.component)


def required_fields(custom_fields: object = None) -> tuple[NamingField, ...]:
    return tuple(field for field in all_fields(custom_fields) if field.required)


def default_components() -> list[str]:
    return [field.key for field in NAMING_FIELDS if field.default_component]


def default_required() -> dict[str, bool]:
    return {
        field.key: bool(field.default_required)
        for field in NAMING_FIELDS
        if field.required
    }


def normalize_naming_components(components: object) -> list[str]:
    if not isinstance(components, list) or not components:
        return default_components()
    out: list[str] = []
    for raw in components:
        key = str(raw or "").strip()
        if not key or key == "result_seq":
            continue
        if key not in out:
            out.append(key)
    return out or default_components()


def ordered_component_keys(components: object, custom_fields: object = None) -> list[str]:
    """Saved component order followed by unchecked catalog fields."""
    saved = normalize_naming_components(components)
    out = list(saved)
    for field in component_fields(custom_fields):
        if field.key not in out:
            out.append(field.key)
    return out


def normalize_required(required: object, custom_fields: object = None) -> dict[str, bool]:
    defaults = default_required()
    for field in required_fields(custom_fields):
        defaults.setdefault(field.key, False)
    if not isinstance(required, dict):
        return dict(defaults)
    out = dict(defaults)
    for key, value in required.items():
        key_text = str(key or "").strip()
        if key_text:
            out[key_text] = bool(value)
    return out


def dynamic_panel_keys(
    components: Iterable[str],
    required: dict[str, bool],
    custom_fields: object = None,
) -> list[str]:
    fixed = {
        "province",
        "site",
        "station",
        "species_id",
        "storage",
        "collection_date",
        "photo_date",
        "date_seg",
    }
    keys: list[str] = []
    for key in list(components) + [k for k, enabled in required.items() if enabled]:
        text = str(key or "").strip()
        field = field_by_key_with_custom(text, custom_fields)
        if (
            text
            and text not in fixed
            and text not in keys
            and (field is None or field.panel_input)
        ):
            keys.append(text)
    return keys
