"""Read camera EXIF from the **original master TIFF** (results/*.tif).

路径由程序按「工作区 + 标本编号」自动解析，无需用户手填。
读取顺序：母版 TIF IFD（Pillow → tifffile）→ 可选 exiftool → 内嵌 JPEG 兜底。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

_EXIF_IFD = 0x8769
_TAG_EXIF_POINTER = 34665
_TAG_JPEG_OFFSET = 513
_TAG_JPEG_LENGTH = 514

# TIFF IFD0 tags on the original file.
_TIFF_IFD0_NAMES = frozenset({"Make", "Model", "DateTime", "Software", "Artist"})

_EXIFTOOL_FIELDS = (
    ("Make", "camera_make"),
    ("Camera Make", "camera_make"),
    ("Model", "camera_model"),
    ("Camera Model Name", "camera_model"),
    ("LensModel", "lens_model"),
    ("Lens Model", "lens_model"),
    ("ExposureTime", "exposure_time"),
    ("Shutter Speed", "exposure_time"),
    ("FNumber", "f_number"),
    ("Aperture", "f_number"),
    ("ISO", "iso"),
    ("PhotographicSensitivity", "iso"),
    ("FocalLength", "focal_length"),
    ("Focal Length", "focal_length"),
    ("DateTimeOriginal", "exif_datetime"),
    ("Create Date", "exif_datetime"),
    ("ImageWidth", "image_width"),
    ("Exif Image Width", "image_width"),
    ("ImageHeight", "image_height"),
    ("Exif Image Height", "image_height"),
)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _tag_name(key: int, tags_map: dict[int, str]) -> str:
    return tags_map.get(key, str(key))


def _collect_exif_pairs(img) -> dict[str, Any]:
    """Merge IFD0 + Exif sub-IFD tag names → values on the opened image."""
    from PIL import ExifTags

    raw: dict[str, Any] = {}
    try:
        exif = img.getexif()
    except Exception:
        exif = None
    if not exif:
        return raw

    tag_sets: list[dict[int, Any]] = [dict(exif)]
    try:
        exif_ifd = exif.get_ifd(_EXIF_IFD)
        if exif_ifd:
            tag_sets.append(dict(exif_ifd))
    except Exception:
        pass

    tags = ExifTags.TAGS
    for block in tag_sets:
        for key, value in block.items():
            raw[_tag_name(int(key), tags)] = _json_safe(value)
    return raw


def _meta_from_raw_exif(raw_exif: dict[str, Any], *, width: Any = None, height: Any = None) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if width is not None:
        meta["image_width"] = int(width)
    if height is not None:
        meta["image_height"] = int(height)
    meta["camera_make"] = raw_exif.get("Make")
    meta["camera_model"] = raw_exif.get("Model")
    meta["lens_model"] = raw_exif.get("LensModel")
    meta["exposure_time"] = str(raw_exif.get("ExposureTime") or "")
    meta["f_number"] = str(raw_exif.get("FNumber") or "")
    meta["iso"] = str(
        raw_exif.get("ISOSpeedRatings")
        or raw_exif.get("PhotographicSensitivity")
        or raw_exif.get("ISO")
        or ""
    )
    meta["focal_length"] = str(raw_exif.get("FocalLength") or "")
    meta["exif_datetime"] = (
        raw_exif.get("DateTimeOriginal")
        or raw_exif.get("DateTimeDigitized")
        or raw_exif.get("DateTime")
    )
    meta["raw_exif_json"] = json.dumps(raw_exif, ensure_ascii=False)
    return meta


def _has_camera_exif(meta: dict[str, Any]) -> bool:
    if not meta:
        return False
    for key in ("camera_make", "camera_model", "exif_datetime", "iso", "exposure_time"):
        val = meta.get(key)
        if val not in (None, ""):
            return True
    return False


def _merge_meta(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base or {})
    for key, val in (extra or {}).items():
        if val in (None, ""):
            continue
        if key not in out or out.get(key) in (None, ""):
            out[key] = val
    return out


def _read_tifffile_original_tags(path: str) -> dict[str, Any]:
    """Read IFD0 tags from the original TIFF via tifffile (Pillow 失败时的补充)."""
    try:
        import tifffile
    except Exception:
        return {}

    raw: dict[str, Any] = {}
    width = height = None
    try:
        with tifffile.TiffFile(path) as tf:
            if not tf.pages:
                return {}
            page = tf.pages[0]
            tags = page.tags

            def _tag_val(code: int):
                tag = tags.get(code)
                return None if tag is None else tag.value

            width = _tag_val(256)
            height = _tag_val(257)
            for code, name in (
                (271, "Make"),
                (272, "Model"),
                (306, "DateTime"),
                (305, "Software"),
            ):
                val = _tag_val(code)
                if val not in (None, ""):
                    raw[name] = _json_safe(val)
    except Exception:
        return {}

    if not raw and width is None and height is None:
        return {}
    return _meta_from_raw_exif(raw, width=width, height=height)


def _read_exiftool_metadata(path: str) -> dict[str, Any]:
    """Optional runtime CLI — 与 cjxl/djxl 一样：有则读，无则跳过."""
    exe = shutil.which("exiftool")
    if not exe:
        return {}
    try:
        proc = subprocess.run(
            [exe, "-json", "-n", "-m", path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}
        payload = json.loads(proc.stdout)
        if not payload or not isinstance(payload[0], dict):
            return {}
        src = payload[0]
        raw: dict[str, Any] = {}
        meta: dict[str, Any] = {"raw_exif_json": json.dumps(src, ensure_ascii=False, default=str)}
        for src_key, dst_key in _EXIFTOOL_FIELDS:
            val = src.get(src_key)
            if val in (None, ""):
                continue
            if dst_key in {"image_width", "image_height"}:
                try:
                    meta[dst_key] = int(val)
                except (TypeError, ValueError):
                    continue
            elif dst_key not in meta or meta.get(dst_key) in (None, ""):
                meta[dst_key] = str(val)
            raw[src_key] = _json_safe(val)
        if raw:
            meta["raw_exif_json"] = json.dumps(raw, ensure_ascii=False)
        return meta
    except Exception:
        return {}


def _read_original_tiff_exif(path: str) -> dict[str, Any]:
    """Read EXIF / camera tags directly from the original TIFF master IFD."""
    try:
        from PIL import ExifTags, Image, TiffTags
    except Exception:
        return {}

    try:
        with Image.open(path) as img:
            try:
                img.seek(0)
            except Exception:
                pass
            width, height = img.width, img.height
            raw = _collect_exif_pairs(img)

            tv2 = getattr(img, "tag_v2", None) or {}
            tiff_tag_names = TiffTags.TAGS
            for code, value in tv2.items():
                name = tiff_tag_names.get(int(code))
                if name in _TIFF_IFD0_NAMES:
                    raw[name] = _json_safe(value)

            # 原始 TIFF 的 Exif 子 IFD（tag 34665 → 0x8769）
            try:
                exif = img.getexif()
                if exif and _TAG_EXIF_POINTER in exif:
                    exif_sub = exif.get_ifd(_EXIF_IFD)
                    exif_names = ExifTags.TAGS
                    for code, value in dict(exif_sub).items():
                        raw[exif_names.get(int(code), str(code))] = _json_safe(value)
            except Exception:
                pass

            if not raw:
                return {
                    "image_width": width,
                    "image_height": height,
                    "raw_exif_json": "{}",
                }
            return _meta_from_raw_exif(raw, width=width, height=height)
    except Exception:
        pass

    return _read_tifffile_original_tags(path)


def _read_embedded_jpeg_exif(path: str) -> dict[str, Any]:
    """Fallback: EXIF from preview JPEG strip inside TIFF (tags 513/514)."""
    try:
        from PIL import Image
    except Exception:
        return {}

    try:
        with open(path, "rb") as fh:
            with Image.open(fh) as im:
                tags = getattr(im, "tag_v2", None) or {}
                offset = tags.get(_TAG_JPEG_OFFSET)
                length = tags.get(_TAG_JPEG_LENGTH)
                if offset is None or length is None:
                    return {}
                fh.seek(int(offset))
                data = fh.read(int(length))
        if not data:
            return {}
        with Image.open(BytesIO(data)) as jpeg:
            raw = _collect_exif_pairs(jpeg)
            if not raw:
                return {}
            return _meta_from_raw_exif(
                raw,
                width=jpeg.width,
                height=jpeg.height,
            )
    except Exception:
        return {}


def read_tiff_exif_metadata(path: str) -> dict[str, Any]:
    """Read camera EXIF from an image path (auto-called on resolved result TIF)."""
    suffix = Path(path).suffix.lower()
    if suffix not in {".tif", ".tiff"}:
        return _read_original_tiff_exif(path) or {"raw_exif_json": "{}"}

    meta: dict[str, Any] = {}
    for reader in (
        _read_original_tiff_exif,
        _read_exiftool_metadata,
        _read_embedded_jpeg_exif,
    ):
        meta = _merge_meta(meta, reader(path))
        if _has_camera_exif(meta):
            return meta
    return meta or {"raw_exif_json": "{}"}
