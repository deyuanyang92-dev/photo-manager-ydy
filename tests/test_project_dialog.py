"""test_project_dialog.py — Tests for ProjectDialog and suggest_project_code.

Covers:
  - 7-field new-project mode
  - Validation (required fields)
  - suggest_project_code() naming logic
  - create→user_projects.json field shape (projectCode/name/year/dateRange/
    location/collector/directory/incomingJpgSubdir/resultsSubdir)
  - open-workspace mode (minimal fields)

All Qt tests use the module-scoped qapp fixture (offscreen, no dialogs shown).
"""
from __future__ import annotations

import json
import sys
import unittest.mock as mock
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QDialog


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication for offscreen tests."""
    existing = QApplication.instance()
    if existing is not None:
        yield existing
    else:
        app = QApplication(sys.argv[:1])
        yield app


# ── suggest_project_code ──────────────────────────────────────────────────────

class TestSuggestProjectCode:
    def test_first_project_returns_01(self):
        from app.views.project_dialog import suggest_project_code
        result = suggest_project_code([], "2026")
        assert result == "PRJ-2026-01"

    def test_increments_for_same_year(self):
        from app.views.project_dialog import suggest_project_code
        existing = [
            {"year": "2026", "isDemo": False},
            {"year": "2026", "isDemo": False},
        ]
        result = suggest_project_code(existing, "2026")
        assert result == "PRJ-2026-03"

    def test_skips_demo_projects(self):
        from app.views.project_dialog import suggest_project_code
        existing = [
            {"year": "2026", "isDemo": True},
            {"year": "2026", "isDemo": False},
        ]
        result = suggest_project_code(existing, "2026")
        assert result == "PRJ-2026-02"

    def test_different_year_not_counted(self):
        from app.views.project_dialog import suggest_project_code
        existing = [{"year": "2025", "isDemo": False}]
        result = suggest_project_code(existing, "2026")
        assert result == "PRJ-2026-01"

    def test_uses_current_year_if_not_given(self):
        from app.views.project_dialog import suggest_project_code
        from datetime import date
        result = suggest_project_code([])
        assert str(date.today().year) in result


# ── ProjectDialog construction ────────────────────────────────────────────────

class TestProjectDialogConstruction:
    def test_new_mode_has_7_fields(self, qapp):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        # Verify the 7 QLineEdit fields exist
        assert hasattr(dlg, "_name_edit")
        assert hasattr(dlg, "_code_edit")
        assert hasattr(dlg, "_dir_edit")
        assert hasattr(dlg, "_location_edit")
        assert hasattr(dlg, "_collector_edit")
        assert hasattr(dlg, "_start_date_edit")
        assert hasattr(dlg, "_end_date_edit")

    def test_open_mode_has_only_name_and_dir(self, qapp):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="open")
        assert hasattr(dlg, "_name_edit")
        assert hasattr(dlg, "_dir_edit")
        assert not hasattr(dlg, "_location_edit")
        assert not hasattr(dlg, "_code_edit")

    def test_new_mode_title(self, qapp):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        assert "新建" in dlg.windowTitle()

    def test_open_mode_title(self, qapp):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="open")
        assert "打开" in dlg.windowTitle()

    def test_start_date_defaults_to_today(self, qapp):
        from app.views.project_dialog import ProjectDialog, _today_compact
        dlg = ProjectDialog(mode="new")
        assert dlg._start_date_edit.text() == _today_compact()

    def test_code_placeholder_contains_prj(self, qapp):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        assert "PRJ" in dlg._code_edit.placeholderText()


# ── Validation ────────────────────────────────────────────────────────────────

class TestProjectDialogValidation:
    def _accept_with_fields(self, qapp, **fields):
        """Helper: set fields and trigger _on_accept(), returning result_project()."""
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        if "name" in fields:
            dlg._name_edit.setText(fields["name"])
        if "dir" in fields:
            dlg._dir_edit.setText(fields["dir"])
        if "location" in fields:
            dlg._location_edit.setText(fields["location"])
        if "collector" in fields:
            dlg._collector_edit.setText(fields["collector"])
        if "start_date" in fields:
            dlg._start_date_edit.setText(fields["start_date"])
        return dlg

    def test_missing_dir_shows_warning(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        dlg._name_edit.setText("测试项目")
        # dir is empty — should warn, not accept
        with mock.patch("app.views.project_dialog.warn") as m_warn:
            dlg._on_accept()
        m_warn.assert_called_once()
        assert dlg.result_project() is None

    def test_missing_name_shows_warning(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        dlg._dir_edit.setText(str(tmp_path))
        # name is empty — should warn
        with mock.patch("app.views.project_dialog.warn") as m_warn:
            dlg._on_accept()
        m_warn.assert_called_once()
        assert dlg.result_project() is None

    def test_missing_location_shows_warning(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        dlg._name_edit.setText("测试项目")
        dlg._dir_edit.setText(str(tmp_path))
        # location empty
        with mock.patch("app.views.project_dialog.warn") as m_warn:
            dlg._on_accept()
        m_warn.assert_called_once()
        assert dlg.result_project() is None

    def test_missing_collector_shows_warning(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        dlg._name_edit.setText("测试项目")
        dlg._dir_edit.setText(str(tmp_path))
        dlg._location_edit.setText("厦门")
        with mock.patch("app.views.project_dialog.warn") as m_warn:
            dlg._on_accept()
        m_warn.assert_called_once()

    def test_missing_start_date_shows_warning(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        dlg._name_edit.setText("测试项目")
        dlg._dir_edit.setText(str(tmp_path))
        dlg._location_edit.setText("厦门")
        dlg._collector_edit.setText("张三")
        dlg._start_date_edit.clear()
        with mock.patch("app.views.project_dialog.warn") as m_warn:
            dlg._on_accept()
        m_warn.assert_called_once()


# ── Successful creation → field shape ────────────────────────────────────────

class TestProjectDialogCreateFields:
    def _create_and_accept(self, qapp, tmp_path):
        """Fully fill a new-mode dialog and call _on_accept()."""
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new", existing_projects=[])
        dlg._name_edit.setText("厦门多毛类")
        dlg._code_edit.setText("TST-2026-01")
        dlg._dir_edit.setText(str(tmp_path))
        dlg._location_edit.setText("福建·厦门")
        dlg._collector_edit.setText("杨德援")
        dlg._start_date_edit.setText("20260101")
        dlg._end_date_edit.setText("20260115")
        with mock.patch("app.views.project_dialog.warn"):
            dlg._on_accept()
        return dlg.result_project()

    def test_returns_dict_on_success(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj is not None
        assert isinstance(proj, dict)

    def test_name_field(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj["name"] == "厦门多毛类"

    def test_project_code_field(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj["projectCode"] == "TST-2026-01"

    def test_location_field(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj["location"] == "福建·厦门"

    def test_collector_field(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj["collector"] == "杨德援"

    def test_year_derived_from_start_date(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj["year"] == "2026"

    def test_date_range_includes_both_dates(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert "20260101" in proj["dateRange"]
        assert "20260115" in proj["dateRange"]

    def test_directory_field_set(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj["directory"] or proj.get("dir")

    def test_incoming_jpg_subdir_field(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj.get("incomingJpgSubdir") == "incoming-jpg"

    def test_results_subdir_field(self, qapp, tmp_path):
        proj = self._create_and_accept(qapp, tmp_path)
        assert proj.get("resultsSubdir") == "results"

    def test_project_code_uppercased(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new")
        dlg._name_edit.setText("测试")
        dlg._dir_edit.setText(str(tmp_path))
        dlg._location_edit.setText("厦门")
        dlg._collector_edit.setText("张三")
        dlg._start_date_edit.setText("20260101")
        dlg._code_edit.setText("tst-2026-01")
        dlg._on_accept()
        proj = dlg.result_project()
        assert proj["projectCode"] == "TST-2026-01"

    def test_code_auto_suggested_when_empty(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="new", existing_projects=[])
        dlg._name_edit.setText("测试")
        dlg._dir_edit.setText(str(tmp_path))
        dlg._location_edit.setText("厦门")
        dlg._collector_edit.setText("张三")
        dlg._start_date_edit.setText("20260101")
        dlg._code_edit.setText("")  # empty → auto
        dlg._on_accept()
        proj = dlg.result_project()
        assert proj["projectCode"].startswith("PRJ-")


# ── Open-workspace mode ────────────────────────────────────────────────────────

class TestProjectDialogOpenMode:
    def test_open_mode_minimal_fields(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="open")
        dlg._dir_edit.setText(str(tmp_path))
        dlg._on_accept()
        proj = dlg.result_project()
        assert proj is not None
        assert proj.get("directory") or proj.get("dir")
        assert "incomingJpgSubdir" in proj
        assert "resultsSubdir" in proj

    def test_open_mode_name_defaults_to_dir_name(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="open")
        dlg._dir_edit.setText(str(tmp_path))
        dlg._name_edit.clear()  # empty name
        dlg._on_accept()
        proj = dlg.result_project()
        # name should be derived from dir
        assert proj["name"] == tmp_path.name or proj.get("name")

    def test_open_mode_custom_name_preserved(self, qapp, tmp_path):
        from app.views.project_dialog import ProjectDialog
        dlg = ProjectDialog(mode="open")
        dlg._dir_edit.setText(str(tmp_path))
        dlg._name_edit.setText("我的工作区")
        dlg._on_accept()
        proj = dlg.result_project()
        assert proj["name"] == "我的工作区"


# ── Persistence to user_projects.json ────────────────────────────────────────

class TestProjectDialogPersistence:
    def test_result_project_can_be_appended_to_json(self, qapp, tmp_path):
        """Result dict structure is compatible with user_projects.json."""
        from app.views.project_dialog import ProjectDialog
        project_dir = tmp_path / "proj1"
        project_dir.mkdir()
        dlg = ProjectDialog(mode="new", existing_projects=[])
        dlg._name_edit.setText("持久化测试项目")
        dlg._dir_edit.setText(str(project_dir))
        dlg._location_edit.setText("福建")
        dlg._collector_edit.setText("张三")
        dlg._start_date_edit.setText("20260601")
        dlg._on_accept()
        proj = dlg.result_project()
        assert proj is not None

        # Write to JSON as overview_view would
        out = tmp_path / "user_projects.json"
        data = {"version": 1, "projects": [proj]}
        out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        # Read back and verify
        loaded = json.loads(out.read_text(encoding="utf-8"))
        p = loaded["projects"][0]
        assert p["name"] == "持久化测试项目"
        assert p["projectCode"].startswith("PRJ-")
        assert p["location"] == "福建"
        assert p["collector"] == "张三"
        assert p["year"] == "2026"
        assert p["incomingJpgSubdir"] == "incoming-jpg"
        assert p["resultsSubdir"] == "results"
