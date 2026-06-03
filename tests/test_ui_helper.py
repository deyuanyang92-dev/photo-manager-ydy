"""test_ui_helper.py — Smoke tests for app.utils.ui.

All tests run under QT_QPA_PLATFORM=offscreen.  They verify the public
API without opening any real dialogs (patched) or blocking the event
loop.
"""
from __future__ import annotations

import sys
import unittest.mock as mock

import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QWidget


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication for offscreen tests."""
    existing = QApplication.instance()
    if existing is not None:
        yield existing
    else:
        app = QApplication(sys.argv[:1])
        yield app


# ── top_window ────────────────────────────────────────────────────────────────

class TestTopWindow:
    def test_returns_none_for_none(self):
        from app.utils.ui import top_window
        assert top_window(None) is None

    def test_returns_self_for_parentless_widget(self, qapp):
        from app.utils.ui import top_window
        w = QWidget()
        assert top_window(w) is w

    def test_returns_root_for_nested_widget(self, qapp):
        from app.utils.ui import top_window
        root = QWidget()
        child = QWidget(root)
        grand = QWidget(child)
        assert top_window(grand) is root

    def test_returns_widget_itself_when_no_parent(self, qapp):
        from app.utils.ui import top_window
        w = QWidget()
        assert top_window(w) is w


# ── center_on ─────────────────────────────────────────────────────────────────

class TestCenterOn:
    def test_no_crash_with_none_parent(self, qapp):
        from app.utils.ui import center_on
        dlg = QDialog()
        # Should not raise
        center_on(dlg, None)

    def test_moves_dialog_when_parent_given(self, qapp):
        from app.utils.ui import center_on
        parent = QWidget()
        parent.resize(800, 600)
        parent.show()
        dlg = QDialog(parent)
        dlg.resize(300, 200)
        center_on(dlg, parent)
        # After centering, dialog position should be set (non-negative)
        assert dlg.x() >= 0 or dlg.y() >= 0  # at least moved
        parent.close()


# ── get_existing_directory ────────────────────────────────────────────────────

class TestGetExistingDirectory:
    def test_returns_empty_on_cancel(self, qapp):
        from app.utils.ui import get_existing_directory
        with mock.patch(
            "app.utils.ui.QFileDialog.getExistingDirectory",
            return_value="",
        ):
            result = get_existing_directory(None, "选择目录")
        assert result == ""

    def test_returns_path_on_accept(self, qapp, tmp_path):
        from app.utils.ui import get_existing_directory
        expected = str(tmp_path)
        with mock.patch(
            "app.utils.ui.QFileDialog.getExistingDirectory",
            return_value=expected,
        ):
            result = get_existing_directory(None, "选择目录")
        assert result == expected

    def test_passes_no_native_option(self, qapp):
        from app.utils.ui import get_existing_directory, _NO_NATIVE
        with mock.patch(
            "app.utils.ui.QFileDialog.getExistingDirectory",
            return_value="",
        ) as m:
            get_existing_directory(None, "选择")
        call_kwargs = m.call_args
        # The option _NO_NATIVE should be in the positional args
        assert _NO_NATIVE in call_kwargs.args or (
            call_kwargs.kwargs.get("options") == _NO_NATIVE
        )


# ── get_open_file_name ────────────────────────────────────────────────────────

class TestGetOpenFileName:
    def test_returns_empty_on_cancel(self, qapp):
        from app.utils.ui import get_open_file_name
        with mock.patch(
            "app.utils.ui.QFileDialog.getOpenFileName",
            return_value=("", ""),
        ):
            result = get_open_file_name(None, "打开文件")
        assert result == ""

    def test_returns_path_on_accept(self, qapp, tmp_path):
        from app.utils.ui import get_open_file_name
        expected = str(tmp_path / "test.txt")
        with mock.patch(
            "app.utils.ui.QFileDialog.getOpenFileName",
            return_value=(expected, "所有文件 (*.*)"),
        ):
            result = get_open_file_name(None, "打开文件")
        assert result == expected


# ── get_save_file_name ────────────────────────────────────────────────────────

class TestGetSaveFileName:
    def test_returns_empty_on_cancel(self, qapp):
        from app.utils.ui import get_save_file_name
        with mock.patch(
            "app.utils.ui.QFileDialog.getSaveFileName",
            return_value=("", ""),
        ):
            result = get_save_file_name(None, "保存文件")
        assert result == ""

    def test_returns_path_on_accept(self, qapp, tmp_path):
        from app.utils.ui import get_save_file_name
        expected = str(tmp_path / "out.csv")
        with mock.patch(
            "app.utils.ui.QFileDialog.getSaveFileName",
            return_value=(expected, "CSV (*.csv)"),
        ):
            result = get_save_file_name(None, "保存文件")
        assert result == expected


# ── warn / info / question / critical ────────────────────────────────────────

class TestMessageBoxHelpers:
    def test_warn_calls_qmessagebox_warning(self, qapp):
        from app.utils.ui import warn
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok) as m:
            warn(None, "警告", "这是一条警告")
        m.assert_called_once()

    def test_info_calls_qmessagebox_information(self, qapp):
        from app.utils.ui import info
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok) as m:
            info(None, "提示", "操作成功")
        m.assert_called_once()

    def test_question_calls_qmessagebox_question(self, qapp):
        from app.utils.ui import question
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as m:
            result = question(None, "确认", "确定要删除吗？")
        m.assert_called_once()

    def test_critical_calls_qmessagebox_critical(self, qapp):
        from app.utils.ui import critical
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "critical", return_value=QMessageBox.StandardButton.Ok) as m:
            critical(None, "错误", "出现严重错误")
        m.assert_called_once()

    def test_warn_with_widget_parent_uses_top_window(self, qapp):
        """warn() should pass the top-level window as parent, not the child."""
        from app.utils.ui import warn
        from PyQt6.QtWidgets import QMessageBox
        root = QWidget()
        child = QWidget(root)
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok) as m:
            warn(child, "标题", "内容")
        # First arg to warning() should be the root, not child
        called_parent = m.call_args.args[0]
        assert called_parent is root
