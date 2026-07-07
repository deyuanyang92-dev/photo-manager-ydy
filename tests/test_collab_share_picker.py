"""Tests for CollabShareProjectPicker widget."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication

from app.db.db_manager import open_project_db
from app.services.project_identity_service import ensure_project_identity
from app.widgets.collab_share_project_picker import CollabShareProjectPicker


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def _make_workspace(tmp_path: Path, name: str) -> str:
    ws = tmp_path / name
    (ws / "_data").mkdir(parents=True)
    db = open_project_db(str(ws), create=True)
    try:
        ensure_project_identity(db, project_name=name)
    finally:
        db.close()
    return str(ws.resolve())


def test_picker_lists_and_applies_selection(qtbot, tmp_path, monkeypatch):
    a = _make_workspace(tmp_path, "proj_a")
    b = _make_workspace(tmp_path, "proj_b")
    monkeypatch.setattr(
        "app.services.project_service.load_user_projects",
        lambda: [
            {"name": "proj_a", "directory": a},
            {"name": "proj_b", "directory": b},
        ],
    )
    from PyQt6.QtCore import QSettings
    from app.config.settings import _APP, _ORG
    qs = QSettings(_ORG, _APP)
    key = "collab/shared_project_dirs"
    old = qs.value(key, "", type=str)
    svc = SimpleNamespace(_shared_project_dirs=set())
    def _set_shared(dirs):
        svc._shared_project_dirs = set(dirs)
    svc.set_shared_project_dirs = _set_shared
    ctx = SimpleNamespace(
        current_project_dir=a,
        settings=SimpleNamespace(_qs=qs),
        collab_service=svc,
    )
    try:
        picker = CollabShareProjectPicker(ctx)
        qtbot.addWidget(picker)
        assert len(picker._checks) == 2
        picker.set_all_checked(False)
        picker._checks[a].setChecked(True)
        selected = picker.apply_selection()
        assert selected == {a}
        assert svc._shared_project_dirs == {a}
    finally:
        qs.setValue(key, old)


def test_picker_can_defer_project_scan_until_reload(qtbot, tmp_path, monkeypatch):
    a = _make_workspace(tmp_path, "proj_a")
    monkeypatch.setattr(
        "app.services.project_service.load_user_projects",
        lambda: [{"name": "proj_a", "directory": a}],
    )
    from PyQt6.QtCore import QSettings
    from app.config.settings import _APP, _ORG
    qs = QSettings(_ORG, _APP)
    key = "collab/shared_project_dirs"
    old = qs.value(key, "", type=str)
    ctx = SimpleNamespace(
        current_project_dir=a,
        settings=SimpleNamespace(_qs=qs),
        collab_service=None,
    )
    try:
        picker = CollabShareProjectPicker(ctx, autoload=False)
        qtbot.addWidget(picker)
        assert picker._checks == {}
        assert not picker._loaded

        picker.reload()

        assert picker._loaded
        assert list(picker._checks) == [a]
    finally:
        qs.setValue(key, old)


def test_picker_filters_previews_and_selects_current(qtbot, tmp_path, monkeypatch):
    a = _make_workspace(tmp_path, "alpha_project")
    b = _make_workspace(tmp_path, "beta_project")
    monkeypatch.setattr(
        "app.services.project_service.load_user_projects",
        lambda: [
            {"name": "alpha_project", "directory": a},
            {"name": "beta_project", "directory": b},
        ],
    )
    from PyQt6.QtCore import QSettings
    from app.config.settings import _APP, _ORG
    qs = QSettings(_ORG, _APP)
    key = "collab/shared_project_dirs"
    old = qs.value(key, "", type=str)
    svc = SimpleNamespace(_shared_project_dirs=set(), set_shared_project_dirs=lambda dirs: None)
    ctx = SimpleNamespace(
        current_project_dir=a,
        settings=SimpleNamespace(_qs=qs),
        collab_service=svc,
    )
    try:
        picker = CollabShareProjectPicker(ctx)
        qtbot.addWidget(picker)

        picker._search_edit.setText("beta")
        assert picker._checks[a].isHidden()
        assert not picker._checks[b].isHidden()
        assert "beta_project" in picker._preview_label.text()

        picker._current_btn.click()
        assert picker.selected_directories() == {a}
        assert "alpha_project" in picker._preview_label.text()
        assert "已选择 1 个项目" in picker._summary_label.text()
    finally:
        qs.setValue(key, old)
