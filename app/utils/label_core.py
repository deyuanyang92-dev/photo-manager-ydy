"""label_core.py — Pure-Python direct translation of label-core.js.

All functions are pure algorithmic transforms: no DOM, no Qt, no IO.
The web oracle is ``prototype-photo-gui/label-core.js`` (2026-06-02).

Exported names mirror the JS LabelCore namespace exactly:
    normalized_date, date_segment, unique_id, has_rna_tissue,
    rna_preservative, specimen_to_label_data, unique_specimen_indices,
    bucket_specimens, normalize_field, normalize_template,
    resolve_line_height, resolve_wrap, calculate_grid,
    estimate_text_scale, qr_metrics, validate_print_job, create_print_job.

Constants
---------
DEFAULT_LINE_HEIGHT : 1.3
DEFAULT_PRINTER_MARGIN_MM : 2
GRID_GAP_MM : 2
SHEET_MARGIN_MM : 8
"""

from __future__ import annotations

import copy
import re
from typing import Any, Optional

# ── Constants (mirror JS vars) ────────────────────────────────────────────────

DEFAULT_LINE_HEIGHT: float = 1.3
DEFAULT_PRINTER_MARGIN_MM: int = 2
GRID_GAP_MM: int = 2
SHEET_MARGIN_MM: int = 8


# ── Internal helpers ──────────────────────────────────────────────────────────

def _clone(value: Any) -> Any:
    """Deep-copy via round-trip serialisation (mirrors ``JSON.parse(JSON.stringify(v))``).

    Note: floats and ints survive; None survives as None.
    """
    return copy.deepcopy(value)


# ── Public functions ──────────────────────────────────────────────────────────

def normalized_date(value: Any) -> str:
    """Strip non-digit characters and keep the first 8 digits.

    Mirror: ``normalizedDate`` in label-core.js.
    """
    return re.sub(r"[^0-9]", "", str(value or ""))[:8]


def date_segment(specimen: Optional[dict]) -> str:
    """Derive the date segment from collectionDate and photoDate.

    Mirror: ``dateSegment`` in label-core.js.

    Rules (identical to JS):
      - collection only → collection
      - no collection   → photo
      - same            → collection
      - same year       → collection + "-" + photo[4:]
      - different year  → collection + "-" + photo
    """
    sp = specimen or {}
    collection = normalized_date(sp.get("collectionDate"))
    photo = normalized_date(sp.get("photoDate"))
    if not collection:
        return photo
    if not photo or collection == photo:
        return collection
    if collection[:4] == photo[:4]:
        return collection + "-" + photo[4:]
    return collection + "-" + photo


def unique_id(specimen: Optional[dict]) -> str:
    """Derive the canonical uniqueId for a specimen dict.

    Mirror: ``uniqueId`` in label-core.js.

    Format: province-site-station-id-storage-dateSegment
    (all six components joined; empty string on None specimen).
    """
    if not specimen:
        return ""
    parts = [
        specimen.get("province", ""),
        specimen.get("site", ""),
        specimen.get("station", ""),
        specimen.get("id", ""),
        specimen.get("storage", ""),
        date_segment(specimen),
    ]
    return "-".join(str(p) for p in parts)


def has_rna_tissue(specimen: Optional[dict]) -> bool:
    """Return True when storage starts with 'R' (case-insensitive).

    Mirror: ``hasRnaTissue`` in label-core.js.
    """
    storage = (specimen or {}).get("storage") or ""
    return bool(re.match(r"^R", str(storage), re.IGNORECASE))


def rna_preservative(specimen: Optional[dict]) -> str:
    """Return 'RNAlater' when specimen has RNA tissue, else ''.

    Mirror: ``rnaPreservative`` in label-core.js.
    """
    return "RNAlater" if has_rna_tissue(specimen) else ""


