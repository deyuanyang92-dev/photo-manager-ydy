"""project_settings_service.py — load/save per-project settings rows.

Mirrors the project-level settings objects in app.js (personnel, codeLabels,
tiffFields, customStorages, projectMeta) stored in project_settings table.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import QSettings

from app.services.naming_field_catalog import default_components, default_required
from app.utils.path_utils import normalize_path

# ── Defaults (mirrors app.js:3084-3092, 9634, 9527, etc.) ─────────────────────

DEFAULT_TIFF_FIELDS: dict[str, bool] = {
    "uniqueId": True,
    "projectName": True,
    "scientificName": True,
    "scientificNameCn": True,
    "collectionDate": True,
    "photoDate": True,
    "collector": True,
    "photographer": True,
    "identifier": True,
    "lon": True,
    "lat": True,
    "geoArea": False,
    "taxonGroup": False,
    "order": False,
    "family": False,
    "notes": False,
    "photoNotes": True,
}

DEFAULT_TIFF_METADATA_WRITE: dict[str, Any] = {
    "enabled": True,
    # fill_empty: write missing workbench metadata only; never replace existing
    # workbench values. skip_written: skip files already marked by this app.
    # force: replace this app's business metadata with current DB values.
    "mode": "fill_empty",
}

DEFAULT_PERSONNEL: dict[str, str] = {
    "verifier": "",
    "logistics": "",
    "collector": "",
    "photographer": "",
    "identifier": "",
}

DEFAULT_CODE_LABELS: dict[str, Any] = {
    "province": "",
    "site": "",
    "stations": {},
    "species": {},
}

DEFAULT_NAMING_RULES: dict[str, Any] = {
    "components": default_components(),
    "required": default_required(),
    "custom_fields": [],
    "site_min_length": 2,
    "date_8_digits": True,
    "storage_prefix": True,
}

DEFAULT_CAPTURE_DEFAULTS: dict[str, str] = {
    # 项目级默认拍摄坐标/地理区（站位级数据的兜底）。新号自动带，选站位后由
    # 采集记录覆盖。空 = 不预填。
    "lon": "",
    "lat": "",
    "geoArea": "",
}

DEFAULT_PROJECT_META: dict[str, str] = {
    "project_code": "",
    "name": "",
    "year": "",
    "date_range": "",
    "location": "",
    "photo_location": "",
}

DEFAULT_PRINT_SETTINGS: dict[str, Any] = {
    # 工作台编号旁打印按钮的项目级偏好。默认贴合拍摄现场流程：
    # 有默认打印机就直出；普通标本打印样品瓶标签，R 前缀标本额外打印
    # RNAlater 组织管标签。完整批量/模板调整仍在标签打印页。
    "quick_print": True,
    # quick_print_mode controls workbench print-button behavior:
    #   "direct" — print directly to configured printer (current behavior)
    #   "dialog" — show printer-selection dialog
    #   "studio" — open the Labels print page (legacy quick_print=False)
    "quick_print_mode": "direct",
    "include_tissue": True,
    # Empty printer name = use the current system default.  Separate fields let
    # sample-bottle and RNAlater tube labels go to different devices/papers.
    "sample_printer": "",
    "tissue_printer": "",
    # Empty = follow the paper mode last chosen in 标签打印. Otherwise one of
    # "label", "a4", "a5".
    "sample_paper_type": "label",
    "tissue_paper_type": "label",
    # Empty = follow the template last chosen in 标签打印. Otherwise a built-in
    # template key or "custom:<template_id>" from the per-bucket library.
    "sample_template_key": "standard",
    "tissue_template_key": "tissueCompact",
    # auto: direct print when the tissue route is a dedicated/small-label path;
    # queue when tissue uses a sheet paper on the same printer.
    "tissue_strategy": "auto",  # auto/direct/queue
    "missing_printer_policy": "open_studio",
    "tissue_sheet": {
        "paper": "a4",
        "label_w_mm": 30.0,
        "label_h_mm": 15.0,
        "margin_left_mm": 5.0,
        "margin_top_mm": 5.0,
        "margin_right_mm": 5.0,
        "margin_bottom_mm": 5.0,
        "gap_x_mm": 2.0,
        "gap_y_mm": 2.0,
        "cut_marks": True,
    },
}

_APP_SETTINGS_ORG = "SpecimenPhotoWorkbench"
_APP_SETTINGS_APP = "标本照片工作台"
_GLOBAL_PRINT_DEFAULTS_KEY = "print/default_settings"
_SQLITE_LOCK_RETRY_DELAYS = (0.08, 0.16, 0.32, 0.64, 1.0)

# Built-in preservation methods — constants, never stored in DB (mirrors app.js:549)
BUILTIN_STORAGES: list[dict[str, Any]] = [
    {"code": "T95E",  "detail": "梯度酒精固定，最终以95%酒精保存",                         "transcriptome": False},
    {"code": "D95E",  "detail": "直接以95%酒精固定并保存",                               "transcriptome": False},
    {"code": "D75E",  "detail": "直接以75%酒精固定并保存",                               "transcriptome": False},
    {"code": "T75E",  "detail": "梯度酒精固定，最终以75%酒精保存",                         "transcriptome": False},
    {"code": "D79",   "detail": "直接以75%酒精固定，然后转95%酒精长期保存",                 "transcriptome": False},
    {"code": "T79",   "detail": "梯度酒精固定至75%，然后转95%酒精长期保存",                 "transcriptome": False},
    {"code": "T100",  "detail": "梯度酒精固定，最终以100%酒精保存",                        "transcriptome": False},
    {"code": "RT95E", "detail": "已取RNA；剩余标本梯度酒精固定，最终以95%酒精保存",          "transcriptome": True},
    {"code": "RD95E", "detail": "已取RNA；剩余标本直接以95%酒精固定并保存",                 "transcriptome": True},
    {"code": "RD75E", "detail": "已取RNA；剩余标本直接以75%酒精固定并保存",                 "transcriptome": True},
    {"code": "RT75E", "detail": "已取RNA；剩余标本梯度酒精固定，最终以75%酒精保存",          "transcriptome": True},
    {"code": "RD79",  "detail": "已取RNA；剩余标本直接以75%酒精固定，然后转95%酒精长期保存",  "transcriptome": True},
    {"code": "RT79",  "detail": "已取RNA；剩余标本梯度酒精固定至75%，然后转95%酒精长期保存",  "transcriptome": True},
    {"code": "RT100", "detail": "已取RNA；剩余标本梯度酒精固定，最终以100%酒精保存",         "transcriptome": True},
]


def builtin_storage_codes() -> set[str]:
    return {str(s["code"]) for s in BUILTIN_STORAGES}


def resolve_storage_detail(code: str, custom: list) -> str:
    """Return detail text for *code*, with project custom/override entries first."""
    normalized = str(code or "").strip().upper()
    if not normalized:
        return ""
    for entry in custom:
        if str(entry.get("code", "")).strip().upper() == normalized:
            return str(entry.get("detail") or "")
    for entry in BUILTIN_STORAGES:
        if entry["code"] == normalized:
            return entry["detail"]
    return ""


def resolve_storage_transcriptome(code: str, custom: list) -> bool:
    normalized = str(code or "").strip().upper()
    if normalized.startswith("R"):
        return True
    for entry in custom:
        if str(entry.get("code", "")).strip().upper() == normalized:
            return bool(entry.get("transcriptome"))
    for entry in BUILTIN_STORAGES:
        if entry["code"] == normalized:
            return bool(entry.get("transcriptome"))
    return False


def load_custom_storages(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return custom/overridden storage entries; always a list (never dict([]))."""
    row = db.execute(
        "SELECT value_json FROM project_settings WHERE setting_key=?",
        ("custom_storages",),
    ).fetchone()
    if row:
        try:
            data = json.loads(row[0])
            if isinstance(data, list):
                return list(data)
        except (ValueError, TypeError):
            pass
    return []


