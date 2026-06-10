"""test_summary_export_dialog.py — 跨工作区汇总导出对话框冒烟.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_summary_export_dialog.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.db.db_manager import open_project_db

_APP = QApplication.instance() or QApplication([])


def _make_workspace(d: Path) -> None:
    """Materialise a real workspace db so discover_workspaces finds it."""
    d.mkdir(parents=True, exist_ok=True)
    open_project_db(str(d), create=True)


def _dlg(initial_root=None):
    from app.widgets.summary_export_dialog import SummaryExportDialog
    return SummaryExportDialog(ctx=None, initial_root=initial_root)


class TestSummaryExportDialog:
    def test_mode_a_discovers_workspaces(self, tmp_path):
        root = tmp_path / "survey"
        _make_workspace(root / "断面A")
        _make_workspace(root / "断面B")

        d = _dlg(initial_root=str(root))

        assert d._mode_a.isChecked()
        # Two workspaces discovered and listed.
        assert len(d._discovered) == 2
        assert d._a_list.count() == 2
        names = {w["name"] for w in d._discovered}
        assert names == {"断面A", "断面B"}

    def test_mode_a_resolve_dirs_root(self, tmp_path):
        root = tmp_path / "survey"
        _make_workspace(root / "T1")
        d = _dlg(initial_root=str(root))
        dirs, resolved_root = d._resolve_dirs_root()
        assert len(dirs) == 1
        assert os.path.normpath(resolved_root) == os.path.normpath(str(root))

    def test_mode_a_export_writes_three_outputs(self, tmp_path, monkeypatch):
        root = tmp_path / "survey"
        _make_workspace(root / "断面A")
        _make_workspace(root / "断面B")

        d = _dlg(initial_root=str(root))
        # All three outputs checked by default.
        assert d._cb_specimen.isChecked()
        assert d._cb_collection.isChecked()
        assert d._cb_qc.isChecked()

        # Modal message boxes block under offscreen — stub them out.
        monkeypatch.setattr("app.widgets.summary_export_dialog.ui.info",
                            lambda *a, **k: None)
        monkeypatch.setattr("app.widgets.summary_export_dialog.ui.warn",
                            lambda *a, **k: None)

        # Drive the export handler directly (no visible screen needed).
        d._on_export()

        exports = root / "_data" / "exports"
        assert exports.is_dir()
        written = sorted(p.name for p in exports.iterdir())
        # specimen xlsx + collection xlsx + qc html + qc xlsx = 4 files
        assert len(written) >= 3
        assert any(name.endswith(".xlsx") for name in written)
        assert any(name.endswith(".html") for name in written)

    def test_empty_dirs_warns_not_export(self, tmp_path, monkeypatch):
        # Mode B with nothing checked → warn, no crash, no files.
        d = _dlg(initial_root=None)
        assert d._mode_b.isChecked()
        warned = {}
        monkeypatch.setattr(
            "app.widgets.summary_export_dialog.ui.warn",
            lambda *a, **k: warned.setdefault("called", True),
        )
        d._on_export()
        assert warned.get("called") is True

    def test_mode_b_common_root_single(self, tmp_path):
        from app.widgets.summary_export_dialog import SummaryExportDialog
        a = tmp_path / "x" / "y" / "ws"
        root = SummaryExportDialog._common_root([str(a)])
        assert os.path.normpath(root) == os.path.normpath(str(a.parent))

    def test_mode_b_common_root_multiple(self, tmp_path):
        from app.widgets.summary_export_dialog import SummaryExportDialog
        a = tmp_path / "survey" / "A"
        b = tmp_path / "survey" / "B"
        root = SummaryExportDialog._common_root([str(a), str(b)])
        assert os.path.normpath(root) == os.path.normpath(str(tmp_path / "survey"))

    def test_mode_b_checked_dirs(self, tmp_path, monkeypatch):
        # Populate mode B list from a controlled project list.
        import app.widgets.summary_export_dialog as mod
        monkeypatch.setattr(
            mod.project_service, "list_projects",
            lambda _p: [{"name": "P1", "directory": str(tmp_path / "p1")},
                        {"name": "P2", "directory": str(tmp_path / "p2")}],
        )
        d = _dlg(initial_root=None)
        d._populate_recent()
        assert d._b_list.count() == 2
        d._b_list.item(0).setCheckState(Qt.CheckState.Checked)
        checked = d._checked_dirs()
        assert checked == [str(tmp_path / "p1")]
