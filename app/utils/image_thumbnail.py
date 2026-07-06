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


def _cache_key(path: str, max_size: int | None) -> tuple[str, int, int, int] | None:
    try:
        p = Path(path)
        st = p.stat()
        size_key = -1 if max_size is None else int(max_size)
        return (str(p.resolve()), int(st.st_mtime_ns), int(st.st_size), size_key)
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
    return _decode_image(path, max(1, int(max_size)), use_cache=use_cache)


def decode_image_pixmap(
    path: str,
    *,
    use_cache: bool = False,
) -> Optional[QPixmap]:
    """Decode an image file at native resolution.

    This is for deliberate large previews where 100% zoom should mean the
    original image pixels.  It reuses the thumbnail decoder backends but does
    not downsample the image while loading.
    """
    return _decode_image(path, None, use_cache=use_cache)


def _decode_image(
    path: str,
    max_size: int | None,
    *,
    use_cache: bool,
) -> Optional[QPixmap]:
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

    pm = _decode_malformed_lzw_tiff(path, max_size)
    if pm is not None:
        if use_cache:
            _cache_put(key, pm)
        return pm

    pm = _decode_with_imagemagick(path, max_size)
    if use_cache:
        _cache_put(key, pm)
    return pm


def _decode_with_qt(path: str, max_size: int | None) -> Optional[QPixmap]:
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
        pixmap = QPixmap.fromImage(image)
        return pixmap
    except Exception:
        return None


def _pil_image_to_pixmap(image, max_size: int | None) -> Optional[QPixmap]:
    if max_size is not None:
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
    if max_size is None:
        return pm
    return pm.scaled(
        max_size,
        max_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _decode_with_pillow(path: str, max_size: int | None) -> Optional[QPixmap]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.seek(0)
            return _pil_image_to_pixmap(image, max_size)
    except Exception:
        return None


def _decode_with_tifffile(path: str, max_size: int | None) -> Optional[QPixmap]:
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
        return _pil_image_to_pixmap(Image.fromarray(arr), max_size)
    except Exception:
        return None


def _decode_malformed_lzw_tiff(path: str, max_size: int | None) -> Optional[QPixmap]:
    """Decode baseline RGB LZW TIFFs whose IFD confuses Pillow/tifffile.

    Some legacy Helicon outputs in the user's results folder expose valid
    strip offsets/byte counts, but high-level readers fail.  This fallback only
    handles simple chunky RGB/RGBA 8-bit LZW strips and returns None for
    anything else.
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
        return _pil_image_to_pixmap(Image.fromarray(arr, mode), max_size)
    except Exception:
        return None


def _decode_with_imagemagick(path: str, max_size: int | None) -> Optional[QPixmap]:
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
        pm = QPixmap()
        if not pm.loadFromData(proc.stdout, "PNG") or pm.isNull():
            return None
        if max_size is None:
            return pm
        return pm.scaled(
            max_size,
            max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    except Exception:
        return None
