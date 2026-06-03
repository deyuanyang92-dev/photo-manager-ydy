"""Tests for app/utils/path_utils.py — TDD first pass (RED).

Covers:
- wsl_to_windows / windows_to_wsl conversions
- repair_doubled_mount
- normalize_path (doubles repair + Windows→POSIX in WSL)
- is_wsl_runtime (detection, not mocked)
- SafePathRegistry: register_root / assert_safe allow + block + path.relative semantics
"""
import os
import sys
import pytest
from pathlib import Path


# ── Helpers ────────────────────────────────────────────────────────────────

def _fresh_registry():
    """Return a new SafePathRegistry with no registered roots."""
    from app.utils.path_utils import SafePathRegistry
    return SafePathRegistry()


# ── wsl_to_windows ─────────────────────────────────────────────────────────

class TestWslToWindows:
    def test_basic_n_drive(self):
        from app.utils.path_utils import wsl_to_windows
        assert wsl_to_windows("/mnt/n/foo/bar") == "N:\\foo\\bar"

    def test_c_drive(self):
        from app.utils.path_utils import wsl_to_windows
        assert wsl_to_windows("/mnt/c/Users/test") == "C:\\Users\\test"

    def test_uppercase_letter(self):
        from app.utils.path_utils import wsl_to_windows
        # /mnt/N/ should still work
        result = wsl_to_windows("/mnt/N/foo")
        assert result is not None
        assert result.startswith("N:\\")

    def test_root_only(self):
        from app.utils.path_utils import wsl_to_windows
        # /mnt/n with no trailing slash — returns drive root
        result = wsl_to_windows("/mnt/n")
        assert result is not None
        assert result.startswith("N:")

    def test_no_mnt_prefix_returns_none(self):
        from app.utils.path_utils import wsl_to_windows
        assert wsl_to_windows("/home/user/foo") is None

    def test_non_wsl_path_returns_none(self):
        from app.utils.path_utils import wsl_to_windows
        assert wsl_to_windows("N:\\foo") is None

    def test_empty_returns_none(self):
        from app.utils.path_utils import wsl_to_windows
        assert wsl_to_windows("") is None


# ── windows_to_wsl ─────────────────────────────────────────────────────────

class TestWindowsToWsl:
    def test_backslash_path(self):
        from app.utils.path_utils import windows_to_wsl
        assert windows_to_wsl("N:\\foo\\bar") == "/mnt/n/foo/bar"

    def test_forward_slash_path(self):
        from app.utils.path_utils import windows_to_wsl
        assert windows_to_wsl("N:/foo/bar") == "/mnt/n/foo/bar"

    def test_c_drive(self):
        from app.utils.path_utils import windows_to_wsl
        assert windows_to_wsl("C:\\Users\\test") == "/mnt/c/Users/test"

    def test_uppercase_drive_lowercased(self):
        from app.utils.path_utils import windows_to_wsl
        result = windows_to_wsl("N:\\foo")
        assert result.startswith("/mnt/n/")

    def test_non_windows_path_returns_none(self):
        from app.utils.path_utils import windows_to_wsl
        assert windows_to_wsl("/mnt/n/foo") is None

    def test_empty_returns_none(self):
        from app.utils.path_utils import windows_to_wsl
        assert windows_to_wsl("") is None

    def test_root_drive_only(self):
        from app.utils.path_utils import windows_to_wsl
        result = windows_to_wsl("C:\\")
        assert result is not None
        assert "/mnt/c" in result


# ── repair_doubled_mount ────────────────────────────────────────────────────

class TestRepairDoubledMount:
    def test_wsl_double(self):
        from app.utils.path_utils import repair_doubled_mount
        assert repair_doubled_mount("/mnt/n/mnt/n/foo/bar") == "/mnt/n/foo/bar"

    def test_wsl_double_no_trailing(self):
        from app.utils.path_utils import repair_doubled_mount
        assert repair_doubled_mount("/mnt/n/mnt/n/x") == "/mnt/n/x"

    def test_no_double_unchanged(self):
        from app.utils.path_utils import repair_doubled_mount
        p = "/mnt/n/foo/bar"
        assert repair_doubled_mount(p) == p

    def test_empty_unchanged(self):
        from app.utils.path_utils import repair_doubled_mount
        assert repair_doubled_mount("") == ""

    def test_different_drive_letters_not_repaired(self):
        from app.utils.path_utils import repair_doubled_mount
        # /mnt/n/mnt/c/... — different letters, should NOT be repaired
        p = "/mnt/n/mnt/c/foo"
        result = repair_doubled_mount(p)
        # must not touch this
        assert "/mnt/c" in result

    def test_windows_double_n_drive(self):
        from app.utils.path_utils import repair_doubled_mount
        result = repair_doubled_mount("N:\\mnt\\n\\foo")
        assert result is not None
        assert "mnt" not in result.lower() or result.startswith("N:\\foo") or result == "N:\\foo"


# ── normalize_path ──────────────────────────────────────────────────────────

