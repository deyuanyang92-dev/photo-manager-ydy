"""Packaging guardrails for the Windows PyInstaller build."""
from __future__ import annotations

from pathlib import Path

from app.views.base_view import BaseView
from app.views.registry import ALL_VIEW_SPECS


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_windows.ps1"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


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

    assert "Remove-Item $duplicateProjData" in text
    assert "Invoke-PackagedSmoke -ExePath $exePath" in text
    assert "Packaged smoke attempt" in text
    assert "Packaged smoke test timed out" in text
    assert "Packaged smoke test failed" in text
    assert text.index("Remove-Item $duplicateProjData") < text.index(
        "Invoke-PackagedSmoke -ExePath $exePath"
    )
    assert text.index("Invoke-PackagedSmoke -ExePath $exePath") < text.index("Compress-Archive")


def test_ci_uploads_versioned_windows_zip_artifact():
    text = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "dist/SpecimenPhotoWorkbench-*-win64.zip" in text
    assert "dist/SpecimenPhotoWorkbench-win64.zip" not in text
    assert "Versioned Windows ZIP was not produced" in text
    assert "Packaged executable was not produced" in text
    assert "if-no-files-found: error" in text


def test_tag_release_workflow_builds_and_uploads_windows_zip():
    text = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert 'tags:' in text
    assert '"v*"' in text
    assert "APP_VERSION" in text
    assert "must match release tag" in text
    assert "scripts\\run_tests_batched.ps1 -IncludePackaging" in text
    assert "scripts\\build_windows.ps1" in text
    assert "gh release create" in text
    assert "gh release upload" in text
    assert "dist -Filter 'SpecimenPhotoWorkbench-*-win64.zip'" in text
    assert "if-no-files-found: error" in text