# ── CRUD ───────────────────────────────────────────────────────────────────────

def load_setting(db: sqlite3.Connection, key: str, default: dict) -> dict:
    """Return parsed JSON for *key*, or a copy of *default* if missing."""
    row = db.execute(
        "SELECT value_json FROM project_settings WHERE setting_key=?", (key,)
    ).fetchone()
    if row:
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            pass
    return dict(default)


def load_setting_if_present(db: sqlite3.Connection, key: str) -> Optional[dict]:
    """Return parsed JSON for *key*, or None when the row is absent/invalid."""
    try:
        # row = db.execute("SELECT value_json FROM project_settings WHERE setting_key=?", (key,)).fetchone()  # §7 旧: legacy db 无 project_settings 表 → OperationalError 崩整个 app
        row = db.execute(
            "SELECT value_json FROM project_settings WHERE setting_key=?", (key,)
        ).fetchone()
    except sqlite3.OperationalError:
        # legacy/早期 db(扫描识别的旧工作区)可能没有 project_settings 表 → 视作无此 setting, 不崩
        return None
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def merge_print_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return print settings with explicit project values applied over *base*."""
    result = _print_settings_copy(base)
    if isinstance(override, dict):
        _merge_all(result, override)
    return result


def _is_sqlite_lock_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return isinstance(exc, sqlite3.OperationalError) and (
        "database is locked" in msg
        or "database table is locked" in msg
        or "database is busy" in msg
    )


def _rollback_quietly(db: sqlite3.Connection) -> None:
    try:
        db.rollback()
    except Exception:
        pass


def save_setting(db: sqlite3.Connection, key: str, data: dict) -> None:
    """Upsert *data* for *key*."""
    payload = json.dumps(data, ensure_ascii=False)
    for delay in (*_SQLITE_LOCK_RETRY_DELAYS, None):
        try:
            db.execute(
                "INSERT OR REPLACE INTO project_settings(setting_key, value_json) VALUES (?,?)",
                (key, payload),
            )
            db.commit()
            return
        except sqlite3.OperationalError as exc:
            if delay is None or not _is_sqlite_lock_error(exc):
                raise
            _rollback_quietly(db)
            time.sleep(delay)


# ── Inheritance along the folder tree ──────────────────────────────────────────
# A project is a folder tree (see plan glittery-riding-oasis): a leaf workspace
# inherits 项目级 settings from its nearest ancestor folder that has its own
# _data/project.db. This kills the re-typing of 地区/样地/站位/人员 — set once at
# the survey root, every 断面 leaf inherits. Read-only: NEVER creates a db
# (walking up to a filesystem root must not litter project.db files everywhere).

def _merge_effective(base: dict, override: dict) -> None:
    """Deep-merge *override* into *base* in place.

    Nearest-wins semantics for inheritance: callers apply ancestors farthest →
    nearest, so a nearer setting overrides a farther one. Empty values
    (""/None/[]/{}) do NOT override an inherited non-empty value. Nested dicts
    (e.g. code_labels.stations / .species) accumulate keys rather than replace.
    """
    for k, v in override.items():
        if isinstance(v, dict):
            child = base.get(k)
            if not isinstance(child, dict):
                child = {}
                base[k] = child
            _merge_effective(child, v)
        elif v in ("", None, [], {}):
            continue
        else:
            base[k] = v


def _merge_all(base: dict, override: dict) -> None:
    """Deep-merge *override* into *base*, including empty values.

    Used for app-wide defaults where choosing an empty string is meaningful
    (for example "跟随标签打印页").
    """
    for k, v in override.items():
        if isinstance(v, dict):
            child = base.get(k)
            if not isinstance(child, dict):
                child = {}
                base[k] = child
            _merge_all(child, v)
        else:
            base[k] = v


def _print_settings_copy(default: Optional[dict] = None) -> dict[str, Any]:
    return json.loads(json.dumps(default or DEFAULT_PRINT_SETTINGS, ensure_ascii=False))


def load_global_print_defaults() -> dict[str, Any]:
    """Return app-wide workbench print defaults.

    These are used before project/folder overrides, so users can configure
    printers/templates once instead of repeating them in every project.
    """
    result = _print_settings_copy()
    raw = QSettings(_APP_SETTINGS_ORG, _APP_SETTINGS_APP).value(
        _GLOBAL_PRINT_DEFAULTS_KEY, ""
    )
    if raw:
        try:
            data = json.loads(str(raw))
            if isinstance(data, dict):
                _merge_all(result, data)
        except (ValueError, TypeError):
            pass
    return result


def save_global_print_defaults(data: dict[str, Any]) -> None:
    """Persist app-wide workbench print defaults."""
    result = _print_settings_copy()
    if isinstance(data, dict):
        _merge_all(result, data)
    qs = QSettings(_APP_SETTINGS_ORG, _APP_SETTINGS_APP)
    qs.setValue(_GLOBAL_PRINT_DEFAULTS_KEY, json.dumps(result, ensure_ascii=False))
    qs.sync()


def get_effective(
    project_dir: str,
    key: str,
    default: dict,
    *,
    root: Optional[str] = None,
) -> dict:
    """Return *key*'s effective value for *project_dir*, inheriting up the tree.

    Walks from *project_dir* upward through its parent folders (stopping at and
    including *root* if given). Each ancestor that already has an
    ``_data/project.db`` contributes its stored setting; nearer ancestors win
    (see :func:`_merge_effective`). Returns a deep copy of *default* if nothing
    is found. Never creates a database file — only existing dbs are read.
    """
    result = json.loads(json.dumps(default))  # independent deep copy

    leaf = Path(normalize_path(project_dir))
    chain = [leaf, *leaf.parents]
    if root:
        rp = Path(normalize_path(root))
        trimmed: list[Path] = []
        for d in chain:
            trimmed.append(d)
            if d == rp:
                break
        # Only honor the trim if root was actually an ancestor; otherwise fall
        # back to the full chain rather than silently reading unrelated trees.
        if trimmed and trimmed[-1] == rp:
            chain = trimmed

    for d in reversed(chain):  # farthest ancestor first → nearest overrides
        db_path = d / "_data" / "project.db"
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT value_json FROM project_settings WHERE setting_key=?",
                    (key,),
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        if not row:
            continue
        try:
            data = json.loads(row[0])
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            _merge_effective(result, data)

    return result


def effective_new_specimen_prefill(
    project_dir: str, *, root: Optional[str] = None
) -> dict:
    """Return the values to pre-fill into a brand-new specimen draft.

    Combines the inherited ``code_labels`` (地区/样地 defaults + 站位 dict) and
    ``personnel`` preset for *project_dir*, resolved up the folder tree. This is
    what wires the project-level defaults into the naming/metadata panels so the
    user never re-types 地区/样地/人员 per specimen (mirrors the web oracle's
    "新建标本自动预填" behaviour the Qt port had left unwired).

    Shape::

        {"province": str, "site": str, "stations": dict,
         "collector": str, "photographer": str, "identifier": str,
         "lon": str, "lat": str, "geo_area": str}

    经纬度/采集地理区是站位级数据，没有项目级"正确值"；这里返回的是
    **项目默认坐标**（capture_defaults），仅作新号兜底。选定具体站位后，
    采集记录会以更高优先级覆盖它（见 workbench._apply_collection_autofill）。
    """
    code_labels = get_effective(project_dir, "code_labels", DEFAULT_CODE_LABELS, root=root)
    personnel = get_effective(project_dir, "personnel", DEFAULT_PERSONNEL, root=root)
    capture = get_effective(project_dir, "capture_defaults", DEFAULT_CAPTURE_DEFAULTS, root=root)
    return {
        "province": code_labels.get("province", "") or "",
        "site": code_labels.get("site", "") or "",
        "stations": code_labels.get("stations", {}) or {},
        "collector": personnel.get("collector", "") or "",
        "photographer": personnel.get("photographer", "") or "",
        "identifier": personnel.get("identifier", "") or "",
        "lon": str(capture.get("lon", "") or ""),
        "lat": str(capture.get("lat", "") or ""),
        "geo_area": capture.get("geoArea", "") or "",
    }


def effective_print_settings(
    project_dir: str, *, root: Optional[str] = None
) -> dict[str, Any]:
    """Return inherited workbench quick-print settings for *project_dir*."""
    return get_effective(project_dir, "print_settings", load_global_print_defaults(), root=root)
