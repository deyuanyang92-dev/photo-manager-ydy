"""Preview master-size profile — synced from AppSettings at runtime."""
from __future__ import annotations

from app.config.project_tree_layout import (
    DEFAULT_PREVIEW_JPEG_QUALITY,
    DEFAULT_PREVIEW_MASTER_SIZE,
    clamp_preview_master_size,
)

_current: int = DEFAULT_PREVIEW_MASTER_SIZE


def current_preview_master_size() -> int:
    return _current


def set_preview_master_size(size: int) -> int:
    global _current
    _current = clamp_preview_master_size(size)
    return _current


def reset_preview_master_size() -> int:
    return set_preview_master_size(DEFAULT_PREVIEW_MASTER_SIZE)


def current_preview_jpeg_quality() -> int:
    """TIFF 预览磁盘缓存 JPEG 质量（高清存档默认 95）。"""
    return max(1, min(100, int(DEFAULT_PREVIEW_JPEG_QUALITY)))
