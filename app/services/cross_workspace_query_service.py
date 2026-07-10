"""cross_workspace_query_service.py — 跨工作区数据汇总查询（Qt-free）.

统一：标本 metadata 筛选 → 关联成片 → 筛选后统计。
项目树「数据汇总」与导出共用此入口。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from app.config.specimen_fields import (
    COMMON_FILTER_PRIORITY,
    FIELD_META,
    HIDDEN_FILTER_FIELDS,
    SUMMARY_ENRICHED_SPECIMEN_KEYS,
    eval_derived,
    field_label,
    filterable_fields,
    is_derived,
)
from app.services import specimen_filter_service as filter_svc
from app.utils.naming import extract_unique_id, normalize_uid, uid_group_key

__all__ = [
    "SummaryScopeResult",
    "query_summary_scope",
    "specimen_uid_key",
    "export_filtered_specimens_csv",
    "SUMMARY_SPECIMEN_COLUMNS",
    "DEFAULT_SUMMARY_VISIBLE_KEYS",
    "summary_all_columns",
    "resolve_summary_visible_columns",
    "summary_specimen_headers",
    "summary_cell_value",
]


@dataclass
class SummaryScopeResult:
    workspaces: list[str]
    conditions: list[dict[str, Any]]
    specimens: list[dict[str, Any]] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def specimen_uid_key(workspace: str, uid: str) -> tuple[str, str]:
    """Match key for specimen row ↔ photo group (workspace + uid without seq)."""
    ws = str(workspace or "")
    text = normalize_uid(str(uid or ""))
    if not text:
        return ws, ""
    stripped = normalize_uid(extract_unique_id(text))
    return ws, stripped or uid_group_key(text)


def _col_label(field: str, fallback: str) -> str:
    meta = FIELD_META.get(field) or {}
    return str(meta.get("label") or fallback)


SUMMARY_DERIVED_DISPLAY: tuple[tuple[str, str], ...] = (
    ("storage_is_rna", field_label("storage_is_rna")),
)

_PHOTO_EXIF_KEYS: tuple[str, ...] = (
    "camera_make",
    "camera_model",
    "lens_model",
    "exposure_time",
    "f_number",
    "iso",
    "focal_length",
    "exif_datetime",
    "image_width",
    "image_height",
)


def _resolve_result_tif_path(path: str) -> str:
    """Normalize to absolute resolved path for summary/export."""
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).resolve())
    except OSError:
        return text


def find_specimen_result_tif(workspace: str, uid: str) -> Optional[str]:
    """Representative result TIF for a specimen (``results/`` / ``results/freeform``)."""
    ws = str(workspace or "").strip()
    text = str(uid or "").strip()
    if not ws or not text:
        return None
    root = Path(ws)
    for sub in ("results", "results/freeform"):
        directory = root / sub
        if not directory.is_dir():
            continue
        try:
            matches = sorted(
                list(directory.glob(f"{text}*.tif"))
                + list(directory.glob(f"{text}*.tiff"))
            )
        except OSError:
            matches = []
        if matches:
            return _resolve_result_tif_path(str(matches[0]))

    # 成片文件名中间可插序号（…-物种-10-保存-日期.tif），uid 前缀 glob 会漏掉。
    try:
        from app.services.project_settings_service import DEFAULT_NAMING_RULES, get_effective
        from app.utils.naming import (
            normalize_naming_components,
            normalize_uid,
            parse_tiff_result_detail,
        )

        naming_rules = get_effective(ws, "naming_rules", DEFAULT_NAMING_RULES)
        components = normalize_naming_components(
            naming_rules.get("components") or DEFAULT_NAMING_RULES["components"]
        )
        target = normalize_uid(text)
        for sub in ("results", "results/freeform"):
            directory = root / sub
            if not directory.is_dir():
                continue
            try:
                candidates = sorted(directory.glob("*.tif")) + sorted(directory.glob("*.tiff"))
            except OSError:
                continue
            for path in candidates:
                detail = parse_tiff_result_detail(path.stem, components)
                if detail and normalize_uid(detail.uid) == target:
                    return _resolve_result_tif_path(str(path))
    except Exception:
        pass
    return None


def _tif_path_map_from_groups(groups: list[dict[str, Any]]) -> dict[str, list[str]]:
    """``workspace\\0uid`` → all master ``.tif`` absolute paths (for export)."""
    out: dict[str, list[str]] = {}
    for group in groups or []:
        ws = str(group.get("_workspace") or "")
        uid = str(group.get("uid") or "")
        if not ws or not uid:
            continue
        cache_key = f"{ws}\0{uid}"
        if cache_key in out:
            continue
        items = sorted(
            list(group.get("items") or []),
            key=lambda item: int(item.get("seq") or 0),
        )
        paths: list[str] = []
        seen: set[str] = set()
        for item in items:
            path = _resolve_result_tif_path(str(item.get("path") or ""))
            if not path.lower().endswith((".tif", ".tiff")):
                continue
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
        if paths:
            out[cache_key] = paths
    return out


def _resolve_specimen_result_tif_paths(
    workspace: str,
    uid: str,
    *,
    groups_map: dict[str, list[str]],
    conn: Any = None,
) -> list[str]:
    """DB 索引优先，其次成片网格扫描；始终返回母版 TIF 绝对路径列表.

    *conn*: 调用方提供的该工作区连接(enrich 批量时每工作区一条私有连接,
    用完即关)。缺省 None 时临时开私有连接——§7 旧实现用 open_project_db
    缓存连接, 每个被汇总的子工作区文件锁被扣到退出。
    """
    from app.db.db_manager import open_project_db_private
    from app.services.specimen_result_tif_service import list_result_tif_paths

    ws = str(workspace or "").strip()
    text = str(uid or "").strip()
    if not ws or not text:
        return []

    indexed: list[str] = []
    if conn is not None:
        try:
            indexed = list_result_tif_paths(conn, text)
        except Exception:
            indexed = []
    else:
        try:
            own = open_project_db_private(ws)
        except Exception:
            own = None
        if own is not None:
            try:
                indexed = list_result_tif_paths(own, text)
            except Exception:
                indexed = []
            finally:
                try:
                    own.close()
                except Exception:
                    pass

    cache_key = f"{ws}\0{text}"
    scanned = list(groups_map.get(cache_key) or [])
    if indexed:
        return indexed

    if scanned:
        return scanned

    single = find_specimen_result_tif(ws, text)
    return [single] if single else []


def enrich_specimens_with_photo_info(
    rows: list[dict[str, Any]],
    *,
    groups: list[dict[str, Any]] | None = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> list[dict[str, Any]]:
    """Attach recorded master TIF paths + camera EXIF to summary rows.

    ``cancel_callback`` 返回 True ⇒ 提前返回已 enrich 的部分行(worker 线程
    取消用;调用方负责丢弃部分结果)。
    """
    from app.services.photo_asset_service import read_image_exif_metadata

    from app.db.db_manager import open_project_db_private

    group_paths = _tif_path_map_from_groups(groups or [])
    cache: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    # 每工作区一条私有连接, 批量 enrich 结束统一关闭(锁泄漏治理)。
    ws_conns: dict[str, Any] = {}

    def _conn_for(ws_dir: str) -> Any:
        if ws_dir not in ws_conns:
            try:
                ws_conns[ws_dir] = open_project_db_private(ws_dir)
            except Exception:
                ws_conns[ws_dir] = None
        return ws_conns[ws_dir]

    try:
        return _enrich_rows(
            rows, cache, out, group_paths, _conn_for, read_image_exif_metadata,
            cancel_callback=cancel_callback,
        )
    finally:
        for c in ws_conns.values():
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass


def _enrich_rows(
    rows, cache, out, group_paths, conn_for, read_image_exif_metadata,
    cancel_callback=None,
):
    for row in rows:
        if cancel_callback is not None and cancel_callback():
            return out
        enriched = dict(row)
        ws = str(row.get("_workspace") or "")
        uid = str(row.get("uid") or "")
        cache_key = f"{ws}\0{uid}"
        if cache_key not in cache:
            slot: dict[str, Any] = {}
            tif_paths = _resolve_specimen_result_tif_paths(
                ws,
                uid,
                groups_map=group_paths,
                conn=conn_for(ws) if ws else None,
            )
            if tif_paths:
                slot["photo_absolute_path"] = tif_paths[0]
                slot["result_tif_paths"] = ";".join(tif_paths)
                primary = tif_paths[0]
                try:
                    meta = read_image_exif_metadata(primary)
                except Exception:
                    meta = {}
                for key in _PHOTO_EXIF_KEYS:
                    val = meta.get(key)
                    if val not in (None, ""):
                        slot[key] = val
            cache[cache_key] = slot
        enriched.update(cache[cache_key])
        out.append(enriched)
    return out

DEFAULT_SUMMARY_VISIBLE_KEYS: tuple[str, ...] = (
    "uid",
    "photo_absolute_path",
    "_workspace_label",
    "station",
    "storage",
    "photographer",
    "scientific_name",
)


def _fallback_db_field_keys() -> set[str]:
    return {k for k in FIELD_META if k not in HIDDEN_FILTER_FIELDS}


def summary_all_columns(workspaces: list[str]) -> list[tuple[str, str]]:
    """跨工作区 specimens 字段并集 + 工作区 + 派生列（CSV 全量导出用）。"""
    ws_list = [str(w) for w in (workspaces or []) if w]
    seen_db: set[str] = set()
    for ws in ws_list:
        db_path = Path(ws) / "_data" / "project.db"
        if not db_path.is_file():
            continue
        for fld in filterable_fields(str(db_path)):
            if not fld.get("derived"):
                seen_db.add(str(fld["key"]))
    if not seen_db:
        seen_db = _fallback_db_field_keys()

    priority_rest: list[str] = []
    for key in COMMON_FILTER_PRIORITY:
        if key in seen_db and not is_derived(key):
            priority_rest.append(key)
    tail = sorted(
        (k for k in seen_db if k not in priority_rest and not is_derived(k)),
        key=field_label,
    )
    ordered = priority_rest + tail

    cols: list[tuple[str, str]] = []
    if "uid" in ordered:
        cols.append(("uid", field_label("uid")))
        ordered = [k for k in ordered if k != "uid"]
    cols.append(("_workspace_label", "工作区"))
    cols.extend((k, field_label(k)) for k in ordered)
    seen = {k for k, _ in cols}
    for key in SUMMARY_ENRICHED_SPECIMEN_KEYS:
        if key not in seen:
            cols.append((key, field_label(key)))
            seen.add(key)
    if "storage_is_rna" not in seen:
        cols.append(("storage_is_rna", field_label("storage_is_rna")))
    return cols


def resolve_summary_visible_columns(
    all_columns: list[tuple[str, str]],
    saved_keys: list[str] | None = None,
) -> list[tuple[str, str]]:
    """按用户保存的 key 顺序解析表格可见列；无效 key 跳过。"""
    all_map = {k: lbl for k, lbl in all_columns}
    keys = list(saved_keys or DEFAULT_SUMMARY_VISIBLE_KEYS)
    out: list[tuple[str, str]] = []
    for key in keys:
        if key in all_map:
            out.append((key, all_map[key]))
    if out:
        return out
    for key in DEFAULT_SUMMARY_VISIBLE_KEYS:
        if key in all_map:
            out.append((key, all_map[key]))
    if out:
        return out
    return list(all_columns)


# 兼容旧引用：无工作区时的静态列清单。
SUMMARY_SPECIMEN_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    summary_all_columns([])
)


def summary_specimen_headers(columns: list[tuple[str, str]] | None = None) -> list[str]:
    cols = columns or list(SUMMARY_SPECIMEN_COLUMNS)
    return [label for _, label in cols]


def summary_cell_value(row: dict[str, Any], key: str) -> str:
    if key == "_workspace_label":
        return str(row.get("_workspace_label") or row.get("_workspace") or "").strip()
    if is_derived(key):
        return "是" if eval_derived(key, row) else "否"
    val = row.get(key)
    if val is None:
        return ""
    return str(val).strip()


def _filter_groups_for_specimens(
    workspaces: list[str],
    specimens: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from app.services import project_service as ps

    if not specimens:
        return []

    keep: set[tuple[str, str]] = {
        specimen_uid_key(str(r.get("_workspace") or ""), str(r.get("uid") or ""))
        for r in specimens
        if r.get("uid")
    }
    merged: list[dict[str, Any]] = []
    ungrouped_all: list[dict] = []

    for ws in workspaces:
        try:
            res = ps.get_project_results(ws)
        except Exception:
            continue
        for g in res.get("groups") or []:
            uid = str(g.get("uid") or "")
            if not uid:
                continue
            if specimen_uid_key(ws, uid) in keep:
                items: list[dict[str, Any]] = []
                for raw in g.get("items") or []:
                    item = dict(raw)
                    resolved = _resolve_result_tif_path(str(item.get("path") or ""))
                    if resolved:
                        item["path"] = resolved
                        items.append(item)
                merged.append({
                    "uid": uid,
                    "items": items,
                    "_workspace": ws,
                })
        for item in res.get("ungrouped") or []:
            ungrouped_all.append(dict(item))

    if ungrouped_all:
        merged.append({"uid": "", "items": ungrouped_all})
    return merged


def _stats_from_scope(
    workspaces: list[str],
    specimens: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    site_ctr: Counter[str] = Counter()
    station_ctr: Counter[str] = Counter()
    province_ctr: Counter[str] = Counter()
    photographer_ctr: Counter[str] = Counter()
    collector_ctr: Counter[str] = Counter()
    identifier_ctr: Counter[str] = Counter()
    rna_count = 0
    species_names: set[str] = set()

    for row in specimens:
        if eval_derived("storage_is_rna", row):
            rna_count += 1
        site = str(row.get("site") or "").strip()
        if site:
            site_ctr[site] += 1
        station = str(row.get("station") or "").strip()
        if station:
            station_ctr[station] += 1
        province = str(row.get("province") or "").strip()
        if province:
            province_ctr[province] += 1
        photographer = str(row.get("photographer") or "").strip()
        if photographer:
            photographer_ctr[photographer] += 1
        collector = str(row.get("collector") or "").strip()
        if collector:
            collector_ctr[collector] += 1
        identifier = str(row.get("identifier") or "").strip()
        if identifier:
            identifier_ctr[identifier] += 1
        sci = str(row.get("scientific_name") or "").strip()
        if sci:
            species_names.add(sci)

    photo_count = sum(len(g.get("items") or []) for g in groups if g.get("uid"))

    from app.services.survey_overview_service import map_points_from_specimen_rows

    map_pts = map_points_from_specimen_rows(specimens, level="station")

    def _rows(counter: Counter[str], limit: int = 12) -> list[dict[str, Any]]:
        return [
            {"name": name, "count": int(count)}
            for name, count in counter.most_common(limit)
        ]

    return {
        "workspace_count": len(workspaces),
        "specimen_count": len(specimens),
        "photo_count": photo_count,
        "rna_count": rna_count,
        "species_count": len(species_names),
        "site_rows": _rows(site_ctr),
        "station_rows": _rows(station_ctr),
        "province_rows": _rows(province_ctr),
        "photographer_rows": _rows(photographer_ctr),
        "collector_rows": _rows(collector_ctr),
        "identifier_rows": _rows(identifier_ctr),
        "map_points": map_pts,
    }


def query_summary_scope(
    workspaces: list[str],
    conditions: list[dict[str, Any]] | None = None,
    *,
    labels: Optional[list[str]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> SummaryScopeResult:
    """跨工作区汇总：筛选 specimens → 关联 photo groups → 统计.

    ``cancel_callback`` 返回 True ⇒ 在阶段边界(查询/同步/enrich/统计之间)
    提前返回空/部分 SummaryScopeResult。worker 线程取消用;取消后的返回值
    不应被展示(调用方按 token 丢弃)。
    """
    ws_list = [str(w) for w in (workspaces or []) if w]
    conds = list(conditions or [])
    label_list = labels
    if label_list is not None and len(label_list) != len(ws_list):
        label_list = None

    if not ws_list:
        return SummaryScopeResult(workspaces=[], conditions=conds)

    def _cancelled() -> bool:
        return cancel_callback is not None and bool(cancel_callback())

    specimens = filter_svc.query_specimens(ws_list, conds, labels=label_list)
    if _cancelled():
        return SummaryScopeResult(workspaces=ws_list, conditions=conds)
    groups = _filter_groups_for_specimens(ws_list, specimens)
    from app.services.specimen_result_tif_service import sync_workspace_result_tifs

    for ws in ws_list:
        if _cancelled():
            return SummaryScopeResult(workspaces=ws_list, conditions=conds)
        try:
            sync_workspace_result_tifs(ws)
        except Exception:
            pass
    specimens = enrich_specimens_with_photo_info(
        specimens, groups=groups, cancel_callback=cancel_callback
    )
    if _cancelled():
        return SummaryScopeResult(workspaces=ws_list, conditions=conds)
    stats = _stats_from_scope(ws_list, specimens, groups)

    return SummaryScopeResult(
        workspaces=ws_list,
        conditions=conds,
        specimens=specimens,
        groups=groups,
        stats=stats,
    )


def export_filtered_specimens_csv(
    rows: list[dict[str, Any]],
    dest: str | Path,
    *,
    columns: Optional[list[tuple[str, str]]] = None,
) -> Path:
    """Export filtered specimen rows to UTF-8 BOM CSV."""
    import csv

    dest_path = Path(dest)
    if columns is not None:
        cols = columns
    else:
        ws_from_rows = sorted({
            str(r.get("_workspace") or "").strip()
            for r in rows
            if str(r.get("_workspace") or "").strip()
        })
        cols = summary_all_columns(ws_from_rows) or list(SUMMARY_SPECIMEN_COLUMNS)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with dest_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for _, label in cols])
        for row in rows:
            writer.writerow([summary_cell_value(row, key) for key, _ in cols])
    return dest_path
