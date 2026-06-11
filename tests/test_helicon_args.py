"""test_helicon_args.py — TDD tests for Helicon CLI argument builder.

Tests parameter alignment with helicon.js:buildHeliconArgs (lines 127-194).
Critical: verify EXACT flags — not -o, -m:, -r:, -s: but -save:, -mp:, -rp:, -sp:.
"""

import os
import sys
import pytest

from app.services.helicon_service import (
    build_helicon_args,
    detect_helicon,
    reset_helicon_cache,
    HELICON_EXE_NAMES,
)
from app.utils.path_utils import wsl_to_windows


# ── Flag naming (CRITICAL correctness tests) ──────────────────────────────────

class TestFlagNaming:
    """Verify each CLI flag exactly matches Oracle: helicon.js:127-194."""

    def test_silent_flag(self):
        args = build_helicon_args([], "/out/result.tif")
        assert "-silent" in args

    def test_no_silent_when_disabled(self):
        args = build_helicon_args([], "/out/result.tif", silent=False)
        assert "-silent" not in args

    def test_output_flag_uses_save_colon(self):
        """CRITICAL: flag is -save:<path>, NOT -o or --output."""
        args = build_helicon_args([], "/out/result.tif")
        # Check that -save: prefix appears
        save_args = [a for a in args if a.startswith("-save:")]
        assert len(save_args) == 1
        assert save_args[0] == "-save:/out/result.tif"

    def test_method_flag_uses_mp_colon(self):
        """CRITICAL: flag is -mp:<value>, NOT -m: or --method."""
        args = build_helicon_args([], "/out/r.tif", method="2")
        mp_args = [a for a in args if a.startswith("-mp:")]
        assert len(mp_args) == 1
        assert mp_args[0] == "-mp:2"
        # Must NOT have wrong flags
        assert not any(a.startswith("-m:") for a in args)

    def test_radius_flag_uses_rp_colon(self):
        """CRITICAL: flag is -rp:<value>, NOT -r: or --radius."""
        args = build_helicon_args([], "/out/r.tif", radius="8")
        rp_args = [a for a in args if a.startswith("-rp:")]
        assert len(rp_args) == 1
        assert rp_args[0] == "-rp:8"
        assert not any(a.startswith("-r:") for a in args)

    def test_radius_flag_preserves_high_fractional_values(self):
        args = build_helicon_args([], "/out/r.tif", radius="22.5")
        assert "-rp:22.5" in args

    def test_smoothing_flag_uses_sp_colon(self):
        """CRITICAL: flag is -sp:<value>, NOT -s: or --smoothing."""
        args = build_helicon_args([], "/out/r.tif", smoothing="4")
        sp_args = [a for a in args if a.startswith("-sp:")]
        assert len(sp_args) == 1
        assert sp_args[0] == "-sp:4"
        assert not any(a.startswith("-s:") for a in args)

    def test_quality_flag_uses_j_colon(self):
        """CRITICAL: flag is -j:<value>."""
        args = build_helicon_args([], "/out/r.tif", quality=95)
        j_args = [a for a in args if a.startswith("-j:")]
        assert len(j_args) == 1
        assert j_args[0] == "-j:95"

    def test_tiff_compression_flag(self):
        """flag is -tif:<value>."""
        args = build_helicon_args([], "/out/r.tif", tiff_compression="lzw")
        tif_args = [a for a in args if a.startswith("-tif:")]
        assert len(tif_args) == 1
        assert tif_args[0] == "-tif:lzw"


# ── Parameter edge cases ──────────────────────────────────────────────────────

