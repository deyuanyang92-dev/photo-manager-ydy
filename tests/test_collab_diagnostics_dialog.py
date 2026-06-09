"""test_collab_diagnostics_dialog.py — offscreen smoke for the doctor dialog."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.services.collab_service import CollabService, PeerInfo
from app.widgets.collab_diagnostics_dialog import CollabDiagnosticsDialog


_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_dialog_constructs_without_service():
    dlg = CollabDiagnosticsDialog(None)
    assert dlg is not None


def test_dialog_shows_health_and_rows():
    svc = CollabService()
    svc.set_group_code("G1")
    with svc._peers_lock:
        svc._peers["1.1.1.1:5050"] = PeerInfo(ip="1.1.1.1", port=5050, group_code="G2")
    dlg = CollabDiagnosticsDialog(svc)
    # group_mismatch should surface → yellow health, at least one row
    assert svc.overall_health() in ("yellow", "red")
    assert dlg._rows_count() >= 1


def test_dialog_green_when_healthy():
    svc = CollabService()
    svc.set_group_code("G1")
    dlg = CollabDiagnosticsDialog(svc)
    assert svc.overall_health() == "green"
    assert dlg._rows_count() >= 1  # shows the "正常" row


def test_adopt_group_action_changes_code():
    svc = CollabService()
    svc.set_group_code("G1")
    with svc._peers_lock:
        svc._peers["1.1.1.1:5050"] = PeerInfo(ip="1.1.1.1", port=5050, group_code="TEAM-X")
    dlg = CollabDiagnosticsDialog(svc)
    dlg._adopt_group("TEAM-X")
    assert svc.group_code == "TEAM-X"
