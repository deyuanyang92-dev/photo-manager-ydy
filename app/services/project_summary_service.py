"""project_summary_service.py — 跨工作区汇总导出（只读，无新建表）.

给定一组工作区目录（每个含 ``<dir>/_data/project.db``）和一个 root 目录（仅用于
计算 断面/来源 标签），合并它们的数据并写出 Excel / HTML 报告。

ZERO 新表：只读已有表（specimens / collection_records / grouping）。复用：
  - ``db_manager.open_project_db(dir, create=False)`` 严格打开（缺库即跳过）
  - ``Specimen.from_row`` 构造标本
  - ``export_service.export_excel`` 的 ``extra_leading`` 前置列 + 其样式辅助
  - ``collection_record_service.list_records`` 取采集记录
  - ``project_tree_service.scan_tree`` 做「文件夹存在但非工作区」质控

纯逻辑、无 Qt。一个缺失/锁定的工作区被静默跳过，绝不中断整个导出。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Iterator, Optional

import openpyxl

from app.db.db_manager import open_project_db
from app.models.specimen import Specimen
from app.services.collection_record_service import list_records
from app.services.export_service import (
    _ALT_FILL,
    _apply_header_row,
    _auto_col_widths,
    export_excel,
)
from app.services.project_paths import ProjectUnavailableError
from app.services.project_tree_service import scan_tree

# 采集站位汇总的列（断面在前，由调用处单独前置）。与 schema.sql 的真实列对齐。
_COLLECTION_COLUMNS: tuple[str, ...] = (
    "province", "site", "station", "collection_date",
    "station_label", "lon", "lat", "geo_area",
    "habitat", "tide", "salinity", "water_temp", "weather",
    "collector", "photographer", "identifier",
    "collection_time", "photo_date", "photo_location",
    "method", "remark",
)

_GROUPING_COMPOSED_STATUSES: tuple[str, ...] = ("composed", "organized")


# ── helpers ────────────────────────────────────────────────────────────────────

def _label(ws_dir: str, root: str) -> str:
    """工作区的 断面/来源 标签 = ``relpath(ws_dir, root)``。

    若 relpath 为 "." → 用 ``basename(ws_dir)``（root 本身就是工作区）。
    若 ws_dir 不在 root 之下（relpath 形如 "../.."，模式 B：无关的近期项目）→
    退回 ``basename(ws_dir)``。
    """
    try:
        rel = os.path.relpath(ws_dir, root)
    except ValueError:
        # 不同盘符（Windows）等无法计算相对路径的情形
        return os.path.basename(os.path.normpath(ws_dir))
    if rel == "." or rel.startswith(".."):
        return os.path.basename(os.path.normpath(ws_dir))
    return rel


def _iter_dbs(dirs: list[str]) -> Iterator[tuple[str, sqlite3.Connection]]:
    """逐个 yield ``(ws_dir, conn)``；缺失/锁定的库静默跳过，绝不中断全流程。"""
    for d in dirs:
        try:
            conn = open_project_db(d, create=False)
        except (ProjectUnavailableError, sqlite3.Error):
            continue
        yield d, conn


def _default_out(root: str, suffix: str, ext: str) -> Path:
    """``<root>/_data/exports/<basename(root)>_<suffix>.<ext>``，并 mkdir 父目录。"""
    root_path = Path(root)
    base = os.path.basename(os.path.normpath(root)) or root_path.name
    out = root_path / "_data" / "exports" / f"{base}_{suffix}.{ext}"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


# ── 1. 标本汇总 ─────────────────────────────────────────────────────────────────

def export_specimen_summary(
    dirs: list[str],
    root: str,
    out_path: str | None = None,
) -> Path:
    """合并各工作区的 specimens，导出带 断面 前置列的 34 列标本汇总 Excel。"""
    all_specs: list[Specimen] = []
    for ws_dir, conn in _iter_dbs(dirs):
        try:
            rows = conn.execute("SELECT * FROM specimens ORDER BY uid").fetchall()
        except sqlite3.Error:
            continue
        for r in rows:
            sp = Specimen.from_row(r)
            sp.owner_project_dir = ws_dir
            all_specs.append(sp)

    if out_path is None:
        out = _default_out(root, "标本汇总", "xlsx")
    else:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

    return export_excel(
        all_specs,
        out,
        extra_leading=[("断面", lambda s: _label(s.owner_project_dir or "", root))],
    )


# ── 2. 采集站位汇总 ─────────────────────────────────────────────────────────────

def export_collection_summary(
    dirs: list[str],
    root: str,
    out_path: str | None = None,
) -> Path:
    """合并各工作区的 collection_records，写带 断面 前置列的「采集站位汇总」表。"""
    headers = ["断面", *_COLLECTION_COLUMNS]
    data_rows: list[list] = []
    for ws_dir, conn in _iter_dbs(dirs):
        try:
            records = list_records(conn)
        except sqlite3.Error:
            continue
        label = _label(ws_dir, root)
        for rec in records:
            row: list = [label]
            for col in _COLLECTION_COLUMNS:
                val = rec.get(col)
                if col in ("lon", "lat"):
                    # 数字按数字写，缺失写空串（CLAUDE.md：空经纬度不写 0）
                    row.append(val if val not in (None, "") else "")
                else:
                    row.append("" if val is None else val)
            data_rows.append(row)

    if out_path is None:
        out = _default_out(root, "采集站位汇总", "xlsx")
    else:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "采集站位汇总"
    ws.freeze_panes = "A2"
    _apply_header_row(ws, headers)
    for row_idx, row_data in enumerate(data_rows, start=2):
        for col_idx, value in enumerate(row_data, start=1):
            ws.cell(row=row_idx, column=col_idx, value=value)
        if (row_idx - 2) % 2 == 1:
            for col_idx in range(1, len(headers) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = _ALT_FILL
    _auto_col_widths(ws, headers, data_rows)

    wb.save(str(out))
    return out.resolve()


# ── 3. 质控报告 ─────────────────────────────────────────────────────────────────

def _entity_from_loc(province, site, station) -> str:
    """以 / 连接 (province, site, station) 中非空的段，作为缺经纬度站位的实体名。"""
    return "/".join(str(x) for x in (province, site, station) if x not in (None, ""))


def collect_qc_findings(dirs: list[str], root: str) -> list[dict]:
    """跨工作区计算质控发现。每条 finding = ``{workspace_label, category, entity, detail}``。

    类别：
      - "缺经纬度站位"：collection_records 中 lon 或 lat 为 NULL 的行。
      - "有标本无成片"：无 status∈(composed,organized) 的 grouping 行的 specimen uid。
      - "分类不完整"：scientific_name 或 family 为空的 specimen。
      - "跨断面UID冲突"：同一 uid 出现在 ≥2 个不同工作区标签下。
      - "文件夹存在但非工作区"：root 下扫描到的、has_data=False 的叶节点目录。
    """
    findings: list[dict] = []
    uid_to_labels: dict[str, set[str]] = {}

    for ws_dir, conn in _iter_dbs(dirs):
        label = _label(ws_dir, root)

        # 缺经纬度站位
        try:
            rows = conn.execute(
                "SELECT province, site, station FROM collection_records "
                "WHERE lon IS NULL OR lat IS NULL"
            ).fetchall()
            for r in rows:
                findings.append({
                    "workspace_label": label,
                    "category": "缺经纬度站位",
                    "entity": _entity_from_loc(r["province"], r["site"], r["station"]),
                    "detail": "经度/纬度缺失",
                })
        except sqlite3.Error:
            pass

        # 有成片的 uid 集合（grouping 表可能在裸库中缺失 → 当作无成片）
        composed_uids: set[str] = set()
        try:
            placeholders = ", ".join("?" for _ in _GROUPING_COMPOSED_STATUSES)
            grows = conn.execute(
                f"SELECT DISTINCT uid FROM grouping WHERE status IN ({placeholders})",
                _GROUPING_COMPOSED_STATUSES,
            ).fetchall()
            composed_uids = {g["uid"] for g in grows}
        except sqlite3.Error:
            composed_uids = set()

        # specimens：有标本无成片 + 分类不完整 + uid→labels 累积
        try:
            srows = conn.execute(
                "SELECT uid, scientific_name, family FROM specimens"
            ).fetchall()
        except sqlite3.Error:
            srows = []
        for s in srows:
            uid = s["uid"]
            uid_to_labels.setdefault(uid, set()).add(label)
            if uid not in composed_uids:
                findings.append({
                    "workspace_label": label,
                    "category": "有标本无成片",
                    "entity": uid,
                    "detail": "无 composed/organized 成片",
                })
            sci = s["scientific_name"]
            fam = s["family"]
            if sci in (None, "") or fam in (None, ""):
                findings.append({
                    "workspace_label": label,
                    "category": "分类不完整",
                    "entity": uid,
                    "detail": "学名或科缺失",
                })

    # 跨断面UID冲突：同一 uid 出现在 ≥2 个不同标签
    for uid, labels in sorted(uid_to_labels.items()):
        if len(labels) >= 2:
            label_list = sorted(labels)
            findings.append({
                "workspace_label": "、".join(label_list),
                "category": "跨断面UID冲突",
                "entity": uid,
                "detail": "出现于断面：" + "、".join(label_list),
            })

    # 文件夹存在但非工作区：root 下扫描到的、非工作区的叶节点
    findings.extend(_orphan_folder_findings(root))

    return findings


def _orphan_folder_findings(root: str) -> list[dict]:
    """root 下扫描到的「文件夹存在但非工作区」叶节点（has_data=False 且无子节点）。"""
    out: list[dict] = []
    try:
        tree = scan_tree(root)
    except OSError:
        return out

    def _walk(node: dict) -> None:
        children = node.get("children", [])
        if not children and not node.get("has_data"):
            # 叶子且非工作区（root 自身若为空叶也会被纳入；用 path != root 排除根）
            if os.path.normpath(node["path"]) != os.path.normpath(root):
                out.append({
                    "workspace_label": _label(node["path"], root),
                    "category": "文件夹存在但非工作区",
                    "entity": node.get("name", ""),
                    "detail": node["path"],
                })
        for child in children:
            _walk(child)

    _walk(tree)
    return out


_QC_CATEGORY_ORDER: tuple[str, ...] = (
    "缺经纬度站位",
    "有标本无成片",
    "分类不完整",
    "跨断面UID冲突",
    "文件夹存在但非工作区",
)

_QC_SHEET_HEADERS = ("类别", "断面", "实体", "详情")


def export_qc_report(
    dirs: list[str],
    root: str,
    out_dir: str | None = None,
) -> tuple[Path, Path]:
    """写出 ``<basename>_质控报告.html`` 和 ``<basename>_质控报告.xlsx``。返回 (html, xlsx)。"""
    findings = collect_qc_findings(dirs, root)

    base = os.path.basename(os.path.normpath(root)) or Path(root).name
    if out_dir is None:
        out_base = Path(root) / "_data" / "exports"
    else:
        out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)
    html_path = out_base / f"{base}_质控报告.html"
    xlsx_path = out_base / f"{base}_质控报告.xlsx"

    # 按类别分组（保持固定顺序，未知类别附后）
    by_cat: dict[str, list[dict]] = {}
    for f in findings:
        by_cat.setdefault(f["category"], []).append(f)
    ordered_cats = [c for c in _QC_CATEGORY_ORDER if c in by_cat]
    ordered_cats += [c for c in by_cat if c not in _QC_CATEGORY_ORDER]

    _write_qc_html(html_path, base, by_cat, ordered_cats)
    _write_qc_xlsx(xlsx_path, findings)

    return html_path.resolve(), xlsx_path.resolve()


def _html_escape(text) -> str:
    s = "" if text is None else str(text)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _write_qc_html(
    html_path: Path,
    base: str,
    by_cat: dict[str, list[dict]],
    ordered_cats: list[str],
) -> None:
    total = sum(len(v) for v in by_cat.values())
    parts: list[str] = []
    parts.append("<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>")
    parts.append(f"<title>{_html_escape(base)} 质控报告</title>")
    parts.append(
        "<style>"
        "body{font-family:'Microsoft YaHei',sans-serif;margin:24px;color:#222;}"
        "h1{font-size:20px;}h2{font-size:16px;margin-top:24px;color:#2C5F8A;}"
        "table{border-collapse:collapse;width:100%;margin-top:8px;}"
        "th,td{border:1px solid #cbd5e1;padding:6px 10px;text-align:left;font-size:13px;}"
        "th{background:#2C5F8A;color:#fff;}"
        "tr:nth-child(even){background:#eef3fa;}"
        ".summary{background:#f1f5f9;padding:10px 14px;border-radius:6px;}"
        ".summary li{margin:2px 0;}"
        "</style></head><body>"
    )
    parts.append(f"<h1>{_html_escape(base)} 质控报告</h1>")
    parts.append("<div class='summary'><strong>汇总</strong><ul>")
    parts.append(f"<li>问题总数：{total}</li>")
    for cat in ordered_cats:
        parts.append(f"<li>{_html_escape(cat)}：{len(by_cat[cat])}</li>")
    parts.append("</ul></div>")

    if not ordered_cats:
        parts.append("<p>未发现质控问题。</p>")
    for cat in ordered_cats:
        parts.append(f"<h2>{_html_escape(cat)}（{len(by_cat[cat])}）</h2>")
        parts.append("<table><thead><tr><th>断面</th><th>实体</th><th>详情</th></tr></thead><tbody>")
        for f in by_cat[cat]:
            parts.append(
                "<tr><td>{}</td><td>{}</td><td>{}</td></tr>".format(
                    _html_escape(f.get("workspace_label")),
                    _html_escape(f.get("entity")),
                    _html_escape(f.get("detail")),
                )
            )
        parts.append("</tbody></table>")
    parts.append("</body></html>")

    html_path.write_text("".join(parts), encoding="utf-8")


def _write_qc_xlsx(xlsx_path: Path, findings: list[dict]) -> None:
    headers = list(_QC_SHEET_HEADERS)
    data_rows: list[list] = []
    for f in findings:
        data_rows.append([
            f.get("category", ""),
            f.get("workspace_label", ""),
            f.get("entity", ""),
            f.get("detail", ""),
        ])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "质控报告"
    ws.freeze_panes = "A2"
    _apply_header_row(ws, headers)
    for row_idx, row_data in enumerate(data_rows, start=2):
        for col_idx, value in enumerate(row_data, start=1):
            ws.cell(row=row_idx, column=col_idx, value=value)
        if (row_idx - 2) % 2 == 1:
            for col_idx in range(1, len(headers) + 1):
                ws.cell(row=row_idx, column=col_idx).fill = _ALT_FILL
    _auto_col_widths(ws, headers, data_rows)

    wb.save(str(xlsx_path))
