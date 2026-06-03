"""label_service.py — Build print-jobs from Specimen records + template library CRUD.

Consumes ``app.utils.label_core`` pure functions.  The only business logic
added here on top of the core is:

  1. Converting PyQt-side ``Specimen`` dataclass instances (or plain dicts)
     to the camelCase dicts that label_core expects.
  2. Routing specimens into the correct bucket via ``bucket_specimens``.
  3. ``LabelTemplateLibrary`` — QSettings-backed named multi-template store,
     mirrors JS readLabelTemplateLibrary / writeLabelTemplateLibrary / etc.

Usage
-----
    from app.services.label_service import LabelService, BUILTIN_TEMPLATES
    from app.services.label_service import LabelTemplateLibrary

    lib = LabelTemplateLibrary("sample")
    rec = lib.upsert({"name": "My Label", "template": {...}})
    lib.select(rec["id"])          # persists choice to QSettings
"""

from __future__ import annotations

import copy
import time
import random
import string
from datetime import datetime, timezone
from typing import Optional, Union

from PyQt6.QtCore import QSettings

from app.models.specimen import Specimen
from app.utils.label_core import (
    bucket_specimens,
    create_print_job,
    normalize_template,
)

# ── Built-in label templates ───────────────────────────────────────────────────
# These mirror the ``labelTemplates`` defined in app.js / print-plan.md.
# Keys match the JS template names used by the web oracle.

BUILTIN_TEMPLATES: dict[str, dict] = {
    "standard": {
        "name": "标准",
        "description": "完整信息标签，适合60×40mm及以上",
        "minSize": {"w": 50, "h": 30},
        "lineHeight": 1.3,
        "rows": [
            {"fields": ["headerId"], "style": "bold", "size": 10},
            {"fields": ["storage"], "size": 9},
            {"fields": ["shortDate"], "size": 9},
            {"fields": ["speciesName"], "size": 8},
            {"fields": ["collectorLabel"], "size": 8},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.40, "ecc": "Q"},
    },
    "compact": {
        "name": "紧凑",
        "description": "仅编号+QR码，适合40×20mm小标签",
        "minSize": {"w": 25, "h": 10},
        "lineHeight": 1.3,
        "rows": [
            {"fields": ["uniqueId"], "style": "bold", "size": 8},
        ],
        "qr": {"content": "uniqueId", "position": "bottom", "sizePct": 0.55, "ecc": "Q"},
    },
    "detailed": {
        "name": "详细",
        "description": "含拉丁名、科、坐标，适合70×50mm及以上",
        "minSize": {"w": 60, "h": 40},
        "lineHeight": 1.3,
        "rows": [
            {"fields": ["headerId"], "style": "bold", "size": 10},
            {"fields": ["storage", "shortDate"], "size": 9, "sep": " | "},
            {"fields": ["speciesName"], "size": 9},
            {"fields": ["latin"], "style": "italic", "size": 8},
            {"fields": ["family"], "size": 8, "prefix": "科: "},
            {"fields": ["region"], "size": 7},
            {"fields": ["collectorLabel"], "size": 7},
            {"fields": ["lon", "lat"], "size": 6, "sep": ", "},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.35, "ecc": "Q"},
    },
    # Tissue (RNAlater tube) templates — flavor: "tissue"
    "tissueCompact": {
        "name": "组织管-标准",
        "description": "30×15mm RNAlater 管，headerId + QR",
        "flavor": "tissue",
        "minSize": {"w": 25, "h": 10},
        "lineHeight": 1.2,
        "rows": [
            {"fields": ["headerId"], "style": "bold", "size": 8},
            {"fields": ["storage", "shortDate"], "size": 7, "sep": "·"},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.50, "ecc": "Q"},
    },
    "tissueMini": {
        "name": "组织管-迷你",
        "description": "25×10mm 缠绕标签，uniqueId + QR",
        "flavor": "tissue",
        "minSize": {"w": 20, "h": 8},
        "lineHeight": 1.1,
        "rows": [
            {"fields": ["uniqueId"], "style": "bold", "size": 6},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.60, "ecc": "Q"},
    },
}

