"""Persistent on-disk thumbnail cache — survives app restarts.

Grid / monitor thumbnails are stored as JPEG under ``data/cache/thumbnails/``.
Keys incorporate source path + mtime + size + normalized decode bucket so
re-synthesis or file edits invalidate automatically.
"""
from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtGui import QImage

_CACHE_ROOT_OVERRIDE: Path | None = None
_LOCK = threading.Lock()

# Match memory cache buckets in image_thumbnail.py
CACHE_SIZE_BUCKETS: tuple[int, ...] = (64, 96, 128, 160, 256, 384, 512, 720, 1080, 1440, 1600)

# TIFF 只解码一次，存这个尺寸的 JPG 主图；网格密度变化时只做缩放
TIFF_PREVIEW_MASTER_SIZE = 720


def normalize_thumb_cache_size(max_size: int | None) -> int | None:
    if max_size is None:
        return None
    ms = max(1, int(max_size))
    for bucket in CACHE_SIZE_BUCKETS:
        if bucket >= ms:
            return bucket
    return CACHE_SIZE_BUCKETS[-1]


def set_cache_root_for_tests(root: Path | None) -> None:
    global _CACHE_ROOT_OVERRIDE
    _CACHE_ROOT_OVERRIDE = root


def thumb_cache_root() -> Path:
    if _CACHE_ROOT_OVERRIDE is not None:
        root = _CACHE_ROOT_OVERRIDE
    else:
        root = Path(__file__).resolve().parents[2] / "data" / "cache" / "thumbnails"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _file_stat_key(path: str) -> tuple[str, int, int] | None:
    try:
        p = Path(path)
        st = p.stat()
        return str(p.resolve()), int(st.st_mtime_ns), int(st.st_size)
    except OSError:
        return None


def cache_entry_hash(resolved: str, mtime_ns: int, file_size: int, bucket: int) -> str:
    raw = f"{resolved}\0{mtime_ns}\0{file_size}\0{bucket}".encode("utf-8", "surrogateescape")
    return hashlib.sha256(raw).hexdigest()


def _cache_paths(digest: str) -> tuple[Path, Path]:
    root = thumb_cache_root()
    sub = root / digest[:2] / digest[2:4]
    return sub / f"{digest}.jpg", sub / f"{digest}.fail"


def preview_jpeg_file_path(path: str, max_size: int | None) -> Optional[Path]:
    """Return on-disk JPEG proxy path when a valid preview already exists."""
    stat = _file_stat_key(path)
    if stat is None:
        return None
    resolved, mtime_ns, file_size = stat
    bucket = normalize_thumb_cache_size(max_size)
    if bucket is None:
        return None
    digest = cache_entry_hash(resolved, mtime_ns, file_size, bucket)
    jpg_path, fail_path = _cache_paths(digest)
    if fail_path.is_file() or not jpg_path.is_file():
        return None
    return jpg_path


def read_disk_thumbnail(path: str, max_size: int | None) -> Optional[QImage]:
    """Load a cached JPEG thumbnail if present and still valid."""
    stat = _file_stat_key(path)
    if stat is None:
        return None
    resolved, mtime_ns, file_size = stat
    bucket = normalize_thumb_cache_size(max_size)
    if bucket is None:
        return None
    digest = cache_entry_hash(resolved, mtime_ns, file_size, bucket)
    jpg_path, fail_path = _cache_paths(digest)
    if fail_path.is_file():
        return None
    if not jpg_path.is_file():
        return None
    try:
        img = QImage(str(jpg_path))
        if img.isNull():
            return None
        return img
    except Exception:
        return None


def write_disk_thumbnail(path: str, max_size: int | None, image: QImage) -> None:
    """Persist a decoded thumbnail (best-effort, never raises)."""
    if image is None or image.isNull():
        return
    stat = _file_stat_key(path)
    if stat is None:
        return
    resolved, mtime_ns, file_size = stat
    bucket = normalize_thumb_cache_size(max_size)
    if bucket is None:
        return
    digest = cache_entry_hash(resolved, mtime_ns, file_size, bucket)
    jpg_path, fail_path = _cache_paths(digest)
    try:
        with _LOCK:
            if fail_path.is_file():
                fail_path.unlink(missing_ok=True)
            jpg_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = jpg_path.with_suffix(".jpg.part")
            from app.config.preview_profile import current_preview_jpeg_quality

            quality = current_preview_jpeg_quality()
            if not image.save(str(tmp), "JPEG", quality):
                tmp.unlink(missing_ok=True)
                return
            os.replace(tmp, jpg_path)
    except Exception:
        pass


def mark_disk_thumbnail_failed(path: str, max_size: int | None) -> None:
    stat = _file_stat_key(path)
    if stat is None:
        return
    resolved, mtime_ns, file_size = stat
    bucket = normalize_thumb_cache_size(max_size)
    if bucket is None:
        return
    digest = cache_entry_hash(resolved, mtime_ns, file_size, bucket)
    jpg_path, fail_path = _cache_paths(digest)
    try:
        with _LOCK:
            jpg_path.unlink(missing_ok=True)
            fail_path.parent.mkdir(parents=True, exist_ok=True)
            fail_path.touch()
    except Exception:
        pass


def clear_disk_thumbnail_cache(path: str | None = None) -> None:
    """Drop disk cache entries. ``None`` wipes the whole tree."""
    root = thumb_cache_root()
    if path is None:
        if _CACHE_ROOT_OVERRIDE is not None:
            import shutil
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
        else:
            import shutil
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
        return
    stat = _file_stat_key(path)
    if stat is None:
        return
    resolved, mtime_ns, file_size = stat
    for bucket in CACHE_SIZE_BUCKETS:
        digest = cache_entry_hash(resolved, mtime_ns, file_size, bucket)
        jpg_path, fail_path = _cache_paths(digest)
        try:
            jpg_path.unlink(missing_ok=True)
            fail_path.unlink(missing_ok=True)
        except Exception:
            pass
