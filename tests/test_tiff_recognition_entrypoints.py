from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_monitor_panel_keeps_tiff_recognition_in_more_menu(qtbot):
    from PyQt6.QtWidgets import QPushButton
    from app.widgets.monitor_panel import MonitorPanel

    panel = MonitorPanel(MagicMock())
    qtbot.addWidget(panel)
    emitted = []
    panel.tiff_recognition_requested.connect(lambda: emitted.append(True))

    visible_toolbar_actions = [
        button.text()
        for button in panel.findChildren(QPushButton)
        if button.isVisibleTo(panel)
    ]
    assert "识别 TIF" not in visible_toolbar_actions

    menu = panel._build_more_menu()
    action = next(
        item for item in menu.actions() if item.text() == "选择 TIF 并识别…"
    )
    action.trigger()

    assert emitted == [True]


def test_manual_tiff_recognition_uses_selected_monitor_tiffs_without_grouping():
    from app.views.workbench_monitor_workflow import WorkbenchMonitorWorkflowMixin

    selected = ["N:/photos/a.tif", "N:/photos/b.tiff"]

    class Harness(WorkbenchMonitorWorkflowMixin):
        _monitor = SimpleNamespace(selected_tiff_paths=lambda: selected)

        def __init__(self):
            self.checked = []

        def _run_tiff_naming_check(self, folder=None, *, paths=None):
            self.checked.append(list(paths or []))

    harness = Harness()
    harness._on_monitor_tiff_recognition()

    assert harness.checked == [selected]


def test_manual_tiff_recognition_opens_tiff_picker_when_queue_has_no_selection(
    monkeypatch,
):
    from PyQt6.QtWidgets import QFileDialog

    from app.views.workbench_monitor_workflow import WorkbenchMonitorWorkflowMixin

    chosen = ["N:/legacy/one.tif"]
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        lambda *args, **kwargs: (chosen, "TIFF 成片"),
    )

    class Harness(WorkbenchMonitorWorkflowMixin):
        _monitor = SimpleNamespace(selected_tiff_paths=lambda: [])

        def __init__(self):
            self.checked = []

        def _run_tiff_naming_check(self, folder=None, *, paths=None):
            self.checked.append(list(paths or []))

    harness = Harness()
    harness._on_monitor_tiff_recognition()

    assert harness.checked == [chosen]


def test_incoming_recognition_uses_only_current_incoming_tiffs(tmp_path):
    from app.views.workbench_monitor_workflow import WorkbenchMonitorWorkflowMixin

    incoming = tmp_path / "incoming-jpg"
    results = tmp_path / "results"
    incoming.mkdir()
    results.mkdir()
    incoming_tiff = incoming / "GXFCG-BLW-SC001-1-R-20260618.tif"
    result_tiff = results / "GXFCG-BLW-SC002-1-R-20260618.tif"
    incoming_tiff.write_bytes(b"tif")
    result_tiff.write_bytes(b"tif")

    class Harness(WorkbenchMonitorWorkflowMixin):
        _last_scan_result = SimpleNamespace(
            incoming_jpg_dir=str(incoming),
            tiff_files=[
                SimpleNamespace(path=str(incoming_tiff)),
                SimpleNamespace(path=str(result_tiff)),
            ],
        )

        def __init__(self):
            self.checked = []

        def _run_tiff_naming_check(self, folder=None, *, paths=None):
            self.checked.append(list(paths or []))

    harness = Harness()
    harness._on_incoming_tiff_recognition()

    assert harness.checked == [[str(incoming_tiff)]]


def test_automatic_monitor_recognition_marks_valid_and_incomplete_tiffs():
    from app.services.tiff_naming_service import annotate_tiff_entries

    valid = SimpleNamespace(
        name="GXFCG-BLW-SC001-1-R-20260618.tif",
        path="N:/incoming/GXFCG-BLW-SC001-1-R-20260618.tif",
        attributed_specimen_id=None,
        naming_ok=None,
    )
    incomplete = SimpleNamespace(
        name="HeliconFocus.tif",
        path="N:/incoming/HeliconFocus.tif",
        attributed_specimen_id=None,
        naming_ok=None,
    )

    annotate_tiff_entries([valid, incomplete])

    assert valid.naming_ok is True
    assert valid.attributed_specimen_id == "GXFCG-BLW-SC001-R-20260618"
    assert incomplete.naming_ok is False
    assert incomplete.attributed_specimen_id is None


def test_monitor_scan_silently_annotates_tiff_cards_before_rendering():
    from app.views.workbench_monitor_workflow import WorkbenchMonitorWorkflowMixin

    entry = SimpleNamespace(
        name="GXFCG-BLW-SC001-1-R-20260618.tif",
        path="N:/incoming/GXFCG-BLW-SC001-1-R-20260618.tif",
        attributed_specimen_id=None,
        naming_ok=None,
    )
    scan = SimpleNamespace(tiff_files=[entry])
    rendered = []

    class Harness(WorkbenchMonitorWorkflowMixin):
        _monitor = SimpleNamespace(load_scan=lambda result: rendered.append(result))

        def _load_naming_components(self):
            return ["province", "site", "species_id", "storage", "date_seg"]

        def _monitor_display_scan_result(self, result):
            return result

        def _refresh_workflow_dashboard(self):
            pass

        def _maybe_auto_process_new_tiff(self, result):
            pass

    harness = Harness()
    harness._apply_monitor_scan_result(scan)

    assert rendered == [scan]
    assert entry.naming_ok is True
    assert entry.attributed_specimen_id == "GXFCG-BLW-SC001-R-20260618"
