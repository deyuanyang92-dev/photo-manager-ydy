"""test_ui_helper.py — Smoke tests for app.utils.ui.

All tests run under QT_QPA_PLATFORM=offscreen.  They verify the public
API without opening any real dialogs (patched) or blocking the event
loop.
"""
from __future__ import annotations

import sys
import unittest.mock as mock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QTreeView, QWidget


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
        from PyQt6.QtWidgets import QDialog

        with mock.patch("app.utils.ui.QFileDialog") as MockFD:
            inst = MockFD.return_value
            inst.exec.return_value = QDialog.DialogCode.Rejected
            result = get_existing_directory(None, "选择目录")
        assert result == ""

    def test_returns_path_on_accept(self, qapp, tmp_path):
        from app.utils.ui import get_existing_directory
        from PyQt6.QtWidgets import QDialog

        expected = str(tmp_path)
        with mock.patch("app.utils.ui.QFileDialog") as MockFD:
            inst = MockFD.return_value
            inst.exec.return_value = QDialog.DialogCode.Accepted
            inst.selectedFiles.return_value = [expected]
            result = get_existing_directory(None, "选择目录")
        assert result == expected

    def test_returns_current_directory_when_no_selection(self, qapp, tmp_path):
        from app.utils.ui import get_existing_directory
        from PyQt6.QtWidgets import QDialog

        with mock.patch("app.utils.ui.QFileDialog") as MockFD:
            inst = MockFD.return_value
            inst.exec.return_value = QDialog.DialogCode.Accepted
            inst.selectedFiles.return_value = []
            inst.directory.return_value.absolutePath.return_value = str(tmp_path)
            result = get_existing_directory(None, "选择目录", str(tmp_path))
        assert result == str(tmp_path)

    def test_passes_no_native_option(self, qapp):
        from app.utils.ui import get_existing_directory, _NO_NATIVE

        with mock.patch("app.utils.ui.QFileDialog") as MockFD:
            inst = MockFD.return_value
            inst.exec.return_value = 0
            get_existing_directory(None, "选择")
        inst.setOption.assert_any_call(_NO_NATIVE, True)


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