# ── Paper size catalogue (mm) ──────────────────────────────────────────────────

PAPER_SIZES: dict[str, dict] = {
    "a4":           {"name": "A4纸",     "w": 210, "h": 297},
    "a5":           {"name": "A5纸",     "w": 148, "h": 210},
    "label_25x10":  {"name": "25×10mm",  "w": 25,  "h": 10},
    "label_30x15":  {"name": "30×15mm",  "w": 30,  "h": 15},
    "label_40x20":  {"name": "40×20mm",  "w": 40,  "h": 20},
    "label_50x30":  {"name": "50×30mm",  "w": 50,  "h": 30},
    "label_60x40":  {"name": "60×40mm",  "w": 60,  "h": 40},
    "label_70x50":  {"name": "70×50mm",  "w": 70,  "h": 50},
    "label_80x60":  {"name": "80×60mm",  "w": 80,  "h": 60},
    "label_100x70": {"name": "100×70mm", "w": 100, "h": 70},
}


def _specimen_to_js_dict(sp: Union[Specimen, dict]) -> dict:
    """Convert a Specimen dataclass (or plain dict) to the camelCase dict
    that label_core functions consume.

    Field mapping (snake_case → camelCase / JS key names):
      collection_date   → collectionDate
      photo_date        → photoDate
      scientific_name   → species       (label_core reads "species")
      scientific_name_cn intentionally omitted from label data
    """
    if isinstance(sp, dict):
        # Already in the expected format (web-side or test data)
        return sp

    # Pull out of raw_json for any fields not in the Specimen dataclass
    raw = sp.raw or {}

    return {
        "province":      sp.province,
        "site":          sp.site,
        "station":       sp.station,
        "id":            sp.id,
        "storage":       sp.storage,
        "collectionDate": sp.collection_date,
        "photoDate":     sp.photo_date,
        "species":       sp.scientific_name_cn or sp.scientific_name or raw.get("species"),
        "latin":         sp.scientific_name or raw.get("latin") or "",
        "collector":     sp.collector,
        "photographer":  sp.photographer,
        "family":        sp.family,
        "region":        sp.geo_area or raw.get("region") or "",
        "lon":           str(sp.lon) if sp.lon is not None else "",
        "lat":           str(sp.lat) if sp.lat is not None else "",
        "geoArea":       sp.geo_area or "",
        "photoNotes":    sp.photo_notes or "",
    }


