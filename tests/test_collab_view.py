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

        assert sb._collab_addr.text() == "分享地址: —"

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
