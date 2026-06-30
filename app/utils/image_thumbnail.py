"""Small, safe image thumbnail decoding helpers for Qt widgets."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImageReader, QPixmap

_THUMB_CACHE_LIMIT = 384
_CACHE_MISS = object()
_CACHE_NEGATIVE = object()
_THUMB_CACHE: "OrderedDict[tuple[str, int, int, int], object]" = OrderedDict()


def clear_thumbnail_cache(path: str | None = None) -> None:
    """Clear decoded thumbnail cache.

    Passing a path clears all cached sizes for that file.  ``None`` clears the
    whole cache, useful for tests or after bulk image edits.
    """
    if path is None:
        _THUMB_CACHE.clear()
        return
    try:
        resolved = str(Path(path).resolve())
    except OSError:
        resolved = str(path)
    for key in list(_THUMB_CACHE):
        if key[0] == resolved:
            _THUMB_CACHE.pop(key, None)


def _cache_key(path: str, max_size: int) -> tuple[str, int, int, int] | None:
    try:
        p = Path(path)
        st = p.stat()
        return (str(p.resolve()), int(st.st_mtime_ns), int(st.st_size), int(max_size))
    except Exception:
        return None


def _cache_get(key):
    if key is None or key not in _THUMB_CACHE:
        return _CACHE_MISS
    _THUMB_CACHE.move_to_end(key)
    cached = _THUMB_CACHE[key]
    if cached is _CACHE_NEGATIVE:
        return None
    return QPixmap(cached)


def _cache_put(key, pixmap: Optional[QPixmap]) -> None:
    if key is None:
        return
    _THUMB_CACHE[key] = QPixmap(pixmap) if pixmap is not None else _CACHE_NEGATIVE
    _THUMB_CACHE.move_to_end(key)
    while len(_THUMB_CACHE) > _THUMB_CACHE_LIMIT:
        _THUMB_CACHE.popitem(last=False)


def decode_image_thumbnail(
    path: str,
    max_size: int = 280,
    *,
    use_cache: bool = True,
) -> Optional[QPixmap]:
    """Decode an image file to a bounded QPixmap thumbnail.

    Qt handles ordinary JPG quickly.  PIL covers many TIFF variants that Qt
    cannot decode.  tifffile is optional and only used as a final fallback.
    """
    if not path:
        return None
    try:
        if not os.path.exists(path):
            return None
    except Exception:
        return None

    key = _cache_key(path, max_size)
    if use_cache:
        cached = _cache_get(key)
        if cached is not _CACHE_MISS:
            return cached

    pm = _decode_with_qt(path, max_size)
    if pm is not None:
        if use_cache:
            _cache_put(key, pm)
        return pm

    pm = _decode_with_pillow(path, max_size)
    if pm is not None:
        if use_cache:
            _cache_put(key, pm)
        return pm

    pm = _decode_with_tifffile(path, max_size)
    if pm is not None:
        if use_cache:
            _cache_put(key, pm)
        return pm

    pm = _decode_with_imagemagick(path, max_size)
    if use_cache:
        _cache_put(key, pm)
    return pm


def _decode_with_qt(path: str, max_size: int) -> Optional[QPixmap]:
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            size.scale(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)
        image = reader.read()
        if image.isNull():
            return None
        return QPixmap.fromImage(image).scaled(
            max_size,
            max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None


def _decode_with_pillow(path: str, max_size: int) -> Optional[QPixmap]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.seek(0)
            image.thumbnail((max_size, max_size))
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                image.save(tmp_path)
                pm = QPixmap(tmp_path)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        if pm.isNull():
            return None
        return pm.scaled(
            max_size,
            max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None


def _decode_with_tifffile(path: str, max_size: int) -> Optional[QPixmap]:
    if Path(path).suffix.lower() not in {".tif", ".tiff"}:
        return None
    try:
        import numpy as np
        import tifffile
        from PIL import Image

        with redirect_stderr(StringIO()):
            arr = tifffile.imread(path)
        arr = np.asarray(arr)
        if arr.size == 0:
            return None
        arr = np.squeeze(arr)
        if arr.ndim > 3:
            arr = arr[0]
        if arr.ndim == 3 and arr.shape[0] in {3, 4} and arr.shape[-1] not in {3, 4}:
            arr = np.moveaxis(arr, 0, -1)
        if arr.ndim not in {2, 3}:
            return None
        if arr.dtype != np.uint8:
            arr = np.nan_to_num(arr.astype("float32", copy=False))
            amin = float(arr.min())
            amax = float(arr.max())
            if amax > amin:
                arr = (arr - amin) * (255.0 / (amax - amin))
            arr = np.clip(arr, 0, 255).astype("uint8")
        image = Image.fromarray(arr)
        image.thumbnail((max_size, max_size))
        if image.mode not in {"RGB", "RGBA", "L"}:
            image = image.convert("RGBA")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            image.save(tmp_path)
            pm = QPixmap(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if pm.isNull():
            return None
        return pm.scaled(
            max_size,
            max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None


def _decode_with_imagemagick(path: str, max_size: int) -> Optional[QPixmap]:
    exe = shutil.which("magick") or shutil.which("convert")
    if not exe:
        return None
    input_path = f"{path}[0]" if Path(path).suffix.lower() in {".tif", ".tiff"} else path
    cmd = [
        exe,
        input_path,
        "-auto-orient",
        "-thumbnail",
        f"{max_size}x{max_size}",
        "png:-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=12,
            check=False,
        )
        if not proc.stdout:
            return None
        pm = QPixmap()
        if not pm.loadFromData(proc.stdout, "PNG") or pm.isNull():
            return None
        return pm.scaled(
            max_size,
            max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None
