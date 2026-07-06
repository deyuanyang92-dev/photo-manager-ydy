"""Packaging guardrails for the Windows PyInstaller build."""
from __future__ import annotations

from pathlib import Path

from app.views.base_view import BaseView
from app.views.registry import ALL_VIEW_SPECS


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_windows.ps1"


def test_lazy_view_registry_resolves_every_page():
    for spec in ALL_VIEW_SPECS:
        cls = spec.resolve()

        assert issubclass(cls, BaseView), spec.module
        assert cls.__name__ == spec.class_name


def test_windows_build_collects_lazy_view_modules():
    """Lazy view registry imports are invisible to PyInstaller analysis.

    Without collecting app.views, the packaged app can start but later fail
    with ``No module named app.views.workbench_view`` when opening Workbench.
    """
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert '"--collect-submodules", "app.views"' in text
    assert "ALL_VIEW_SPECS" in text
    for spec in ALL_VIEW_SPECS:
        assert spec.module in text or "spec.module" in text


def test_windows_build_runs_packaged_smoke_before_zip():
    text = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert "Invoke-PackagedSmoke -ExePath $exePath" in text
    assert "Packaged smoke attempt" in text
    assert "Packaged smoke test timed out" in text
    assert "Packaged smoke test failed" in text
    assert text.index("Invoke-PackagedSmoke -ExePath $exePath") < text.index("Compress-Archive")