class TestNormalizePath:
    def test_already_posix_unchanged(self):
        from app.utils.path_utils import normalize_path
        p = "/tmp/test"
        result = normalize_path(p)
        # Should resolve to an absolute path
        assert result.startswith("/")

    def test_doubles_repaired(self):
        from app.utils.path_utils import normalize_path
        result = normalize_path("/mnt/n/mnt/n/projects/abc")
        # After repair, should NOT contain /mnt/n/mnt/n/
        assert "/mnt/n/mnt/n/" not in result

    def test_windows_to_posix_in_wsl(self):
        """On WSL runtime, Windows path N:\\foo should become /mnt/n/foo."""
        from app.utils.path_utils import normalize_path, is_wsl_runtime
        if not is_wsl_runtime():
            pytest.skip("Only meaningful on WSL")
        result = normalize_path("N:\\foo\\bar")
        assert result.startswith("/mnt/n/")
        assert "foo" in result

    def test_non_empty_result(self):
        from app.utils.path_utils import normalize_path
        assert normalize_path("/mnt/n/foo") != ""


# ── is_wsl_runtime ──────────────────────────────────────────────────────────

class TestIsWslRuntime:
    def test_returns_bool(self):
        from app.utils.path_utils import is_wsl_runtime
        result = is_wsl_runtime()
        assert isinstance(result, bool)

    def test_true_on_wsl_env(self):
        """If WSL_DISTRO_NAME env var is set, must return True."""
        from app.utils.path_utils import is_wsl_runtime
        original = os.environ.get("WSL_DISTRO_NAME")
        os.environ["WSL_DISTRO_NAME"] = "Ubuntu"
        try:
            assert is_wsl_runtime() is True
        finally:
            if original is None:
                del os.environ["WSL_DISTRO_NAME"]
            else:
                os.environ["WSL_DISTRO_NAME"] = original

    def test_false_without_wsl_env_on_non_linux(self, monkeypatch):
        """On non-linux platform with no WSL markers, must return False."""
        from app.utils.path_utils import is_wsl_runtime
        monkeypatch.setattr(sys, "platform", "win32")
        # Remove WSL env var if set
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        assert is_wsl_runtime() is False


# ── SafePathRegistry ────────────────────────────────────────────────────────

class TestSafePathRegistry:
    def test_empty_registry_blocks_all(self, tmp_path):
        """No roots registered → assert_safe raises PermissionError."""
        reg = _fresh_registry()
        with pytest.raises(PermissionError):
            reg.assert_safe(str(tmp_path / "foo"))

    def test_registered_root_allows_self(self, tmp_path):
        reg = _fresh_registry()
        root = str(tmp_path)
        reg.register_root(root)
        # root itself must pass
        reg.assert_safe(root)

    def test_registered_root_allows_child(self, tmp_path):
        reg = _fresh_registry()
        root = str(tmp_path)
        reg.register_root(root)
        child = str(tmp_path / "subdir" / "file.tif")
        reg.assert_safe(child)

    def test_path_outside_root_raises(self, tmp_path):
        reg = _fresh_registry()
        root = str(tmp_path / "project_a")
        reg.register_root(root)
        outside = str(tmp_path / "project_b" / "secret.tif")
        with pytest.raises(PermissionError):
            reg.assert_safe(outside)

    def test_dotdot_traversal_blocked(self, tmp_path):
        """Path using .. to escape root must be blocked."""
        reg = _fresh_registry()
        root = str(tmp_path / "project")
        reg.register_root(root)
        # Construct a path that escapes via ../
        escape = str(tmp_path / "project" / ".." / "other" / "file.tif")
        with pytest.raises(PermissionError):
            reg.assert_safe(escape)

    def test_multiple_roots_any_passes(self, tmp_path):
        reg = _fresh_registry()
        root_a = str(tmp_path / "a")
        root_b = str(tmp_path / "b")
        reg.register_root(root_a)
        reg.register_root(root_b)
        # Both children should pass
        reg.assert_safe(str(tmp_path / "a" / "x.tif"))
        reg.assert_safe(str(tmp_path / "b" / "y.tif"))

    def test_register_same_root_twice_no_error(self, tmp_path):
        """Duplicate registration must not crash or duplicate entries."""
        reg = _fresh_registry()
        root = str(tmp_path)
        reg.register_root(root)
        reg.register_root(root)
        # Still allows child
        reg.assert_safe(str(tmp_path / "file.tif"))

    def test_label_in_error_message(self, tmp_path):
        reg = _fresh_registry()
        root = str(tmp_path / "proj")
        reg.register_root(root)
        outside = str(tmp_path / "elsewhere" / "x")
        with pytest.raises(PermissionError) as exc_info:
            reg.assert_safe(outside, label="archive_path")
        assert "archive_path" in str(exc_info.value)

    def test_relative_semantics_not_startswith(self, tmp_path):
        """Must use path.relative_to (not startswith) to prevent prefix spoofing."""
        reg = _fresh_registry()
        # register /tmp/proj
        root = str(tmp_path / "proj")
        reg.register_root(root)
        # /tmp/proj_evil starts-with /tmp/proj but is NOT a child
        evil = str(tmp_path / "proj_evil" / "file.tif")
        with pytest.raises(PermissionError):
            reg.assert_safe(evil)

    def test_default_registry_importable(self):
        """default_registry must be importable as a module-level instance."""
        from app.utils.path_utils import default_registry, SafePathRegistry
        assert isinstance(default_registry, SafePathRegistry)
