"""test_archive_service.py — TDD tests for archive_service.

Tests the full archive pipeline:
  - fast plain JPG ZIP generation and optional high-compression JXL bridge
  - ZIP creation + testzip
  - SHA-256 pre-delete safety checks
  - archive/organise does not auto-delete TIFF
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
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image

from app.services.archive_service import (
    ArchiveCancelled,
    archive_group,
    commit_jpg_deletion_after_archive,
    compress_to_jxl,
    has_cjxl,
    has_djxl,
    reset_tool_cache,
    restore_archive,
    restore_archive_to_original_paths,
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

    def test_commit_jpg_deletion_runs_after_deferred_archive(self):
        """Two-phase archive: ZIP first, delete JPG only after explicit commit."""
        jpg = _make_jpg(self.tmpdir, "deferred.jpg")
        tiff = _make_tiff(self.tmpdir, "deferred.tif")
        staged = archive_group([jpg], tiff, self.tmpdir, delete_jpg=False)
        assert staged.ok
        assert staged.delete_jpg is False
        assert os.path.isfile(jpg)

        committed = commit_jpg_deletion_after_archive(staged, [jpg])
        assert committed.delete_jpg is True
        assert not os.path.isfile(jpg)

    def test_commit_refuses_delete_set_that_differs_from_archive_sources(self):
        """A late-added JPG must never be deleted by an earlier archive run."""
        archived_jpg = _make_jpg(self.tmpdir, "archived.jpg")
        late_jpg = _make_jpg(self.tmpdir, "late-added.jpg")
        tiff = _make_tiff(self.tmpdir, "deferred-mismatch.tif")
        staged = archive_group(
            [archived_jpg],
            tiff,
            self.tmpdir,
            delete_jpg=False,
        )

        committed = commit_jpg_deletion_after_archive(
            staged,
            [archived_jpg, late_jpg],
        )

        assert committed.delete_jpg is False
        assert committed.requested_delete_jpg is True
        assert "不一致" in committed.deletion_skipped_reason
        assert os.path.isfile(archived_jpg)
        assert os.path.isfile(late_jpg)

    def test_archive_group_does_not_auto_delete_tiff(self):
        """archive_group must not auto-delete TIFF while deleting loose JPGs."""
        jpg = _make_jpg(self.tmpdir, "img001.jpg")
        tiff = _make_tiff(self.tmpdir, "result.tif")
        result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=True)
        assert os.path.isfile(tiff), "archive_group must not delete TIFF"

    def test_zip_created(self):
        """A ZIP file must be created."""
        jpg = _make_jpg(self.tmpdir, "img_zip_test.jpg")
        tiff = _make_tiff(self.tmpdir, "result_zip.tif")
        result = archive_group([jpg], tiff, self.tmpdir)
        assert result.ok
        assert os.path.isfile(result.zip_path)

    def test_standard_zip_stores_jpg_even_when_jxl_tools_available(self):
        """Default archive is fast plain JPG ZIP even when cjxl/djxl exist."""
        jpg = _make_jpg(self.tmpdir, "img_manifest.jpg")
        tiff = _make_tiff(self.tmpdir, "result_manifest.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=True):
            with patch("app.services.archive_service.has_djxl", return_value=True):
                with patch(
                    "app.services.archive_service.compress_to_jxl",
                    side_effect=AssertionError("standard mode must not use JXL"),
                ):
                    result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=False)
        with zipfile.ZipFile(result.zip_path, "r") as zf:
            names = zf.namelist()
        assert names == ["img_manifest.jpg"]
        assert result.manifest["format"] == "jpg-zip"
        assert result.manifest["method"] == "fast-plain-jpg-zip"
        assert result.manifest["files"][0]["zipCompression"] == "store"

    def test_maximum_archive_uses_internal_jxl_when_tools_available(self):
        """High-compression mode is explicit because JXL transcode is slow."""
        jpg = _make_jpg(self.tmpdir, "img_manifest.jpg")
        tiff = _make_tiff(self.tmpdir, "result_manifest.tif")
        captured_effort = []

        def fake_compress(_src, dst, effort=9):
            captured_effort.append(effort)
            Path(dst).write_bytes(b"fake-jxl")

        with patch("app.services.archive_service.has_cjxl", return_value=True):
            with patch("app.services.archive_service.has_djxl", return_value=True):
                with patch("app.services.archive_service.compress_to_jxl", side_effect=fake_compress):
                    with patch(
                        "app.services.archive_service._verify_jxl_zip_complete",
                        return_value=CheckResult(ok=True),
                    ):
                        result = archive_group(
                            [jpg],
                            tiff,
                            self.tmpdir,
                            delete_jpg=False,
                            method="maximum",
                        )

        with zipfile.ZipFile(result.zip_path, "r") as zf:
            names = sorted(zf.namelist())
        assert names == ["img_manifest.jxl", "manifest.json"]
        assert captured_effort == [9]
        assert result.manifest["format"] == "jxl-zip"
        assert result.manifest["jxlEffort"] == 9

    def test_manifest_contains_correct_fields(self):
        """In-memory manifest remains available for UI/status, but is not zipped."""
        jpg = _make_jpg(self.tmpdir, "img_mfield.jpg")
        tiff = _make_tiff(self.tmpdir, "result_mfield.tif")
        result = archive_group([jpg], tiff, self.tmpdir, delete_jpg=False)
        manifest = result.manifest
        assert manifest["format"] == "jpg-zip"
        assert isinstance(manifest["files"], list)
        assert len(manifest["files"]) == 1
        assert manifest["files"][0]["originalName"] == "img_mfield.jpg"
        assert manifest["files"][0]["archiveName"] == "img_mfield.jpg"
        assert manifest["method"] == "fast-plain-jpg-zip"

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
                    result = archive_group(
                        [jpg],
                        tiff,
                        self.tmpdir,
                        delete_jpg=True,
                        method="maximum",
                    )
        assert result.ok
        assert result.manifest["format"] == "jpg-zip"
        assert result.delete_jpg is True
        assert not os.path.isfile(jpg)

    def test_standard_archive_skips_jxl_and_precompression(self):
        """Fast standard mode should read each JPG once and store it directly."""
        jpg = _make_jpg(self.tmpdir, "img_fast.jpg")
        tiff = _make_tiff(self.tmpdir, "result_fast.tif")
        with patch("app.services.archive_service.has_cjxl", return_value=True):
            with patch("app.services.archive_service.has_djxl", return_value=True):
                with patch(
                    "app.services.archive_service.compress_to_jxl",
                    side_effect=AssertionError("standard mode must not use JXL"),
                ):
                    with patch(
                        "app.services.archive_service.zlib.compressobj",
                        side_effect=AssertionError("standard mode must not pre-compress JPG"),
                    ):
                        with patch(
                            "app.services.archive_service._measure_jpg_for_archive",
                            side_effect=AssertionError("standard mode must stream directly"),
                        ):
                            result = archive_group(
                                [jpg],
                                tiff,
                                self.tmpdir,
                                delete_jpg=False,
                                method="standard",
                            )
        assert result.manifest["format"] == "jpg-zip"
        assert result.manifest["method"] == "fast-plain-jpg-zip"
        assert result.manifest["files"][0]["zipCompression"] == "store"

    def test_archive_cancel_keeps_loose_jpgs_before_delete_gate(self):
        """User cancellation must stop before delete and preserve original JPGs."""
        jpg_a = _make_jpg(self.tmpdir, "img_cancel_a.jpg")
        jpg_b = _make_jpg(self.tmpdir, "img_cancel_b.jpg")
        tiff = _make_tiff(self.tmpdir, "result_cancel.tif")
        seen_progress = {"value": False}

        def _progress(_current: int, _total: int, _filename: str) -> None:
            seen_progress["value"] = True

        def _cancel_after_first_write() -> bool:
            return seen_progress["value"]

        with pytest.raises(ArchiveCancelled):
            archive_group(
                [jpg_a, jpg_b],
                tiff,
                self.tmpdir,
                delete_jpg=True,
                progress_callback=_progress,
                cancel_callback=_cancel_after_first_write,
            )

        assert os.path.isfile(jpg_a)
        assert os.path.isfile(jpg_b)
        assert os.path.isfile(tiff)

    def test_cancel_after_delete_starts_does_not_half_delete_group(self):
        """Once deletion starts, finish it instead of reporting a partial cancel."""
        jpg_a = _make_jpg(self.tmpdir, "img_delete_a.jpg")
        jpg_b = _make_jpg(self.tmpdir, "img_delete_b.jpg")
        tiff = _make_tiff(self.tmpdir, "result_delete_atomic.tif")
        delete_started = {"value": False}
        deleted: list[str] = []
        real_unlink = os.unlink

        def _cancel_during_delete() -> bool:
            return delete_started["value"]

        def _unlink(path: str) -> None:
            delete_started["value"] = True
            deleted.append(os.path.basename(path))
            real_unlink(path)

        with patch("app.services.archive_service.os.unlink", side_effect=_unlink):
            result = archive_group(
                [jpg_a, jpg_b],
                tiff,
                self.tmpdir,
                delete_jpg=True,
                cancel_callback=_cancel_during_delete,
            )

        assert result.delete_jpg is True
        assert sorted(deleted) == ["img_delete_a.jpg", "img_delete_b.jpg"]
        assert not os.path.exists(jpg_a)
        assert not os.path.exists(jpg_b)

    def test_cancel_preserves_preexisting_archive(self):
        jpg = _make_jpg(self.tmpdir, "cancel-safe.jpg")
        tiff = _make_tiff(self.tmpdir, "result_cancel_safe.tif")
        final_zip = Path(self.tmpdir) / "result_cancel_safe.zip"
        final_zip.write_bytes(b"existing-valid-archive")

        def _cancelled_direct(*args, output_dir=None, **kwargs):
            Path(output_dir, "result_cancel_safe.zip").write_bytes(b"partial")
            raise ArchiveCancelled("cancelled")

        with patch(
            "app.services.archive_service._archive_group_direct",
            side_effect=_cancelled_direct,
        ):
            with pytest.raises(ArchiveCancelled):
                archive_group([jpg], tiff, self.tmpdir, delete_jpg=False)

        assert final_zip.read_bytes() == b"existing-valid-archive"

    def test_jxl_compression_honors_parallelism(self):
        jpgs = [_make_jpg(self.tmpdir, f"parallel-{index}.jpg") for index in range(4)]
        tiff = _make_tiff(self.tmpdir, "parallel-result.tif")
        state = {"active": 0, "maximum": 0}
        lock = threading.Lock()

        def _compress(src, dst, effort=9):
            with lock:
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            time.sleep(0.03)
            shutil.copy2(src, dst)
            with lock:
                state["active"] -= 1

        with patch("app.services.archive_service.has_cjxl", return_value=True), patch(
            "app.services.archive_service.has_djxl", return_value=True
        ), patch(
            "app.services.archive_service.compress_to_jxl", side_effect=_compress
        ), patch(
            "app.services.archive_service._verify_jxl_zip_complete",
            return_value=CheckResult(ok=True),
        ):
            archive_group(
                jpgs,
                tiff,
                self.tmpdir,
                delete_jpg=False,
                method="maximum",
                concurrency=3,
            )

        assert state["maximum"] >= 2

    def test_restore_archive_to_original_paths_recovers_deleted_jpgs(self):
        """Undo-organise can restore exact JPGs to their original paths."""
        jpg_a = _make_jpg(self.tmpdir, "img_restore_a.jpg")
        jpg_b = _make_jpg(self.tmpdir, "img_restore_b.jpg")
        expected_a = Path(jpg_a).read_bytes()
        expected_b = Path(jpg_b).read_bytes()
        tiff = _make_tiff(self.tmpdir, "result_restore.tif")

        archived = archive_group([jpg_a, jpg_b], tiff, self.tmpdir, delete_jpg=True)
        assert not os.path.exists(jpg_a)
        assert not os.path.exists(jpg_b)

        restored = restore_archive_to_original_paths(
            archived.zip_path,
            [jpg_a, jpg_b],
        )

        assert restored.ok
        assert restored.count == 2
        assert Path(jpg_a).read_bytes() == expected_a
        assert Path(jpg_b).read_bytes() == expected_b

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

    def test_invalid_restore_does_not_destroy_existing_jpg(self):
        zip_path = os.path.join(self.tmpdir, "invalid-overwrite.zip")
        manifest = {"version": 2, "files": [{
            "originalName": "a.jpg",
            "archiveName": "a.jpg",
            "originalSize": 999,
            "originalSha256": "invalid",
        }]}
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("a.jpg", b"bad replacement")
        out = Path(self.tmpdir) / "safe-overwrite"
        out.mkdir()
        existing = out / "a.jpg"
        existing.write_bytes(b"keep me")

        result = restore_archive(zip_path, str(out), overwrite=True)

        assert not result.ok
        assert existing.read_bytes() == b"keep me"

    def test_zip_traversal_is_rejected(self):
        zip_path = os.path.join(self.tmpdir, "traversal.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../escaped.jpg", b"escape")
        out = Path(self.tmpdir) / "restore-root"

        with pytest.raises(ValueError, match="不安全路径"):
            restore_archive(zip_path, str(out))

        assert not (Path(self.tmpdir) / "escaped.jpg").exists()

    def test_manifest_original_name_cannot_escape_output(self):
        zip_path = os.path.join(self.tmpdir, "manifest-traversal.zip")
        manifest = {"version": 2, "files": [{
            "originalName": "../escaped.jpg",
            "archiveName": "safe.jpg",
        }]}
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("safe.jpg", b"safe")
        out = Path(self.tmpdir) / "manifest-restore-root"

        result = restore_archive(zip_path, str(out))

        assert not result.ok
        assert not (Path(self.tmpdir) / "escaped.jpg").exists()

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
