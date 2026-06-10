"""export_service.py — Specimen data export: Excel (34-col), CSV, Darwin Core.

Oracle:
  - server.js:595-721  (_buildSpecimensWorkbook — 34-column layout)
  - db_manager.py      (darwin_core view definition)
  - Specimen dataclass (field names)

Three public functions:
  export_excel(specimens, path, columns=None)   — openpyxl workbook, blue header
  export_csv(specimens, path, columns=None)     — UTF-8 with BOM (Excel-compatible)
  export_darwin_core(db, path)                  — reads darwin_core VIEW from SQLite
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.models.specimen import Specimen
from app.utils.naming import specimen_date_seg

# ── Storage-code helpers (mirrors server.js _spPresDetail / _spIsRNA) ─────────

_STORAGE_LABELS: dict[str, str] = {
    "T95E": "梯度酒精 95%→80%→75% 脱水固定",
    "T80E": "梯度酒精 80%→75% 脱水固定",
    "T75E": "梯度酒精 75% 脱水固定",
    "D75E": "直接 75% 乙醇固定",
    "D95E": "直接 95% 乙醇固定",
    "D70E": "直接 70% 乙醇固定",
    "FA":   "甲醛固定",
    "DRY":  "干燥固定",
    "FRZ":  "冷冻保存",
    "LIVE": "活体",
}


def _pres_detail(storage: Optional[str]) -> str:
    """Return human-readable preservation description from storage code."""
    if not storage:
        return ""
    return _STORAGE_LABELS.get(storage.upper(), storage)


def _is_rna(storage: Optional[str]) -> bool:
    """Return True when the storage code indicates RNAlater treatment (R prefix)."""
    if not storage:
        return False
    return str(storage).upper().startswith("R")


def _meta_score(sp: Specimen) -> int:
    """Return metadata completeness percentage (0-100).

    Mirrors server.js _spMetaScore: counts 5 key fields.
    """
    fields = [sp.scientific_name, sp.family, sp.collector, sp.lon, sp.lat]
    filled = sum(1 for f in fields if f is not None and str(f).strip() != "")
    return round(filled / len(fields) * 100)


def _taxon_complete(sp: Specimen) -> str:
    """Return checkmark if scientific_name and family are set, else cross."""
    return "✓" if (sp.scientific_name and sp.family) else "✗"


def _date_seg(sp: Specimen) -> str:
    return specimen_date_seg(sp.collection_date, sp.photo_date)


# ── Column definitions (34 columns, mirrors Sheet 1 of _buildSpecimensWorkbook) ─

#: Master column list (header label, accessor callable).
#: The accessor receives a Specimen and returns a scalar value.
COLUMNS: list[tuple[str, callable]] = [
    ("标本唯一编号",      lambda s: s.uid or ""),
    ("物种拼音编号",      lambda s: s.id or ""),
    ("物种中名",          lambda s: s.scientific_name_cn or ""),
    ("物种拉丁名",        lambda s: s.scientific_name or ""),
    ("类群中名",          lambda s: s.taxon_group_cn or ""),
    ("类群拉丁名",        lambda s: s.taxon_group or ""),
    ("目中名",            lambda s: s.order_cn or ""),
    ("目拉丁名",          lambda s: s.order_name or ""),
    ("科中名",            lambda s: s.family_cn or ""),
    ("科拉丁名",          lambda s: s.family or ""),
    ("属中名",            lambda s: s.genus_cn or ""),
    ("属拉丁名",          lambda s: s.genus or ""),
    ("省份代码",          lambda s: s.province or ""),
    ("样地代码",          lambda s: s.site or ""),
    ("站位",              lambda s: s.station or ""),
    ("采集地全称",        lambda s: s.geo_area or (
        (s.province or "")
        + ("·" + s.site if s.site else "")
        + ("·" + s.station if s.station else "")
    )),
    ("经度",              lambda s: float(s.lon) if s.lon is not None else ""),
    ("纬度",              lambda s: float(s.lat) if s.lat is not None else ""),
    ("保存方式代码",      lambda s: s.storage or ""),
    ("固定方式全文",      lambda s: _pres_detail(s.storage)),
    ("采集日期",          lambda s: s.collection_date or ""),
    ("拍照日期",          lambda s: s.photo_date or ""),
    ("日期段",            _date_seg),
    ("采集人",            lambda s: s.collector or ""),
    ("拍摄人",            lambda s: s.photographer or ""),
    ("鉴定人",            lambda s: s.identifier or ""),
    ("角度标记",          lambda s: s.angle or ""),
    ("分类完整",          _taxon_complete),
    ("RNA标本",           lambda s: "✓" if _is_rna(s.storage) else "✗"),
    ("Metadata完整度(%)", _meta_score),
    ("备注",              lambda s: s.notes or ""),
    ("拍照备注",          lambda s: s.photo_notes or ""),
    ("元数据标志",        lambda s: s.metadata or 0),
    ("置顶",              lambda s: "✓" if s.pinned else "✗"),
]

# Verify we have exactly 34 columns
assert len(COLUMNS) == 34, f"Expected 34 export columns, got {len(COLUMNS)}"

COLUMN_HEADERS: list[str] = [h for h, _ in COLUMNS]


# ── openpyxl style constants ───────────────────────────────────────────────────

_HDR_FILL = PatternFill("solid", fgColor="2C5F8A")
_HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
_HDR_ALIGN = Alignment(horizontal="center", vertical="center")
_HDR_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
_ALT_FILL = PatternFill("solid", fgColor="EEF3FA")


def _apply_header_row(ws, headers: list[str]) -> None:
    """Write a styled header row (row 1) to *ws*."""
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
        cell.alignment = _HDR_ALIGN
        cell.border = _HDR_BORDER
    ws.row_dimensions[1].height = 22


def _auto_col_widths(ws, headers: list[str], data_rows: list[list]) -> None:
    """Set column widths based on header and data content (max 50, min 8)."""
    for col_idx, header in enumerate(headers, start=1):
        max_len = sum(2 if ord(c) > 127 else 1 for c in header)
        for row in data_rows:
            val = str(row[col_idx - 1]) if row[col_idx - 1] is not None else ""
            length = sum(2 if ord(c) > 127 else 1 for c in val)
            if length > max_len:
                max_len = length
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 1, 8), 50)


def _build_data_rows(
    specimens: Sequence[Specimen],
    columns: list[tuple[str, callable]],
) -> list[list]:
    """Convert specimen list to list of row lists using column accessors."""
    rows = []
    for sp in specimens:
        row = []
        for _header, accessor in columns:
            try:
                row.append(accessor(sp))
            except Exception:
                row.append("")
        rows.append(row)
    return rows


def _resolve_columns(
    columns: Optional[list[str]],
) -> list[tuple[str, callable]]:
    """Return the filtered column definitions matching requested header labels.

    If *columns* is None or empty, return all 34 columns.
    Unknown column names are silently skipped.
    """
    if not columns:
        return COLUMNS
    col_map = {h: acc for h, acc in COLUMNS}
    return [(h, col_map[h]) for h in columns if h in col_map]


# ── Public API ─────────────────────────────────────────────────────────────────

def export_excel(
    specimens: Sequence[Specimen],
    path: str | Path,
    columns: Optional[list[str]] = None,
    extra_leading: Optional[list[tuple[str, callable]]] = None,
) -> Path:
    """Export specimens to an Excel file (.xlsx) at *path*.

    Parameters
    ----------
    specimens:
        Iterable of Specimen instances to export.
    path:
        Destination file path (will be created or overwritten).
    columns:
        Optional list of column header strings to include.
        Defaults to all 34 columns.
    extra_leading:
        Optional list of ``(header, accessor)`` pairs prepended *before* the
        resolved columns. Each accessor takes a Specimen and returns a scalar
        (same contract as the master columns; a raising accessor yields "").
        When None/empty the output is byte-for-byte identical to before — this
        is a purely additive parameter and must never alter the default 34-col
        layout (red line: oracle server.js:595-721).

    Returns
    -------
    Path
        Resolved path of the written file.

    Oracle: server.js:595-721 (_buildSpecimensWorkbook Sheet 1)
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    active_cols = _resolve_columns(columns)
    if extra_leading:
        active_cols = list(extra_leading) + active_cols
    headers = [h for h, _ in active_cols]
    data_rows = _build_data_rows(specimens, active_cols)

    wb = openpyxl.Workbook()
    wb.properties.creator = "拍照工作台"

    ws = wb.active
    ws.title = "标本汇总"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"

    _apply_header_row(ws, headers)

    for row_idx, row_data in enumerate(data_rows, start=2):
        for col_idx, value in enumerate(row_data, start=1):
            ws.cell(row=row_idx, column=col_idx, value=value)
        # Alternating row fill (odd data rows = white, even = light blue)
        if (row_idx - 2) % 2 == 1:
            for col_idx in range(1, len(headers) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = _ALT_FILL

    _auto_col_widths(ws, headers, data_rows)

    # Metadata sheet
    ws2 = wb.create_sheet("导出信息")
    ws2["A1"] = "导出日期"
    ws2["B1"] = date.today().isoformat()
    ws2["A2"] = "标本数"
    ws2["B2"] = len(data_rows)
    ws2["A3"] = "列数"
    ws2["B3"] = len(headers)

    wb.save(str(path))
    return path.resolve()


def export_csv(
    specimens: Sequence[Specimen],
    path: str | Path,
    columns: Optional[list[str]] = None,
) -> Path:
    """Export specimens to a CSV file with UTF-8 BOM (Excel-compatible).

    Parameters
    ----------
    specimens:
        Iterable of Specimen instances to export.
    path:
        Destination file path (will be created or overwritten).
    columns:
        Optional list of column header strings to include.
        Defaults to all 34 columns.

    Returns
    -------
    Path
        Resolved path of the written file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    active_cols = _resolve_columns(columns)
    headers = [h for h, _ in active_cols]
    data_rows = _build_data_rows(specimens, active_cols)

    # utf-8-sig = UTF-8 with BOM; Excel on Windows opens correctly
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for row in data_rows:
            writer.writerow(row)

    return path.resolve()


def export_darwin_core(db: sqlite3.Connection, path: str | Path) -> Path:
    """Export the darwin_core VIEW to a CSV file.

    Reads directly from the SQLite darwin_core view (created by db_manager).
    DwC columns: occurrenceID, scientificName, family, genus, order,
                 decimalLongitude, decimalLatitude, eventDate, recordedBy,
                 identifiedBy, locality, verbatimPreservation.

    Parameters
    ----------
    db:
        Open SQLite connection with the darwin_core view available.
    path:
        Destination .csv file path.

    Returns
    -------
    Path
        Resolved path of the written file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    cursor = db.execute("SELECT * FROM darwin_core")
    col_names = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()

    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(col_names)
        for row in rows:
            writer.writerow(list(row))

    return path.resolve()
