"""tests/test_tiff_exif_read.py — TIFF 内嵌 JPEG / EXIF 读取."""
from __future__ import annotations

import pytest


def _make_jpeg_with_exif(tmp_path, *, make: str = "Canon", model: str = "EOS R5") -> str:
    from PIL import Image

    jpg = tmp_path / "src.jpg"
    img = Image.new("RGB", (80, 60), color=(10, 20, 30))
    exif = img.getexif()
    exif[271] = make
    exif[272] = model
    exif[33434] = "1/125"
    exif[33437] = "8"
    exif[34855] = 400
    exif[37386] = "105"
    exif[36867] = "2026:06:18 14:22:11"
    img.save(jpg, exif=exif)
    return str(jpg)


def test_read_jpeg_exif_metadata(tmp_path) -> None:
    from app.utils.tiff_exif_read import read_tiff_exif_metadata

    jpg = _make_jpeg_with_exif(tmp_path)
    meta = read_tiff_exif_metadata(jpg)
    assert meta.get("camera_make") == "Canon"
    assert meta.get("camera_model") == "EOS R5"
    assert meta.get("iso") == "400"
    assert meta.get("exif_datetime") == "2026:06:18 14:22:11"


def test_read_tiff_prefers_original_over_embedded(monkeypatch, tmp_path) -> None:
    from app.utils import tiff_exif_read as ter

    calls = {"original": 0, "embedded": 0}

    def _fake_original(_path: str) -> dict:
        calls["original"] += 1
        return {
            "camera_make": "Canon",
            "camera_model": "R5",
            "iso": "200",
            "raw_exif_json": "{}",
        }

    def _fake_embedded(_path: str) -> dict:
        calls["embedded"] += 1
        return {
            "camera_make": "Nikon",
            "camera_model": "Z9",
            "raw_exif_json": "{}",
        }

    monkeypatch.setattr(ter, "_read_original_tiff_exif", _fake_original)
    monkeypatch.setattr(ter, "_read_embedded_jpeg_exif", _fake_embedded)
    tif = tmp_path / "result.tif"
    tif.write_bytes(b"fake")
    meta = ter.read_tiff_exif_metadata(str(tif))
    assert calls["original"] == 1
    assert calls["embedded"] == 0
    assert meta.get("camera_make") == "Canon"


def test_read_tiff_falls_back_to_embedded_when_original_empty(monkeypatch, tmp_path) -> None:
    from app.utils import tiff_exif_read as ter

    monkeypatch.setattr(ter, "_read_original_tiff_exif", lambda _p: {"raw_exif_json": "{}"})
    monkeypatch.setattr(
        ter,
        "_read_embedded_jpeg_exif",
        lambda _p: {"camera_make": "Nikon", "camera_model": "Z9", "raw_exif_json": "{}"},
    )
    tif = tmp_path / "result.tif"
    tif.write_bytes(b"fake")
    meta = ter.read_tiff_exif_metadata(str(tif))
    assert meta.get("camera_make") == "Nikon"


def test_photo_asset_service_uses_tiff_reader(tmp_path) -> None:
    from app.services.photo_asset_service import read_image_exif_metadata

    jpg = _make_jpeg_with_exif(tmp_path, make="Sony", model="A7R V")
    meta = read_image_exif_metadata(jpg)
    assert meta.get("camera_make") == "Sony"
    assert meta.get("camera_model") == "A7R V"
