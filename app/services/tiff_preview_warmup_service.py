"""tiff_preview_warmup_service.py — 数据汇总后台预热 TIFF 预览 JPG（只读母版）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from app.services.tiff_jpeg_export_service import (
    EXPORT_PRESETS,
    TiffJpegExportSettings,
)

TIFF_SUFFIXES = {".tif", ".tiff"}
CancelCallback = Callable[[], bool]


@dataclass
class WarmupResult:
    total: int = 0
    created: int = 0
    skipped: int = 0
    failed: int = 0
    paths_created: list[str] = field(default_factory=list)


def default_preview_export_settings() -> TiffJpegExportSettings:
    """数据汇总后台预览默认：高清存档（quality=95, subsampling=0）。"""
    for preset in EXPORT_PRESETS:
        if preset.preset_id == "archive":
            return preset.settings.clamped()
    return TiffJpegExportSettings(quality=95, max_long_edge=None, subsampling=0).clamped()


def _normalize_tif_path(path: str) -> Optional[str]:
    text = str(path or "").strip()
    if not text:
        return None
    try:
        from app.utils.path_utils import localize_path

        text = localize_path(text)
    except Exception:
        pass
    try:
        if not Path(text).is_file():
            return None
    except OSError:
        return None
    if Path(text).suffix.lower() not in TIFF_SUFFIXES:
        return None
    return str(Path(text).resolve())


def preview_cache_exists(path: str) -> bool:
    """磁盘预览 JPG 是否已存在（按当前 master 档位）。"""
    local = _normalize_tif_path(path)
    if not local:
        return False
    from app.config.preview_profile import current_preview_master_size
    from app.utils.thumbnail_disk_cache import preview_jpeg_file_path

    return preview_jpeg_file_path(local, current_preview_master_size()) is not None


def collect_tif_paths_from_summary(
    specimens: Iterable[dict],
    groups: Iterable[dict] | None = None,
) -> list[str]:
    """汇总范围内去重后的母版 TIF 绝对路径列表。"""
    from app.services.cross_workspace_query_service import _resolve_result_tif_path

    seen: set[str] = set()
    out: list[str] = []

    def _add(raw: str) -> None:
        local = _normalize_tif_path(raw)
        if not local or local in seen:
            return
        seen.add(local)
        out.append(local)

    for row in specimens or []:
        primary = str(row.get("photo_absolute_path") or "").strip()
        if primary:
            _add(primary)
        multi = str(row.get("result_tif_paths") or "").strip()
        if multi:
            for part in multi.split(";"):
                part = part.strip()
                if part:
                    _add(part)

    for group in groups or []:
        for item in group.get("items") or []:
            resolved = _resolve_result_tif_path(str(item.get("path") or ""))
            if resolved:
                _add(resolved)

    return out


def warmup_single_tif(path: str) -> bool:
    """确保单张 TIFF 有磁盘预览 JPG；已存在则跳过。成功返回 True。"""
    local = _normalize_tif_path(path)
    if not local:
        return False
    if preview_cache_exists(local):
        return True
    from app.utils.image_thumbnail import ensure_tiff_master_jpeg

    return ensure_tiff_master_jpeg(local) is not None


def warmup_tif_paths(
    paths: Iterable[str],
    *,
    cancel_callback: CancelCallback | None = None,
) -> WarmupResult:
    """顺序预热 TIFF 预览 JPG（后台线程调用，不阻塞 UI）。"""
    seen: set[str] = set()
    unique: list[str] = []
    for raw in paths or []:
        local = _normalize_tif_path(str(raw))
        if not local or local in seen:
            continue
        seen.add(local)
        unique.append(local)

    result = WarmupResult(total=len(unique))
    for path in unique:
        if cancel_callback is not None and cancel_callback():
            break
        if preview_cache_exists(path):
            result.skipped += 1
            continue
        if warmup_single_tif(path):
            result.created += 1
            result.paths_created.append(path)
        else:
            result.failed += 1
    return result
