"""入口确认：取消则不得写盘 / 改绑 / 取消归属。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QMessageBox


def test_organise_cancel_does_not_start_worker(tmp_path, monkeypatch, qtbot):
    from app.services.grouping_service import Group, SpecimenGrouping
    from app.views.workbench_view import WorkbenchView
    from tests.test_workbench_view import _make_ctx, _make_db

    project_dir = str(tmp_path / "proj")
    Path(project_dir, "_data").mkdir(parents=True)
    Path(project_dir, "results").mkdir()
    db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
    uid = "FJ-XM-B2-DLC001-T95E-20260601"
    j1 = tmp_path / "a.jpg"
    j2 = tmp_path / "b.jpg"
    j1.write_bytes(b"j")
    j2.write_bytes(b"j")
    tiff = Path(project_dir) / "incoming-jpg" / "x.tif"
    tiff.parent.mkdir(parents=True, exist_ok=True)
    tiff.write_bytes(b"t")
    grouping = SpecimenGrouping(
        uid=uid,
        groups=[
            Group(
                group_index=0,
                jpg_paths=[str(j1), str(j2)],
                composed_tiff_path=str(tiff),
                status="composed",
            )
        ],
    )
    w = WorkbenchView(_make_ctx(project_dir=project_dir, db=db))
    qtbot.addWidget(w)
    w._grouping.load_grouping(uid, grouping)
    monkeypatch.setattr(w, "_maybe_rename_tiff_before_organize", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.services.organize_service._check_organize_gate",
        lambda *a, **k: None,
    )
    started = {"n": 0}

    def _fake_start(self):
        started["n"] += 1

    monkeypatch.setattr(
        "app.workers.supp_compression_worker.SuppCompressionWorker.start",
        _fake_start,
    )
    monkeypatch.setattr(
        "app.utils.ui.question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )

    assert w._on_organise_requested(uid, 0) is False
    assert started["n"] == 0
    db.close()


def test_unassign_cancel_keeps_group(tmp_path, monkeypatch, qtbot):
    from app.services.grouping_service import Group, save_grouping, load_grouping
    from app.widgets.monitor_panel import MonitorPanel
    from tests.test_workbench_view import _make_ctx, _make_db

    db = _make_db(str(tmp_path / "project.db"))
    p = str(tmp_path / "g.jpg")
    save_grouping(
        db,
        "ZJ-TMW-B2-001",
        [Group(group_index=0, jpg_paths=[p])],
        clean_phantoms=False,
    )
    panel = MonitorPanel(_make_ctx(project_dir=str(tmp_path), db=db))
    qtbot.addWidget(panel)
    monkeypatch.setattr(
        "app.utils.ui.question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )
    panel._on_ctx_unassign(p)
    paths = [x for g in load_grouping(db, "ZJ-TMW-B2-001").groups for x in g.jpg_paths]
    assert p in paths
    db.close()
