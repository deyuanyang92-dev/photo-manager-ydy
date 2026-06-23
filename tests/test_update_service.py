from pathlib import Path

import pytest

from app.services import update_service


def test_version_key_compares_release_tags():
    assert update_service.version_key("v0.10") > update_service.version_key("v0.02")
    assert update_service.is_newer_version("v1.0.0", "v0.99")
    assert not update_service.is_newer_version("v0.02", "v0.02")


def test_release_from_api_payload_selects_windows_zip():
    payload = {
        "tag_name": "v0.03",
        "html_url": "https://github.com/example/releases/tag/v0.03",
        "assets": [
            {"name": "source.zip", "browser_download_url": "https://x/source.zip"},
            {
                "name": "SpecimenPhotoWorkbench-v0.03-win64.zip",
                "browser_download_url": "https://x/app.zip",
                "size": 123,
            },
        ],
    }

    release = update_service.release_from_api_payload(payload)

    assert release.tag_name == "v0.03"
    assert release.asset_name == "SpecimenPhotoWorkbench-v0.03-win64.zip"
    assert release.asset_url == "https://x/app.zip"
    assert release.asset_size == 123


def test_release_from_api_payload_requires_windows_zip():
    with pytest.raises(ValueError):
        update_service.release_from_api_payload({"tag_name": "v0.03", "assets": []})


def test_can_self_update_requires_windows_app_exe(monkeypatch):
    monkeypatch.setattr(update_service.sys, "frozen", True, raising=False)

    assert update_service.can_self_update(
        executable=r"C:\Apps\SpecimenPhotoWorkbench\SpecimenPhotoWorkbench.exe",
        platform="win32",
    )
    assert not update_service.can_self_update(
        executable=r"C:\Python314\python.exe",
        platform="win32",
    )
    assert not update_service.can_self_update(
        executable="/tmp/SpecimenPhotoWorkbench.exe",
        platform="linux",
    )


def test_extract_update_zip_returns_package_dir(tmp_path):
    import zipfile

    zip_path = tmp_path / "update.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SpecimenPhotoWorkbench.exe", b"exe")
        zf.writestr("_internal/app.txt", b"data")

    package_dir = update_service.extract_update_zip(zip_path, tmp_path / "package")

    assert package_dir == tmp_path / "package"
    assert (package_dir / "SpecimenPhotoWorkbench.exe").is_file()


def test_make_update_script_waits_copies_and_restarts():
    script = update_service.make_update_script(
        install_dir=Path(r"C:\Apps\SpecimenPhotoWorkbench"),
        package_dir=Path(r"C:\Temp\update package"),
        exe_path=Path(r"C:\Apps\SpecimenPhotoWorkbench\SpecimenPhotoWorkbench.exe"),
        backup_dir=Path(r"C:\Temp\backup"),
        pid=1234,
    )

    assert "Wait-Process -Id $pidToWait" in script
    assert "Copy-Item -Path (Join-Path $packageDir '*') -Destination $installDir" in script
    assert "Start-Process -FilePath $exePath" in script
    assert "$pidToWait = 1234" in script