class LabelService:
    """Service layer: converts specimens into bucket_specimens items and print-jobs.

    All methods are class-level (no instance state needed).
    """

    @staticmethod
    def bucket(
        specimens: list[Union[Specimen, dict]],
        selected_indices: Optional[list[int]] = None,
        edits: Optional[dict] = None,
    ) -> dict:
        """Build sample and tissue buckets from a list of specimens.

        Parameters
        ----------
        specimens:
            Full specimen list (Specimen objects or camelCase dicts).
        selected_indices:
            Subset indices to include. None → all specimens.
        edits:
            Per-index field overrides ``{idx: {field: value}}``.

        Returns
        -------
        dict: ``{"samples": [...], "tissues": [...]}`` from
        ``label_core.bucket_specimens``.
        """
        js_specimens = [_specimen_to_js_dict(sp) for sp in specimens]
        indices = (
            selected_indices
            if selected_indices is not None
            else list(range(len(js_specimens)))
        )
        return bucket_specimens(indices, js_specimens, edits)

    @staticmethod
    def build_print_job(
        specimens: list[Union[Specimen, dict]],
        template: Optional[dict],
        bucket: str = "sample",
        *,
        selected_indices: Optional[list[int]] = None,
        dims: Optional[dict] = None,
        paper_type: str = "label",
        paper: Optional[dict] = None,
        copies: int = 1,
        printer_margin: int = 2,
        edits: Optional[dict] = None,
    ) -> dict:
        """Build a complete LabelPrintJob dict ready for the Qt print adapter.

        Double-bucket rule (hard rule — R-prefix enters both buckets):
          bucket="sample"  → uses ``buckets["samples"]`` items (ALL specimens)
          bucket="tissue"  → uses ``buckets["tissues"]`` items (R-prefix only)

        Parameters
        ----------
        specimens       : list of Specimen / dict to print.
        template        : label template dict (use BUILTIN_TEMPLATES or custom).
                          None → default normalised template.
        bucket          : "sample" | "tissue"
        selected_indices: subset, None = all.
        dims            : label size dict ``{"w": mm, "h": mm}``.
        paper_type      : "label" | "a4" | "a5"
        paper           : page dimensions for grid layout (a4/a5 only).
        copies          : copies per specimen.
        printer_margin  : safe-margin in mm (default 2).
        edits           : per-index field overrides.

        Returns
        -------
        dict: complete print-job (see label_core.create_print_job).
        """
        buckets = LabelService.bucket(specimens, selected_indices, edits)
        items = buckets["samples"] if bucket == "sample" else buckets["tissues"]

        return create_print_job({
            "template": normalize_template(template),
            "copies": copies,
            "items": items,
            "paperType": paper_type,
            "paper": paper,
            "dims": dims or {},
            "bucket": bucket,
            "printerMargin": printer_margin,
        })


# ── Template library (QSettings-backed) ───────────────────────────────────────
# Mirrors web JS: readLabelTemplateLibrary / writeLabelTemplateLibrary /
# normalizeLibraryRecord / upsertLabelTemplateRecord / getLabelTemplateRecord /
# labelTemplateRecords / createLabelTemplateId / migrateLegacyLabelTemplate

# QSettings keys mirror web localStorage keys exactly.
_QSETTINGS_ORG = "PhotoPlatform"
_QSETTINGS_APP = "LabelTemplates"

_LIBRARY_QSETTINGS_KEY = {
    "sample": "labelSampleTemplateLibrary",
    "tissue": "labelTissueTemplateLibrary",
}
_SELECTED_QSETTINGS_KEY = {
    "sample": "labelSampleTemplateKey",
    "tissue": "labelTissueTemplateKey",
}
_SIZE_QSETTINGS_KEY = {
    "sample": "labelSampleSizeKey",
    "tissue": "labelTissueSizeKey",
}
_MIGRATION_QSETTINGS_KEY = {
    "sample": "labelSampleTemplateLibraryMigrated",
    "tissue": "labelTissueTemplateLibraryMigrated",
}
_LEGACY_CUSTOM_QSETTINGS_KEY = {
    "sample": "labelCustomTemplate",
    "tissue": "labelTissueCustomTemplate",
}


def _create_template_id() -> str:
    """Mirror JS createLabelTemplateId()."""
    ts = format(int(time.time() * 1000), "x")  # base-36-ish via hex approximation
    rnd = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"custom-{ts}-{rnd}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_library_key(key: str) -> bool:
    """Mirror JS isLabelLibraryKey(key) — True when key starts with 'custom:'."""
    return str(key or "").startswith("custom:")


def _id_from_key(key: str) -> str:
    return str(key)[7:] if _is_library_key(key) else ""


def _key_from_id(template_id: str) -> str:
    return f"custom:{template_id}"


