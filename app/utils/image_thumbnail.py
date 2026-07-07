"""Small, safe image thumbnail decoding helpers for Qt widgets.

2026-07-08 (spec survey-summary-view §5): split into a thread-safe QImage path
(``decode_image_data``) and a main-thread pixmap bridge (``make_pixmap``).
Backends now return ``QImage`` (reference-counted pixel buffer, safe off the GUI
thread). Workers emit ``QImage``; the main thread converts via ``make_pixmap``.
Legacy ``decode_image_thumbnail`` / ``decode_image_pixmap`` keep returning
``QPixmap`` for existing callers (project_tree_view, monitor panels) via the
new bridge.

【§7 编码约定】原 QPixmap 返回路径的旧实现已用 `#` 注释保留在文末归档区,
新实现(QImage)在前。改既有代码不删旧体。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections import OrderedDict
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QImageReader, QPixmap

_THUMB_CACHE_LIMIT = 384
_CACHE_MISS = object()
_CACHE_NEGATIVE = object()
# Cache now stores QImage (thread-safe). Key: (resolved_path, mtime_ns, size, max_size).
_THUMB_CACHE: "OrderedDict[tuple, object]" = OrderedDict()


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


def _cache_key(path: str, max_size: int | None) -> tuple[str, int, int, int] | None:
    try:
        p = Path(path)
        st = p.stat()
        size_key = -1 if max_size is None else int(max_size)
        return (str(p.resolve()), int(st.st_mtime_ns), int(st.st_size), size_key)
    except Exception:
        return None


def _cache_get(key):
    """Return an independent ``QImage`` copy (cache stores copies; mutating the
    returned handle detaches via copy-on-write and never poisons the cache)."""
    if key is None or key not in _THUMB_CACHE:
        return _CACHE_MISS
    _THUMB_CACHE.move_to_end(key)
    cached = _THUMB_CACHE[key]
    if cached is _CACHE_NEGATIVE:
        return None
    return QImage(cached)  # copy -> distinct handle, same pixels


def _cache_put(key, image: Optional[QImage]) -> None:
    if key is None:
        return
    _THUMB_CACHE[key] = QImage(image) if image is not None else _CACHE_NEGATIVE
    _THUMB_CACHE.move_to_end(key)
    while len(_THUMB_CACHE) > _THUMB_CACHE_LIMIT:
        _THUMB_CACHE.popitem(last=False)


# ── New thread-safe public API (spec §5) ───────────────────────────────────────

def decode_image_data(
    path: str,
    max_size: int = 280,
    *,
    use_cache: bool = True,
) -> Optional[QImage]:
    """Decode an image file to a bounded ``QImage`` (thread-safe).

    Workers call this. Returns ``None`` for missing/undecodable files. The
    returned handle is independent of the cache; mutating it is safe.
    """
    return _decode_image(path, max(1, int(max_size)) if max_size is not None else None, use_cache=use_cache)


def make_pixmap(image: Optional[QImage]) -> Optional[QPixmap]:
    """Main-thread only: bridge ``QImage`` -> ``QPixmap``. ``None``-safe."""
    if image is None or image.isNull():
        return None
    return QPixmap.fromImage(image)


# ── Legacy QPixmap API (back-compat for existing callers) ─────────────────────

def decode_image_thumbnail(
    path: str,
    max_size: int = 280,
    *,
    use_cache: bool = True,
) -> Optional[QPixmap]:
    """Decode to a bounded ``QPixmap`` thumbnail (main thread).

    Qt handles ordinary JPG quickly.  PIL covers many TIFF variants that Qt
    cannot decode.  tifffile is optional and only used as a final fallback.
    """
    return make_pixmap(decode_image_data(path, max_size, use_cache=use_cache))


def decode_image_pixmap(
    path: str,
    *,
    use_cache: bool = False,
) -> Optional[QPixmap]:
    """Decode at native resolution to a ``QPixmap`` (main thread).

    For deliberate large previews where 100% zoom means original pixels.
    """
    return make_pixmap(decode_image_data(path, None, use_cache=use_cache))


# ── Internal decode chain (returns QImage) ────────────────────────────────────

def _decode_image(
    path: str,
    max_size: int | None,
    *,
    use_cache: bool,
) -> Optional[QImage]:
    if not path:
        return None
    try:
        from app.utils.path_utils import localize_path
        path = localize_path(path)
    except Exception:
        pass
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

    img = _decode_with_qt(path, max_size)
    if img is not None:
        if use_cache:
            _cache_put(key, img)
        return img

    img = _decode_with_pillow(path, max_size)
    if img is not None:
        if use_cache:
            _cache_put(key, img)
        return img

    img = _decode_with_tifffile(path, max_size)
    if img is not None:
        if use_cache:
            _cache_put(key, img)
        return img

    img = _decode_malformed_lzw_tiff(path, max_size)
    if img is not None:
        if use_cache:
            _cache_put(key, img)
        return img

    img = _decode_with_imagemagick(path, max_size)
    if use_cache:
        _cache_put(key, img)
    return img


def _decode_with_qt(path: str, max_size: int | None) -> Optional[QImage]:
    try:
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if max_size is not None and size.isValid() and size.width() > 0 and size.height() > 0:
            size.scale(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size)
        image = reader.read()
        if image.isNull():
            return None
        return image
    except Exception:
        return None


def _pil_image_to_qimage(image, max_size: int | None) -> Optional[QImage]:
    """PIL Image -> QImage via numpy (no tempfile roundtrip)."""
    import numpy as np

    if max_size is not None:
        image.thumbnail((max_size, max_size))
    if image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGBA")
    arr = np.asarray(image)
    if arr.ndim == 2:
        fmt = QImage.Format.Format_Grayscale8
        stride = int(arr.shape[1])
    else:
        channels = arr.shape[2]
        fmt = {
            3: QImage.Format.Format_RGB888,
            4: QImage.Format.Format_RGBA8888,
        }.get(channels)
        if fmt is None:
            return None
        stride = int(arr.shape[1] * channels)
    out = QImage(arr.tobytes(), int(arr.shape[1]), int(arr.shape[0]), stride, fmt)
    return out.copy()  # detach from the numpy buffer


def _decode_with_pillow(path: str, max_size: int | None) -> Optional[QImage]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.seek(0)
            return _pil_image_to_qimage(image, max_size)
    except Exception:
        return None


def _decode_with_tifffile(path: str, max_size: int | None) -> Optional[QImage]:
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
        return _pil_image_to_qimage(Image.fromarray(arr), max_size)
    except Exception:
        return None


def _decode_malformed_lzw_tiff(path: str, max_size: int | None) -> Optional[QImage]:
    """Decode baseline RGB LZW TIFFs whose IFD confuses Pillow/tifffile.

    Some legacy Helicon outputs expose valid strip offsets/byte counts, but
    high-level readers fail.  Only handles simple chunky RGB/RGBA 8-bit LZW
    strips; returns None otherwise.
    """
    if Path(path).suffix.lower() not in {".tif", ".tiff"}:
        return None
    try:
        import imagecodecs
        import numpy as np
        import tifffile
        from PIL import Image

        with tifffile.TiffFile(str(path)) as tf:
            page = tf.pages[0]
            tags = page.tags

            def tag_value(code: int, default=None):
                tag = tags.get(code)
                return tag.value if tag is not None else default

            width = int(tag_value(256, 0) or 0)
            height = int(tag_value(257, 0) or 0)
            bits = tag_value(258, ())
            if isinstance(bits, int):
                bits = (bits,)
            compression = int(tag_value(259, 0) or 0)
            photometric = int(tag_value(262, 0) or 0)
            offsets = tuple(tag_value(273, ()) or ())
            samples = int(tag_value(277, 1) or 1)
            rows_per_strip = int(tag_value(278, height) or height)
            bytecounts = tuple(tag_value(279, ()) or ())
            planar = int(tag_value(284, 1) or 1)
            predictor = int(tag_value(317, 1) or 1)

        if (
            width <= 0
            or height <= 0
            or compression != 5
            or photometric != 2
            or samples not in {3, 4}
            or planar != 1
            or not offsets
            or len(offsets) != len(bytecounts)
            or any(int(b) != 8 for b in bits)
        ):
            return None

        data = Path(path).read_bytes()
        arr = np.empty((height, width, samples), dtype=np.uint8)
        row = 0
        for offset, bytecount in zip(offsets, bytecounts):
            rows = min(rows_per_strip, height - row)
            if rows <= 0:
                break
            raw = data[int(offset): int(offset) + int(bytecount)]
            decoded = imagecodecs.lzw_decode(raw)
            expected = rows * width * samples
            if len(decoded) < expected:
                return None
            strip = np.frombuffer(decoded[:expected], dtype=np.uint8).reshape(
                rows, width, samples
            )
            if predictor == 2:
                strip = (np.cumsum(strip.astype(np.uint16), axis=1) & 0xFF).astype(
                    np.uint8
                )
            elif predictor != 1:
                return None
            arr[row: row + rows] = strip
            row += rows
        if row < height:
            return None
        mode = "RGBA" if samples == 4 else "RGB"
        return _pil_image_to_qimage(Image.fromarray(arr, mode), max_size)
    except Exception:
        return None


def _decode_with_imagemagick(path: str, max_size: int | None) -> Optional[QImage]:
    exe = shutil.which("magick") or shutil.which("convert")
    if not exe:
        return None
    input_path = f"{path}[0]" if Path(path).suffix.lower() in {".tif", ".tiff"} else path
    cmd = [
        exe,
        input_path,
        "-auto-orient",
    ]
    if max_size is not None:
        cmd.extend(["-thumbnail", f"{max_size}x{max_size}"])
    cmd.append("png:-")
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
        img = QImage()
        if not img.loadFromData(proc.stdout, "PNG") or img.isNull():
            return None
        return img
    except Exception:
        return None


# =============================================================================
# 【§7 归档】原 QPixmap 返回路径旧实现 —— 注释保留,勿动(新实现见上)。
# 2026-07-08 spec §5 重构为 QImage;旧体保留供回溯/回滚。
# =============================================================================
#
# def _cache_get(key):
#     if key is None or key not in _THUMB_CACHE:
#         return _CACHE_MISS
#     _THUMB_CACHE.move_to_end(key)
#     cached = _THUMB_CACHE[key]
#     if cached is _CACHE_NEGATIVE:
#         return None
#     return QPixmap(cached)
#
# def _cache_put(key, pixmap: Optional[QPixmap]) -> None:
#     if key is None:
#         return
#     _THUMB_CACHE[key] = QPixmap(pixmap) if pixmap is not None else _CACHE_NEGATIVE
#     _THUMB_CACHE.move_to_end(key)
#     while len(_THUMB_CACHE) > _THUMB_CACHE_LIMIT:
#         _THUMB_CACHE.popitem(last=False)
#
# def _decode_image(path, max_size, *, use_cache) -> Optional[QPixmap]:
#     if not path:
#         return None
#     try:
#         from app.utils.path_utils import localize_path
#         path = localize_path(path)
#     except Exception:
#         pass
#     try:
#         if not os.path.exists(path):
#             return None
#     except Exception:
#         return None
#     key = _cache_key(path, max_size)
#     if use_cache:
#         cached = _cache_get(key)
#         if cached is not _CACHE_MISS:
#             return cached
#     pm = _decode_with_qt(path, max_size)
#     if pm is not None:
#         if use_cache: _cache_put(key, pm)
#         return pm
#     pm = _decode_with_pillow(path, max_size)
#     if pm is not None:
#         if use_cache: _cache_put(key, pm)
#         return pm
#     pm = _decode_with_tifffile(path, max_size)
#     if pm is not None:
#         if use_cache: _cache_put(key, pm)
#         return pm
#     pm = _decode_malformed_lzw_tiff(path, max_size)
#     if pm is not None:
#         if use_cache: _cache_put(key, pm)
#         return pm
#     pm = _decode_with_imagemagick(path, max_size)
#     if use_cache: _cache_put(key, pm)
#     return pm
#
# def _decode_with_qt(path, max_size) -> Optional[QPixmap]:
#     try:
#         reader = QImageReader(path)
#         reader.setAutoTransform(True)
#         size = reader.size()
#         if max_size is not None and size.isValid() and size.width() > 0 and size.height() > 0:
#             size.scale(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio)
#             reader.setScaledSize(size)
#         image = reader.read()
#         if image.isNull():
#             return None
#         pixmap = QPixmap.fromImage(image)
#         return pixmap
#     except Exception:
#         return None
#
# def _pil_image_to_pixmap(image, max_size) -> Optional[QPixmap]:
#     if max_size is not None:
#         image.thumbnail((max_size, max_size))
#     if image.mode not in {"RGB", "RGBA", "L"}:
#         image = image.convert("RGBA")
#     import tempfile
#     with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
#         tmp_path = tmp.name
#     try:
#         image.save(tmp_path)
#         pm = QPixmap(tmp_path)
#     finally:
#         try: os.unlink(tmp_path)
#         except OSError: pass
#     if pm.isNull():
#         return None
#     if max_size is None:
#         return pm
#     return pm.scaled(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio,
#                      Qt.TransformationMode.SmoothTransformation)
#
# def _decode_with_pillow(path, max_size) -> Optional[QPixmap]:
#     try:
#         from PIL import Image
#         with Image.open(path) as image:
#             image.seek(0)
#             return _pil_image_to_pixmap(image, max_size)
#     except Exception:
#         return None
#
# def _decode_with_tifffile(path, max_size) -> Optional[QPixmap]:
#     if Path(path).suffix.lower() not in {".tif", ".tiff"}:
#         return None
#     try:
#         import numpy as np, tifffile
#         from PIL import Image
#         with redirect_stderr(StringIO()):
#             arr = tifffile.imread(path)
#         arr = np.asarray(arr)
#         if arr.size == 0: return None
#         arr = np.squeeze(arr)
#         if arr.ndim > 3: arr = arr[0]
#         if arr.ndim == 3 and arr.shape[0] in {3, 4} and arr.shape[-1] not in {3, 4}:
#             arr = np.moveaxis(arr, 0, -1)
#         if arr.ndim not in {2, 3}: return None
#         if arr.dtype != np.uint8:
#             arr = np.nan_to_num(arr.astype("float32", copy=False))
#             amin = float(arr.min()); amax = float(arr.max())
#             if amax > amin: arr = (arr - amin) * (255.0 / (amax - amin))
#             arr = np.clip(arr, 0, 255).astype("uint8")
#         return _pil_image_to_pixmap(Image.fromarray(arr), max_size)
#     except Exception:
#         return None
#
# def _decode_with_imagemagick(path, max_size) -> Optional[QPixmap]:
#     exe = shutil.which("magick") or shutil.which("convert")
#     if not exe: return None
#     input_path = f"{path}[0]" if Path(path).suffix.lower() in {".tif", ".tiff"} else path
#     cmd = [exe, input_path, "-auto-orient"]
#     if max_size is not None:
#         cmd.extend(["-thumbnail", f"{max_size}x{max_size}"])
#     cmd.append("png:-")
#     try:
#         proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
#                               timeout=12, check=False)
#         if not proc.stdout: return None
#         pm = QPixmap()
#         if not pm.loadFromData(proc.stdout, "PNG") or pm.isNull(): return None
#         if max_size is None: return pm
#         return pm.scaled(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio,
#                          Qt.TransformationMode.SmoothTransformation)
#     except Exception:
#         return None