class TestFileDialogSorting:
    def test_priority_terms_match_uid_with_inserted_angle_sequence(self):
        from app.utils.ui import _priority_terms_match

        assert _priority_terms_match(
            "GXFCG-BLW-BZC003-3-R-20260618-广西防城港-白龙尾.tif",
            ["GXFCG-BLW-BZC003-R-20260618"],
        )
        assert not _priority_terms_match(
            "GXFCG-BLW-SC002-RD79-20260618.tif",
            ["GXFCG-BLW-BZC003-R-20260618"],
        )

    def test_sort_dialog_by_mtime_desc_uses_detail_date_column(self, qapp, tmp_path):
        from app.utils.ui import _NO_NATIVE, _sort_dialog_by_mtime_desc

        dlg = QFileDialog(None, "选择照片", str(tmp_path))
        dlg.setOption(_NO_NATIVE, True)

        _sort_dialog_by_mtime_desc(dlg)

        tree = next(
            view for view in dlg.findChildren(QTreeView)
            if view.objectName() == "treeView"
        )
        assert dlg.viewMode() == QFileDialog.ViewMode.Detail
        assert tree.header().sortIndicatorSection() == 3
        assert tree.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder
        assert dlg.width() >= 820

    def test_sort_dialog_can_pin_priority_files_first(self, qapp, tmp_path):
        from app.utils.ui import _NO_NATIVE, _sort_dialog_by_mtime_desc

        priority = tmp_path / "GXFCG-BLW-SC002-R-20260618.tif"
        priority.write_bytes(b"t")
        other = tmp_path / "unrelated.tif"
        other.write_bytes(b"u")
        dlg = QFileDialog(None, "选择照片", str(tmp_path))
        dlg.setOption(_NO_NATIVE, True)

        _sort_dialog_by_mtime_desc(dlg, priority_paths=[str(priority)])

        tree = next(
            view for view in dlg.findChildren(QTreeView)
            if view.objectName() == "treeView"
        )
        assert dlg.proxyModel() is not None
        assert dlg.viewMode() == QFileDialog.ViewMode.Detail
        assert tree.header().sortIndicatorSection() == 0
        assert tree.header().sortIndicatorOrder() == Qt.SortOrder.AscendingOrder

    def test_sort_dialog_can_pin_priority_terms_first(self, qapp, tmp_path):
        from app.utils.ui import _NO_NATIVE, _sort_dialog_by_mtime_desc

        related = tmp_path / "GXFCG-BLW-BZC003-3-R-20260618.tif"
        related.write_bytes(b"t")
        other = tmp_path / "P6191317.JPG"
        other.write_bytes(b"j")
        dlg = QFileDialog(None, "选择照片", str(tmp_path))
        dlg.setOption(_NO_NATIVE, True)

        _sort_dialog_by_mtime_desc(
            dlg,
            priority_terms=["GXFCG-BLW-BZC003-R-20260618"],
        )

        tree = next(
            view for view in dlg.findChildren(QTreeView)
            if view.objectName() == "treeView"
        )
        assert dlg.proxyModel() is not None
        assert dlg.viewMode() == QFileDialog.ViewMode.Detail
        assert tree.header().sortIndicatorSection() == 0
        assert tree.header().sortIndicatorOrder() == Qt.SortOrder.AscendingOrder

    def test_priority_proxy_uses_matching_tif_as_time_anchor(self, qapp, tmp_path):
        from app.utils.ui import _NO_NATIVE, _PriorityFileSortProxy

        related_tif = tmp_path / "GXFCG-BLW-BZC003-3-R-20260618.tif"
        near_jpg = tmp_path / "P6191292.JPG"
        far_jpg = tmp_path / "P6191317.JPG"
        related_tif.write_bytes(b"t")
        near_jpg.write_bytes(b"n")
        far_jpg.write_bytes(b"f")
        anchor = 1_800_000_000
        near = anchor + 8
        far = anchor + 86_400
        for path, ts in ((related_tif, anchor), (near_jpg, near), (far_jpg, far)):
            path.touch()
            import os
            os.utime(path, (ts, ts))

        dlg = QFileDialog(None, "选择照片", str(tmp_path))
        dlg.setOption(_NO_NATIVE, True)
        proxy = _PriorityFileSortProxy(
            [],
            ["GXFCG-BLW-BZC003-R-20260618"],
            parent=dlg,
        )
        dlg.setProxyModel(proxy)
        model = proxy.sourceModel()
        assert model is not None

        related_idx = model.index(str(related_tif))
        near_idx = model.index(str(near_jpg))
        far_idx = model.index(str(far_jpg))

        assert proxy._rank_tuple(related_idx) == (1, 0)
        assert proxy._rank_tuple(near_idx) == (2, 8)
        assert proxy._rank_tuple(far_idx) == (2, 86_400)

    def test_priority_proxy_filters_to_matching_tif_and_nearby_jpgs(self, qapp, tmp_path):
        from app.utils.ui import _NO_NATIVE, _PriorityFileSortProxy

        related_tif = tmp_path / "GXFCG-BLW-BZC003-3-R-20260618.tif"
        near_jpg = tmp_path / "P6191292.JPG"
        far_jpg = tmp_path / "P6191317.JPG"
        related_tif.write_bytes(b"t")
        near_jpg.write_bytes(b"n")
        far_jpg.write_bytes(b"f")
        anchor = 1_800_000_000
        for path, ts in (
            (related_tif, anchor),
            (near_jpg, anchor + 8),
            (far_jpg, anchor + 86_400),
        ):
            import os
            os.utime(path, (ts, ts))

        dlg = QFileDialog(None, "选择照片", str(tmp_path))
        dlg.setOption(_NO_NATIVE, True)
        proxy = _PriorityFileSortProxy(
            [],
            ["GXFCG-BLW-BZC003-R-20260618"],
            ["GXFCG-BLW-BZC003-R-20260618"],
            dlg,
        )
        dlg.setProxyModel(proxy)
        model = proxy.sourceModel()
        assert model is not None

        assert proxy._is_related_file(str(related_tif), anchor) is True
        assert proxy._is_related_file(str(near_jpg), anchor + 8) is True
        assert proxy._is_related_file(str(far_jpg), anchor + 86_400) is False


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
        with mock.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok) as m:
            assert warn(None, "警告", "这是一条警告") == QMessageBox.StandardButton.Ok
        m.assert_called_once()

    def test_info_calls_qmessagebox_information(self, qapp):
        from app.utils.ui import info
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok) as m:
            assert info(None, "提示", "操作成功") == QMessageBox.StandardButton.Ok
        m.assert_called_once()

    def test_question_calls_qmessagebox_question(self, qapp):
        from app.utils.ui import question
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.No) as m:
            result = question(None, "确认", "确定要删除吗？")
        m.assert_called_once()

    def test_critical_calls_qmessagebox_critical(self, qapp):
        from app.utils.ui import critical
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Ok) as m:
            assert critical(None, "错误", "出现严重错误") == QMessageBox.StandardButton.Ok
        m.assert_called_once()

    def test_detailed_message_box_can_copy_diagnostics(self, qapp):
        from app.utils.ui import critical
        from PyQt6.QtWidgets import QMessageBox

        qapp.clipboard().clear()

        def fake_exec(box):
            for button in box.buttons():
                if button.text() == "复制详情":
                    button.click()
                    break
            return QMessageBox.StandardButton.Ok

        with mock.patch.object(QMessageBox, "exec", fake_exec):
            result = critical(
                None,
                "程序遇到错误",
                "boom",
                informative_text="操作没有按预期完成。",
                detailed_text="Traceback line",
            )

        copied = qapp.clipboard().text()
        assert result == QMessageBox.StandardButton.Ok
        assert "Title: 程序遇到错误" in copied
        assert "Message: boom" in copied
        assert "Log:" in copied
        assert "Traceback line" in copied

    def test_warn_with_widget_parent_uses_top_window(self, qapp):
        """warn() should pass the top-level window as parent, not the child."""
        from app.utils.ui import warn
        from PyQt6.QtWidgets import QMessageBox
        root = QWidget()
        child = QWidget(root)
        with mock.patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Ok) as m:
            warn(child, "标题", "内容", buttons=QMessageBox.StandardButton.Ok)
        # First arg to warning() should be the root, not child
        called_parent = m.call_args.args[0]
        assert called_parent is root


# ── dispose_widget / clear_layout_widgets ─────────────────────────────────────

class TestWidgetDisposal:
    def test_setparent_none_promotes_widget_to_window(self, qapp):
        from PyQt6.QtWidgets import QFrame, QVBoxLayout
        from app.utils.ui import dispose_widget

        host = QWidget()
        lay = QVBoxLayout(host)
        child = QFrame(host)
        child.setWindowTitle("PGC001-R")
        lay.addWidget(child)
        lay.removeWidget(child)
        child.setParent(None)
        try:
            assert child.isWindow()
        finally:
            dispose_widget(child)

    def test_dispose_widget_keeps_widget_non_window(self, qapp):
        from PyQt6.QtWidgets import QFrame, QVBoxLayout
        from app.utils.ui import dispose_widget

        host = QWidget()
        lay = QVBoxLayout(host)
        child = QFrame(host)
        child.setWindowTitle("未分组")
        lay.addWidget(child)
        lay.removeWidget(child)
        dispose_widget(child)
        assert not child.isWindow()
