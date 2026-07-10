"""project_tree_layout.py — 项目树三栏/缩略图网格布局参数（集中配置，可持久化覆盖）.

视图层与 ``UidGroupedGrid`` 只调用这里的函数，避免在 UI 文件里散落魔法数字。
用户可调项（网格密度、splitter 位置）走 ``AppSettings``；其余默认在此维护。
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

# ── 缩略图网格 ────────────────────────────────────────────────────────────────

DEFAULT_THUMB_SIZE = 160
THUMB_SIZE_MIN = 16
THUMB_SIZE_MAX = 720  # 单列大图预览需要超过旧 280 上限
THUMB_CELL_PAD_X = 8
THUMB_CELL_CAPTION_H = 24
GRID_CELL_SPACING = 4
GRID_VIEWPORT_PAD = 8
AUTO_FIT_COL_GAP = 12
AUTO_FIT_CELL_PAD = 8
AUTO_FIT_MIN_THUMB = 96
AUTO_FIT_FALLBACK_THUMB = 128

# 滑块档位：左=更密（多列小图），右=更疏（单列大图）
GRID_COLUMN_PRESETS: tuple[int, ...] = (32, 24, 16, 12, 8, 6, 4, 3, 2, 1)
DEFAULT_GRID_DENSITY_INDEX = GRID_COLUMN_PRESETS.index(4)  # 默认约 4 列

# 快捷按钮：用户指定的固定列数
GRID_QUICK_COLUMN_PRESETS: tuple[int, ...] = (1, 2, 4, 8, 16, 32)

GRID_SORT_MODES: tuple[tuple[str, str], ...] = (
    ("uid_seq", "编号 + 序号"),
    ("filename", "文件名"),
    ("mtime_desc", "修改时间 ↓"),
    ("mtime_asc", "修改时间 ↑"),
)
DEFAULT_GRID_SORT = "uid_seq"

# 成片网格缩略图下方标注（项目树 / 汇总网格 unified 模式）
GRID_CAPTION_MODES: tuple[tuple[str, str], ...] = (
    ("smart", "智能显示"),
    ("core", "地区-样地-物种"),
    ("core_storage", "含保存方式"),
    ("core_seq", "核心 + 序号"),
    ("station_species", "站位-物种"),
    ("filename", "文件名"),
    ("full_uid", "完整编号"),
)
DEFAULT_GRID_CAPTION = "smart"

# 项目树中栏：仅数据汇总（统计 KPI 在右侧「调查概览」）
CONTENT_MODES: tuple[tuple[str, str], ...] = (
    ("data_summary", "数据汇总"),
)
DEFAULT_CONTENT_MODE = "data_summary"

# 旧模式迁移到 data_summary
LEGACY_CONTENT_MODES: dict[str, str] = {
    "overview": "data_summary",
    "photos": "data_summary",
    "species": "data_summary",
    "summary": "data_summary",
}

# 旧版像素滑块范围（仅迁移用）
THUMB_SLIDER_MIN = 96

# ── 三栏 splitter ─────────────────────────────────────────────────────────────

SPLIT_STRETCH_TREE = 2
SPLIT_STRETCH_GRID = 7
SPLIT_STRETCH_DETAIL = 2
SPLIT_MIN_TREE = 220
SPLIT_MIN_GRID = 360
SPLIT_MIN_DETAIL = 240
SPLIT_DEFAULT_TOTAL = 1320  # 仅在没有 saveState 时的参考总宽

# 中栏内部：编号索引 | 照片网格
GRID_INNER_UID_MIN = 88
GRID_INNER_PHOTO_MIN = 280
GRID_INNER_UID_DEFAULT = 148

# 数据汇总中栏：编号表 | 成片网格（纵向可拖；仅表格区，不含筛选条）
SUMMARY_BODY_TABLE_MIN = 72
SUMMARY_BODY_PHOTO_MIN = 160
SUMMARY_BODY_DEFAULT_HEIGHT = 680
SUMMARY_BODY_TABLE_DEFAULT = 168

# ── 预览 ──────────────────────────────────────────────────────────────────────

PREVIEW_DECODE_MAX = 1600
PREVIEW_MIN_WIDTH = 360
PREVIEW_MIN_HEIGHT = 320
COVER_CARD_DECODE_MAX = 320

# TIFF 预览主图（磁盘缓存）可选档位 — 右键/设置可切换
PREVIEW_MASTER_PRESETS: tuple[int, ...] = (512, 720, 1080, 1440)
DEFAULT_PREVIEW_MASTER_SIZE = 1080

# 数据汇总后台 TIF→JPG 预览默认质量（与「TIFF 转 JPG · 高清存档」预设一致）
DEFAULT_PREVIEW_JPEG_QUALITY = 95


def clamp_preview_master_size(size: int | float | None) -> int:
    try:
        n = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = DEFAULT_PREVIEW_MASTER_SIZE
    if n in PREVIEW_MASTER_PRESETS:
        return n
    return min(PREVIEW_MASTER_PRESETS, key=lambda p: abs(p - n))

# ── 卡片视图 ──────────────────────────────────────────────────────────────────

CARD_GRID_COLUMNS = 3


def clamp_thumb_size(size: int | float | None) -> int:
    try:
        n = int(size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = DEFAULT_THUMB_SIZE
    return max(THUMB_SIZE_MIN, min(THUMB_SIZE_MAX, n))


def clamp_density_index(index: int | float | None) -> int:
    try:
        idx = int(index)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        idx = DEFAULT_GRID_DENSITY_INDEX
    return max(0, min(len(GRID_COLUMN_PRESETS) - 1, idx))


def density_slider_range() -> tuple[int, int]:
    return 0, len(GRID_COLUMN_PRESETS) - 1


def columns_for_density_index(index: int) -> int:
    return GRID_COLUMN_PRESETS[clamp_density_index(index)]


def density_index_for_columns(columns: int) -> int:
    """Map a column count to the nearest preset slider index."""
    try:
        target = max(1, int(columns))
    except (TypeError, ValueError):
        return DEFAULT_GRID_DENSITY_INDEX
    best_idx = DEFAULT_GRID_DENSITY_INDEX
    best_diff = 10_000
    for idx, cols in enumerate(GRID_COLUMN_PRESETS):
        diff = abs(cols - target)
        if diff < best_diff:
            best_diff = diff
            best_idx = idx
    return best_idx


def density_index_from_legacy_thumb(viewport_width: int, thumb_px: int) -> int:
    """Migrate old pixel-based slider values to a density preset index."""
    vw = max(SPLIT_MIN_GRID, int(viewport_width) - GRID_VIEWPORT_PAD)
    try:
        target = int(thumb_px)
    except (TypeError, ValueError):
        return DEFAULT_GRID_DENSITY_INDEX
    best_idx = DEFAULT_GRID_DENSITY_INDEX
    best_diff = 10_000
    for idx, cols in enumerate(GRID_COLUMN_PRESETS):
        diff = abs(thumb_size_for_columns(vw, cols) - target)
        if diff < best_diff:
            best_diff = diff
            best_idx = idx
    return best_idx


def density_label(columns: int) -> str:
    cols = max(1, int(columns))
    if cols == 1:
        return "1 张/行"
    return f"{cols} 列"


def cell_dims(thumb_size: int) -> tuple[int, int, int]:
    """Return ``(thumb_px, cell_w, cell_h)`` for grid cells."""
    ts = clamp_thumb_size(thumb_size)
    return ts, ts + THUMB_CELL_PAD_X, ts + THUMB_CELL_CAPTION_H


def thumb_size_for_columns(viewport_width: int, columns: int) -> int:
    """Pixel thumb size so *columns* fit *viewport_width* (may exceed old 280 cap)."""
    vw = max(SPLIT_MIN_GRID, int(viewport_width) - GRID_VIEWPORT_PAD)
    cols = max(1, int(columns))
    ts = (vw - cols * AUTO_FIT_COL_GAP) // cols - AUTO_FIT_CELL_PAD
    if cols >= 16:
        min_thumb = 16
    elif cols >= 8:
        min_thumb = 24
    elif cols > 1:
        min_thumb = AUTO_FIT_MIN_THUMB
    else:
        min_thumb = 120
    if cols >= 8:
        ts = min(ts, 128)
    return clamp_thumb_size(max(min_thumb, ts))


def thumb_size_for_density(viewport_width: int, density_index: int) -> int:
    return thumb_size_for_columns(
        viewport_width,
        columns_for_density_index(density_index),
    )


def auto_fit_thumb_for_viewport(viewport_width: int) -> int:
    """Default thumb for first open ≈ ``DEFAULT_GRID_DENSITY_INDEX`` preset."""
    return thumb_size_for_density(viewport_width, DEFAULT_GRID_DENSITY_INDEX)


def normalize_grid_sort(mode: str | None) -> str:
    allowed = {k for k, _ in GRID_SORT_MODES}
    m = str(mode or DEFAULT_GRID_SORT)
    return m if m in allowed else DEFAULT_GRID_SORT


def normalize_grid_caption_mode(mode: str | None) -> str:
    allowed = {k for k, _ in GRID_CAPTION_MODES}
    m = str(mode or DEFAULT_GRID_CAPTION)
    return m if m in allowed else DEFAULT_GRID_CAPTION


def normalize_content_mode(mode: str | None) -> str:
    allowed = {k for k, _ in CONTENT_MODES}
    m = str(mode or DEFAULT_CONTENT_MODE)
    m = LEGACY_CONTENT_MODES.get(m, m)
    return m if m in allowed else DEFAULT_CONTENT_MODE


def format_grid_caption(
    item: dict[str, Any],
    *,
    mode: str,
    group_uid: str = "",
    unified: bool = False,
    catalog_counts: dict[str, int] | None = None,
    abbrev_fn: Callable[[str], str] | None = None,
    catalog_key_fn: Callable[[str], str] | None = None,
    effective_uid_fn: Callable[[str, dict[str, Any]], str] | None = None,
) -> str:
    """Build the text shown under a grid thumbnail."""
    from pathlib import Path as _Path

    from app.utils.naming import (
        uid_display_core,
        uid_display_core_storage,
        uid_display_station_species,
        uid_group_key,
    )

    mode = normalize_grid_caption_mode(mode)
    eff = effective_uid_fn or (lambda gu, it: str(gu or it.get("_uid") or it.get("uid") or ""))
    ckey = catalog_key_fn or uid_group_key

    item_uid = eff(str(group_uid or ""), item)
    name = str(item.get("name") or "")
    seq = item.get("seq")
    ck = ckey(item_uid)

    if not unified:
        if seq is not None:
            return f"#{seq}"
        return name

    if not item_uid or ck == "__ungrouped__":
        stem = _Path(name).stem if name else item_uid
        return stem or "未分组"

    if mode == "filename":
        return _Path(name).stem if name else uid_display_core(item_uid)
    if mode == "full_uid":
        return item_uid
    if mode == "core_storage":
        label = uid_display_core_storage(item_uid)
    elif mode == "station_species":
        label = uid_display_station_species(item_uid)
    elif mode == "core":
        label = uid_display_core(item_uid)
    else:
        label = uid_display_core(item_uid)

    if mode == "core_seq":
        if seq is not None:
            return f"{label} · #{seq}"
        return label

    if mode != "smart":
        return label

    dup = int((catalog_counts or {}).get(ck, 1))
    if dup > 1:
        if seq is not None:
            return f"{label} · #{seq}"
        if name:
            return _Path(name).stem
    return label


def _seq_sort_key(seq: Any) -> tuple:
    if seq is None:
        return (1, 0)
    try:
        return (0, int(seq))
    except (TypeError, ValueError):
        return (1, 0)


def sort_flat_grid_items(items: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Sort flattened grid rows (unified mode)."""
    mode = normalize_grid_sort(mode)
    rows = list(items or [])
    if mode == "filename":
        return sorted(rows, key=lambda x: (str(x.get("name") or "").lower(), str(x.get("path") or "")))
    if mode == "mtime_desc":
        return sorted(
            rows,
            key=lambda x: (str(x.get("mtime") or ""), str(x.get("name") or "")),
            reverse=True,
        )
    if mode == "mtime_asc":
        return sorted(
            rows,
            key=lambda x: (str(x.get("mtime") or ""), str(x.get("name") or "")),
        )
    # uid_seq — 未分组放最后，再按 UID、片内序号
    return sorted(
        rows,
        key=lambda x: (
            not bool(str(x.get("_uid") or x.get("uid") or "")),
            str(x.get("_uid") or x.get("uid") or ""),
            _seq_sort_key(x.get("seq")),
            str(x.get("name") or ""),
        ),
    )


