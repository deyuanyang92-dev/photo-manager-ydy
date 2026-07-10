"""test_collab_view.py — TDD tests for 1-J, 1-K, 1-L tasks.

Coverage:
  1-J: task table has CustomContextMenu policy
  1-L: _CollabShareDialog shows address from collab_service.local_address()
  1-K: specimen_sidebar collab strip updates on peers_changed via update_collab_status

Run:
    QT_QPA_PLATFORM=offscreen pytest tests/test_collab_view.py -v --tb=short
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_ctx():
    ctx = MagicMock()
    ctx.collab_service = None
    ctx.settings = MagicMock()
    ctx.settings.operator_name = "test_operator"
    ctx.settings.remote_collab_enabled = False
    ctx.settings.team_code = ""
    ctx.settings.remote_relay_url = ""
    ctx.settings.remote_account_id = ""
    ctx.settings.remote_device_token = ""
    ctx.settings.last_project_dir = ""
    ctx.current_project_dir = ""
    ctx.remote_collab_service = None
    ctx.get_db.return_value = None
    ctx.ensure_collab_service = MagicMock(return_value=None)
    return ctx


@pytest.fixture()
def mock_ctx_with_service():
    ctx = MagicMock()
    svc = MagicMock()
    svc.local_address.return_value = "192.168.1.10:5050"
    svc.peers.return_value = []
    svc.store.all.return_value = []
    svc.store.list_tasks.return_value = []
    svc.group_code = ""
    svc.project_name = ""
    svc.is_running.return_value = True
    svc.ensure_running.return_value = True
    def _set_group_code(code):
        svc.group_code = code
    svc.set_group_code.side_effect = _set_group_code
    ctx.collab_service = svc
    ctx.settings = MagicMock()
    ctx.settings.operator_name = "test_operator"
    ctx.settings.remote_collab_enabled = False
    ctx.settings.team_code = ""
    ctx.settings.remote_relay_url = ""
    ctx.settings.remote_account_id = ""
    ctx.settings.remote_device_token = ""
    ctx.settings.last_project_dir = ""
    ctx.current_project_dir = ""
    ctx.remote_collab_service = None
    ctx.get_db.return_value = None
    ctx.ensure_collab_service = MagicMock(return_value=svc)
    return ctx


@pytest.fixture()
def mock_ctx_with_remote_service(mock_ctx_with_service):
    remote = MagicMock()
    remote.is_available.return_value = True
    remote.create_invite.return_value = SimpleNamespace(
        code="REMOTE-INVITE-1",
        expires_at="12:30",
        permission="read_only",
        scope="proj",
    )
    remote.join_invite.return_value = SimpleNamespace(
        status="requested",
        session_id="S1",
        peer_name="Alice",
        message="请求已发送，等待对方确认。",
    )
    mock_ctx_with_service.remote_collab_service = remote
    return mock_ctx_with_service


# ── 1-J: Task table context menu policy ──────────────────────────────────────

class TestTaskTableContextMenu:
    """CollabView._task_table must have CustomContextMenu policy set."""

    def test_task_table_has_context_menu_policy(self, qtbot, mock_ctx):
        from PyQt6.QtCore import Qt
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        assert (
            view._task_table.contextMenuPolicy()
            == Qt.ContextMenuPolicy.CustomContextMenu
        )

    def test_context_menu_connected(self, qtbot, mock_ctx):
        """customContextMenuRequested signal must have at least one connection."""
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        # Signal must be connected — receivers() returns count of connections
        assert view._task_table.receivers(
            view._task_table.customContextMenuRequested
        ) >= 1


# ── 1-L: Share address dialog ─────────────────────────────────────────────────

class TestCollabShareDialog:
    """_CollabShareDialog must display the address from collab_service.local_address()."""

    def test_share_dialog_shows_addr(self, qtbot, mock_ctx_with_service):
        from app.views.collab_view import _CollabShareDialog

        dlg = _CollabShareDialog(mock_ctx_with_service)
        qtbot.addWidget(dlg)

        assert dlg._addr_edit.text() == "192.168.1.10:5050"

    def test_share_dialog_empty_when_no_service(self, qtbot, mock_ctx):
        from app.views.collab_view import _CollabShareDialog

        dlg = _CollabShareDialog(mock_ctx)
        qtbot.addWidget(dlg)

        assert dlg._addr_edit.text() == ""

    def test_share_dialog_readonly(self, qtbot, mock_ctx_with_service):
        from app.views.collab_view import _CollabShareDialog

        dlg = _CollabShareDialog(mock_ctx_with_service)
        qtbot.addWidget(dlg)

        assert dlg._addr_edit.isReadOnly()

    def test_collab_view_has_share_button(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        assert hasattr(view, "_share_btn"), "CollabView must have _share_btn"
        assert view._share_btn.parent() is view
        assert view._share_btn.isHidden()

    def test_collab_view_shows_beginner_entries_without_service(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        assert view._next_step_label.text() == "下一步：选择协作方式"
        assert view._setup_btn.text() == "设置永久码"
        assert view._setup_btn.objectName() == "Primary"
        assert view._setup_btn.isEnabled()
        assert view._project_code_btn.isEnabled()
        assert not view._share_btn.isEnabled()

    def test_collab_view_disables_remote_when_relay_missing(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        assert view._remote_connection_panel.property("mode") == "remote"
        assert view._remote_invite_btn.text() == "生成远程邀请码"
        assert view._remote_join_code_edit.placeholderText() == "粘贴队友发来的远程邀请码"
        assert view._remote_join_btn.text() == "请求远程连接"
        assert not view._remote_invite_btn.isEnabled()
        assert not view._remote_join_code_edit.isEnabled()
        assert not view._remote_join_btn.isEnabled()
        assert "远程服务未配置" in view._remote_status_label.text()

    def test_collab_view_presents_remote_method_with_relay(self, qtbot, mock_ctx_with_remote_service):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx_with_remote_service)
        qtbot.addWidget(view)

        assert view._setup_btn.text() == "设置永久码"
        assert view._share_btn.text() == "复制局域网连接码"
        assert view._pick_project_btn.text() == "配对项目"
        assert view._project_code_btn.text() == "打开项目码共享"
        assert view._remote_invite_btn.isEnabled()
        assert view._remote_join_code_edit.isEnabled()
        assert view._remote_join_btn.isEnabled()
        assert view._remote_invite_btn.text() == "生成远程邀请码"
        assert view._remote_join_btn.text() == "请求远程连接"
        summary = view._remote_security_summary_label.text()
        assert "账号验证" in summary
        assert "一次性邀请码" in summary

    def test_method_tabs_switch_inline_detail_panels(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        assert view._method_team_btn.property("selected") is True
        assert view._method_stack.currentWidget() is view._team_method_detail

        view._select_collab_method("project")
        assert view._method_project_btn.property("selected") is True
        assert view._method_stack.currentWidget() is view._project_method_detail
        assert view._project_sync_panel is not None

        view._select_collab_method("remote")
        assert view._method_remote_btn.property("selected") is True
        assert view._method_stack.currentWidget() is view._remote_method_detail

    def test_setup_button_opens_inline_team_panel(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        view._on_setup_wizard()

        assert not view._team_setup_panel.isHidden()

    def test_project_code_button_opens_inline_project_panel(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        view._on_project_sync_code()

        assert view._project_sync_panel is not None
        assert not view._project_sync_panel.isHidden()

    def test_remote_invite_generate_uses_remote_service(self, qtbot, mock_ctx_with_remote_service):
        from PyQt6.QtWidgets import QApplication
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx_with_remote_service)
        qtbot.addWidget(view)

        view._on_remote_invite()

        code = QApplication.clipboard().text()
        assert code == "REMOTE-INVITE-1"
        assert view._remote_join_code_edit.text() == code
        assert "远程邀请码已复制" in view._remote_status_label.text()
        mock_ctx_with_remote_service.remote_collab_service.create_invite.assert_called_once()
        mock_ctx_with_remote_service.collab_service.add_manual_peer.assert_not_called()

    def test_remote_join_uses_remote_service_not_lan_peer(self, qtbot, mock_ctx_with_remote_service):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx_with_remote_service)
        qtbot.addWidget(view)

        view._remote_join_code_edit.setText("REMOTE-INVITE-2")
        view._on_remote_join()

        mock_ctx_with_remote_service.remote_collab_service.join_invite.assert_called_once_with(
            "REMOTE-INVITE-2",
            project_name="",
        )
        mock_ctx_with_remote_service.collab_service.add_manual_peer.assert_not_called()
        assert view._remote_join_code_edit.text() == ""
        assert "等待对方确认" in view._remote_status_label.text()

    def test_remote_join_requires_invite_code(self, qtbot, mock_ctx_with_remote_service):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx_with_remote_service)
        qtbot.addWidget(view)

        view._on_remote_join()

        assert "请先粘贴队友发来的远程邀请码" in view._remote_status_label.text()
        mock_ctx_with_remote_service.remote_collab_service.join_invite.assert_not_called()

    def test_collab_view_constructor_does_not_scan_shared_projects(
        self, qtbot, mock_ctx, monkeypatch
    ):
        def fail_scan(**_kwargs):
            raise AssertionError("shared project scan should not run in constructor")

        monkeypatch.setattr(
            "app.widgets.collab_share_project_picker.list_local_share_candidates",
            fail_scan,
        )
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        assert not hasattr(view, "_share_picker")

    def test_manual_connection_uses_dialog_not_inline_panel(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        assert not hasattr(view, "_manual_group")
        assert not view._manual_toggle_btn.isEnabled()

    def test_project_code_dialog_can_generate_code_for_any_local_project(
        self, qtbot, tmp_path, monkeypatch
    ):
        from app.db import db_manager
        from app.services.project_identity_service import (
            ensure_project_identity,
            parse_project_sync_code,
            read_project_identity,
        )
        from app.views.collab_view import _ProjectSyncCodeDialog
        from PyQt6.QtWidgets import QLabel

        def make_workspace(name: str) -> str:
            ws = tmp_path / name
            ws.mkdir()
            db = db_manager.open_project_db(str(ws), create=True)
            try:
                ensure_project_identity(db, project_name=name)
            finally:
                db_manager.close_project_db(str(ws))
            return str(ws.resolve())

        def make_workspace_without_identity(name: str) -> str:
            ws = tmp_path / name
            ws.mkdir()
            db_manager.open_project_db(str(ws), create=True)
            db_manager.close_project_db(str(ws))
            return str(ws.resolve())

        alpha = make_workspace("alpha_project")
        beta = make_workspace_without_identity("beta_project")
        monkeypatch.setattr(
            "app.services.project_service.load_user_projects",
            lambda: [
                {"name": "alpha_project", "directory": alpha},
                {"name": "beta_project", "directory": beta},
            ],
        )
        ctx = MagicMock()
        ctx.current_project_dir = alpha
        ctx.collab_service = None

        dlg = _ProjectSyncCodeDialog(ctx)
        qtbot.addWidget(dlg)

        labels = " ".join(label.text() for label in dlg.findChildren(QLabel))
        assert "第一台电脑" in labels
        assert "第 2、3、4 台电脑" in labels

        idx = dlg._local_project_combo.findText("beta_project")
        assert idx >= 0
        dlg._local_project_combo.setCurrentIndex(idx)

        parsed = parse_project_sync_code(dlg._local_code.text())
        assert parsed["projectName"] == "beta_project"
        assert "beta_project" in dlg._local_path_label.text()
        db = db_manager.open_project_db_private(beta)
        try:
            assert read_project_identity(db) == parsed["projectId"]
        finally:
            db.close()

    def test_project_code_dialog_applies_code_to_selected_local_project(
        self, qtbot, tmp_path, monkeypatch
    ):
        from app.db import db_manager
        from app.services.project_identity_service import (
            project_sync_code,
            read_project_identity,
        )
        from app.views.collab_view import _ProjectSyncCodeDialog

        ws = tmp_path / "local_target"
        ws.mkdir()
        db_manager.open_project_db(str(ws), create=True)
        db_manager.close_project_db(str(ws))
        target = str(ws.resolve())
        monkeypatch.setattr(
            "app.services.project_service.load_user_projects",
            lambda: [{"name": "local_target", "directory": target}],
        )
        ctx = MagicMock()
        ctx.current_project_dir = ""
        ctx.collab_service = None

        dlg = _ProjectSyncCodeDialog(ctx)
        qtbot.addWidget(dlg)
        dlg._join_code.setText(project_sync_code("b" * 32, project_name="remote"))
        dlg._apply_join_code()

        db = db_manager.open_project_db_private(target)
        try:
            assert read_project_identity(db) == "b" * 32
        finally:
            db.close()
        assert "第 3、4 台电脑" in dlg._project_status_label.text()


class TestUserFacingEmptyStates:
    def test_device_empty_state_explains_next_action(self, qtbot):
        from app.services.collab_service import CollabService
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        ctx.collab_service = svc
        ctx.ensure_collab_service = lambda: svc

        view = CollabView(ctx)
        qtbot.addWidget(view)
        view.on_activate()

        assert view._setup_btn.text() == "修改永久码"
        assert view._share_btn.isEnabled()
        assert view._device_panel.isHidden()
        assert "等待队友" in view._connection_result_title.text()
        assert "还未连接成功" in view._connection_result_title.text()
        assert "TEAM-1" in view._connection_result_detail.text()
        assert "等待队友" in view._next_step_label.text()
        view.close()
        svc.stop()

    def test_task_empty_state_points_back_to_workbench(self, qtbot):
        from app.services.collab_service import CollabService
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        ctx.collab_service = svc
        ctx.ensure_collab_service = lambda: svc

        view = CollabView(ctx)
        qtbot.addWidget(view)
        view.on_activate()

        assert view._device_panel.isHidden()
        assert view._task_panel.isHidden()
        assert "等待队友" in view._connection_result_title.text()
        view.close()
        svc.stop()

    def test_team_save_requires_operator_name(self, qtbot):
        from app.services.collab_service import CollabService
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        ctx.collab_service = svc
        ctx.ensure_collab_service = lambda: svc
        ctx.settings = MagicMock()
        ctx.settings.team_code = ""
        ctx.settings.last_project_dir = ""
        ctx.current_project_dir = ""
        ctx.get_db.return_value = None

        view = CollabView(ctx)
        qtbot.addWidget(view)
        view._team_code_edit.setText("TEAM-REQ-OP")
        view._team_operator_edit.clear()
        view._save_team_setup_inline()

        assert "名字" in view._team_setup_status.text()
        assert svc.operator_name == ""
        view.close()

    def test_team_save_shows_post_save_copy_block(self, qtbot):
        from app.services.collab_service import CollabService
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        ctx.collab_service = svc
        ctx.ensure_collab_service = lambda: svc
        ctx.settings = MagicMock()
        ctx.settings.team_code = ""
        ctx.settings.last_project_dir = ""
        ctx.current_project_dir = ""
        ctx.get_db.return_value = None

        view = CollabView(ctx)
        qtbot.addWidget(view)
        view._team_code_edit.setText("TEAM-SAVE-1")
        view._team_operator_edit.setText("小王")
        view._save_team_setup_inline()

        assert not view._team_post_save_frame.isHidden()
        assert view._team_setup_status.isHidden()
        assert view._share_btn.isHidden()
        assert view._team_save_btn.text() == "保存修改"
        assert "等待队友" in view._connection_result_title.text()
        assert svc.operator_name == "小王"
        view.close()
        svc.stop()

    def test_running_service_can_expand_manual_connection(self, qtbot):
        from app.services.collab_service import CollabService
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        ctx.collab_service = svc
        ctx.ensure_collab_service = lambda: svc

        view = CollabView(ctx)
        qtbot.addWidget(view)
        view.on_activate()

        assert view._manual_toggle_btn.isEnabled()
        assert view._shared_scope_btn.isEnabled()
        view.close()
        svc.stop()


# ── 1-K: Sidebar collab strip update ─────────────────────────────────────────

class TestSidebarCollabStrip:
    """SpecimenSidebar.update_collab_status() must update the collab strip labels."""

    def _make_sidebar(self, qtbot, ctx):
        from app.widgets.specimen_sidebar import SpecimenSidebar
        sb = SpecimenSidebar(ctx)
        qtbot.addWidget(sb)
        return sb

    def test_update_collab_status_with_service(self, qtbot, mock_ctx_with_service):
        sb = self._make_sidebar(qtbot, mock_ctx_with_service)
        svc = mock_ctx_with_service.collab_service

        sb.update_collab_status(svc)

        assert "192.168.1.10:5050" in sb._collab_addr.text()

    def test_update_collab_status_none(self, qtbot, mock_ctx):
        sb = self._make_sidebar(qtbot, mock_ctx)

        sb.update_collab_status(None)

        assert sb._collab_addr.text() == "连接地址: —"

    def test_open_collab_view_calls_navigate(self, qtbot, mock_ctx):
        """_open_collab_view must call navigate_to('collab') on the window."""
        from app.widgets.specimen_sidebar import SpecimenSidebar

        sb = SpecimenSidebar(mock_ctx)
        qtbot.addWidget(sb)

        win = MagicMock()
        win.navigate_to = MagicMock()

        with patch.object(sb, "window", return_value=win):
            sb._open_collab_view()

        win.navigate_to.assert_called_once_with("collab")
