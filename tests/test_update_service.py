from pathlib import Path
import ssl
import urllib.error

import pytest

from app.services import update_service


class _FakeResponse:
    def __init__(self, chunks, *, headers=None):
        self._chunks = list(chunks)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, _size=-1):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk


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


def test_fetch_latest_release_retries_transient_ssl_eof(monkeypatch):
    payload = (
        b'{"tag_name":"v0.03","html_url":"https://github.com/example/releases/tag/v0.03",'
        b'"assets":[{"name":"SpecimenPhotoWorkbench-v0.03-win64.zip",'
        b'"browser_download_url":"https://x/app.zip","size":123}]}'
    )
    calls = []

    def fake_urlopen(req, timeout):
        calls.append((req, timeout))
        if len(calls) == 1:
            raise urllib.error.URLError(
                ssl.SSLError("UNEXPECTED_EOF_WHILE_READING")
            )
        return _FakeResponse([payload])

    monkeypatch.setattr(update_service.urllib.request, "urlopen", fake_urlopen)

    release = update_service.fetch_latest_release(attempts=2, retry_delay=0)

    assert release.tag_name == "v0.03"
    assert release.asset_url == "https://x/app.zip"
    assert len(calls) == 2


def test_download_file_retries_transient_read_error_and_clears_partial(monkeypatch, tmp_path):
    path = tmp_path / "update.zip"
    calls = []
    eof = urllib.error.URLError(ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"))

    def fake_urlopen(req, timeout):
        calls.append((req, timeout))
        if len(calls) == 1:
            return _FakeResponse([b"bad", eof], headers={"Content-Length": "4"})
        return _FakeResponse([b"good", b""], headers={"Content-Length": "4"})

    monkeypatch.setattr(update_service.urllib.request, "urlopen", fake_urlopen)

    update_service.download_file(
        "https://x/app.zip",
        path,
        attempts=2,
        retry_delay=0,
    )

    assert path.read_bytes() == b"good"
    assert len(calls) == 2


def test_download_file_reports_network_hint_and_removes_partial(monkeypatch, tmp_path):
    path = tmp_path / "update.zip"
    eof = urllib.error.URLError(ssl.SSLError("UNEXPECTED_EOF_WHILE_READING"))

    def fake_urlopen(req, timeout):
        return _FakeResponse([b"bad", eof], headers={"Content-Length": "4"})

    monkeypatch.setattr(update_service.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(update_service.UpdateNetworkError) as excinfo:
        update_service.download_file(
            "https://x/app.zip",
            path,
            attempts=1,
            retry_delay=0,
        )

    assert not path.exists()
    message = str(excinfo.value)
    assert "GitHub" in message
    assert "releases/latest" in message
    assert "网络/代理" in message


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


def test_release_payload_captures_body_and_signature():
    payload = {
        "tag_name": "v0.60",
        "html_url": "https://github.com/example/releases/tag/v0.60",
        "body": "## 更新内容\n- 修复若干问题",
        "assets": [
            {
                "name": "SpecimenPhotoWorkbench-v0.60-win64.zip",
                "browser_download_url": "https://x/app.zip",
                "size": 999,
            },
            {
                "name": "SpecimenPhotoWorkbench-v0.60-win64.zip.sig",
                "browser_download_url": "https://x/app.zip.sig",
            },
        ],
    }

    release = update_service.release_from_api_payload(payload)

    assert release.signature_url == "https://x/app.zip.sig"
    assert "更新内容" in release.body


def _ed25519_keypair():
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return priv, base64.b64encode(pub_raw).decode("ascii")


def test_verify_update_signature_roundtrip(tmp_path):
    import base64

    zip_path = tmp_path / "app.zip"
    zip_path.write_bytes(b"pretend-this-is-a-zip")
    priv, pub_b64 = _ed25519_keypair()
    sig_b64 = base64.b64encode(priv.sign(zip_path.read_bytes())).decode("ascii")

    assert update_service.verify_update_signature(
        zip_path, sig_b64, public_key_b64=pub_b64
    ) is True

    # 没有公钥 → 视为未启用校验，返回 False（不抛错）
    assert update_service.verify_update_signature(
        zip_path, sig_b64, public_key_b64=""
    ) is False

    # 篡改内容 → 校验失败
    zip_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="签名验证失败"):
        update_service.verify_update_signature(zip_path, sig_b64, public_key_b64=pub_b64)


def test_signature_required_follows_env_key(monkeypatch):
    monkeypatch.delenv("SPECIMEN_UPDATE_PUBKEY", raising=False)
    monkeypatch.setattr(update_service, "UPDATE_PUBLIC_KEY_B64", "", raising=False)
    assert update_service.signature_required() is False

    monkeypatch.setenv("SPECIMEN_UPDATE_PUBKEY", "abc")
    assert update_service.signature_required() is True
    assert update_service.update_public_key_b64() == "abc"


def test_prepare_update_rejects_missing_signature_when_key_set(monkeypatch, tmp_path):
    _priv, pub_b64 = _ed25519_keypair()
    monkeypatch.setenv("SPECIMEN_UPDATE_PUBKEY", pub_b64)
    monkeypatch.setattr(update_service, "download_file", lambda *a, **k: None)
    monkeypatch.setattr(update_service, "verify_downloaded_package", lambda *a, **k: None)
    monkeypatch.setattr(update_service.tempfile, "gettempdir", lambda: str(tmp_path))

    release = update_service.ReleaseInfo(
        tag_name="v0.60",
        asset_name="SpecimenPhotoWorkbench-v0.60-win64.zip",
        asset_url="https://x/app.zip",
        asset_size=0,
        page_url="https://x",
        signature_url="",  # 没有 .sig
    )

    with pytest.raises(ValueError, match="缺少 .sig|签名"):
        update_service.prepare_update(release, executable=r"C:\x\python.exe", smoke=False)


def test_install_requires_elevation_false_on_writable_dir(tmp_path):
    assert update_service.install_requires_elevation(tmp_path) is False


def test_launch_update_script_elevate_uses_runas(monkeypatch, tmp_path):
    import subprocess

    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    script = tmp_path / "apply-update.ps1"
    script.write_text("echo hi", encoding="utf-8")

    update_service.launch_update_script(script, elevate=False)
    update_service.launch_update_script(script, elevate=True)

    plain_args = " ".join(calls[0][0][0])
    elevated_args = " ".join(calls[1][0][0])
    assert "-File" in plain_args
    assert "RunAs" in elevated_args


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
