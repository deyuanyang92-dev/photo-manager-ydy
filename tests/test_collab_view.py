"""test_collab_view.py — TDD tests for 1-J, 1-K, 1-L tasks.

Coverage:
  1-J: task table has CustomContextMenu policy
  1-L: _CollabShareDialog shows address from collab_service.local_address()
  1-K: specimen_sidebar collab strip updates on peers_changed via update_collab_status

Run:
    QT_QPA_PLATFORM=offscreen pytest tests/test_collab_view.py -v --tb=short
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_ctx():
    ctx = MagicMock()
    ctx.collab_service = None
    ctx.settings = MagicMock()
    ctx.settings.operator_name = "test_operator"
    ctx.get_db.return_value = None
    return ctx


@pytest.fixture()
def mock_ctx_with_service():
    ctx = MagicMock()
    svc = MagicMock()
    svc.local_address.return_value = "192.168.1.10:5050"
    svc.peers.return_value = []
    svc.store.all.return_value = []
    ctx.collab_service = svc
    ctx.settings = MagicMock()
    ctx.settings.operator_name = "test_operator"
    ctx.get_db.return_value = None
    return ctx


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

    def test_collab_view_shows_two_beginner_entries_without_service(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        assert view._next_step_label.text() == "下一步：选择协作方式"
        assert view._setup_btn.text() == "设置永久码"
        # v0.56: 向导按钮挂到主 CTA 旁并降为 Outline(见 TestTeamCodeUpdate)
        assert view._setup_btn.objectName() == "Outline"
        assert view._setup_btn.isEnabled()
        assert view._project_code_btn.isEnabled()
        assert not view._share_btn.isEnabled()




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



class TestUserFacingEmptyStates:
    def test_device_empty_state_explains_next_action(self, qtbot):
        from app.services.collab_service import CollabService
        from app.views.collab_view import CollabView

        ctx = MagicMock()
        svc = CollabService()
        svc._running = True
        svc.set_group_code("TEAM-1")
        ctx.collab_service = svc

        view = CollabView(ctx)
        qtbot.addWidget(view)
        view.on_activate()

        assert view._setup_btn.text() == "修改永久码"
        assert view._share_btn.isEnabled()
        assert "暂无在线设备" in view._device_list.item(0, 0).text()
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

        view = CollabView(ctx)
        qtbot.addWidget(view)
        view.on_activate()

        assert "照片工作区" in view._task_table.item(0, 0).text()
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


class TestTeamCodeUpdate:
    """v0.56: 服务运行中修改永久码必须真正生效(重启服务重新注册 mDNS),
    且「设置/修改永久码」按钮必须真的在界面上(v0.55 后成了孤儿控件)。"""

    def test_setup_btn_is_placed_in_a_layout(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)
        assert view._setup_btn.parentWidget() is not None, (
            "设置/修改永久码按钮必须挂进布局, 不能是孤儿控件"
        )

    def test_changing_code_while_running_restarts_service(self, qtbot, mock_ctx):
        from unittest.mock import MagicMock

        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        svc = MagicMock()
        svc.is_running.return_value = True
        svc.group_code = "TEAM-OLD-111"
        svc.local_address.return_value = "192.168.1.10:5050"
        mock_ctx.ensure_collab_service.return_value = svc
        mock_ctx.collab_service = svc

        view._team_code_edit.setText("TEAM-NEW-222")
        view._team_operator_edit.setText("小王")
        view._save_team_setup_inline()

        svc.set_group_code.assert_called_with("TEAM-NEW-222")
        assert svc.stop.called, "运行中改码必须停旧服务(否则 mDNS 仍广播旧码)"
        assert svc.start.called, "停旧后必须以新码重启"

    def test_same_code_while_running_does_not_restart(self, qtbot, mock_ctx):
        from unittest.mock import MagicMock

        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)

        svc = MagicMock()
        svc.is_running.return_value = True
        svc.group_code = "TEAM-SAME-333"
        svc.local_address.return_value = "192.168.1.10:5050"
        mock_ctx.ensure_collab_service.return_value = svc
        mock_ctx.collab_service = svc

        view._team_code_edit.setText("TEAM-SAME-333")
        view._save_team_setup_inline()

        assert not svc.stop.called, "码没变不应重启服务"


class TestTeamSetupClosedLoop:
    @staticmethod
    def _running_ctx():
        ctx = MagicMock()
        ctx.settings = MagicMock()
        ctx.settings.team_code = "TEAM-SAVED-001"
        ctx.settings.operator_name = "小王"
        ctx.settings.collab_enabled = True
        ctx.current_project_dir = ""
        svc = MagicMock()
        running = [True]
        svc.group_code = "TEAM-SAVED-001"
        svc.is_running.side_effect = lambda: running[0]
        svc.stop.side_effect = lambda: running.__setitem__(0, False)
        svc.local_address.return_value = "192.168.1.10:5050"
        svc.peers.return_value = []
        svc.store.all.return_value = []
        ctx.collab_service = svc
        ctx.ensure_collab_service.return_value = svc
        return ctx, svc

    def test_saved_running_state_is_read_only_with_edit_action(self, qtbot):
        from app.views.collab_view import CollabView

        ctx, _svc = self._running_ctx()
        view = CollabView(ctx)
        qtbot.addWidget(view)

        assert view._team_code_edit.isReadOnly()
        assert view._team_operator_edit.isReadOnly()
        assert view._team_save_btn.isHidden()
        assert not view._setup_btn.isHidden()
        assert view._setup_btn.text() == "修改永久码"

    def test_cancel_edit_restores_saved_values(self, qtbot):
        from app.views.collab_view import CollabView

        ctx, _svc = self._running_ctx()
        view = CollabView(ctx)
        qtbot.addWidget(view)

        view._begin_team_edit()
        assert not view._team_code_edit.isReadOnly()
        assert view._team_save_btn.text() == "保存修改"
        assert not view._team_cancel_btn.isHidden()
        view._team_code_edit.setText("TEAM-NOT-SAVED")
        view._team_operator_edit.setText("未保存名字")

        view._cancel_team_edit()

        assert view._team_code_edit.text() == "TEAM-SAVED-001"
        assert view._team_operator_edit.text() == "小王"
        assert view._team_code_edit.isReadOnly()
        assert "未保存任何更改" in view._team_setup_status.text()

    def test_successful_edit_returns_to_read_only_state(self, qtbot):
        from app.views.collab_view import CollabView

        ctx, svc = self._running_ctx()
        view = CollabView(ctx)
        qtbot.addWidget(view)
        view._begin_team_edit()
        view._team_operator_edit.setText("小李")

        view._save_team_setup_inline()

        assert view._team_code_edit.isReadOnly()
        assert view._team_save_btn.isHidden()
        assert not view._setup_btn.isHidden()
        assert view._team_setup_status.text() == "修改已保存。"
        assert not svc.stop.called

    def test_background_refresh_preserves_midedit_input(self, qtbot):
        # BUG 1: 用户点“修改永久码”进入编辑态后，一次后台刷新
        # (_refresh_team_setup_panel，如 on_activate / 设备刷新触发) 不得用
        # 已保存值回填、冲掉用户尚未保存的输入。
        from app.views.collab_view import CollabView

        ctx, _svc = self._running_ctx()
        view = CollabView(ctx)
        qtbot.addWidget(view)

        view._begin_team_edit()
        assert view._team_editing is True
        view._team_code_edit.setText("TEAM-EDITING-XYZ")
        view._team_operator_edit.setText("编辑中的名字")

        # 模拟后台刷新
        view._refresh_team_setup_panel()

        assert view._team_code_edit.text() == "TEAM-EDITING-XYZ"
        assert view._team_operator_edit.text() == "编辑中的名字"


class TestCollabPageNavigationAndResponsiveLayout:
    def test_page_activation_does_not_start_network_service(
        self, qtbot, mock_ctx_with_service
    ):
        from app.views.collab_view import CollabView

        svc = mock_ctx_with_service.collab_service
        svc.store.list_tasks.return_value = []
        view = CollabView(mock_ctx_with_service)
        qtbot.addWidget(view)

        view.on_activate()

        svc.ensure_running.assert_not_called()

    def test_short_window_scrolls_instead_of_clipping_page(self, qtbot, mock_ctx):
        from PyQt6.QtCore import Qt
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)
        view.resize(940, 480)
        view.show()
        qtbot.wait(10)

        assert view._page_scroll.widgetResizable()
        assert (
            view._page_scroll.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert view._page_scroll.verticalScrollBar().maximum() > 0

    def test_narrow_window_reflows_header_and_method_cards(self, qtbot, mock_ctx):
        from PyQt6.QtWidgets import QBoxLayout
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)
        view.show()

        view.resize(1400, 900)
        qtbot.wait(10)
        assert view._header_layout.direction() == QBoxLayout.Direction.LeftToRight
        assert view._method_layout.direction() == QBoxLayout.Direction.LeftToRight

        view.resize(940, 700)
        qtbot.wait(10)
        assert view._header_layout.direction() == QBoxLayout.Direction.TopToBottom
        assert view._method_layout.direction() == QBoxLayout.Direction.TopToBottom


class TestGuidePulse:
    """v0.56 醒目引导: 未完成时呼吸脉冲, 切走页面必停(项目 QTimer 泄漏红线)。"""

    def test_pulse_runs_while_incomplete(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)  # 无服务 = 未完成
        qtbot.addWidget(view)
        view.on_activate()
        assert not view._guide_frame.isHidden()
        assert view._guide_pulse_timer.isActive(), "未完成时引导应脉冲"

    def test_pulse_stops_on_deactivate(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)
        view.on_activate()
        assert view._guide_pulse_timer.isActive()
        view.on_deactivate()
        assert not view._guide_pulse_timer.isActive(), "切走页面必须停脉冲定时器"
        assert not view._retry_timer.isActive(), "切走页面也应停重试定时器"

    def test_toggle_flips_pulse_property(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)
        view._apply_guide_pulse_property("off")
        view._toggle_guide_pulse()
        assert view._guide_frame.property("pulse") == "on"
        view._toggle_guide_pulse()
        assert view._guide_frame.property("pulse") == "off"

    def test_inline_feedback_hidden_when_empty(self, qtbot, mock_ctx):
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)
        assert view._team_setup_status.isHidden(), "无反馈时不显示常驻灰带"
        view._flash_team_status("团队永久码已复制。")
        assert not view._team_setup_status.isHidden()
        assert view._team_setup_status.text() == "团队永久码已复制。"
        view._flash_team_status("")
        assert view._team_setup_status.isHidden()

    def test_pulse_auto_stops_after_max_toggles(self, qtbot, mock_ctx):
        """v0.57: 呼吸最多 _GUIDE_PULSE_MAX_TOGGLES 次后自动停在高亮态,
        不再「未完成就永远闪」(用户报'一直闪屏')。"""
        from app.views.collab_view import CollabView

        view = CollabView(mock_ctx)
        qtbot.addWidget(view)
        view.on_activate()
        assert view._guide_pulse_timer.isActive()

        for _ in range(view._GUIDE_PULSE_MAX_TOGGLES + 1):
            view._toggle_guide_pulse()

        assert not view._guide_pulse_timer.isActive(), "限次后必须自动停"
        assert view._guide_frame.property("pulse") == "on", "停在高亮态保持醒目"

        # 再次进页重新计数, 重新呼吸一轮
        view.on_deactivate()
        view.on_activate()
        assert view._guide_pulse_timer.isActive()
        assert view._guide_pulse_count == 0
