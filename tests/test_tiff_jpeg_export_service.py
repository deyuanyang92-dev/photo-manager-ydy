"""tests/test_tiff_jpeg_export_service.py"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def sample_tiff(tmp_path) -> Path:
    from PIL import Image

    path = tmp_path / "specimen.tif"
    Image.new("RGB", (1200, 800), "#224466").save(path, format="TIFF")
    return path


def test_collect_tiff_paths_recursive(tmp_path, sample_tiff) -> None:
    from app.services.tiff_jpeg_export_service import collect_tiff_paths

    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    from PIL import Image

    other = nested / "nested.tif"
    Image.new("RGB", (64, 64), "#fff").save(other, format="TIFF")

    paths = collect_tiff_paths([str(tmp_path)], recursive=True)
    assert str(sample_tiff.resolve()) in paths
    assert str(other.resolve()) in paths


def test_analyze_tiff(sample_tiff) -> None:
    from app.services.tiff_jpeg_export_service import analyze_tiff

    info = analyze_tiff(str(sample_tiff))
    assert info is not None
    assert info.width == 1200
    assert info.height == 800
    assert info.long_edge == 1200


def test_recommend_settings_large_edge() -> None:
    from app.services.tiff_jpeg_export_service import (
        TiffAnalysis,
        recommend_export_settings,
    )

    big = TiffAnalysis(
        path="/x.tif",
        width=9000,
        height=6000,
        long_edge=9000,
        megapixels=54.0,
        file_size=90 * 1024 * 1024,
        mode="RGB",
        bits_per_sample=8,
        has_alpha=False,
    )
    cfg = recommend_export_settings(big)
    assert cfg.quality <= 92
    assert cfg.max_long_edge == 4096


def test_convert_tiff_to_jpeg_creates_file(sample_tiff, tmp_path) -> None:
    from app.services.tiff_jpeg_export_service import (
        TiffJpegExportSettings,
        convert_tiff_to_jpeg,
    )

    out = tmp_path / "out.jpg"
    before = sample_tiff.stat()
    result = convert_tiff_to_jpeg(
        str(sample_tiff),
        str(out),
        TiffJpegExportSettings(quality=88, max_long_edge=512),
    )
    after = sample_tiff.stat()
    assert out.is_file()
    assert result.bytes_written > 0
    assert max(result.width, result.height) <= 512
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns


def test_convert_tiff_to_jpeg_can_preserve_metadata(tmp_path) -> None:
    from PIL import Image

    from app.services.tiff_jpeg_export_service import (
        TiffJpegExportSettings,
        convert_tiff_to_jpeg,
    )

    src = tmp_path / "metadata.tif"
    out = tmp_path / "metadata.jpg"
    exif = Image.Exif()
    exif[271] = "TestMake"
    Image.new("RGB", (64, 48), "#224466").save(
        src,
        format="TIFF",
        exif=exif,
        dpi=(300, 300),
        icc_profile=b"test-profile",
    )

    result = convert_tiff_to_jpeg(
        str(src),
        str(out),
        TiffJpegExportSettings(
            preserve_exif=True,
            preserve_icc_profile=True,
            preserve_dpi=True,
        ),
    )

    assert result.metadata_preserved == ["EXIF", "ICC", "DPI"]
    with Image.open(out) as jpg:
        assert jpg.getexif().get(271) == "TestMake"
        assert jpg.info.get("icc_profile") == b"test-profile"
        assert jpg.info.get("dpi") == (300, 300)


def test_convert_tiff_to_jpeg_alpha_background_option(tmp_path) -> None:
    from PIL import Image

    from app.services.tiff_jpeg_export_service import (
        TiffJpegExportSettings,
        convert_tiff_to_jpeg,
    )

    src = tmp_path / "transparent.tif"
    out = tmp_path / "transparent.jpg"
    Image.new("RGBA", (8, 8), (255, 0, 0, 0)).save(src, format="TIFF")

    convert_tiff_to_jpeg(
        str(src),
        str(out),
        TiffJpegExportSettings(
            quality=100,
            subsampling=0,
            optimize=False,
            alpha_background=(0, 0, 0),
        ),
    )

    with Image.open(out) as jpg:
        px = jpg.convert("RGB").getpixel((0, 0))
    assert max(px) < 8


def test_batch_convert_skip_existing(sample_tiff, tmp_path) -> None:
    from app.services.tiff_jpeg_export_service import (
        OverwritePolicy,
        TiffJpegExportSettings,
        batch_convert_tiffs,
        convert_tiff_to_jpeg,
    )

    dest = sample_tiff.with_suffix(".jpg")
    convert_tiff_to_jpeg(
        str(sample_tiff),
        str(dest),
        TiffJpegExportSettings(quality=80),
        overwrite=OverwritePolicy.OVERWRITE,
    )
    result = batch_convert_tiffs(
        [str(sample_tiff.parent)],
        TiffJpegExportSettings(quality=80),
        overwrite=OverwritePolicy.SKIP,
    )
    assert result.ok_count == 0
    assert len(result.skipped) == 1


def test_batch_convert_disambiguates_duplicate_output_names(tmp_path) -> None:
    from PIL import Image

    from app.services.tiff_jpeg_export_service import (
        TiffJpegExportSettings,
        batch_convert_tiffs,
    )

    src_a = tmp_path / "a"
    src_b = tmp_path / "b"
    src_a.mkdir()
    src_b.mkdir()
    tif_a = src_a / "same.tif"
    tif_b = src_b / "same.tif"
    Image.new("RGB", (24, 24), "red").save(tif_a, format="TIFF")
    Image.new("RGB", (24, 24), "blue").save(tif_b, format="TIFF")
    out_dir = tmp_path / "out"

    result = batch_convert_tiffs(
        [str(tif_a), str(tif_b)],
        TiffJpegExportSettings(quality=90),
        output_dir=str(out_dir),
    )

    outputs = sorted(Path(item.output).name for item in result.converted)
    assert result.ok_count == 2
    assert outputs == ["same.jpg", "same_1.jpg"]
    assert (out_dir / "same.jpg").is_file()
    assert (out_dir / "same_1.jpg").is_file()


def test_allocate_output_paths_rename_avoids_existing_and_batch_duplicates(tmp_path) -> None:
    from PIL import Image

    from app.services.tiff_jpeg_export_service import (
        OverwritePolicy,
        allocate_jpeg_output_paths,
    )

    src_a = tmp_path / "a" / "same.tif"
    src_b = tmp_path / "b" / "same.tif"
    src_a.parent.mkdir()
    src_b.parent.mkdir()
    Image.new("RGB", (8, 8), "red").save(src_a, format="TIFF")
    Image.new("RGB", (8, 8), "blue").save(src_b, format="TIFF")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "same.jpg").write_bytes(b"existing")

    outputs = allocate_jpeg_output_paths(
        [str(src_a.resolve()), str(src_b.resolve())],
        output_dir=str(out_dir),
        overwrite=OverwritePolicy.RENAME,
    )

    names = sorted(path.name for path in outputs.values())
    assert names == ["same_1.jpg", "same_2.jpg"]


def test_resolve_output_path_mirror(tmp_path, sample_tiff) -> None:
    from app.services.tiff_jpeg_export_service import resolve_jpeg_output_path

    out_root = tmp_path / "jpg-out"
    rel = resolve_jpeg_output_path(
        sample_tiff,
        output_dir=out_root,
        source_root=tmp_path,
    )
    assert rel == out_root / "specimen.jpg"
