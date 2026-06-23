"""Standalone photo-folder mode: local persistence without project scaffolding."""

from types import SimpleNamespace


def test_enter_photo_folder_creates_only_local_data_store(tmp_path):
    from app.services.project_service import enter_photo_folder

    photos = tmp_path / "旧照片"
    photos.mkdir()
    ctx = SimpleNamespace(current_project_dir=None, current_project_root=None)

    resolved = enter_photo_folder(ctx, str(photos))

    assert resolved == str(photos.resolve())
    assert ctx.current_project_dir == resolved
    assert ctx.current_project_root == resolved
    assert (photos / "_data" / "project.db").is_file()
    assert not (photos / "incoming-jpg").exists()
    assert not (photos / "results").exists()


def test_enter_photo_folder_requires_existing_directory(tmp_path):
    import pytest

    from app.services.project_paths import ProjectUnavailableError
    from app.services.project_service import enter_photo_folder

    ctx = SimpleNamespace(current_project_dir=None, current_project_root=None)

    with pytest.raises(ProjectUnavailableError):
        enter_photo_folder(ctx, str(tmp_path / "不存在"))
