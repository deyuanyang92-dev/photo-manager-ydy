"""test_overview_view.py — Tests for the faithful-web OverviewView.

Verifies:
  - Module imports without error.
  - OverviewView constructs without crashing (offscreen).
  - view_id / nav_title / nav_icon are correct.
  - on_activate() does not crash on empty data.
  - Top-bar header row contains + 新建项目 and + 打开工作区 buttons.
  - Time-filter buttons (全部 / 2026 / 2025) exist and are checkable.
  - QTableWidget has exactly 6 columns with correct headers.
  - _load_projects() with an empty JSON results in 0 rows.
  - _load_projects() with seeded JSON populates table rows.
  - Year filter narrows visible rows.
  - _on_detail() opens _ProjectDetailDialog without crashing.
  - enter_workspace_requested signal carries the project directory.

Runs headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


# ── Mock context ──────────────────────────────────────────────────────────────

def _make_ctx(project_dir: str | None = None):
    ctx = MagicMock()
    ctx.has_project = project_dir is not None
    ctx.current_project_dir = project_dir
    ctx.get_db.return_value = None
    ctx.settings = MagicMock()
    return ctx


# ── Sample project data ───────────────────────────────────────────────────────

_SAMPLE_PROJECTS = [
    {
        "id": "proj-001",
        "name": "厦门潮间带调查",
        "directory": "/tmp/xiamen-2026",
        "year": "2026",
        "dateRange": "2026-05",
        "location": "福建·厦门",
        "collector": "王博士",
    },
    {
        "id": "proj-002",
        "name": "广州湾海藻普查",
        "directory": "/tmp/guangzhou-2025",
        "year": "2025",
        "dateRange": "2025-11",
        "location": "广东·广州",
        "collector": "陈研究员",
    },
    {
        "id": "proj-003",
        "name": "三亚珊瑚礁监测",
        "directory": "/tmp/sanya-2026",
        "year": "2026",
        "dateRange": "2026-04",
        "location": "海南·三亚",
        "collector": "李工",
    },
]


# ── Import smoke tests ────────────────────────────────────────────────────────

class TestImports:
    def test_import_overview_view(self):
        from app.views.overview_view import OverviewView
        assert OverviewView is not None

    def test_import_detail_dialog(self):
        from app.views.overview_view import _ProjectDetailDialog
        assert _ProjectDetailDialog is not None

    def test_import_new_project_dialog(self):
        from app.views.overview_view import _NewProjectDialog
        assert _NewProjectDialog is not None

    def test_import_result_lightbox_dialog(self):
        from app.views.overview_view import _ResultLightboxDialog
        assert _ResultLightboxDialog is not None

    def test_import_subdir_control_widget(self):
        from app.views.overview_view import _SubdirControlWidget
        assert _SubdirControlWidget is not None


# ── Construction & identity ───────────────────────────────────────────────────

class TestConstruction:
    def test_constructs_without_error(self):
        from app.views.overview_view import OverviewView
        ctx = _make_ctx()
        w = OverviewView(ctx)
        assert w is not None

    def test_view_id(self):
        from app.views.overview_view import OverviewView
        assert OverviewView.view_id == "overview"

    def test_nav_title(self):
        from app.views.overview_view import OverviewView
        assert OverviewView.nav_title == "最近工作区"

    def test_nav_icon(self):
        from app.views.overview_view import OverviewView
        assert OverviewView.nav_icon != ""


# ── on_activate ───────────────────────────────────────────────────────────────

class TestOnActivate:
    def test_on_activate_no_projects(self, tmp_path):
        """on_activate must not crash when user_projects.json is missing."""
        from app.views.overview_view import OverviewView
        ctx = _make_ctx()
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=tmp_path / "nonexistent.json"):
            w = OverviewView(ctx)
            w.on_activate()   # must not raise

    def test_on_activate_with_empty_projects(self, tmp_path):
        json_path = tmp_path / "user_projects.json"
        json_path.write_text(
            json.dumps({"version": 1, "projects": []}), encoding="utf-8"
        )
        from app.views.overview_view import OverviewView
        ctx = _make_ctx()
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=json_path):
            w = OverviewView(ctx)
            w.on_activate()
            assert w._table.rowCount() == 0


# ── Header buttons ────────────────────────────────────────────────────────────

class TestHeaderButtons:
    def _make_view(self):
        from app.views.overview_view import OverviewView
        ctx = _make_ctx()
        return OverviewView(ctx)

    def test_new_project_button_exists(self):
        w = self._make_view()
        assert w._btn_new is not None
        assert "新建项目" in w._btn_new.text()

    def test_open_workspace_button_exists(self):
        w = self._make_view()
        assert w._btn_open is not None
        assert "打开工作区" in w._btn_open.text()

    def test_new_project_button_is_primary(self):
        w = self._make_view()
        assert w._btn_new.objectName() == "Primary"

    def test_open_workspace_button_is_outline(self):
        w = self._make_view()
        assert w._btn_open.objectName() == "Outline"


# ── Time-filter buttons ───────────────────────────────────────────────────────

class TestTimeFilter:
    def _make_view(self):
        from app.views.overview_view import OverviewView
        return OverviewView(_make_ctx())

    def test_all_button_exists_and_checked_by_default(self):
        w = self._make_view()
        assert w._btn_all is not None
        assert w._btn_all.isCheckable()
        assert w._btn_all.isChecked()

    def test_year_buttons_exist(self):
        w = self._make_view()
        assert w._btn_2026 is not None
        assert w._btn_2025 is not None
        assert w._btn_2026.isCheckable()
        assert w._btn_2025.isCheckable()

    def test_year_filter_2026_exclusive_check(self):
        w = self._make_view()
        w._set_year_filter("2026")
        assert w._btn_2026.isChecked()
        assert not w._btn_all.isChecked()
        assert not w._btn_2025.isChecked()

    def test_year_filter_none_restores_all(self):
        w = self._make_view()
        w._set_year_filter("2026")
        w._set_year_filter(None)
        assert w._btn_all.isChecked()
        assert not w._btn_2026.isChecked()


# ── Table structure ───────────────────────────────────────────────────────────

class TestTableStructure:
    def _make_view(self):
        from app.views.overview_view import OverviewView
        return OverviewView(_make_ctx())

    def test_column_count(self):
        w = self._make_view()
        assert w._table.columnCount() == 6

    def test_column_headers(self):
        w = self._make_view()
        expected = ["项目名称", "磁盘目录", "时间", "地点", "负责人", "操作"]
        for col, exp in enumerate(expected):
            assert w._table.horizontalHeaderItem(col).text() == exp

    def test_vertical_header_hidden(self):
        w = self._make_view()
        assert not w._table.verticalHeader().isVisible()


# ── Table population ──────────────────────────────────────────────────────────

class TestTablePopulation:
    def _make_view_with_projects(self, tmp_path, projects: list[dict]):
        json_path = tmp_path / "user_projects.json"
        json_path.write_text(
            json.dumps({"version": 1, "projects": projects}), encoding="utf-8"
        )
        from app.views.overview_view import OverviewView
        ctx = _make_ctx()
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=json_path):
            w = OverviewView(ctx)
            # Force reload with patched path
            with patch("app.views.overview_view._resolve_projects_json",
                       return_value=json_path):
                w._load_projects()
        return w

    def test_empty_projects_gives_zero_rows(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, [])
        assert w._table.rowCount() == 0

    def test_three_projects_gives_three_rows(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        assert w._table.rowCount() == 3

    def test_project_name_in_first_column(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        assert "厦门" in w._table.item(0, 0).text()

    def test_directory_in_second_column(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        assert "/tmp/xiamen" in w._table.item(0, 1).text()

    def test_location_in_fourth_column(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        assert "厦门" in w._table.item(0, 3).text()

    def test_collector_in_fifth_column(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        assert "王博士" in w._table.item(0, 4).text()

    def test_action_cell_widget_in_sixth_column(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        # Should have a QWidget in the operations cell, not a plain QTableWidgetItem
        cell = w._table.cellWidget(0, 5)
        assert cell is not None

    def test_action_cell_has_enter_workspace_button(self, tmp_path):
        from PyQt6.QtWidgets import QPushButton
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        cell = w._table.cellWidget(0, 5)
        btns = cell.findChildren(QPushButton)
        labels = [b.text() for b in btns]
        assert any("进入工作区" in t for t in labels)

    def test_action_cell_has_detail_button(self, tmp_path):
        from PyQt6.QtWidgets import QPushButton
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        cell = w._table.cellWidget(0, 5)
        btns = cell.findChildren(QPushButton)
        labels = [b.text() for b in btns]
        assert any("详情" in t for t in labels)


# ── Year-filter narrows rows ──────────────────────────────────────────────────

class TestYearFilter:
    def _make_view_with_projects(self, tmp_path, projects):
        json_path = tmp_path / "user_projects.json"
        json_path.write_text(
            json.dumps({"version": 1, "projects": projects}), encoding="utf-8"
        )
        from app.views.overview_view import OverviewView
        ctx = _make_ctx()
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=json_path):
            w = OverviewView(ctx)
            with patch("app.views.overview_view._resolve_projects_json",
                       return_value=json_path):
                w._load_projects()
        return w

    def test_filter_2026_shows_two_rows(self, tmp_path):
        """_SAMPLE_PROJECTS has 2 x 2026 projects and 1 x 2025."""
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        w._set_year_filter("2026")
        assert w._table.rowCount() == 2

    def test_filter_2025_shows_one_row(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        w._set_year_filter("2025")
        assert w._table.rowCount() == 1

    def test_filter_all_shows_all_rows(self, tmp_path):
        w = self._make_view_with_projects(tmp_path, _SAMPLE_PROJECTS)
        w._set_year_filter("2026")
        w._set_year_filter(None)
        assert w._table.rowCount() == 3


# ── Detail dialog ─────────────────────────────────────────────────────────────

class TestDetailDialog:
    def test_constructs_without_error(self):
        from app.views.overview_view import _ProjectDetailDialog
        dlg = _ProjectDetailDialog(_SAMPLE_PROJECTS[0])
        assert dlg is not None

    def test_shows_project_name(self):
        from app.views.overview_view import _ProjectDetailDialog
        from PyQt6.QtWidgets import QLabel
        dlg = _ProjectDetailDialog(_SAMPLE_PROJECTS[0])
        labels = dlg.findChildren(QLabel)
        texts = " ".join(lbl.text() for lbl in labels)
        assert "厦门" in texts

    def test_constructs_without_error_no_directory(self):
        """Detail dialog must not crash for a project dict with no directory."""
        from app.views.overview_view import _ProjectDetailDialog
        proj = {"id": "x", "name": "无目录项目", "year": "2026"}
        dlg = _ProjectDetailDialog(proj)
        assert dlg is not None

    def test_constructs_with_real_directory(self, tmp_path):
        """Stat cards block must not crash even when the directory is empty."""
        from app.views.overview_view import _ProjectDetailDialog
        proj = {
            "id": "y",
            "name": "有目录项目",
            "year": "2026",
            "directory": str(tmp_path),
        }
        dlg = _ProjectDetailDialog(proj)
        assert dlg is not None

    def test_stat_cards_show_numbers(self, tmp_path):
        """Stat cards should display numeric labels (possibly '0')."""
        from app.views.overview_view import _ProjectDetailDialog
        from PyQt6.QtWidgets import QLabel
        proj = {
            "id": "z",
            "name": "统计测试",
            "year": "2026",
            "directory": str(tmp_path),
        }
        dlg = _ProjectDetailDialog(proj)
        # Find the stat-value labels: they are QLabel children of QFrame#StatCard.
        # We look for labels whose text is a digit string or '—'.
        labels = dlg.findChildren(QLabel)
        numeric_labels = [l for l in labels if l.text().isdigit() or l.text() == "—"]
        # Stat cards: 3 value labels expected (specimenCount / resultCount / pending)
        assert len(numeric_labels) >= 3


# ── enter_workspace_requested signal ─────────────────────────────────────────

class TestEnterWorkspaceSignal:
    def test_signal_emitted_with_directory(self, tmp_path):
        from app.views.overview_view import OverviewView
        json_path = tmp_path / "user_projects.json"
        # Directory must really exist — entering an offline/missing dir now
        # warns (modal) instead of fabricating a ghost workspace.
        (tmp_path / "proj_dir").mkdir()
        proj = {
            "id": "s001",
            "name": "Test",
            "directory": str(tmp_path / "proj_dir"),
            "year": "2026",
        }
        json_path.write_text(
            json.dumps({"version": 1, "projects": [proj]}), encoding="utf-8"
        )
        ctx = _make_ctx()
        emitted = []
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=json_path):
            w = OverviewView(ctx)
            with patch("app.views.overview_view._resolve_projects_json",
                       return_value=json_path):
                w._load_projects()
        w.enter_workspace_requested.connect(lambda d: emitted.append(d))
        # Simulate clicking 進入工作區 — call handler directly
        w._on_enter_workspace(proj)
        assert len(emitted) == 1
        assert emitted[0] == str(tmp_path / "proj_dir")


# ── Row stats chip ────────────────────────────────────────────────────────────

class TestRowStatsChip:
    """Row stats chip: inline stats under project name for real projects."""

    def _make_view_with_real_project(self, tmp_path):
        """Create a view with one real project that has a directory on disk."""
        proj_dir = tmp_path / "real_proj"
        proj_dir.mkdir()
        (proj_dir / "incoming-jpg").mkdir()
        (proj_dir / "results").mkdir()
        proj = {
            "id": "chip001",
            "name": "真实项目测试",
            "directory": str(proj_dir),
            "year": "2026",
        }
        json_path = tmp_path / "user_projects.json"
        json_path.write_text(
            json.dumps({"version": 1, "projects": [proj]}), encoding="utf-8"
        )
        from app.views.overview_view import OverviewView
        ctx = _make_ctx()
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=json_path):
            w = OverviewView(ctx)
            with patch("app.views.overview_view._resolve_projects_json",
                       return_value=json_path):
                w._load_projects()
        return w

    def test_chip_text_in_name_cell(self, tmp_path):
        """Name cell text includes stats chip when project has directory."""
        w = self._make_view_with_real_project(tmp_path)
        assert w._table.rowCount() == 1
        cell_text = w._table.item(0, 0).text()
        # Stats chip: must include "标本" or "成片" keywords
        assert "标本" in cell_text or "成片" in cell_text

    def test_chip_contains_numeric_counts(self, tmp_path):
        """Stats chip values are numeric counts (typically 0 for empty dir)."""
        w = self._make_view_with_real_project(tmp_path)
        cell_text = w._table.item(0, 0).text()
        # chip line: "N 标本 · N 成片 · N 待处理"
        assert "0 标本" in cell_text or "标本" in cell_text

    def test_no_chip_for_demo_project(self, tmp_path):
        """Demo projects (isDemo=True) do NOT get the stats chip."""
        demo_proj = {
            "id": "demo001",
            "name": "演示项目",
            "directory": str(tmp_path / "demo_dir"),
            "year": "2026",
            "isDemo": True,
        }
        json_path = tmp_path / "user_projects.json"
        json_path.write_text(
            json.dumps({"version": 1, "projects": [demo_proj]}), encoding="utf-8"
        )
        from app.views.overview_view import OverviewView
        ctx = _make_ctx()
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=json_path):
            w = OverviewView(ctx)
            with patch("app.views.overview_view._resolve_projects_json",
                       return_value=json_path):
                w._load_projects()
        cell_text = w._table.item(0, 0).text()
        # Demo project: no chip line
        assert "待处理" not in cell_text


# ── Detail dialog — results section + subdir controls ──────────────────────

class TestDetailDialogResultsSection:
    """_ProjectDetailDialog results section and subdir controls."""

    def test_detail_dialog_with_directory_constructs(self, tmp_path):
        """Detail dialog must not crash when project has a real directory."""
        from app.views.overview_view import _ProjectDetailDialog
        proj_dir = tmp_path / "proj_with_results"
        proj_dir.mkdir()
        (proj_dir / "results").mkdir()
        (proj_dir / "incoming-jpg").mkdir()
        proj = {"id": "dr001", "name": "有成果项目", "directory": str(proj_dir)}
        dlg = _ProjectDetailDialog(proj)
        assert dlg is not None

    def test_detail_dialog_results_section_no_tifs(self, tmp_path):
        """Detail dialog shows empty-state message when no TIFs in results."""
        from app.views.overview_view import _ProjectDetailDialog
        from PyQt6.QtWidgets import QLabel
        proj_dir = tmp_path / "empty_results"
        proj_dir.mkdir()
        (proj_dir / "results").mkdir()
        proj = {"id": "dr002", "name": "无成果项目", "directory": str(proj_dir)}
        dlg = _ProjectDetailDialog(proj)
        labels = dlg.findChildren(QLabel)
        texts = " ".join(lbl.text() for lbl in labels)
        assert "成果" in texts

    def test_detail_dialog_results_section_with_tifs(self, tmp_path):
        """Detail dialog shows UID list and total count when TIFs present."""
        from app.views.overview_view import _ProjectDetailDialog
        from PyQt6.QtWidgets import QLabel
        proj_dir = tmp_path / "proj_tifs"
        proj_dir.mkdir()
        results = proj_dir / "results"
        results.mkdir()
        (results / "FJ-YGLZ-B2-DLC001-1-RD75E-20260506.tif").write_bytes(b"")
        (results / "FJ-YGLZ-B2-DLC001-2-RD75E-20260506.tif").write_bytes(b"")
        proj = {"id": "dr003", "name": "有TIF项目", "directory": str(proj_dir)}
        dlg = _ProjectDetailDialog(proj)
        labels = dlg.findChildren(QLabel)
        texts = " ".join(lbl.text() for lbl in labels)
        assert "2" in texts  # total count = 2

    def test_subdir_controls_present_when_directory_given(self, tmp_path):
        """Subdir control widgets should be present when directory is set."""
        from app.views.overview_view import _ProjectDetailDialog, _SubdirControlWidget
        proj_dir = tmp_path / "subdir_proj"
        proj_dir.mkdir()
        proj = {"id": "sd001", "name": "子目录测试", "directory": str(proj_dir)}
        dlg = _ProjectDetailDialog(proj)
        ctrls = dlg.findChildren(_SubdirControlWidget)
        # Should have exactly 2: incoming + results
        assert len(ctrls) == 2

    def test_subdir_controls_absent_without_directory(self):
        """Subdir controls should NOT appear when no directory."""
        from app.views.overview_view import _ProjectDetailDialog, _SubdirControlWidget
        proj = {"id": "sd002", "name": "无目录项目"}
        dlg = _ProjectDetailDialog(proj)
        ctrls = dlg.findChildren(_SubdirControlWidget)
        assert len(ctrls) == 0

    def test_lightbox_constructs(self, tmp_path):
        """_ResultLightboxDialog constructs without error with empty item list."""
        from app.views.overview_view import _ResultLightboxDialog
        dlg = _ResultLightboxDialog(items=[], start_idx=0)
        assert dlg is not None

    def test_lightbox_constructs_with_items(self, tmp_path):
        """_ResultLightboxDialog with items must not crash."""
        from app.views.overview_view import _ResultLightboxDialog
        items = [
            {"path": str(tmp_path / "fake.tif"), "name": "fake.tif", "seq": 1},
        ]
        dlg = _ResultLightboxDialog(items=items, start_idx=0)
        assert dlg is not None


# ── defaultToRecentRealProject on_activate ────────────────────────────────────

class TestDefaultToRecentRealProject:
    """on_activate auto-selects the most recent real project when ctx has none."""

    def test_auto_selects_recent_project(self, tmp_path):
        """on_activate must set ctx.current_project_dir to the last real project."""
        json_path = tmp_path / "user_projects.json"
        proj_dir = tmp_path / "auto_proj"
        proj_dir.mkdir()
        projs = [{"id": "ap001", "name": "自动选择项目", "directory": str(proj_dir)}]
        json_path.write_text(
            json.dumps({"version": 1, "projects": projs}), encoding="utf-8"
        )
        from app.views.overview_view import OverviewView
        ctx = _make_ctx(project_dir=None)
        ctx.current_project_dir = None
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=json_path):
            w = OverviewView(ctx)
            w.on_activate()
        # ctx.current_project_dir should be set to the project directory
        assert ctx.current_project_dir == str(proj_dir)

    def test_no_override_if_already_set(self, tmp_path):
        """on_activate must NOT override ctx.current_project_dir if already set."""
        json_path = tmp_path / "user_projects.json"
        proj_dir = tmp_path / "another_proj"
        proj_dir.mkdir()
        projs = [{"id": "ap002", "name": "另一个项目", "directory": str(proj_dir)}]
        json_path.write_text(
            json.dumps({"version": 1, "projects": projs}), encoding="utf-8"
        )
        from app.views.overview_view import OverviewView
        existing_dir = "/tmp/already_set"
        ctx = _make_ctx(project_dir=existing_dir)
        ctx.current_project_dir = existing_dir
        with patch("app.views.overview_view._resolve_projects_json",
                   return_value=json_path):
            w = OverviewView(ctx)
            w.on_activate()
        # Must remain unchanged
        assert ctx.current_project_dir == existing_dir
