"""test_archive_service.py — TDD tests for archive_service.

Tests the full archive pipeline:
  - high-compression JXL bridge or plain JPG ZIP fallback generation
  - ZIP creation + testzip
  - SHA-256 pre-delete safety checks
  - TIFF never deleted
  - delete_jpg=False → no deletion
  - all ZIP preconditions satisfied → deletion happens
  - legacy JXL restore behavior still works

Oracle: archive.js:28-61, 150-168; compress.js:32-45.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

from app.services.archive_service import (
    archive_group,
    compress_to_jxl,
    has_cjxl,
    has_djxl,
    reset_tool_cache,
    restore_archive,
    verify_manifest_complete,
    verify_jpg_zip_complete,
    verify_jxl_recoverable,
    CheckResult,
    RestoreResult,
    ZipResult,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_jpg(directory: str, name: str = "test.jpg", size: tuple = (10, 10)) -> str:
    """Create a minimal JPEG file using Pillow."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    img = Image.new("RGB", size, color=(128, 64, 32))
    img.save(path, format="JPEG", quality=90)
    return path


def _make_tiff(directory: str, name: str = "result.tif") -> str:
    """Create a minimal TIFF file using Pillow."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    img = Image.new("RGB", (10, 10), color=(0, 128, 255))
    img.save(path, format="TIFF")
    return path


# ── verify_manifest_complete ──────────────────────────────────────────────────

class TestVerifyManifestComplete:
    def _make_manifest_files(self, count=2):
        return [
            {
                "originalName": f"img{i:03d}.jpg",
                "archiveName": f"img{i:03d}.jxl",
                "originalSize": 1000 + i,
                "compressedSize": 800 + i,
                "jxlPath": f"/tmp/img{i:03d}.jxl",
            }
            for i in range(count)
        ]

    def test_valid_manifest(self):
        files = self._make_manifest_files(2)
        manifest = {
            "files": [
                {
                    "originalName": f["originalName"],
                    "archiveName": f["archiveName"],
                    "originalSize": f["originalSize"],
                    "compressedSize": f["compressedSize"],
                }
                for f in files
            ]
        }
        result = verify_manifest_complete(manifest, files)
        assert result.ok is True

    def test_missing_manifest_fails(self):
        result = verify_manifest_complete(None, [])
        assert result.ok is False
        assert "清单缺失" in result.reason

    def test_count_mismatch_fails(self):
        files = self._make_manifest_files(2)
        manifest = {"files": [{"originalName": "a.jpg", "archiveName": "a.jxl",
                                "originalSize": 1, "compressedSize": 1}]}
        result = verify_manifest_complete(manifest, files)
        assert result.ok is False
        assert "数量" in result.reason

    def test_missing_archive_name_fails(self):
        files = self._make_manifest_files(1)
        manifest = {"files": [{"originalName": "other.jpg", "archiveName": "other.jxl",
                                "originalSize": files[0]["originalSize"],
                                "compressedSize": files[0]["compressedSize"]}]}
        result = verify_manifest_complete(manifest, files)
        assert result.ok is False

    def test_size_mismatch_fails(self):
        files = self._make_manifest_files(1)
        manifest = {"files": [{"originalName": files[0]["originalName"],
                                "archiveName": files[0]["archiveName"],
                                "originalSize": 9999,  # wrong
                                "compressedSize": files[0]["compressedSize"]}]}
        result = verify_manifest_complete(manifest, files)
        assert result.ok is False
        assert "大小" in result.reason


# ── verify_jxl_recoverable ────────────────────────────────────────────────────

class TestVerifyJxlRecoverable:
    def test_djxl_unavailable_returns_failure(self):
        """djxl missing → check fails → JPGs must NOT be deleted."""
        with patch("app.services.archive_service.has_djxl", return_value=False):
            result = verify_jxl_recoverable([], "/tmp")
            assert result.ok is False
            assert "djxl" in result.reason.lower()

    def test_missing_jxl_file_fails(self, tmp_path):
        files = [{"archiveName": "missing.jxl", "originalName": "a.jpg",
                  "jxlPath": str(tmp_path / "missing.jxl")}]
        with patch("app.services.archive_service.has_djxl", return_value=True):
            result = verify_jxl_recoverable(files, str(tmp_path))
            assert result.ok is False
            assert "缺失" in result.reason

    def test_djxl_failure_returns_false(self, tmp_path):
        """djxl process fails → check fails."""
        jxl = tmp_path / "a.jxl"
        jxl.write_bytes(b"fake jxl data")
        files = [{"archiveName": "a.jxl", "originalName": "a.jpg",
                  "jxlPath": str(jxl)}]
        with patch("app.services.archive_service.has_djxl", return_value=True):
            with patch("subprocess.run", side_effect=Exception("djxl failed")):
                result = verify_jxl_recoverable(files, str(tmp_path))
                assert result.ok is False

    def test_empty_restored_file_fails(self, tmp_path):
        """djxl runs but produces empty file → check fails."""
        jxl = tmp_path / "a.jxl"
        jxl.write_bytes(b"x")

        def fake_run(cmd, **kwargs):
            # Create an empty restore file
            out = tmp_path / "restore-a.jpg"
            out.write_bytes(b"")
            return MagicMock(returncode=0)

        files = [{"archiveName": "a.jxl", "originalName": "a.jpg",
                  "jxlPath": str(jxl)}]
        with patch("app.services.archive_service.has_djxl", return_value=True):
            with patch("subprocess.run", side_effect=fake_run):
                result = verify_jxl_recoverable(files, str(tmp_path))
                assert result.ok is False


# ── archive_group integration ─────────────────────────────────────────────────

class TestArchiveGroup:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        reset_tool_cache()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        reset_tool_cache()

    def test_default_delete_jpg_after_verified_archive(self):
        """Default workflow removes loose JPG after exact archive verification."""
        jpg = _make_jpg(self.tmpdir, "img001.jpg")
        tiff = _make_tiff(self.tmpdir, "result.tif")
        result = archive_group([jpg], tiff, self.tmpdir)
        assert result.ok
        assert result.delete_jpg is True
        assert not os.path.isfile(jpg)

    def test_tiff_never_deleted(self):
        """TIFF must never be deleted under any circumstances."""
        jpg = _make_jpg(self.tmpdir, "img001.jpg")
        tiff = _make_tiff(self.tmpdir, "result.tif")
        result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=True)
        assert os.path.isfile(tiff), "TIFF must NEVER be deleted"

    def test_zip_created(self):
        """A ZIP file must be created."""
        jpg = _make_jpg(self.tmpdir, "img_zip_test.jpg")
        tiff = _make_tiff(self.tmpdir, "result_zip.tif")
        result = archive_group([jpg], tiff, self.tmpdir)
        assert result.ok
        assert os.path.isfile(result.zip_path)

    def test_zip_uses_internal_jxl_when_tools_available(self):
        """Default archive is high-compression internal JXL when cjxl/djxl exist."""
        jpg = _make_jpg(self.tmpdir, "img_manifest.jpg")
        tiff = _make_tiff(self.tmpdir, "result_manifest.tif")
        result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=False)
        with zipfile.ZipFile(result.zip_path, "r") as zf:
            names = zf.namelist()
        if has_cjxl() and has_djxl():
            assert "manifest.json" in names
            assert "img_manifest.jxl" in names
            assert result.manifest["format"] == "jxl-zip"
        else:
            assert names == ["img_manifest.jpg"]
            assert result.manifest["format"] == "jpg-zip"

    def test_manifest_contains_correct_fields(self):
        """In-memory manifest remains available for UI/status, but is not zipped."""
        jpg = _make_jpg(self.tmpdir, "img_mfield.jpg")
        tiff = _make_tiff(self.tmpdir, "result_mfield.tif")
        result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=False)
        manifest = result.manifest
        assert manifest["format"] in {"jpg-zip", "jxl-zip"}
        assert isinstance(manifest["files"], list)
        assert len(manifest["files"]) == 1
        assert manifest["files"][0]["originalName"] == "img_mfield.jpg"
        assert manifest["files"][0]["archiveName"] in {"img_mfield.jpg", "img_mfield.jxl"}

    def test_tool_unavailable_falls_back_to_plain_jpg_zip_deletion(self):
        """Without JXL tools, fallback plain JPG ZIP still verifies and deletes."""
        jpg = _make_jpg(self.tmpdir, "img_safe.jpg")
        tiff = _make_tiff(self.tmpdir, "result_safe.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            with patch("app.services.archive_service.has_djxl", return_value=False):
                result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=True)
        assert result.ok
        assert result.manifest["format"] == "jpg-zip"
        assert result.delete_jpg is True
        assert not os.path.isfile(jpg)

    def test_jxl_verify_failure_falls_back_to_plain_jpg_zip(self):
        """Broken JXL bridge is not accepted; exact plain JPG ZIP fallback is used."""
        jpg = _make_jpg(self.tmpdir, "img_safe.jpg")
        tiff = _make_tiff(self.tmpdir, "result_safe.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=True):
            with patch("app.services.archive_service.has_djxl", return_value=True):
                with patch(
                    "app.services.archive_service._verify_jxl_zip_complete",
                    return_value=CheckResult(ok=False, reason="模拟 JXL 校验失败"),
                ):
                    result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=True)
        assert result.ok
        assert result.manifest["format"] == "jpg-zip"
        assert result.delete_jpg is True
        assert not os.path.isfile(jpg)

    def test_all_preconditions_met_deletes_jpg(self, tmp_path):
        """All ZIP preconditions satisfied → JPG is deleted."""
        jpg = _make_jpg(str(tmp_path), "delete_me.jpg")
        tiff = _make_tiff(str(tmp_path), "result_del.tif")

        result = archive_group([jpg], tiff, str(tmp_path), delete_jpg=True)

        assert result.ok
        assert result.delete_jpg is True, "All ZIP preconditions met → should delete JPG"
        assert not os.path.isfile(jpg), "JPG must be deleted after all checks pass"
        assert os.path.isfile(tiff), "TIFF must survive"

    def test_empty_jpg_paths_raises(self):
        """No JPGs → ValueError."""
        tiff = _make_tiff(self.tmpdir, "r.tif")
        with pytest.raises((ValueError, Exception)):
            archive_group([], tiff, self.tmpdir)

    def test_missing_jpg_raises(self):
        """Non-existent JPG path → FileNotFoundError."""
        tiff = _make_tiff(self.tmpdir, "r.tif")
        with pytest.raises((FileNotFoundError, Exception)):
            archive_group(["/nonexistent/path/img.jpg"], tiff, self.tmpdir)

    def test_zip_size_non_trivial(self):
        """ZIP must be larger than 32 bytes."""
        jpg = _make_jpg(self.tmpdir, "img_size.jpg")
        tiff = _make_tiff(self.tmpdir, "r_size.tif")
        result = archive_group([jpg], tiff, self.tmpdir)
        assert result.zip_size > 32

    def test_zip_integrity_check(self):
        """ZIP must pass testzip (no corruption)."""
        jpg = _make_jpg(self.tmpdir, "img_integrity.jpg")
        tiff = _make_tiff(self.tmpdir, "r_integrity.tif")
        result = archive_group([jpg], tiff, self.tmpdir)
        with zipfile.ZipFile(result.zip_path, "r") as zf:
            bad = zf.testzip()
            assert bad is None, f"ZIP corruption detected: {bad}"


# ── Red-line #4 contract: cjxl flags preserve JPEG bitstream exactly ──────────

class TestCjxlFlagsContract:
    """Bit-exact JPEG roundtrip requires `--lossless_jpeg=1 -e <effort>`."""

    def test_cjxl_flags_exact(self):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return MagicMock(returncode=0)

        with patch("app.services.archive_service.has_cjxl", return_value=True):
            with patch("app.services.archive_service.subprocess.run", side_effect=fake_run):
                compress_to_jxl("/in.jpg", "/out.jxl", effort=7)

        cmd = captured["cmd"]
        assert cmd == ["cjxl", "/in.jpg", "/out.jxl", "--lossless_jpeg=1", "-e", "7"]
        joined = " ".join(cmd)
        assert "--quality" not in joined
        assert "--modular" not in joined
        assert "--distance" not in joined


# ── restore_archive — one-click recover original JPGs from a ZIP ───────────────

class TestRestoreArchive:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        reset_tool_cache()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        reset_tool_cache()

    def test_roundtrip_lossless(self):
        """archive_group → restore_archive must return bit-exact original JPGs."""
        srcdir = os.path.join(self.tmpdir, "src")
        a = _make_jpg(srcdir, "img001.jpg")
        b = _make_jpg(srcdir, "img002.jpg")
        tiff = _make_tiff(srcdir, "result.tif")
        with open(a, "rb") as f:
            a_bytes = f.read()
        with open(b, "rb") as f:
            b_bytes = f.read()

        result = archive_group([a, b], tiff, srcdir)
        out = os.path.join(self.tmpdir, "restored")
        r = restore_archive(result.zip_path, out)

        assert r.ok
        assert r.count == 2
        assert not r.failures
        with open(os.path.join(out, "img001.jpg"), "rb") as f:
            assert f.read() == a_bytes, "还原 JPG 必须与原图 bit-exact"
        with open(os.path.join(out, "img002.jpg"), "rb") as f:
            assert f.read() == b_bytes

    def test_missing_djxl_no_output(self):
        """djxl absent + ZIP holds .jxl → ok=False and no half-product written."""
        zip_path = os.path.join(self.tmpdir, "fake.zip")
        manifest = {"version": 1, "files": [
            {"originalName": "a.jpg", "archiveName": "a.jxl",
             "originalSize": 10, "compressedSize": 8}]}
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("a.jxl", b"fake jxl bytes")
        out = os.path.join(self.tmpdir, "out")
        with patch("app.services.archive_service.has_djxl", return_value=False):
            r = restore_archive(zip_path, out)
        assert r.ok is False
        assert os.listdir(out) == [], "缺 djxl 时不得产出任何半成品文件"

    def test_size_mismatch_flagged(self):
        """Restored JPG size != manifest originalSize → failure, half-product removed."""
        zip_path = os.path.join(self.tmpdir, "m.zip")
        manifest = {"version": 1, "files": [
            {"originalName": "a.jpg", "archiveName": "a.jxl",
             "originalSize": 99999, "compressedSize": 8}]}
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("a.jxl", b"fake")
        out = os.path.join(self.tmpdir, "out")

        def fake_djxl(cmd, **kwargs):
            with open(cmd[2], "wb") as f:
                f.write(b"\xff\xd8\xff" + b"\x00" * 20)  # size != 99999
            return MagicMock(returncode=0)

        with patch("app.services.archive_service.has_djxl", return_value=True):
            with patch("app.services.archive_service.subprocess.run", side_effect=fake_djxl):
                r = restore_archive(zip_path, out)
        assert r.ok is False
        assert any("a.jpg" in x for x in r.failures)
        assert not os.path.isfile(os.path.join(out, "a.jpg")), "大小不符的半成品必须删除"

    def test_skip_vs_overwrite_existing(self):
        """overwrite=False skips existing files; overwrite=True replaces them."""
        zip_path = os.path.join(self.tmpdir, "f.zip")
        manifest = {"version": 1, "files": [
            {"originalName": "a.jpg", "archiveName": "a.jpg",
             "originalSize": 5, "compressedSize": 5}]}
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("a.jpg", b"NEW!!")
        out = os.path.join(self.tmpdir, "out")
        os.makedirs(out)
        existing = os.path.join(out, "a.jpg")
        with open(existing, "wb") as f:
            f.write(b"OLD")

        r = restore_archive(zip_path, out, overwrite=False)
        assert existing in r.skipped
        with open(existing, "rb") as f:
            assert f.read() == b"OLD", "overwrite=False 不得覆盖已存在文件"

        r2 = restore_archive(zip_path, out, overwrite=True)
        assert existing in r2.restored
        with open(existing, "rb") as f:
            assert f.read() == b"NEW!!"

    def test_manifest_missing_degrades(self):
        """No manifest.json → still recover raw entries by name."""
        zip_path = os.path.join(self.tmpdir, "nomani.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("a.jpg", b"hello world")
        out = os.path.join(self.tmpdir, "out")
        r = restore_archive(zip_path, out)
        assert r.ok
        assert os.path.isfile(os.path.join(out, "a.jpg"))

    def test_missing_zip_raises(self):
        with pytest.raises((FileNotFoundError, Exception)):
            restore_archive("/nonexistent/foo.zip", self.tmpdir)
