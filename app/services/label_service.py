"""label_service.py — Build print-jobs from Specimen records.

Consumes ``app.utils.label_core`` pure functions.  The only business logic
added here on top of the core is:

  1. Converting PyQt-side ``Specimen`` dataclass instances (or plain dicts)
     to the camelCase dicts that label_core expects.
  2. Routing specimens into the correct bucket via ``bucket_specimens``.

Usage
-----
    from app.services.label_service import LabelService, BUILTIN_TEMPLATES

    items, tissues = LabelService.build_print_job(specimens, template, bucket="sample")
"""

from __future__ import annotations

from typing import Optional, Union

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