def specimen_to_label_data(specimen: dict) -> dict:
    """Convert a specimen dict into a flat label-data dict.

    Mirror: ``specimenToLabelData`` in label-core.js.

    All source fields are read from ``specimen`` using the same camelCase
    key names as the JS oracle.  The returned dict carries every field that
    label renderers reference.
    """
    ds = date_segment(specimen)
    return {
        "province": specimen.get("province"),
        "site": specimen.get("site"),
        "station": specimen.get("station"),
        "speciesId": specimen.get("id"),
        "storage": specimen.get("storage"),
        "date": ds,
        "collectionDate": normalized_date(specimen.get("collectionDate")),
        "photoDate": normalized_date(specimen.get("photoDate")),
        "photoNotes": specimen.get("photoNotes") or "",
        "speciesName": specimen.get("species"),
        "latin": specimen.get("latin") or "",
        "collector": specimen.get("collector"),
        "photographer": specimen.get("photographer") or "",
        "region": specimen.get("region") or "",
        "lon": specimen.get("lon") or "",
        "lat": specimen.get("lat") or "",
        "geoArea": specimen.get("geoArea") or "",
        "family": specimen.get("family") or "",
        "uniqueId": unique_id(specimen),
        "headerId": "-".join(
            str(p)
            for p in [
                specimen.get("province"),
                specimen.get("site"),
                specimen.get("station"),
                specimen.get("id"),
            ]
        ),
        "shortDate": ds,
        "fullDate": ds,
        "collectorLabel": (specimen.get("collector") or "") + "采集",
        "transcriptome": has_rna_tissue(specimen),
        "rnaPreservative": rna_preservative(specimen),
    }


def unique_specimen_indices(indices: list[int], specimens: list[dict]) -> list[int]:
    """Return de-duplicated indices, keeping only the first per uniqueId.

    Mirror: ``uniqueSpecimenIndices`` in label-core.js.
    """
    seen: dict[str, int] = {}
    for idx in (indices or []):
        sp = specimens[idx] if idx < len(specimens) else None
        if sp is None:
            continue
        uid = unique_id(sp)
        if uid not in seen:
            seen[uid] = idx
    return list(seen.values())


def bucket_specimens(
    indices: list[int],
    specimens: list[dict],
    edits: Optional[dict] = None,
) -> dict:
    """Split de-duplicated specimens into sample and tissue buckets.

    Mirror: ``bucketSpecimens`` in label-core.js.

    Rules
    -----
    - **All** de-duplicated specimens → ``samples``.
    - R-prefix specimens → **also** appended to ``tissues``.
    - Both buckets reference the same item object (edits applied once).

    Returns ``{"samples": [...], "tissues": [...]}`` where each item is
    ``{"idx": int, "data": dict}``.
    """
    samples: list[dict] = []
    tissues: list[dict] = []
    for idx in unique_specimen_indices(indices, specimens):
        data = specimen_to_label_data(specimens[idx])
        item_edits = (edits or {}).get(idx)
        if item_edits:
            for key, val in item_edits.items():
                if val is not None:
                    data[key] = val
        item = {"idx": idx, "data": data}
        samples.append(item)
        if data["transcriptome"]:
            tissues.append(item)
    return {"samples": samples, "tissues": tissues}


def normalize_field(field: Any) -> dict:
    """Normalise a field descriptor into a canonical dict form.

    Mirror: ``normalizeField`` in label-core.js.

    Accepts:
      - str       → {key: str, style: "", size: None, offsetX: 0, offsetY: 0}
      - dict-like → fill defaults for any missing keys
      - other     → empty-key fallback
    """
    if isinstance(field, str):
        return {"key": field, "style": "", "size": None, "offsetX": 0, "offsetY": 0}
    if not field or not isinstance(field, dict):
        return {"key": "", "style": "", "size": None, "offsetX": 0, "offsetY": 0}
    return {
        "key": field.get("key") or "",
        "style": field.get("style") or "",
        "size": field.get("size") or None,
        "offsetX": field.get("offsetX") or 0,
        "offsetY": field.get("offsetY") or 0,
    }


