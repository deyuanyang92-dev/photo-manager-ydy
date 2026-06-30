from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _write_image(path) -> None:
    img = QImage(24, 16, QImage.Format.Format_RGB32)
    img.fill(QColor("#336699"))
    assert img.save(str(path))


def test_decode_image_thumbnail_uses_stat_cache(monkeypatch, tmp_path) -> None:
    from app.utils import image_thumbnail

    path = tmp_path / "thumb.jpg"
    _write_image(path)
    image_thumbnail.clear_thumbnail_cache()

    first = image_thumbnail.decode_image_thumbnail(str(path), max_size=32)
    assert first is not None and not first.isNull()

    def fail_backend(*_args, **_kwargs):
        raise AssertionError("backend should not be called on cache hit")

    monkeypatch.setattr(image_thumbnail, "_decode_with_qt", fail_backend)
    monkeypatch.setattr(image_thumbnail, "_decode_with_pillow", fail_backend)
    monkeypatch.setattr(image_thumbnail, "_decode_with_tifffile", fail_backend)
    monkeypatch.setattr(image_thumbnail, "_decode_with_imagemagick", fail_backend)

    cached = image_thumbnail.decode_image_thumbnail(str(path), max_size=32)
    assert cached is not None and not cached.isNull()


def test_clear_thumbnail_cache_forces_decode(monkeypatch, tmp_path) -> None:
    from app.utils import image_thumbnail

    path = tmp_path / "thumb.jpg"
    _write_image(path)
    image_thumbnail.clear_thumbnail_cache()
    assert image_thumbnail.decode_image_thumbnail(str(path), max_size=32) is not None

    image_thumbnail.clear_thumbnail_cache(str(path))
    calls = {"qt": 0}

    def miss_qt(*_args, **_kwargs):
        calls["qt"] += 1
        return None

    monkeypatch.setattr(image_thumbnail, "_decode_with_qt", miss_qt)
    monkeypatch.setattr(image_thumbnail, "_decode_with_pillow", lambda *_a, **_k: None)
    monkeypatch.setattr(image_thumbnail, "_decode_with_tifffile", lambda *_a, **_k: None)
    monkeypatch.setattr(image_thumbnail, "_decode_with_imagemagick", lambda *_a, **_k: None)

    assert image_thumbnail.decode_image_thumbnail(str(path), max_size=32) is None
    assert calls["qt"] == 1
    assert image_thumbnail.decode_image_thumbnail(str(path), max_size=32) is None
    assert calls["qt"] == 1
