from pathlib import Path

from app.services.photo_import_service import (
    ImportedMediaRecord,
    clear_pending_imports,
    import_jpgs_to_incoming,
    import_media_to_project,
    record_imported_media,
)


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


def test_import_media_to_project_copies_jpg_and_tiff_to_incoming(tmp_path):
    source_dir = tmp_path / "camera"
    incoming = tmp_path / "project" / "incoming-jpg"
    source_dir.mkdir()
    jpg = source_dir / "P6202064.JPG"
    tif = source_dir / "HeliconFocus.tif"
    jpg.write_bytes(b"jpg")
    tif.write_bytes(b"tif")

    result = import_media_to_project([str(jpg), str(tif)], incoming)

    assert result.errors == []
    assert result.skipped_paths == []
    assert result.imported_jpg_paths == [str((incoming / jpg.name).resolve())]
    assert result.imported_tiff_paths == [str((incoming / tif.name).resolve())]
    assert (incoming / jpg.name).read_bytes() == b"jpg"
    assert (incoming / tif.name).read_bytes() == b"tif"
    assert jpg.read_bytes() == b"jpg"
    assert tif.read_bytes() == b"tif"


def test_import_media_to_project_never_overwrites_tiff(tmp_path):
    source_dir = tmp_path / "camera"
    incoming = tmp_path / "project" / "incoming-jpg"
    source_dir.mkdir()
    incoming.mkdir(parents=True)
    tif = source_dir / "result.tif"
    tif.write_bytes(b"new")
    (incoming / "result.tif").write_bytes(b"old")

    result = import_media_to_project([str(tif)], incoming)

    assert (incoming / "result.tif").read_bytes() == b"old"
    assert (incoming / "result-2.tif").read_bytes() == b"new"
    assert result.imported_tiff_paths == [str((incoming / "result-2.tif").resolve())]


def test_import_media_to_project_skips_duplicate_jpg_already_in_incoming(tmp_path):
    source_dir = tmp_path / "camera"
    incoming = tmp_path / "project" / "incoming-jpg"
    source_dir.mkdir()
    incoming.mkdir(parents=True)
    jpg = source_dir / "P6191373.JPG"
    jpg.write_bytes(b"same-photo")
    existing = incoming / jpg.name
    existing.write_bytes(b"same-photo")

    result = import_media_to_project([str(jpg)], incoming)

    assert result.imported_paths == []
    assert result.imported_jpg_paths == []
    assert result.skipped_duplicate_paths == [str(existing.resolve())]
    assert not (incoming / "P6191373-2.JPG").exists()


def test_import_media_to_project_skips_duplicate_tiff_already_in_incoming(tmp_path):
    source_dir = tmp_path / "camera"
    incoming = tmp_path / "project" / "incoming-jpg"
    source_dir.mkdir()
    incoming.mkdir(parents=True)
    tif = source_dir / "GXQZ-SNW-XTC003-1-R-260619.tif"
    tif.write_bytes(b"same-tiff")
    existing = incoming / tif.name
    existing.write_bytes(b"same-tiff")

    result = import_media_to_project([str(tif)], incoming)

    assert result.imported_paths == []
    assert result.imported_tiff_paths == []
    assert result.skipped_duplicate_paths == [str(existing.resolve())]
    assert not (incoming / "GXQZ-SNW-XTC003-1-R-260619-2.tif").exists()


def test_import_media_to_project_keeps_same_name_when_content_differs(tmp_path):
    source_dir = tmp_path / "camera"
    incoming = tmp_path / "project" / "incoming-jpg"
    source_dir.mkdir()
    incoming.mkdir(parents=True)
    jpg = source_dir / "IMG_0001.JPG"
    jpg.write_bytes(b"new-photo")
    (incoming / jpg.name).write_bytes(b"old-photo")

    result = import_media_to_project([str(jpg)], incoming)

    assert result.skipped_duplicate_paths == []
    assert (incoming / "IMG_0001-2.JPG").read_bytes() == b"new-photo"
    assert result.imported_jpg_paths == [str((incoming / "IMG_0001-2.JPG").resolve())]


def test_import_media_result_records_source_for_pending_clear(tmp_path):
    source_dir = tmp_path / "camera"
    incoming = tmp_path / "project" / "incoming-jpg"
    source_dir.mkdir()
    src = source_dir / "IMG_0001.JPG"
    src.write_bytes(b"jpg")

    result = import_media_to_project([str(src)], incoming)

    assert len(result.imported_records) == 1
    record = result.imported_records[0]
    assert record.source_path == str(src.resolve())
    assert record.imported_path == str((incoming / src.name).resolve())
    assert record.kind == "jpg"


def test_clear_pending_returns_to_missing_source_path(tmp_path):
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    camera = tmp_path / "camera"
    incoming.mkdir(parents=True)
    camera.mkdir()
    source = camera / "IMG_0001.JPG"
    imported = incoming / "IMG_0001.JPG"
    imported.write_bytes(b"jpg")
    record_imported_media(
        project,
        [ImportedMediaRecord(str(source), str(imported), "jpg")],
    )

    result = clear_pending_imports(project, [str(imported)])

    assert result.errors == []
    assert result.returned_paths == [str(source.resolve())]
    assert source.read_bytes() == b"jpg"
    assert not imported.exists()


def test_clear_pending_stashes_import_copy_when_source_still_exists(tmp_path):
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    camera = tmp_path / "camera"
    incoming.mkdir(parents=True)
    camera.mkdir()
    source = camera / "IMG_0001.JPG"
    imported = incoming / "IMG_0001.JPG"
    source.write_bytes(b"jpg")
    imported.write_bytes(b"jpg")
    record_imported_media(
        project,
        [ImportedMediaRecord(str(source), str(imported), "jpg")],
    )

    result = clear_pending_imports(project, [str(imported)])

    stash = project / "_data" / "cleared-pending" / imported.name
    assert result.errors == []
    assert result.stashed_paths == [str(stash.resolve())]
    assert source.read_bytes() == b"jpg"
    assert stash.read_bytes() == b"jpg"
    assert not imported.exists()


def test_clear_pending_without_import_record_stashes_file_instead_of_deleting(tmp_path):
    project = tmp_path / "project"
    incoming = project / "incoming-jpg"
    incoming.mkdir(parents=True)
    imported = incoming / "unknown.JPG"
    imported.write_bytes(b"jpg")

    result = clear_pending_imports(project, [str(imported)])

    stash = project / "_data" / "cleared-pending" / "unknown.JPG"
    assert result.errors == []
    assert result.stashed_paths == [str(stash.resolve())]
    assert stash.read_bytes() == b"jpg"
    assert not imported.exists()
