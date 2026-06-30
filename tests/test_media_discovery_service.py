from __future__ import annotations

import os

from app.services.media_discovery_service import (
    scan_jpgs_near_tiff_in_dir,
    scan_related_files_in_dir,
    split_media_paths,
)


def test_split_media_paths_returns_jpgs_and_tiffs_only(tmp_path):
    jpg = tmp_path / "a.JPG"
    tif = tmp_path / "b.tif"
    txt = tmp_path / "c.txt"

    jpg.write_bytes(b"x")
    tif.write_bytes(b"x")
    txt.write_bytes(b"x")

    assert split_media_paths([str(jpg), str(tif), str(txt)]) == (
        [str(jpg)],
        [str(tif)],
    )


def test_scan_related_files_in_dir_uses_previous_tiff_block(tmp_path):
    previous_tif = tmp_path / "GXFCG-BLW-SC001-1-R-20260618.tif"
    old_jpg = tmp_path / "P6201900.JPG"
    anchor_tif = tmp_path / "GXFCG-BLW-BZC003-4-R-20260618.tif"
    before_jpg = tmp_path / "P6201979.JPG"
    after_jpg = tmp_path / "P6201980.JPG"
    for path in (previous_tif, old_jpg, anchor_tif, before_jpg, after_jpg):
        path.write_bytes(b"x")

    anchor = 1_800_000_000
    os.utime(old_jpg, (anchor - 600, anchor - 600))
    os.utime(previous_tif, (anchor - 500, anchor - 500))
    os.utime(before_jpg, (anchor - 60, anchor - 60))
    os.utime(anchor_tif, (anchor, anchor))
    os.utime(after_jpg, (anchor + 60, anchor + 60))

    result = scan_related_files_in_dir(
        tmp_path,
        "GXFCG-BLW-BZC003-R-20260618",
    )

    assert [item["name"] for item in result] == [
        "P6201979.JPG",
        "GXFCG-BLW-BZC003-4-R-20260618.tif",
    ]
    assert result[0]["relative_to_tif"] == "before"
    assert result[0]["default_related"] is True


def test_scan_jpgs_near_tiff_uses_time_window(tmp_path):
    tif = tmp_path / "result.tif"
    near = tmp_path / "near.JPG"
    far = tmp_path / "far.JPG"
    for path in (tif, near, far):
        path.write_bytes(b"x")
    os.utime(tif, (1000, 1000))
    os.utime(near, (970, 970))
    os.utime(far, (1, 1))

    result = scan_jpgs_near_tiff_in_dir(tmp_path, str(tif), near_seconds=60)

    assert [item["name"] for item in result] == ["near.JPG"]