def normalize_template(template: Optional[dict], opts: Optional[dict] = None) -> dict:
    """Normalise a label template, filling all defaults.

    Mirror: ``normalizeTemplate`` in label-core.js.

    Default fallback template used when *template* is None:
      name="标签", rows=[], qr={content:"uniqueId", position:"right",
      sizePct:0.4, ecc:"Q"}
    """
    opts = opts or {}
    fallback: dict = opts.get("fallback") or {
        "name": "标签",
        "rows": [],
        "qr": {
            "content": "uniqueId",
            "position": "right",
            "sizePct": 0.4,
            "ecc": "Q",
        },
    }
    out = _clone(template if template is not None else fallback)

    # rows
    if not isinstance(out.get("rows"), list):
        out["rows"] = []
    if not isinstance(out.get("lineHeight"), (int, float)):
        out["lineHeight"] = DEFAULT_LINE_HEIGHT

    normalised_rows = []
    for row in out["rows"]:
        row = row or {}
        next_row: dict = {k: v for k, v in row.items()}
        next_row["fields"] = (
            [normalize_field(f) for f in row["fields"]]
            if isinstance(row.get("fields"), list)
            else []
        )
        if next_row.get("wrap") is None:
            next_row["wrap"] = True
        if not next_row.get("align"):
            next_row["align"] = "left"
        normalised_rows.append(next_row)
    out["rows"] = normalised_rows

    # qr
    if not isinstance(out.get("qr"), dict):
        out["qr"] = {}
    out["qr"]["content"] = out["qr"].get("content") or "uniqueId"
    out["qr"]["position"] = out["qr"].get("position") or "right"
    out["qr"]["sizePct"] = out["qr"].get("sizePct") or 0.4
    out["qr"]["ecc"] = out["qr"].get("ecc") or "Q"
    return out


def resolve_line_height(template: Optional[dict], row: Optional[dict]) -> float:
    """Return the effective line-height for a row.

    Mirror: ``resolveLineHeight`` in label-core.js.
    Row-level value beats template-level which beats the global default.
    """
    if row and isinstance(row.get("lineHeight"), (int, float)):
        return float(row["lineHeight"])
    if template and isinstance(template.get("lineHeight"), (int, float)):
        return float(template["lineHeight"])
    return DEFAULT_LINE_HEIGHT


def resolve_wrap(row: Optional[dict]) -> bool:
    """Return the effective word-wrap flag for a row.

    Mirror: ``resolveWrap`` in label-core.js.
    Defaults to True; False only when explicitly set.
    """
    if row and row.get("wrap") is False:
        return False
    return True


