from pathlib import Path

from app.services.photo_import_service import import_jpgs_to_incoming


def test_import_jpgs_to_incoming_copies_without_moving_original(tmp_path):
    source_dir = tmp_path / "camera"
    incoming = tmp_path / "project" / "incoming-jpg"
    source_dir.mkdir()
    src = source_dir / "IMG_0001.JPG"
    src.write_bytes(b"jpg")

    result = import_jpgs_to_incoming([str(src)], incoming)

    assert src.read_bytes() == b"jpg"
    assert result.errors == []
    assert result.imported_paths == [str((incoming / "IMG_0001.JPG").resolve())]
    assert (incoming / "IMG_0001.JPG").read_bytes() == b"jpg"


def test_import_jpgs_to_incoming_never_overwrites_existing_name(tmp_path):
    source_dir = tmp_path / "camera"
    incoming = tmp_path / "project" / "incoming-jpg"
    source_dir.mkdir()
    incoming.mkdir(parents=True)
    src = source_dir / "IMG_0001.jpg"
    src.write_bytes(b"new")
    (incoming / "IMG_0001.jpg").write_bytes(b"old")

    result = import_jpgs_to_incoming([str(src)], incoming)

    assert (incoming / "IMG_0001.jpg").read_bytes() == b"old"
    assert (incoming / "IMG_0001-2.jpg").read_bytes() == b"new"
    assert result.imported_paths == [str((incoming / "IMG_0001-2.jpg").resolve())]


def test_import_jpgs_to_incoming_skips_non_jpg(tmp_path):
    tif = tmp_path / "result.tif"
    tif.write_bytes(b"tif")

    result = import_jpgs_to_incoming([str(tif)], tmp_path / "incoming-jpg")

    assert result.imported_paths == []
    assert result.skipped_paths == [str(tif)]