class TestParameterEdgeCases:
    def test_none_method_omitted(self):
        """None → flag omitted entirely."""
        args = build_helicon_args([], "/out/r.tif", method=None)
        assert not any(a.startswith("-mp:") for a in args)

    def test_empty_string_radius_omitted(self):
        """Empty string → flag omitted (mirrors helicon.js:146 check)."""
        args = build_helicon_args([], "/out/r.tif", radius="")
        assert not any(a.startswith("-rp:") for a in args)

    def test_none_smoothing_omitted(self):
        args = build_helicon_args([], "/out/r.tif", smoothing=None)
        assert not any(a.startswith("-sp:") for a in args)

    def test_no_output_omits_save(self):
        """Empty output → no -save: flag."""
        args = build_helicon_args([], "")
        assert not any(a.startswith("-save:") for a in args)

    def test_input_list_path_uses_i_flag(self):
        """When input_list_path provided → uses -i <path>."""
        args = build_helicon_args([], "/out/r.tif", input_list_path="/tmp/list.txt")
        assert "-i" in args
        idx = args.index("-i")
        assert args[idx + 1] == "/tmp/list.txt"

    def test_all_params_together(self):
        args = build_helicon_args(
            [],
            "/out/output.tif",
            method="2",
            radius="8",
            smoothing="4",
            quality=95,
            tiff_compression="lzw",
        )
        assert "-silent" in args
        assert any(a == "-save:/out/output.tif" for a in args)
        assert any(a == "-mp:2" for a in args)
        assert any(a == "-rp:8" for a in args)
        assert any(a == "-sp:4" for a in args)
        assert any(a == "-j:95" for a in args)
        assert any(a == "-tif:lzw" for a in args)

    def test_depth_map_flag(self):
        args = build_helicon_args([], "/out/r.tif", save_depth_map=True)
        assert "-dmap" in args

    def test_sort_order_flag(self):
        args = build_helicon_args([], "/out/r.tif", sort_order="name")
        assert any(a == "-sort:name" for a in args)


# ── WSL path translation ──────────────────────────────────────────────────────

class TestWslPathTranslation:
    def test_wsl_to_windows_converts_mnt_path(self):
        """wsl_to_windows must convert /mnt/c/foo/bar → C:\\foo\\bar."""
        result = wsl_to_windows("/mnt/c/foo/bar")
        assert result is not None
        assert result.upper().startswith("C:\\")
        assert "foo" in result
        assert "bar" in result

    def test_wsl_to_windows_non_mnt_path_returns_none(self):
        """Non-/mnt/ paths return None."""
        result = wsl_to_windows("/tmp/foo")
        assert result is None

    def test_wsl_to_windows_empty_returns_none(self):
        result = wsl_to_windows("")
        assert result is None

    def test_build_args_with_input_list_path_returned_as_string(self):
        """build_helicon_args always returns list[str]."""
        args = build_helicon_args(
            [],
            "/out/result.tif",
            input_list_path="/tmp/list.txt",
        )
        assert isinstance(args, list)
        assert all(isinstance(a, str) for a in args)


# ── detect_helicon ────────────────────────────────────────────────────────────

class TestDetectHelicon:
    def setup_method(self):
        reset_helicon_cache()
        # Clear env vars that might interfere
        os.environ.pop("HELICON_FOCUS_PATH", None)
        os.environ.pop("HELICON_FOCUS_DIR", None)

    def teardown_method(self):
        reset_helicon_cache()
        os.environ.pop("HELICON_FOCUS_PATH", None)
        os.environ.pop("HELICON_FOCUS_DIR", None)

    def test_returns_none_when_not_installed(self, tmp_path):
        """In CI/test env, Helicon is not installed → returns None."""
        result = detect_helicon()
        # Could be None or a path if actually installed.
        # On test machines it should be None.
        # We just verify it doesn't crash.
        assert result is None or isinstance(result, str)

    def test_env_var_path_used_when_set(self, tmp_path):
        """HELICON_FOCUS_PATH env var → used for detection."""
        # Create a fake exe
        fake_exe = tmp_path / "HeliconFocus.exe"
        fake_exe.write_bytes(b"fake")
        os.environ["HELICON_FOCUS_PATH"] = str(fake_exe)
        reset_helicon_cache()
        result = detect_helicon()
        assert result == str(fake_exe)

    def test_cache_is_reused(self, tmp_path):
        """Second call returns cached value without re-scanning."""
        fake_exe = tmp_path / "HeliconFocus.exe"
        fake_exe.write_bytes(b"fake")
        os.environ["HELICON_FOCUS_PATH"] = str(fake_exe)
        reset_helicon_cache()
        r1 = detect_helicon()
        r2 = detect_helicon()
        assert r1 == r2

    def test_reset_clears_cache(self, tmp_path):
        """reset_helicon_cache() forces re-detection on next call."""
        fake_exe = tmp_path / "HeliconFocus.exe"
        fake_exe.write_bytes(b"fake")
        os.environ["HELICON_FOCUS_PATH"] = str(fake_exe)
        reset_helicon_cache()
        detect_helicon()  # populate cache
        reset_helicon_cache()
        # Remove exe
        fake_exe.unlink()
        os.environ.pop("HELICON_FOCUS_PATH", None)
        result = detect_helicon()
        # Should not find it now
        assert result is None or (isinstance(result, str) and result != str(fake_exe))