def sort_result_groups(groups: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Sort grouped payload (segmented / 汇总 mode)."""
    mode = normalize_grid_sort(mode)
    out: list[dict[str, Any]] = []
    for g in groups or []:
        uid = str(g.get("uid") or "")
        items = list(g.get("items") or [])
        if mode == "filename":
            items.sort(key=lambda x: (str(x.get("name") or "").lower(), str(x.get("path") or "")))
        elif mode == "mtime_desc":
            items.sort(
                key=lambda x: (str(x.get("mtime") or ""), str(x.get("name") or "")),
                reverse=True,
            )
        elif mode == "mtime_asc":
            items.sort(key=lambda x: (str(x.get("mtime") or ""), str(x.get("name") or "")))
        else:
            items.sort(
                key=lambda x: (
                    _seq_sort_key(x.get("seq")),
                    str(x.get("name") or ""),
                )
            )
        out.append({"uid": uid, "items": items})
    if mode == "filename":
        out.sort(
            key=lambda g: (
                not bool(g["uid"]),
                min((str(i.get("name") or "").lower() for i in g["items"]), default=""),
                g["uid"],
            )
        )
    elif mode in {"mtime_desc", "mtime_asc"}:
        reverse = mode == "mtime_desc"
        out.sort(
            key=lambda g: (
                not bool(g["uid"]),
                max((str(i.get("mtime") or "") for i in g["items"]), default=""),
                g["uid"],
            ),
            reverse=reverse,
        )
    else:
        out.sort(key=lambda g: (not bool(g["uid"]), g["uid"]))
    return out


def split_stretch_factors() -> tuple[int, int, int]:
    return SPLIT_STRETCH_TREE, SPLIT_STRETCH_GRID, SPLIT_STRETCH_DETAIL


def split_minimum_widths() -> tuple[int, int, int]:
    return SPLIT_MIN_TREE, SPLIT_MIN_GRID, SPLIT_MIN_DETAIL


def compute_split_sizes(total_width: int | None = None) -> list[int]:
    """Initial pixel widths for tree | grid | detail from stretch ratios."""
    total = max(
        sum(split_minimum_widths()),
        int(total_width or SPLIT_DEFAULT_TOTAL),
    )
    mins = split_minimum_widths()
    stretches = split_stretch_factors()
    ratio_sum = sum(stretches)
    sizes = [max(mins[i], int(total * stretches[i] / ratio_sum)) for i in range(3)]
    overflow = sum(sizes) - total
    if overflow > 0:
        sizes[1] = max(mins[1], sizes[1] - overflow)
    return sizes


def compute_grid_inner_split_sizes(total_width: int | None = None) -> list[int]:
    """Initial pixel widths for 编号索引 | 照片区 inside the grid column."""
    total = max(
        GRID_INNER_UID_MIN + GRID_INNER_PHOTO_MIN,
        int(total_width or SPLIT_MIN_GRID),
    )
    uid = min(GRID_INNER_UID_DEFAULT, max(GRID_INNER_UID_MIN, total // 4))
    photo = max(GRID_INNER_PHOTO_MIN, total - uid)
    return [uid, photo]


def compute_summary_body_split_sizes(total_height: int | None = None) -> list[int]:
    """Initial pixel heights for 编号表 | 成片区 inside data-summary column."""
    total = max(
        SUMMARY_BODY_TABLE_MIN + SUMMARY_BODY_PHOTO_MIN,
        int(total_height or SUMMARY_BODY_DEFAULT_HEIGHT),
    )
    table = min(
        max(SUMMARY_BODY_TABLE_MIN, SUMMARY_BODY_TABLE_DEFAULT),
        total - SUMMARY_BODY_PHOTO_MIN,
    )
    photo = max(SUMMARY_BODY_PHOTO_MIN, total - table)
    table = max(SUMMARY_BODY_TABLE_MIN, total - photo)
    return [table, photo]


def emphasize_grid_split_sizes(current_sizes: Sequence[int]) -> list[int]:
    """Give the middle column remaining width without changing the total."""
    if len(current_sizes) != 3:
        return list(compute_split_sizes())
    mins = split_minimum_widths()
    total = sum(int(x) for x in current_sizes)
    tree = max(mins[0], int(current_sizes[0]))
    detail = max(mins[2], int(current_sizes[2]))
    if tree + detail + mins[1] > total:
        total = tree + detail + mins[1]
    grid = max(mins[1], total - tree - detail)
    if tree + grid + detail > total:
        overflow = tree + grid + detail - total
        if tree - mins[0] >= overflow:
            tree -= overflow
        elif detail - mins[2] >= overflow:
            detail -= overflow
        else:
            grid = max(mins[1], total - tree - detail)
    return [tree, grid, detail]


# 旧 API 别名
def thumb_slider_range() -> tuple[int, int]:
    return density_slider_range()
