"""项目封面图路径选取（可读可写，5 级 fallback 简化版）."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.utils.path_utils import normalize_path

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def pick_project_cover_path(project_dir: str) -> Optional[str]:
    """Return one image path for card/cover display, or None."""
    root = Path(normalize_path(project_dir))
    if not root.is_dir():
        return None

    # 1. user cover_image in project_settings (best-effort)
    manual = _read_manual_cover(root)
    if manual:
        return manual

    # 3. latest result tif (skip zip)
    for sub in (root / "results", root / "results" / "freeform"):
        hit = _latest_image(sub, {".tif", ".tiff"})
        if hit:
            return hit

    # 4. incoming jpg
    for sub in (root / "incoming-jpg", root / "新拍JPG"):
        hit = _latest_image(sub, {".jpg", ".jpeg"})
        if hit:
            return hit

    # shallow scan at root (legacy loose files)
    return _latest_image(root, _IMAGE_EXTS, max_depth=2)


def set_project_cover_path(project_dir: str, image_path: str) -> str:
    """Persist a manual cover into project_meta.cover_image (relative if under project).

    Returns the stored path string. Raises ValueError / OSError on failure.
    """
    root = Path(normalize_path(project_dir))
    if not root.is_dir():
        raise ValueError("项目目录不存在")
    src = Path(image_path).expanduser().resolve()
    if not src.is_file():
        raise ValueError("封面文件不存在")
    if src.suffix.lower() not in _IMAGE_EXTS:
        raise ValueError("请选择 JPG / PNG / TIFF 图片")
    try:
        stored = str(src.relative_to(root.resolve()))
    except ValueError:
        stored = str(src)
    _write_manual_cover(root, stored)
    return stored


def clear_project_cover_path(project_dir: str) -> None:
    """Remove manual cover; next pick falls back to auto."""
    root = Path(normalize_path(project_dir))
    if not root.is_dir():
        return
    _write_manual_cover(root, "")


def _read_manual_cover(root: Path) -> Optional[str]:
    try:
        # from app.db.db_manager import open_project_db  # §7 旧: 卡片网格读外部项目封面 → 缓存连接锁泄漏
        from app.db.db_manager import open_project_db_private
        from app.services import project_settings_service as pss

        # db = open_project_db(str(root), create=False)  # §7 旧
        db = open_project_db_private(str(root), create=False)
        try:
            meta = pss.load_setting(db, "project_meta", pss.DEFAULT_PROJECT_META)
        finally:
            db.close()
        cover = str(meta.get("cover_image") or meta.get("coverImage") or "").strip()
        if not cover:
            return None
        p = Path(cover)
        if not p.is_absolute():
            p = root / cover
        if p.is_file():
            return str(p.resolve())
    except Exception:
        return None
    return None


def _write_manual_cover(root: Path, stored: str) -> None:
    from app.db.db_manager import open_project_db
    from app.services import project_settings_service as pss

    db = open_project_db(str(root), create=True)
    meta = dict(pss.load_setting(db, "project_meta", pss.DEFAULT_PROJECT_META))
    if stored:
        meta["cover_image"] = stored
    else:
        meta.pop("cover_image", None)
        meta.pop("coverImage", None)
    pss.save_setting(db, "project_meta", meta)


def _latest_image(
    directory: Path,
    exts: set[str],
    *,
    max_depth: int = 1,
) -> Optional[str]:
    if not directory.is_dir():
        return None
    best: tuple[float, str] | None = None
    queue: list[tuple[Path, int]] = [(directory, 0)]
    while queue:
        current, depth = queue.pop(0)
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir():
                    if depth + 1 < max_depth:
                        queue.append((entry, depth + 1))
                    continue
            except OSError:
                continue
            if entry.suffix.lower() not in exts:
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0.0
            path = str(entry.resolve())
            if best is None or mtime > best[0]:
                best = (mtime, path)
    return best[1] if best else None
