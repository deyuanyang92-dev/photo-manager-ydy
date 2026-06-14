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
import json
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
    unique_id,
)

# ── Built-in label templates ───────────────────────────────────────────────────
# These mirror the ``labelTemplates`` defined in app.js / print-plan.md.
# Keys match the JS template names used by the web oracle.

BUILTIN_TEMPLATES: dict[str, dict] = {
    "standard": {
        "name": "标准",
        "desc": "完整信息，适合50×30mm+",
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
        "desc": "仅编号+QR，适合25×10mm+",
        "minSize": {"w": 25, "h": 10},
        "lineHeight": 1.3,
        "rows": [
            {"fields": ["uniqueId"], "style": "bold", "size": 8},
        ],
        "qr": {"content": "uniqueId", "position": "bottom", "sizePct": 0.55, "ecc": "Q"},
    },
    "detailed": {
        "name": "详细",
        "desc": "含拉丁名/科/坐标，适合60×40mm+",
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
        "name": "RNAlater 组织管 30×15",
        "desc": "RNAlater 组织管：headerId + storage·日期段 + QR",
        "flavor": "tissue",
        "minSize": {"w": 25, "h": 10},
        "lineHeight": 1.2,
        "rows": [
            {"fields": ["headerId"], "style": "bold", "size": 7},
            {"fields": ["storage", "shortDate"], "size": 6, "sep": " "},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.50, "ecc": "Q"},
    },
    "tissueMini": {
        "name": "RNAlater 组织管 25×10",
        "desc": "0.5-1.5ml 极小管：仅 uniqueId + QR",
        "flavor": "tissue",
        "minSize": {"w": 20, "h": 8},
        "lineHeight": 1.1,
        "rows": [
            {"fields": ["uniqueId"], "style": "bold", "size": 5},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.60, "ecc": "Q"},
    },
    "tissueCustom": {
        "name": "转录组 · 自定义",
        "desc": "复用模板编辑器，仅用于转录组桶",
        "flavor": "tissue",
        "minSize": {"w": 25, "h": 10},
        "lineHeight": 1.2,
        "rows": [
            {"fields": ["headerId"], "style": "bold", "size": 7},
            {"fields": ["storage"], "size": 6},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.50, "ecc": "Q"},
    },
    # ── 管形标签 / 圆形盖子标签 (Task 4) ──────────────────────────────────────
    "cryo2mlSide": {
        "name": "2ml冻存管·侧面",
        "desc": "38×13mm，headerId+存储·日期+QR",
        "minSize": {"w": 30, "h": 10},
        "lineHeight": 1.1,
        "rows": [
            {"fields": [{"key": "headerId",   "style": "bold", "size": 6, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "shortDate",  "style": "",     "size": 5, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "storage",    "style": "",     "size": 5, "offsetX": 0, "offsetY": 0}]},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.55, "ecc": "M"},
    },
    "cryo2mlCap": {
        "name": "2ml冻存管·盖子",
        "desc": "13mm圆形，uniqueId+QR居下",
        "shape": "circle",
        "bgColor": "#ffffff",
        "minSize": {"w": 13, "h": 13},
        "lineHeight": 1.0,
        "rows": [
            {"fields": [{"key": "uniqueId", "style": "bold", "size": 4, "offsetX": 0, "offsetY": 0}],
             "align": "center"},
        ],
        "qr": {"content": "uniqueId", "position": "bottom", "sizePct": 0.60, "ecc": "M"},
    },
    "falcon5ml": {
        "name": "5ml Falcon管",
        "desc": "45×17mm，headerId+存储日期+物种",
        "minSize": {"w": 40, "h": 15},
        "lineHeight": 1.15,
        "rows": [
            {"fields": [{"key": "headerId",    "style": "bold", "size": 7, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "storage",     "style": "",     "size": 6, "offsetX": 0, "offsetY": 0},
                        {"key": "shortDate",   "style": "",     "size": 6, "offsetX": 0, "offsetY": 0}],
             "sep": " "},
            {"fields": [{"key": "speciesName", "style": "",     "size": 5, "offsetX": 0, "offsetY": 0}]},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.45, "ecc": "Q"},
    },
    "falcon15ml": {
        "name": "15ml Falcon管",
        "desc": "55×20mm，完整字段",
        "minSize": {"w": 50, "h": 18},
        "lineHeight": 1.2,
        "rows": [
            {"fields": [{"key": "headerId",       "style": "bold", "size": 8, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "storage",        "style": "",     "size": 7, "offsetX": 0, "offsetY": 0},
                        {"key": "shortDate",      "style": "",     "size": 7, "offsetX": 0, "offsetY": 0}],
             "sep": " | "},
            {"fields": [{"key": "speciesName",    "style": "",     "size": 6, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "collectorLabel", "style": "",     "size": 6, "offsetX": 0, "offsetY": 0}]},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.42, "ecc": "Q"},
    },
    "falcon50ml": {
        "name": "50ml Falcon管",
        "desc": "75×25mm，含拉丁名",
        "minSize": {"w": 60, "h": 20},
        "lineHeight": 1.25,
        "rows": [
            {"fields": [{"key": "headerId",       "style": "bold",   "size": 9, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "storage",        "style": "",       "size": 8, "offsetX": 0, "offsetY": 0},
                        {"key": "shortDate",      "style": "",       "size": 8, "offsetX": 0, "offsetY": 0}],
             "sep": " | "},
            {"fields": [{"key": "speciesName",    "style": "",       "size": 7, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "latin",          "style": "italic", "size": 6, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "collectorLabel", "style": "",       "size": 6, "offsetX": 0, "offsetY": 0}]},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.38, "ecc": "Q"},
    },
    "bottle500ml": {
        "name": "500ml标本瓶",
        "desc": "90×35mm，完整信息含坐标",
        "minSize": {"w": 80, "h": 30},
        "lineHeight": 1.3,
        "rows": [
            {"fields": [{"key": "headerId",       "style": "bold",   "size": 10, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "storage",        "style": "",       "size": 9,  "offsetX": 0, "offsetY": 0},
                        {"key": "shortDate",      "style": "",       "size": 9,  "offsetX": 0, "offsetY": 0}],
             "sep": " | "},
            {"fields": [{"key": "speciesName",    "style": "",       "size": 8,  "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "latin",          "style": "italic", "size": 7,  "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "family",         "style": "",       "size": 7,  "offsetX": 0, "offsetY": 0}],
             "prefix": "科: "},
            {"fields": [{"key": "region",         "style": "",       "size": 6,  "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "lon",            "style": "",       "size": 6,  "offsetX": 0, "offsetY": 0},
                        {"key": "lat",            "style": "",       "size": 6,  "offsetX": 0, "offsetY": 0}],
             "sep": ", "},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.32, "ecc": "Q"},
    },
    "qrFirst": {
        "name": "测序公司·QR优先",
        "desc": "40×25mm，QR居左，BGI/Illumina风格",
        "minSize": {"w": 35, "h": 20},
        "lineHeight": 1.2,
        "rows": [
            {"fields": [{"key": "uniqueId",    "style": "bold", "size": 7, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "speciesName", "style": "",     "size": 6, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "storage",     "style": "",     "size": 6, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "shortDate",   "style": "",     "size": 5, "offsetX": 0, "offsetY": 0}]},
        ],
        "qr": {"content": "uniqueId", "position": "left", "sizePct": 0.45, "ecc": "H"},
    },
    "museumDense": {
        "name": "博物馆标本签",
        "desc": "80×60mm，含拉丁/科/坐标/采集人",
        "minSize": {"w": 70, "h": 50},
        "lineHeight": 1.35,
        "rows": [
            {"fields": [{"key": "headerId",       "style": "bold",   "size": 11, "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "speciesName",    "style": "bold",   "size": 9,  "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "latin",          "style": "italic", "size": 8,  "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "family",         "style": "",       "size": 8,  "offsetX": 0, "offsetY": 0}],
             "prefix": "Family: "},
            {"fields": [{"key": "region",         "style": "",       "size": 7,  "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "storage",        "style": "",       "size": 7,  "offsetX": 0, "offsetY": 0},
                        {"key": "shortDate",      "style": "",       "size": 7,  "offsetX": 0, "offsetY": 0}],
             "sep": " | "},
            {"fields": [{"key": "collectorLabel", "style": "",       "size": 7,  "offsetX": 0, "offsetY": 0}]},
            {"fields": [{"key": "lon",            "style": "",       "size": 6,  "offsetX": 0, "offsetY": 0},
                        {"key": "lat",            "style": "",       "size": 6,  "offsetX": 0, "offsetY": 0}],
             "sep": ", "},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.30, "ecc": "Q"},
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
    # ── 管形/圆盖专用尺寸 (Task 4) ────────────────────────────────────────────
    "label_13x13":  {"name": "13×13mm(圆盖)",  "w": 13,  "h": 13},
    "label_38x13":  {"name": "38×13mm(冻存管)", "w": 38,  "h": 13},
    "label_45x17":  {"name": "45×17mm(5ml管)",  "w": 45,  "h": 17},
    "label_55x20":  {"name": "55×20mm(15ml管)", "w": 55,  "h": 20},
    "label_75x25":  {"name": "75×25mm(50ml管)", "w": 75,  "h": 25},
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
        fill_blank: bool = False,
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
        fill_blank      : when True AND no specimen was selected (empty
                          ``selected_indices``) AND the bucket is empty, emit a
                          single blank item so the user can print ``copies``
                          copies of the bare template (bound fields render
                          blank). Lets the print/preview/排版 pipeline run
                          without selecting a specimen — "编号" is supported,
                          not required. Does NOT fabricate when specimens were
                          selected but a bucket is legitimately empty (e.g.
                          tissue with no R-prefix).

        Returns
        -------
        dict: complete print-job (see label_core.create_print_job).
        """
        buckets = LabelService.bucket(specimens, selected_indices, edits)
        items = buckets["samples"] if bucket == "sample" else buckets["tissues"]
        # Standalone blank print: only the generic "sample" bucket — the tissue
        # bucket stays strictly R-prefix-derived (fabricating a blank RNAlater
        # label would be semantically wrong).
        if fill_blank and bucket == "sample" and not selected_indices and not items:
            items = [{"idx": -1, "data": {}}]  # blank label; copies multiplies it

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

    @staticmethod
    def quick_print_jobs_for_specimen(
        specimens: list[Union[Specimen, dict]],
        uid: str,
        *,
        copies: Optional[int] = None,
        paper_types: Optional[dict] = None,
    ) -> list[dict]:
        """Build ready-to-print jobs for ONE specimen using the user's persisted
        template / size / paper / copies — no label-studio wizard needed.

        Powers the workbench one-click direct print. Always yields the sample
        job; an R-prefix specimen additionally yields the RNAlater tissue job
        (so 一键 = 样品瓶 + 组织管). Returns ``[]`` when *uid* matches no specimen.

        ``copies`` / ``paper_types`` override the persisted values when given
        (e.g. for testing); otherwise read from QSettings.
        """
        js = [_specimen_to_js_dict(sp) for sp in specimens]
        idx = next((i for i, d in enumerate(js) if unique_id(d) == uid), None)
        if idx is None:
            return []
        n_copies = persisted_copies() if copies is None else max(1, int(copies))
        jobs: list[dict] = []
        for bucket in ("sample", "tissue"):
            lib = LabelTemplateLibrary(bucket)
            tmpl = resolve_template(lib)
            dims = resolve_dims(lib, lib.selected_custom_dims())
            ptype = (
                (paper_types or {}).get(bucket)
                if paper_types is not None
                else persisted_paper_type(bucket)
            ) or "label"
            paper = PAPER_SIZES.get(ptype) if ptype in ("a4", "a5") else None
            job = LabelService.build_print_job(
                specimens, tmpl, bucket,
                selected_indices=[idx], dims=dims, copies=n_copies,
                paper_type=ptype, paper=paper,
            )
            if job.get("items"):
                jobs.append(job)
        return jobs


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
# Persisted custom W×H (mm) for the "custom" size key — lets a dimension edited
# inside the free-form designer survive across sessions ("存为新自定尺寸").
_CUSTOM_DIMS_QSETTINGS_KEY = {
    "sample": "labelSampleCustomDims",
    "tissue": "labelTissueCustomDims",
}
# Paper-type (per bucket) + copies (shared). Session UI persists here so the
# workbench one-click direct print reuses the user's last studio choice.
LABEL_PAPER_QSETTINGS_KEY = {
    "sample": "labelSamplePaperType",
    "tissue": "labelTissuePaperType",
}
LABEL_COPIES_QSETTINGS_KEY = "labelCopies"
_VALID_PAPER_TYPES = ("label", "a4", "a5")
_MIGRATION_QSETTINGS_KEY = {
    "sample": "labelSampleTemplateLibraryMigrated",
    "tissue": "labelTissueTemplateLibraryMigrated",
}
_LEGACY_CUSTOM_QSETTINGS_KEY = {
    "sample": "labelCustomTemplate",
    "tissue": "labelTissueCustomTemplate",
}
_BACKUP_QSETTINGS_KEY = {
    "sample": "labelCustomTemplateBackup",
    "tissue": "labelTissueCustomTemplateBackup",
}
_MAX_BACKUPS = 20


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

    MAX_BACKUP_SLOTS = 20

    def __init__(self, bucket: str) -> None:
        if bucket not in ("sample", "tissue"):
            raise ValueError(f"bucket must be 'sample' or 'tissue', got {bucket!r}")
        self._bucket = bucket
        self._qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
        self._migrate_legacy()

    @property
    def bucket(self) -> str:
        """Which bucket this library serves ("sample" | "tissue")."""
        return self._bucket

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
        If rec has an 'id' that already exists, it is updated (and the old
        version is snapshotted to the per-template rolling backup first).
        Returns the normalized record.
        """
        normalized = self._normalize_record(rec)
        lib = self._read_raw()
        idx = next(
            (i for i, x in enumerate(lib["templates"]) if x.get("id") == normalized["id"]),
            -1,
        )
        if idx >= 0:
            self.backup_template(normalized["id"], "overwrite")
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
        self.backup_template(template_id, "delete")
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

    def selected_custom_dims(self) -> dict:
        """Return the persisted custom label dimensions ``{"w","h"}`` (mm).

        Used by the "custom" size key; defaults to the bucket's standard label.
        """
        import json as _json
        default = {"w": 30.0, "h": 15.0} if self._bucket == "tissue" else {"w": 50.0, "h": 30.0}
        raw = self._qs.value(_CUSTOM_DIMS_QSETTINGS_KEY[self._bucket], None)
        if raw:
            try:
                val = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(val, dict) and "w" in val and "h" in val:
                    return {"w": float(val["w"]), "h": float(val["h"])}
            except Exception:
                pass
        return default

    def set_custom_dims(self, w: float, h: float) -> None:
        """Persist custom label dimensions (mm) for the "custom" size key."""
        import json as _json
        self._qs.setValue(_CUSTOM_DIMS_QSETTINGS_KEY[self._bucket],
                          _json.dumps({"w": float(w), "h": float(h)}))

    # ── Template backup (mirror JS backupLabelCustomTemplate / latestLabelCustomBackup
    #    / restoreLatestLabelCustomBackup) ─────────────────────────────────────

    def _read_backups(self) -> list:
        import json as _json
        qkey = _BACKUP_QSETTINGS_KEY[self._bucket]
        raw = self._qs.value(qkey, None)
        if raw:
            try:
                val = _json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(val, list):
                    return val
            except Exception:
                pass
        return []

    def _write_backups(self, backups: list) -> None:
        import json as _json
        qkey = _BACKUP_QSETTINGS_KEY[self._bucket]
        self._qs.setValue(qkey, _json.dumps(backups))

    # ── Per-template rolling backup ───────────────────────────────────────────

    def _backup_key(self, template_id: str) -> str:
        return f"label_backup/{template_id}/slots"

    def backup_template(self, template_id: str, reason: str = "") -> bool:
        """Save current template record to rolling backup (max 20 slots).

        Returns True if a snapshot was stored, False when template not found.
        """
        import json as _json, time as _time
        current = self.get(template_id)
        if not current:
            return False
        raw = self._qs.value(self._backup_key(template_id), "[]")
        try:
            slots = _json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            slots = []
        slots.append({"ts": int(_time.time()), "reason": reason, "data": current})
        slots = slots[-self.MAX_BACKUP_SLOTS:]
        self._qs.setValue(self._backup_key(template_id), _json.dumps(slots))
        return True

    def restore_backup(self, template_id: str, slot_index: int = -1) -> bool:
        """Restore template from per-template backup slot (default: latest).

        Returns True if restored, False when no backup exists.
        """
        import json as _json
        raw = self._qs.value(self._backup_key(template_id), "[]")
        try:
            slots = _json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            return False
        if not slots:
            return False
        entry = slots[slot_index]
        self.upsert(entry["data"])
        return True

    def backup_library(self, reason: str = "修改前备份") -> bool:
        """Snapshot current library into a rolling 20-slot backup.

        Mirror: ``backupLabelCustomTemplate(bucket, reason)`` in app.js.

        Returns True if a snapshot was stored (False when library is empty
        or snapshot is identical to the most-recent one).
        """
        import json as _json
        lib = self._read_raw()
        if not lib.get("templates"):
            return False
        raw = _json.dumps(lib)
        backups = self._read_backups()
        if backups and backups[0].get("raw") == raw:
            return True  # no change since last backup
        backups.insert(0, {"at": _now_iso(), "reason": reason, "raw": raw})
        backups = backups[:_MAX_BACKUPS]
        self._write_backups(backups)
        return True

    def latest_backup(self, template_id: Optional[str] = None) -> Optional[dict]:
        """Return the most-recent backup snapshot dict, or None.

        Without arguments: returns library-level snapshot
        ``{"at": iso, "reason": str, "raw": json_str}`` or None.
        Mirror: ``latestLabelCustomBackup(bucket)`` in app.js.

        With template_id: returns per-template snapshot
        ``{"ts": int, "reason": str, "data": dict}`` or None.
        """
        if template_id is not None:
            import json as _json
            raw = self._qs.value(self._backup_key(template_id), "[]")
            try:
                slots = _json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                return None
            return slots[-1] if slots else None
        backups = self._read_backups()
        return backups[0] if backups else None

    def restore_latest_backup(self) -> bool:
        """Restore the most-recent backup into the active library.

        Mirror: ``restoreLatestLabelCustomBackup(bucket)`` in app.js.

        Steps:
          1. Snapshot current state before restoring.
          2. Overwrite library with backup content.
        Returns True on success, False when no backup exists.
        """
        import json as _json
        backup = self.latest_backup()
        if not backup or not backup.get("raw"):
            return False
        # Snapshot current state so the restore is itself undoable
        self.backup_library("恢复备份前")
        try:
            lib = _json.loads(backup["raw"])
            self._write_raw(lib)
            return True
        except Exception:
            return False


# ── Default template / size per bucket (mirror web defaults) ──────────────────

DEFAULT_TEMPLATE_KEY = {"sample": "standard", "tissue": "tissueCompact"}
DEFAULT_SIZE_KEY = {"sample": "label_50x30", "tissue": "label_30x15"}

# Selectable label sizes (excludes a4/a5 page papers), mirror web labelSizeKeys.
LABEL_SIZE_KEYS = [
    "label_25x10", "label_30x15", "label_40x20", "label_50x30",
    "label_60x40", "label_70x50", "label_80x60", "label_100x70",
    "label_13x13", "label_38x13", "label_45x17", "label_55x20", "label_75x25",
]


def resolve_template(lib: "LabelTemplateLibrary") -> dict:
    """Resolve the active normalized template for a library's bucket.

    Honors the persisted selected key: a ``custom:<id>`` key resolves from the
    library, otherwise from ``BUILTIN_TEMPLATES``; falls back to the bucket
    default.  Shared by Step2 (card grid), Step3 (size preview) and Step4 (job).
    """
    bucket = lib.bucket
    default_key = DEFAULT_TEMPLATE_KEY[bucket]
    key = lib.selected_key() or default_key
    if _is_library_key(key):
        rec = lib.get(_id_from_key(key))
        if rec and rec.get("template"):
            return normalize_template(rec["template"])
        key = default_key
    return normalize_template(BUILTIN_TEMPLATES.get(key) or BUILTIN_TEMPLATES[default_key])


def resolve_dims(lib: "LabelTemplateLibrary", custom_dims: Optional[dict] = None) -> dict:
    """Resolve the active label dimensions ``{"w": mm, "h": mm}`` for a bucket.

    ``custom_dims`` supplies width/height when the persisted size key is
    "custom" (held in-memory by the view, not persisted per the web oracle).
    """
    bucket = lib.bucket
    size_key = lib.selected_size_key() or DEFAULT_SIZE_KEY[bucket]
    if size_key == "custom":
        d = custom_dims if custom_dims is not None else lib.selected_custom_dims()
        return {"w": float(d.get("w", 50)), "h": float(d.get("h", 30))}
    ps = PAPER_SIZES.get(size_key)
    if ps:
        return {"w": float(ps["w"]), "h": float(ps["h"])}
    return {"w": 50.0, "h": 30.0}


def load_specimen_dicts(db) -> list[dict]:
    """Read all specimens from a project DB as label-ready camelCase dicts.

    Single source for the specimens → label-data column mapping (region maps to
    ``geo_area``, species to ``scientific_name_cn``/``scientific_name`` etc.).
    Shared by the label studio and the workbench one-click print so the two can
    never drift. Returns ``[]`` on no DB or any read error.
    """
    if db is None:
        return []
    out: list[dict] = []
    try:
        rows = db.execute("SELECT * FROM specimens ORDER BY id").fetchall()
    except Exception:
        return []
    for row in rows:
        d = dict(row)
        out.append({
            "province":       d.get("province"),
            "site":           d.get("site"),
            "station":        d.get("station"),
            "id":             d.get("id"),
            "storage":        d.get("storage"),
            "collectionDate": d.get("collection_date"),
            "photoDate":      d.get("photo_date"),
            "species":        d.get("scientific_name_cn") or d.get("scientific_name"),
            "latin":          d.get("scientific_name") or "",
            "collector":      d.get("collector"),
            "photographer":   d.get("photographer"),
            "family":         d.get("family"),
            "region":         d.get("geo_area") or "",
            "lon":            str(d.get("lon") or ""),
            "lat":            str(d.get("lat") or ""),
            "geoArea":        d.get("geo_area") or "",
            "photoNotes":     d.get("photo_notes") or "",
        })
    return out


def persisted_paper_type(bucket: str) -> str:
    """Read the persisted paper type for *bucket* ("label" default)."""
    qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
    val = str(qs.value(LABEL_PAPER_QSETTINGS_KEY[bucket], "label") or "label")
    return val if val in _VALID_PAPER_TYPES else "label"


def persisted_copies() -> int:
    """Read the persisted shared copies count (clamped to 1..10)."""
    qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
    try:
        return max(1, min(10, int(qs.value(LABEL_COPIES_QSETTINGS_KEY, 1) or 1)))
    except (TypeError, ValueError):
        return 1


# ── 排版设计 (imposition) persistence ─────────────────────────────────────────
# Per-bucket JSON blob; whitelist-validated through sanitize_imposition so a
# corrupt/hand-edited value can never feed garbage into the print geometry.

LABEL_IMPOSITION_QSETTINGS_KEY = {
    "sample": "labelSampleImposition",
    "tissue": "labelTissueImposition",
}

# float keys → max mm (min is always 0)
_IMPOSITION_FLOAT_KEYS = {
    "marginMm": 50.0,
    "marginTopMm": 50.0,
    "marginBottomMm": 50.0,
    "marginLeftMm": 50.0,
    "marginRightMm": 50.0,
    "gapMm": 30.0,
    "gapXMm": 30.0,
    "gapYMm": 30.0,
}


def sanitize_imposition(value) -> dict:
    """Whitelist + clamp an imposition dict; anything invalid is dropped.

    Default-equivalent values (force 0 = auto, startSlot 0, False bools,
    portrait) are dropped so the stored dict stays minimal and "no keys"
    keeps meaning "legacy geometry".
    """
    if not isinstance(value, dict):
        return {}
    out: dict = {}
    for key, hi in _IMPOSITION_FLOAT_KEYS.items():
        if key in value:
            try:
                v = float(value[key])
            except (TypeError, ValueError):
                continue
            out[key] = max(0.0, min(hi, v))
    for key in ("forceCols", "forceRows"):
        if key in value:
            try:
                v = int(value[key])
            except (TypeError, ValueError):
                continue
            if v > 0:
                out[key] = min(50, v)
    if "startSlot" in value:
        try:
            v = int(value["startSlot"])
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            out["startSlot"] = v
    for key in ("cutMarks", "shrinkToFit"):
        if value.get(key):
            out[key] = True
    if value.get("orientation") == "landscape":
        out["orientation"] = "landscape"
    return out


def persisted_imposition(bucket: str) -> dict:
    """Read the persisted imposition dict for *bucket* ({} on any problem)."""
    qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
    raw = qs.value(LABEL_IMPOSITION_QSETTINGS_KEY[bucket], "")
    if not raw:
        return {}
    try:
        return sanitize_imposition(json.loads(str(raw)))
    except (ValueError, TypeError):
        return {}


def persist_imposition(bucket: str, imposition: Optional[dict]) -> None:
    """Persist *imposition* for *bucket*; an empty dict removes the key."""
    qs = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP)
    clean = sanitize_imposition(imposition or {})
    key = LABEL_IMPOSITION_QSETTINGS_KEY[bucket]
    if clean:
        qs.setValue(key, json.dumps(clean, ensure_ascii=False))
    else:
        qs.remove(key)


# ── Module-level helpers (used by labels_view) ────────────────────────────────

def is_library_key(key: str) -> bool:
    """True when key is a custom:<id> library key."""
    return _is_library_key(key)


def key_from_id(template_id: str) -> str:
    return _key_from_id(template_id)


def id_from_key(key: str) -> str:
    return _id_from_key(key)