class LabelTemplateLibrary:
    """QSettings-backed named multi-template store for one bucket.

    Mirrors JS functions: readLabelTemplateLibrary / writeLabelTemplateLibrary /
    normalizeLibraryRecord / upsertLabelTemplateRecord / getLabelTemplateRecord /
    labelTemplateRecords / migrateLegacyLabelTemplate.

    Parameters
    ----------
    bucket : "sample" | "tissue"
    """

    def __init__(self, bucket: str) -> None:
        if bucket not in ("sample", "tissue"):
            raise ValueError(f"bucket must be 'sample' or 'tissue', got {bucket!r}")
        self._bucket = bucket
        self._qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        self._migrate_legacy()

    # ── Persistence helpers ───────────────────────────────────────────────────

    def _read_raw(self) -> dict:
        """Load library dict from QSettings.  Always returns {version:1, templates:[...]}."""
        key = _LIBRARY_QSETTINGS_KEY[self._bucket]
        import json as _json
        raw = self._qs.value(key, None)
        if raw:
            try:
                lib = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(lib, dict) and isinstance(lib.get("templates"), list):
                    return lib
            except Exception:
                pass
        return {"version": 1, "templates": []}

    def _write_raw(self, lib: dict) -> None:
        import json as _json
        lib.setdefault("version", 1)
        if not isinstance(lib.get("templates"), list):
            lib["templates"] = []
        key = _LIBRARY_QSETTINGS_KEY[self._bucket]
        self._qs.setValue(key, _json.dumps(lib))

    # ── Migration (JS migrateLegacyLabelTemplate) ─────────────────────────────

    def _migrate_legacy(self) -> None:
        """One-time migration of old single-custom-template slot into the library."""
        mkey = _MIGRATION_QSETTINGS_KEY[self._bucket]
        if self._qs.value(mkey) == "1":
            return
        import json as _json
        lkey = _LEGACY_CUSTOM_QSETTINGS_KEY[self._bucket]
        raw = self._qs.value(lkey, None)
        if raw:
            try:
                tmpl = _json.loads(raw) if isinstance(raw, str) else raw
                lib = self._read_raw()
                if not lib["templates"]:
                    default_name = (
                        "我的 RNAlater 模板" if self._bucket == "tissue"
                        else "我的样品瓶模板"
                    )
                    rec = self._normalize_record({
                        "name": (tmpl.get("name") if isinstance(tmpl, dict) else None)
                               or default_name,
                        "source": "legacy",
                        "template": tmpl,
                    })
                    lib["templates"].append(rec)
                    self._write_raw(lib)
            except Exception:
                pass
        self._qs.setValue(mkey, "1")

    # ── normalizeLibraryRecord ────────────────────────────────────────────────

    def _normalize_record(self, rec: dict) -> dict:
        """Mirror JS normalizeLibraryRecord(bucket, rec)."""
        if not rec or not rec.get("template"):
            raise ValueError("record must have a 'template' key")
        now = _now_iso()
        template_id = rec.get("id") or _create_template_id()
        tmpl = copy.deepcopy(rec["template"])
        if self._bucket == "tissue":
            tmpl["flavor"] = "tissue"
        if not tmpl.get("name"):
            tmpl["name"] = rec.get("name") or (
                "我的 RNAlater 模板" if self._bucket == "tissue"
                else "我的样品瓶模板"
            )
        return {
            "id": template_id,
            "name": rec.get("name") or tmpl["name"],
            "bucket": self._bucket,
            "source": rec.get("source") or "",
            "createdAt": rec.get("createdAt") or now,
            "updatedAt": rec.get("updatedAt") or now,
            "template": tmpl,
        }

    # ── Public CRUD API ───────────────────────────────────────────────────────

    def records(self) -> list[dict]:
        """Return all normalized records.  Mirror JS labelTemplateRecords(bucket)."""
        lib = self._read_raw()
        result = []
        for r in lib["templates"]:
            try:
                result.append(self._normalize_record(r))
            except Exception:
                pass
        return result

    def get(self, template_id: str) -> Optional[dict]:
        """Return a single record by id, or None.  Mirror JS getLabelTemplateRecord."""
        lib = self._read_raw()
        for r in lib["templates"]:
            if r.get("id") == template_id:
                try:
                    return self._normalize_record(r)
                except Exception:
                    return None
        return None

    def upsert(self, rec: dict) -> dict:
        """Insert or update a record.  Mirror JS upsertLabelTemplateRecord.

        If rec has no 'id', a new id is generated (insert).
        If rec has an 'id' that already exists, it is updated.
        Returns the normalized record.
        """
        normalized = self._normalize_record(rec)
        lib = self._read_raw()
        idx = next(
            (i for i, x in enumerate(lib["templates"]) if x.get("id") == normalized["id"]),
            -1,
        )
        if idx >= 0:
            normalized["updatedAt"] = _now_iso()
            lib["templates"][idx] = normalized
        else:
            lib["templates"].append(normalized)
        self._write_raw(lib)
        return normalized

    def clone_from_builtin(self, src_tmpl: dict, src_name: str) -> dict:
        """Clone a built-in template into the library as a new custom record.

        Mirror JS cloneLabelTemplateToCustom(bucket, srcTmpl, srcName).
        """
        clone = copy.deepcopy(src_tmpl)
        clone["name"] = f"自定义（基于 {src_name}）"
        if self._bucket == "tissue":
            clone["flavor"] = "tissue"
        return self.upsert({
            "name": clone["name"],
            "source": src_name or "",
            "template": clone,
        })

    def rename(self, template_id: str, new_name: str) -> Optional[dict]:
        """Rename a template record."""
        rec = self.get(template_id)
        if not rec:
            return None
        rec["name"] = new_name
        rec["template"]["name"] = new_name
        return self.upsert(rec)

    def delete(self, template_id: str) -> bool:
        """Delete a template record by id.  Returns True if deleted."""
        lib = self._read_raw()
        before = len(lib["templates"])
        lib["templates"] = [r for r in lib["templates"] if r.get("id") != template_id]
        if len(lib["templates"]) < before:
            self._write_raw(lib)
            return True
        return False

    def duplicate(self, template_id: str) -> Optional[dict]:
        """Duplicate an existing record with a new id."""
        rec = self.get(template_id)
        if not rec:
            return None
        new_rec = copy.deepcopy(rec)
        new_rec.pop("id", None)          # force new id
        new_rec["name"] = rec["name"] + " (副本)"
        new_rec["template"]["name"] = new_rec["name"]
        new_rec["source"] = f"copy of {template_id}"
        new_rec.pop("createdAt", None)
        new_rec.pop("updatedAt", None)
        return self.upsert(new_rec)

    # ── Per-bucket selected template key ──────────────────────────────────────

    def selected_key(self) -> str:
        """Return the persisted selected template key (builtin name or 'custom:<id>')."""
        qkey = _SELECTED_QSETTINGS_KEY[self._bucket]
        default = "tissueCompact" if self._bucket == "tissue" else "standard"
        return str(self._qs.value(qkey, default) or default)

    def set_selected_key(self, key: str) -> None:
        """Persist the selected template key."""
        qkey = _SELECTED_QSETTINGS_KEY[self._bucket]
        self._qs.setValue(qkey, key)

    def select_record(self, template_id: str) -> None:
        """Set active template to a library record.  Mirrors JS chooseCustomLabelTemplate."""
        self.set_selected_key(_key_from_id(template_id))

    # ── Per-bucket size key ───────────────────────────────────────────────────

    def selected_size_key(self) -> str:
        """Return the persisted selected paper size key."""
        qkey = _SIZE_QSETTINGS_KEY[self._bucket]
        default = "label_30x15" if self._bucket == "tissue" else "label_50x30"
        return str(self._qs.value(qkey, default) or default)

    def set_selected_size_key(self, size_key: str) -> None:
        """Persist the selected paper size key."""
        qkey = _SIZE_QSETTINGS_KEY[self._bucket]
        self._qs.setValue(qkey, size_key)


# ── Module-level helpers (used by labels_view) ────────────────────────────────

def is_library_key(key: str) -> bool:
    """True when key is a custom:<id> library key."""
    return _is_library_key(key)


def key_from_id(template_id: str) -> str:
    return _key_from_id(template_id)


def id_from_key(key: str) -> str:
    return _id_from_key(key)
