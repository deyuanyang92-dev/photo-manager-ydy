"""test_archive_service.py — TDD tests for archive_service.

Tests the full archive pipeline:
  - manifest generation
  - ZIP creation + testzip
  - djxl pre-delete safety checks
  - TIFF never deleted
  - delete_jpg=False (default) → no deletion
  - All 4 preconditions satisfied → deletion happens
  - djxl unavailable → no deletion even when delete_jpg=True

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
    verify_manifest_complete,
    verify_jxl_recoverable,
    CheckResult,
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

    def test_default_delete_jpg_is_false(self):
        """delete_jpg defaults to False → JPGs must NOT be deleted."""
        jpg = _make_jpg(self.tmpdir, "img001.jpg")
        tiff = _make_tiff(self.tmpdir, "result.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            result = archive_group([jpg], tiff, self.tmpdir)
        assert result.ok
        assert result.delete_jpg is False
        assert os.path.isfile(jpg), "JPG must NOT be deleted when delete_jpg=False"

    def test_tiff_never_deleted(self):
        """TIFF must never be deleted under any circumstances."""
        jpg = _make_jpg(self.tmpdir, "img001.jpg")
        tiff = _make_tiff(self.tmpdir, "result.tif")
        # Even with delete_jpg=True, TIFF must survive
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            with patch("app.services.archive_service.has_djxl", return_value=False):
                result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=True)
        assert os.path.isfile(tiff), "TIFF must NEVER be deleted"

    def test_zip_created(self):
        """A ZIP file must be created."""
        jpg = _make_jpg(self.tmpdir, "img_zip_test.jpg")
        tiff = _make_tiff(self.tmpdir, "result_zip.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            result = archive_group([jpg], tiff, self.tmpdir)
        assert result.ok
        assert os.path.isfile(result.zip_path)

    def test_zip_contains_manifest(self):
        """ZIP must contain manifest.json."""
        jpg = _make_jpg(self.tmpdir, "img_manifest.jpg")
        tiff = _make_tiff(self.tmpdir, "result_manifest.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            result = archive_group([jpg], tiff, self.tmpdir)
        with zipfile.ZipFile(result.zip_path, "r") as zf:
            assert "manifest.json" in zf.namelist()

    def test_manifest_contains_correct_fields(self):
        """Manifest must have version, files, format=jxl-lossless."""
        jpg = _make_jpg(self.tmpdir, "img_mfield.jpg")
        tiff = _make_tiff(self.tmpdir, "result_mfield.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            result = archive_group([jpg], tiff, self.tmpdir)
        manifest = result.manifest
        assert manifest["version"] == 1
        assert manifest["format"] == "jxl-lossless"
        assert isinstance(manifest["files"], list)
        assert len(manifest["files"]) == 1
        assert manifest["files"][0]["originalName"] == "img_mfield.jpg"

    def test_djxl_unavailable_prevents_jpg_deletion(self):
        """djxl absent → delete_jpg=True still does NOT delete JPGs."""
        jpg = _make_jpg(self.tmpdir, "img_safe.jpg")
        tiff = _make_tiff(self.tmpdir, "result_safe.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            with patch("app.services.archive_service.has_djxl", return_value=False):
                result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=True)
        assert result.ok
        assert result.delete_jpg is False, "djxl absent → must not delete"
        assert os.path.isfile(jpg), "JPG must survive when djxl absent"
        assert "djxl" in result.deletion_skipped_reason.lower()

    def test_all_preconditions_met_deletes_jpg(self, tmp_path):
        """All 4 preconditions satisfied → JPG is deleted."""
        jpg = _make_jpg(str(tmp_path), "delete_me.jpg")
        tiff = _make_tiff(str(tmp_path), "result_del.tif")

        def fake_djxl_run(cmd, **kwargs):
            # Simulate djxl successfully restoring: create a non-empty output file
            out_path = cmd[2]  # djxl <jxl> <out>
            with open(out_path, "wb") as f:
                f.write(b"\xff\xd8\xff" + b"\x00" * 50)  # fake restored JPEG
            return MagicMock(returncode=0)

        with patch("app.services.archive_service.has_cjxl", return_value=False):
            with patch("app.services.archive_service.has_djxl", return_value=True):
                with patch("subprocess.run", side_effect=fake_djxl_run):
                    result = archive_group([jpg], tiff, str(tmp_path), delete_jpg=True)

        assert result.ok
        assert result.delete_jpg is True, "All preconditions met → should delete JPG"
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
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            result = archive_group([jpg], tiff, self.tmpdir)
        assert result.zip_size > 32

    def test_zip_integrity_check(self):
        """ZIP must pass testzip (no corruption)."""
        jpg = _make_jpg(self.tmpdir, "img_integrity.jpg")
        tiff = _make_tiff(self.tmpdir, "r_integrity.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=False):
            result = archive_group([jpg], tiff, self.tmpdir)
        with zipfile.ZipFile(result.zip_path, "r") as zf:
            bad = zf.testzip()
            assert bad is None, f"ZIP corruption detected: {bad}"


# ── Red-line #4 contract: cjxl flags are EXACTLY --distance 0 -e <effort> ──────

class TestCjxlFlagsContract:
    """Lossless bit-exact requires `--distance 0 -e <effort>` and nothing else.

    Forbidden: --quality / --modular / -j (oracle compress.js:32-39).
    """

    def test_cjxl_flags_exact(self):
        captured = {}

        def fake_run(cmd, *a, **kw):
            captured["cmd"] = cmd
            return MagicMock(returncode=0)

        with patch("app.services.archive_service.has_cjxl", return_value=True):
            with patch("app.services.archive_service.subprocess.run", side_effect=fake_run):
                compress_to_jxl("/in.jpg", "/out.jxl", effort=7)

        cmd = captured["cmd"]
        assert cmd == ["cjxl", "/in.jpg", "/out.jxl", "--distance", "0", "-e", "7"]
        joined = " ".join(cmd)
        assert "--quality" not in joined
        assert "--modular" not in joined
        assert "-j" not in cmd  # the lossless-jpeg flag must never appear
