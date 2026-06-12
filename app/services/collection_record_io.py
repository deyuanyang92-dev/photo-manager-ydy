"""collection_record_io.py — 采集记录 Excel/CSV 模板导出 + 导入（步骤 5）.

用户工作流：导出一个 Excel 模板（含已有记录，便于离线/在 Excel 里批量填）→
离线填写 → 导回软件。复用 openpyxl（requirements 已含）与 taxonomy_view 的
表头别名+行循环模式；写入走 collection_record_service.upsert_record。

纯逻辑、无 Qt，便于测试。
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.services import collection_record_service as crs

# 导出/导入列：(字段 key, 中文表头)。顺序即列序，覆盖 collection_records 全部可填字段。
IO_COLUMNS: list[tuple[str, str]] = [
    ("province", "地区"),
    ("site", "样地"),
    ("station", "站位"),
    ("collection_date", "采集日期"),
    ("station_label", "站位说明"),
    ("lon", "经度"),
    ("lat", "纬度"),
    ("geo_area", "采集地理区"),
    ("water_body", "海区"),
    ("cruise", "航次"),
    ("vessel", "船号"),
    ("tidal_zone", "潮区"),
    ("depth", "水深(m)"),
    ("habitat", "生境"),
    ("tide", "潮水"),
    ("salinity", "盐度"),
    ("water_temp", "水温(表层)"),
    ("bottom_temp", "底层水温"),
    ("dissolved_oxygen", "溶解氧"),
    ("ph", "pH"),
    ("weather", "天气"),
    ("sample_type", "采集性质"),
    ("method", "采样方法"),
    ("sampler_model", "采泥器型号"),
    ("sampler_spec", "采样器规格"),
    ("sample_area", "取样面积(m²)"),
    ("replicates", "取样次数"),
    ("sieve_mesh", "网筛孔径(mm)"),
    ("sample_no", "样品编号"),
    ("collector", "采集人"),
    ("recorder", "记录人"),
    ("checker", "核对人"),
    ("photographer", "拍摄人"),
    ("identifier", "鉴定人"),
    ("collection_time", "采集时刻"),
    ("photo_date", "拍摄日期"),
    ("photo_location", "拍摄地点"),
    ("remark", "备注"),
]

# 必填四键——导入时缺任一则跳过该行。
_KEY_FIELDS: tuple[str, ...] = ("province", "site", "station", "collection_date")


# 旧表头别名 → 字段 key：兼容字段优化前导出的模板（标签曾为 水温 / 采集方法）。
_LEGACY_HEADER_ALIASES: dict[str, str] = {
    "水温": "water_temp",
    "采集方法": "method",
}


def _alias_map() -> dict[str, str]:
    """Build {header → field key}: accept 中文表头 / 英文 key（大小写不敏感）."""
    amap: dict[str, str] = dict(_LEGACY_HEADER_ALIASES)
    for key, zh in IO_COLUMNS:
        amap[zh] = key
        amap[key] = key
        amap[key.lower()] = key
    return amap


@dataclass
class ImportReport:
    ok: bool = True
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ── Export ──────────────────────────────────────────────────────────────────
def export_template(
    db: sqlite3.Connection,
    path: str,
    *,
    province: str = "",
    site: str = "",
    blank_rows: int = 20,
) -> int:
    """Write an .xlsx template at *path*; return the row count written.

    Includes every existing record (so the user can bulk-edit offline) followed by
    *blank_rows* empty rows pre-filled with the inherited 地区/样地 for quick entry.
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "采集记录"

    headers = [zh for _k, zh in IO_COLUMNS]
    ws.append(headers)
    # Header styling (mirrors export_service blue header).
    hdr_fill = PatternFill("solid", fgColor="2C5F8A")
    hdr_font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = hdr_fill
        cell.font = hdr_font

    written = 0
    records = crs.list_records(db) if db is not None else []
    for rec in records:
        ws.append([_cell(rec.get(k)) for k, _zh in IO_COLUMNS])
        written += 1

    # Blank rows pre-seeded with 地区/样地 so offline entry doesn't re-type them.
    for _ in range(max(0, blank_rows)):
        row = []
        for k, _zh in IO_COLUMNS:
            if k == "province":
                row.append(province or "")
            elif k == "site":
                row.append(site or "")
            else:
                row.append("")
        ws.append(row)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return written


def _cell(val) -> str:
    return "" if val in (None,) else str(val)


# ── Import ──────────────────────────────────────────────────────────────────
def import_file(db: sqlite3.Connection, path: str) -> ImportReport:
    """Import .xlsx or .csv at *path* into collection_records (upsert per row)."""
    if db is None:
        return ImportReport(ok=False, errors=["没有打开的项目"])
    p = Path(path)
    try:
        if p.suffix.lower() in (".xlsx", ".xlsm"):
            header, rows = _read_xlsx(path)
        else:
            header, rows = _read_csv(path)
    except Exception as exc:  # noqa: BLE001
        return ImportReport(ok=False, errors=[f"读取失败：{exc}"])

    return _import_rows(db, header, rows)


def _read_xlsx(path: str) -> tuple[list[str], list[list]]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = [("" if c is None else str(c)).strip() for c in next(rows_iter)]
    except StopIteration:
        return [], []
    data = [list(r) for r in rows_iter]
    return header, data


def _read_csv(path: str) -> tuple[list[str], list[list]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return [], []
    header = [c.strip() for c in rows[0]]
    return header, rows[1:]


def _import_rows(db, header: list[str], rows: list[list]) -> ImportReport:
    amap = _alias_map()
    # column index → field key
    col_to_key: dict[int, str] = {}
    for i, h in enumerate(header):
        key = amap.get(h) or amap.get(h.lower())
        if key:
            col_to_key[i] = key

    if not col_to_key:
        return ImportReport(ok=False, errors=["表头无法识别（需含 地区/样地/站位/采集日期 等列）"])

    report = ImportReport()
    for r_idx, raw in enumerate(rows, start=2):
        data: dict = {}
        for i, key in col_to_key.items():
            val = raw[i] if i < len(raw) else None
            data[key] = ("" if val is None else str(val)).strip()
        # Skip wholly-empty rows silently.
        if not any(data.get(k) for k in col_to_key.values()):
            continue
        # Need the 4 key fields to identify a record.
        if not all(data.get(k) for k in _KEY_FIELDS):
            report.skipped += 1
            continue
        try:
            crs.upsert_record(db, data)
            report.imported += 1
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"第 {r_idx} 行：{exc}")
    return report
