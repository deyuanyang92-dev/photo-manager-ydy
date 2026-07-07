"""taxon_inventory_service.py — 跨断面物种名录聚合 (spec survey-summary-view T1).

Pure,无 Qt,易测。给定多个工作区目录,扫各自 ``_data/project.db`` 的
``specimens`` 表,按 ``scientific_name`` 去重聚合,产出物种名录(学名/中名/科/属
/出现断面/各断面编号数)。

复用模式参考 ``project_settings_service.get_effective`` 的 ``sqlite3.connect`` 直连
(只 SELECT,不写),损坏/缺失的工作区跳过而不抛 —— 与 inheritance 链上读 db 的容错一致。

红线遵守:
- 无 ``species``/``species_cn`` 列 (用 ``scientific_name`` / ``scientific_name_cn``)。
- 只读:不创建 db,不改 db。
- 空学名 (NULL/''/纯空白) 不计入名录 —— 物种名录按定义只列已命名物种。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

__all__ = ["aggregate_taxon_inventory"]


# 只 SELECT,不写;读取的列固定(避免 SELECT * 受 schema 演进影响)。
_SPECIMEN_QUERY = (
    "SELECT scientific_name, scientific_name_cn, family, genus, order_name "
    "FROM specimens "
    "WHERE scientific_name IS NOT NULL AND TRIM(scientific_name) <> '' "
    "ORDER BY scientific_name"
)


def _site_label(workspace: str, label: Optional[str]) -> str:
    """断面标签:显式 label 优先,否则取工作区目录 basename。"""
    if label:
        lbl = str(label).strip()
        if lbl:
            return lbl
    name = Path(workspace).name
    return name or str(workspace)


def _scan_one(workspace: str) -> list[tuple[str, str, str, str, str]]:
    """读单个工作区的 specimens 行;db 缺失/损坏/锁定 → 返回空列表(容错跳过)。"""
    db_path = Path(workspace) / "_data" / "project.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(_SPECIMEN_QUERY).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        # 损坏 / 非 SQLite 文件 / 表缺失等 —— 跳过该断面,不阻断整体聚合。
        return []
    return [(str(r[0] or "").strip(),
             str(r[1] or ""),
             str(r[2] or ""),
             str(r[3] or ""),
             str(r[4] or "")) for r in rows]


def aggregate_taxon_inventory(
    workspaces: list[str],
    *,
    labels: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """跨断面物种名录聚合。

    参数:
        workspaces: 工作区目录列表(每个含 ``_data/project.db``)。缺失/损坏的条目
            静默跳过,不抛异常。
        labels: 可选的断面显示标签列表,长度须与 ``workspaces`` 一致;未提供则用
            各工作区目录的 ``Path.name`` 作断面名(与 ``discover_workspaces`` 的
            basename 兜底一致)。

    返回:按 ``scientific_name`` 升序排列的物种名录,每项形如::

        {
            "scientific_name": str,
            "scientific_name_cn": str,   # 多断面中首个非空值
            "family": str,               # 首个非空
            "genus": str,                # 首个非空
            "order_name": str,           # 首个非空
            "sites": list[str],          # 升序的出现断面名
            "count_per_site": dict[str, int],  # 断面 → 编号(标本)数
            "total_count": int,          # 跨断面合计编号数
        }

    去重:按裁剪空白后的 ``scientific_name`` 精确匹配(同种跨断面合并,站点累积)。
    """
    if labels is not None and len(labels) != len(workspaces):
        raise ValueError(
            f"labels 长度({len(labels)})与 workspaces 长度({len(workspaces)})不一致"
        )

    # key = 裁剪后的学名;value = 聚合条目(尚未物化 sites/total_count)。
    bucket: dict[str, dict[str, Any]] = {}

    for i, workspace in enumerate(workspaces):
        site = _site_label(workspace, labels[i] if labels else None)
        for sn, scn, family, genus, order_name in _scan_one(workspace):
            entry = bucket.get(sn)
            if entry is None:
                entry = {
                    "scientific_name": sn,
                    "scientific_name_cn": scn,
                    "family": family,
                    "genus": genus,
                    "order_name": order_name,
                    "count_per_site": {},
                }
                bucket[sn] = entry
            else:
                # 多断面合并:显示字段取首个非空(同种但某断面缺分类信息时由
                # 另一断面补齐)。
                if not entry["scientific_name_cn"] and scn:
                    entry["scientific_name_cn"] = scn
                if not entry["family"] and family:
                    entry["family"] = family
                if not entry["genus"] and genus:
                    entry["genus"] = genus
                if not entry["order_name"] and order_name:
                    entry["order_name"] = order_name
            cps = entry["count_per_site"]
            cps[site] = cps.get(site, 0) + 1

    result: list[dict[str, Any]] = []
    for sn in sorted(bucket.keys()):
        entry = bucket[sn]
        cps = entry["count_per_site"]
        result.append({
            "scientific_name": entry["scientific_name"],
            "scientific_name_cn": entry["scientific_name_cn"],
            "family": entry["family"],
            "genus": entry["genus"],
            "order_name": entry["order_name"],
            "sites": sorted(cps.keys()),
            "count_per_site": dict(cps),
            "total_count": sum(cps.values()),
        })
    return result
