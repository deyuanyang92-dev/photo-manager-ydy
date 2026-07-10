from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from app.config.settings import AppSettings
from app.widgets.collab_pairing import decode_pairing, encode_pairing
from app.widgets.collab_setup_wizard import CollabSetupWizard


class FakeCollabService(QObject):
    server_ready = pyqtSignal(int)
    peers_changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.group_code = ""
        self.operator_name = ""
        self.started = []
        self.added_peers = []
        self._running = False

    def set_group_code(self, code: str) -> None:
        self.group_code = code

    def set_operator_name(self, name: str) -> None:
        self.operator_name = name

    def is_running(self) -> bool:
        return self._running

    def start(self, **kwargs) -> None:
        self.started.append(kwargs)
        self._running = True

    def local_address(self) -> str:
        return "192.168.1.10:5050"

    def peers(self) -> list:
        return []

    def add_manual_peer(self, ip: str, port: int) -> None:
        self.added_peers.append((ip, port))


def _ctx(tmp_path):
    QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings._qs.clear()
    settings._qs.sync()
    svc = FakeCollabService()
    return SimpleNamespace(
        settings=settings,
        collab_service=svc,
        current_project_dir=str(tmp_path),
    )


def test_create_mode_autogenerates_group_and_pairing_code(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    dlg = CollabSetupWizard(ctx)
    qtbot.addWidget(dlg)

    assert dlg._group_code_edit.text()
    dlg._operator_edit.setText("小王")
    dlg._go_next()

    assert ctx.collab_service.started
    assert ctx.settings.collab_enabled is True
    assert ctx.settings.team_code == dlg._group_code_edit.text()
    pairing = decode_pairing(dlg._pairing_display.text())
    assert pairing.ip == "192.168.1.10"
    assert pairing.port == 5050
    assert pairing.group_code == ctx.settings.team_code


def test_join_mode_uses_pairing_code_without_manual_group_or_ip(qtbot, tmp_path):
    ctx = _ctx(tmp_path)
    dlg = CollabSetupWizard(ctx)
    qtbot.addWidget(dlg)

    code = encode_pairing("192.168.1.44", 5050, "TEAM-ABC-123")
    dlg._adv_toggle.setChecked(True)
    dlg._pairing_edit.setText(code)
    dlg._operator_edit.setText("小王")
    dlg._go_next()

    assert ctx.settings.team_code == "TEAM-ABC-123"
    assert ctx.collab_service.started[0]["group_code"] == "TEAM-ABC-123"
    assert ctx.collab_service.added_peers == [("192.168.1.44", 5050)]


def test_team_code_is_saved_even_when_service_is_unavailable(qtbot, tmp_path):
    QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings._qs.clear()
    settings._qs.sync()
    ctx = SimpleNamespace(
        settings=settings,
        collab_service=None,
        current_project_dir=str(tmp_path),
    )
    dlg = CollabSetupWizard(ctx)
    qtbot.addWidget(dlg)

    dlg._group_code_edit.setText("TEAM-SAVED")
    dlg._operator_edit.setText("小王")
    dlg._go_next()

    assert ctx.settings.collab_enabled is True
    assert ctx.settings.team_code == "TEAM-SAVED"
