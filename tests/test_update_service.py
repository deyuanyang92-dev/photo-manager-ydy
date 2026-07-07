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
                "digest": "sha256:" + "a" * 64,
            },
        ],
    }

    release = update_service.release_from_api_payload(payload)

    assert release.tag_name == "v0.03"
    assert release.asset_name == "SpecimenPhotoWorkbench-v0.03-win64.zip"
    assert release.asset_url == "https://x/app.zip"
    assert release.asset_size == 123
    assert release.asset_digest == "sha256:" + "a" * 64


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


def test_extract_update_zip_rejects_path_traversal(tmp_path):
    import zipfile

    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../evil.txt", b"bad")

    with pytest.raises(ValueError, match="unsafe path"):
        update_service.extract_update_zip(zip_path, tmp_path / "package")


def test_extract_update_zip_requires_internal_dir(tmp_path):
    import zipfile

    zip_path = tmp_path / "bad-shape.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SpecimenPhotoWorkbench.exe", b"exe")

    with pytest.raises(ValueError, match="_internal"):
        update_service.extract_update_zip(zip_path, tmp_path / "package")


def test_extract_update_zip_rejects_excessive_uncompressed_size(monkeypatch, tmp_path):
    import zipfile

    zip_path = tmp_path / "oversized.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SpecimenPhotoWorkbench.exe", b"exe")
        zf.writestr("_internal/payload.bin", b"payload")

    monkeypatch.setattr(update_service, "MAX_UPDATE_UNCOMPRESSED_BYTES", 4)

    with pytest.raises(ValueError, match="too large"):
        update_service.extract_update_zip(zip_path, tmp_path / "package")


def test_extract_update_zip_rejects_too_many_entries(monkeypatch, tmp_path):
    import zipfile

    zip_path = tmp_path / "too-many.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SpecimenPhotoWorkbench.exe", b"exe")
        zf.writestr("_internal/app.txt", b"data")

    monkeypatch.setattr(update_service, "MAX_UPDATE_ZIP_ENTRIES", 1)

    with pytest.raises(ValueError, match="too many files"):
        update_service.extract_update_zip(zip_path, tmp_path / "package")


def test_verify_downloaded_package_checks_size_and_digest(tmp_path):
    payload = b"package"
    zip_path = tmp_path / "update.zip"
    zip_path.write_bytes(payload)
    release = update_service.ReleaseInfo(
        tag_name="v1",
        asset_name="SpecimenPhotoWorkbench-v1-win64.zip",
        asset_url="https://x/app.zip",
        asset_size=len(payload),
        page_url="https://x",
        asset_digest="sha256:" + update_service.sha256_file(zip_path),
    )

    update_service.verify_downloaded_package(release, zip_path)

    bad = update_service.ReleaseInfo(
        tag_name="v1",
        asset_name=release.asset_name,
        asset_url=release.asset_url,
        asset_size=len(payload),
        page_url=release.page_url,
        asset_digest="sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="SHA-256"):
        update_service.verify_downloaded_package(bad, zip_path)


def test_run_packaged_smoke_rejects_traceback(monkeypatch, tmp_path):
    exe = tmp_path / "SpecimenPhotoWorkbench.exe"
    exe.write_text("", encoding="utf-8")

    class Result:
        returncode = 0
        stdout = ""
        stderr = "Traceback: boom"

    monkeypatch.setattr(update_service.subprocess, "run", lambda *a, **kw: Result())

    with pytest.raises(RuntimeError, match="packaged smoke"):
        update_service.run_packaged_smoke(exe)


def test_make_update_script_waits_copies_and_restarts():
    script = update_service.make_update_script(
        install_dir=Path(r"C:\Apps\SpecimenPhotoWorkbench"),
        package_dir=Path(r"C:\Temp\update package"),
        exe_path=Path(r"C:\Apps\SpecimenPhotoWorkbench\SpecimenPhotoWorkbench.exe"),
        backup_dir=Path(r"C:\Temp\backup"),
        protected_dir=Path(r"C:\Temp\protected"),
        pid=1234,
    )

    assert "Wait-Process -Id $pidToWait" in script
    assert "function Copy-Tree" in script
    assert "function Clear-InstallDir" in script
    assert "Copy-Tree -Source $packageDir -Destination $installDir" in script
    save_idx = script.index("\n    Save-ProtectedFiles\n")
    clear_idx = script.index("\n    Clear-InstallDir\n")
    install_idx = script.index("\n    Copy-Tree -Source $packageDir -Destination $installDir")
    assert save_idx < clear_idx < install_idx
    assert "_internal\\data\\user_projects.json" in script
    assert "_internal\\data\\user_taxonomy.json" in script
    assert "_internal\\data\\worms_cache.json" in script
    assert "_internal\\data\\worms_jobs.json" in script
    assert "_internal\\data\\worms_taxonomy.json" in script
    assert "data\\user_projects.json" in script
    assert "data\\user_taxonomy.json" in script
    assert "data\\worms_cache.json" in script
    assert "data\\worms_jobs.json" in script
    assert "data\\worms_taxonomy.json" in script
    assert "Invoke-Smoke -Path $exePath" in script
    assert "Rollback succeeded" in script
    assert "Start-Process -FilePath $exePath" in script
    assert "$pidToWait = 1234" in script