def calculate_grid(
    label_w: float,
    label_h: float,
    page_w: float,
    page_h: float,
    opts: Optional[dict] = None,
) -> dict:
    """Calculate the column / row grid that fits labels on a page.

    Mirror: ``calculateGrid`` in label-core.js.

    Parameters
    ----------
    label_w, label_h : label dimensions in mm.
    page_w, page_h   : page dimensions in mm.
    opts             : optional overrides ``{marginMm, gapMm}``.

    Returns
    -------
    dict with keys: cols, rows, perPage, margin, gap, usableW, usableH.
    """
    opts = opts or {}
    margin = SHEET_MARGIN_MM if opts.get("marginMm") is None else opts["marginMm"]
    gap = GRID_GAP_MM if opts.get("gapMm") is None else opts["gapMm"]
    usable_w = max(0.0, page_w - 2 * margin)
    usable_h = max(0.0, page_h - 2 * margin)
    cols = max(1, int((usable_w + gap) // (label_w + gap)))
    rows = max(1, int((usable_h + gap) // (label_h + gap)))
    return {
        "cols": cols,
        "rows": rows,
        "perPage": cols * rows,
        "margin": margin,
        "gap": gap,
        "usableW": usable_w,
        "usableH": usable_h,
    }


def estimate_text_scale(template: Optional[dict], dims: dict) -> float:
    """Estimate the scale factor needed to fit template text into dims.

    Mirror: ``estimateTextScale`` in label-core.js.

    Returns a value in [0.4, 1.0] — 1.0 means text fits without scaling.
    """
    tmpl = normalize_template(template)
    qr = tmpl.get("qr") or {}
    has_qr = qr.get("position") != "none"
    pos = qr.get("position") if has_qr else "none"
    padding = 2.0
    w: float = dims.get("w", 1)
    h: float = dims.get("h", 1)
    avail_h = max(1.0, h - padding)
    if pos in ("top", "bottom"):
        avail_h -= w * (qr.get("sizePct") or 0.55)
    total_h = 0.0
    for row in tmpl["rows"]:
        size = row.get("size") or 9
        total_h += size * 0.35 * resolve_line_height(tmpl, row)
    if total_h <= avail_h:
        return 1.0
    return max(0.4, avail_h / total_h)


def qr_metrics(template: Optional[dict], dims: dict) -> Optional[dict]:
    """Calculate QR code position and size metrics within label dims (mm).

    Mirror: ``qrMetrics`` in label-core.js.

    Returns None when QR position is "none".
    Returns a dict with keys: x, y, sizeMm, distLeft, distTop, distRight, distBottom.
    """
    tmpl = normalize_template(template)
    qr = tmpl.get("qr") or {}
    if qr.get("position") == "none":
        return None
    w: float = dims.get("w", 1)
    h: float = dims.get("h", 1)
    pos = qr.get("position", "right")
    size_pct = qr.get("sizePct") or 0.4

    if pos == "free":
        size_mm = qr.get("sizeMm") or min(w, h) * size_pct
        x = qr.get("x") or 0.0
        y = qr.get("y") or 0.0
    elif pos in ("top", "bottom"):
        size_mm = w * (size_pct if qr.get("sizePct") else 0.55)
        x = max(0.0, (w - size_mm) / 2)
        y = max(0.0, h - size_mm) if pos == "bottom" else 0.0
    elif pos == "right":
        size_mm = h * size_pct
        x = max(0.0, w - size_mm)
        y = max(0.0, (h - size_mm) / 2)
    elif pos == "left":
        size_mm = h * size_pct
        x = 0.0
        y = max(0.0, (h - size_mm) / 2)
    else:
        # fallback: treat as right
        size_mm = h * size_pct
        x = max(0.0, w - size_mm)
        y = max(0.0, (h - size_mm) / 2)

    return {
        "x": x,
        "y": y,
        "sizeMm": size_mm,
        "distLeft": x,
        "distTop": y,
        "distRight": w - x - size_mm,
        "distBottom": h - y - size_mm,
    }


def validate_print_job(job: dict) -> list[dict]:
    """Validate a print-job dict and return a list of warning objects.

    Mirror: ``validatePrintJob`` in label-core.js.

    Each warning is ``{"level": "error"|"warn", "code": str, "message": str}``.
    """
    warnings: list[dict] = []
    dims: dict = job.get("dims") or {}
    tmpl = normalize_template(job.get("template"))

    # Empty items
    if not job.get("items"):
        warnings.append({
            "level": "error",
            "code": "empty",
            "message": "本桶没有可打印标签",
        })

    # Invalid dims
    w = dims.get("w", 0)
    h = dims.get("h", 0)
    if not dims or w <= 0 or h <= 0:
        warnings.append({
            "level": "error",
            "code": "bad-size",
            "message": "标签尺寸无效",
        })

    # Tiny label
    if dims and (w < 20 or h < 8):
        warnings.append({
            "level": "warn",
            "code": "tiny-label",
            "message": "标签尺寸过小，文字和 QR 可能不可读",
        })

    # Tissue-specific tiny warning
    if job.get("bucket") == "tissue" and dims and (w <= 25 or h <= 10):
        warnings.append({
            "level": "warn",
            "code": "tissue-mini",
            "message": "25×10mm RNAlater 管标签建议使用高 DPI 热敏或激光打印",
        })

    # Text overflow
    if estimate_text_scale(tmpl, dims) < 0.85:
        warnings.append({
            "level": "warn",
            "code": "text-overflow",
            "message": "文字内容偏多，打印时会自动缩小",
        })

    # QR margin and size
    qr = qr_metrics(tmpl, dims)
    if qr is not None:
        printer_margin = (
            DEFAULT_PRINTER_MARGIN_MM
            if job.get("printerMargin") is None
            else job["printerMargin"]
        )
        min_dist = min(qr["distLeft"], qr["distTop"], qr["distRight"], qr["distBottom"])
        if min_dist < printer_margin:
            warnings.append({
                "level": "warn",
                "code": "qr-margin",
                "message": (
                    f"QR 距边 {min_dist:.1f}mm，小于安全边距 {printer_margin}mm"
                ),
            })
        if qr["sizeMm"] < 6:
            warnings.append({
                "level": "warn",
                "code": "qr-small",
                "message": "QR 小于 6mm，扫码可靠性不足",
            })
    else:
        warnings.append({
            "level": "warn",
            "code": "qr-none",
            "message": "模板未启用 QR",
        })

    return warnings


def label_data_text(data: dict) -> str:
    """Return plain-text summary of label data (for clipboard/export).

    Mirrors web labelDataText(). One value per line, in display order.
    Uses the camelCase keys returned by specimen_to_label_data().
    Skips empty/None/falsy fields.
    Falls back to 'latin' when 'speciesName' is absent.
    """
    d = data or {}
    species = d.get("speciesName") or d.get("latin") or ""
    FIELD_VALUES = [
        d.get("uniqueId"),
        species,
        d.get("region"),
        d.get("collectorLabel"),
    ]
    lines = [str(v) for v in FIELD_VALUES if v]
    return "\n".join(lines)


def create_print_job(opts: Optional[dict] = None) -> dict:
    """Build a complete print-job dict from options.

    Mirror: ``createPrintJob`` in label-core.js.

    Parameters (all in opts dict)
    ------------------------------
    template   : label template dict (will be normalised)
    copies     : int, default 1
    items      : list of item dicts (each has "data" key or is flat)
    paperType  : "label" | "a4" | "a5"
    paper      : dict with "w"/"h" (required when paperType is a4/a5)
    dims       : dict with "w"/"h" in mm
    bucket     : "sample" | "tissue"
    printerMargin : int mm

    Returns
    -------
    dict: complete print job including ``warnings``.
    """
    opts = opts or {}
    tmpl = normalize_template(opts.get("template"))
    copies = max(1, int(opts.get("copies") or 1))
    items = opts.get("items") or []
    labels = []
    for item in items:
        for _ in range(copies):
            labels.append(item.get("data") if isinstance(item, dict) and "data" in item else item)
    paper_type = opts.get("paperType") or "label"
    paper = opts.get("paper") or None
    dims = opts.get("dims") or {}
    layout = None
    if paper_type in ("a4", "a5") and paper:
        layout = calculate_grid(dims.get("w", 0), dims.get("h", 0), paper.get("w", 0), paper.get("h", 0))
    job = {
        "bucket": opts.get("bucket") or "sample",
        "items": items,
        "labels": labels,
        "template": tmpl,
        "dims": dims,
        "paperType": paper_type,
        "paper": paper,
        "layout": layout,
        "copies": copies,
        "printerMargin": (
            DEFAULT_PRINTER_MARGIN_MM
            if opts.get("printerMargin") is None
            else opts["printerMargin"]
        ),
        "adapter": "pyqt6-qprinter",
        "warnings": [],
    }
    job["warnings"] = validate_print_job(job)
    return job
