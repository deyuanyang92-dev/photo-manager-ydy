"""test_monitor_panel.py — Tests for 1-C, 1-D, and 2-A context menu actions.

1-C: Right-click context menu on JPG cards has "复制路径" action.
1-D: "隐藏已分组原片" checkbox hides cards where is_grouped=True.
2-A: Context menu "加入当前分组" / "指定归属标本" / "取消归属" actions.
"""

import os
import sqlite3
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from PyQt6.QtWidgets import QApplication, QMenu
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtTest import QTest

from app.services.monitor_service import FileEntry, ScanResult
from app.services import grouping_service, activation_service
from app.widgets.monitor_panel import MonitorPanel, _FileCard


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ctx():
    """Minimal AppContext stub."""
    c = MagicMock()
    c.get_db.return_value = None
    return c


@pytest.fixture
def db():
    """In-memory SQLite DB with tasks + grouping tables ready."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    grouping_service._ensure_grouping_table(conn)
    activation_service._ensure_tasks_table(conn)
    return conn


@pytest.fixture
def ctx_with_db(db):
    """AppContext stub that returns a real in-memory DB."""
    c = MagicMock()
    c.get_db.return_value = db
    return c


@pytest.fixture
def panel_with_db(qtbot, ctx_with_db):
    w = MonitorPanel(ctx_with_db)
    qtbot.addWidget(w)
    w.show()
    return w


@pytest.fixture
def panel(qtbot, ctx):
    w = MonitorPanel(ctx)
    qtbot.addWidget(w)
    w.show()
    return w


def _jpg_entry(name="photo.jpg", path="/tmp/photo.jpg", is_grouped=False):
    e = FileEntry(
        name=name,
        path=path,
        kind="jpg",
        size=1000,
        mtime="2026-01-01T00:00:00+00:00",
    )
    e.is_grouped = is_grouped
    return e


def _scan(jpg_entries, tiff_entries=None):
    return ScanResult(
        project_dir="/tmp",
        jpg_files=list(jpg_entries),
        tiff_files=list(tiff_entries or []),
    )


# ── 1-C: clipboard copy action ────────────────────────────────────────────────

class TestClipboardCopyAction:
    def test_clipboard_copy_action_exists(self, qtbot, ctx):
        """_FileCard right-click context menu must contain '复制路径'."""
        entry = _jpg_entry(path="/tmp/myfile.jpg")
        card = _FileCard(entry)
        qtbot.addWidget(card)

        # Simulate context menu event by triggering _on_jpg_context_menu
        # directly (or via the card's context menu method)
        assert hasattr(card, "_on_jpg_context_menu"), \
            "_FileCard must implement _on_jpg_context_menu method"

        # Capture menu actions by patching QMenu.exec
        actions_seen = []
        original_exec = QMenu.exec

        def fake_exec(self_menu, *args, **kwargs):
            actions_seen.extend([a.text() for a in self_menu.actions()])
            return None

        with patch.object(QMenu, "exec", fake_exec):
            card._on_jpg_context_menu(QPoint(0, 0))

        assert "复制路径" in actions_seen, \
            f"Expected '复制路径' in context menu, got: {actions_seen}"

    def test_copy_action_sets_clipboard(self, qtbot, ctx):
        """Triggering '复制路径' puts the path into the clipboard."""
        path = "/tmp/specimen_photo.jpg"
        entry = _jpg_entry(path=path)
        card = _FileCard(entry)
        qtbot.addWidget(card)

        # Capture which action was triggered
        triggered_action = None

        def fake_exec(self_menu, *args, **kwargs):
            for a in self_menu.actions():
                if a.text() == "复制路径":
                    a.trigger()
            return None

        with patch.object(QMenu, "exec", fake_exec):
            card._on_jpg_context_menu(QPoint(0, 0))

        clipboard = QApplication.clipboard()
        assert clipboard.text() == path


# ── 1-D: hide archived filter ─────────────────────────────────────────────────

class TestHideArchivedFilter:
    def test_hide_grouped_checkbox_exists(self, panel):
        """MonitorPanel must have a '隐藏已分组原片' QCheckBox."""
        from PyQt6.QtWidgets import QCheckBox
        checkboxes = panel.findChildren(QCheckBox)
        labels = [c.text() for c in checkboxes]
        assert "隐藏已分组原片" in labels, \
            f"Expected '隐藏已分组原片' checkbox, found: {labels}"

    def test_hide_grouped_filter(self, panel, qtbot):
        """When '隐藏已分组原片' is checked, grouped files are hidden."""
        from PyQt6.QtWidgets import QCheckBox

        grouped_entry = _jpg_entry("grouped.jpg", "/tmp/grouped.jpg", is_grouped=True)
        solo_entry = _jpg_entry("solo.jpg", "/tmp/solo.jpg", is_grouped=False)

        panel.load_scan(_scan([grouped_entry, solo_entry]))

        # Initially both cards visible
        visible_names_before = _visible_card_names(panel)
        assert "grouped.jpg" in visible_names_before
        assert "solo.jpg" in visible_names_before

        # Check the checkbox
        cb = next(c for c in panel.findChildren(QCheckBox) if c.text() == "隐藏已分组原片")
        cb.setChecked(True)

        visible_names_after = _visible_card_names(panel)
        assert "grouped.jpg" not in visible_names_after, \
            "grouped.jpg should be hidden when '隐藏已分组原片' is checked"
        assert "solo.jpg" in visible_names_after

    def test_uncheck_restores_all_files(self, panel, qtbot):
        """Unchecking '隐藏已分组原片' restores all files."""
        from PyQt6.QtWidgets import QCheckBox

        grouped_entry = _jpg_entry("grouped.jpg", "/tmp/grouped.jpg", is_grouped=True)
        solo_entry = _jpg_entry("solo.jpg", "/tmp/solo.jpg", is_grouped=False)
        panel.load_scan(_scan([grouped_entry, solo_entry]))

        cb = next(c for c in panel.findChildren(QCheckBox) if c.text() == "隐藏已分组原片")
        cb.setChecked(True)
        cb.setChecked(False)

        visible_names = _visible_card_names(panel)
        assert "grouped.jpg" in visible_names
        assert "solo.jpg" in visible_names

    def test_hide_grouped_menu_item_label(self, panel):
        """"…"菜单项与行为一致:隐藏的是已分组原片,不是"已归档"。"""
        actions_seen = []

        def fake_exec(self_menu, *args, **kwargs):
            actions_seen.extend([a.text() for a in self_menu.actions()])
            return None

        with patch.object(QMenu, "exec", fake_exec):
            panel._open_more_menu(QPoint(0, 0))

        assert "隐藏已分组原片" in actions_seen, \
            f"Expected '隐藏已分组原片' in more-menu, got: {actions_seen}"
        assert "隐藏已归档" not in actions_seen


# ── 2-A: enhanced context menu ───────────────────────────────────────────────

class TestContextMenuAddToGroup:
    """2-A: 加入当前分组 — adds jpg_path to the active specimen's first group."""

    def test_context_menu_has_add_to_group_action(self, qtbot, ctx_with_db):
        """Context menu must contain '加入当前分组' item for JPG cards."""
        entry = _jpg_entry(path="/tmp/myfile.jpg")
        card = _FileCard(entry)
        qtbot.addWidget(card)

        actions_seen = []

        def fake_exec(self_menu, *args, **kwargs):
            actions_seen.extend([a.text() for a in self_menu.actions()])
            return None

        with patch.object(QMenu, "exec", fake_exec):
            card._on_jpg_context_menu(QPoint(0, 0))

        assert "加入当前分组" in actions_seen, \
            f"Expected '加入当前分组' in context menu, got: {actions_seen}"

    def test_context_menu_add_to_group(self, qtbot, ctx_with_db, db, tmp_path):
        """加入当前分组: creates a group if none exists and adds jpg_path."""
        jpg = tmp_path / "photo.jpg"
        jpg.write_bytes(b"\xff\xd8" * 10)
        jpg_path = str(jpg)

        # Activate a specimen
        uid = "ZJ-TMW-B2-001"
        db.execute(
            "INSERT INTO tasks (uid, is_active, activated_at) VALUES (?, 1, '2026-01-01T00:00:00+00:00')",
            (uid,),
        )
        db.commit()

        entry = _jpg_entry(path=jpg_path)
        panel = MonitorPanel(ctx_with_db)
        qtbot.addWidget(panel)
        panel.load_scan(_scan([entry]))

        # Trigger the action by patching QMenu.exec to click "加入当前分组"
        def fake_exec(self_menu, *args, **kwargs):
            for a in self_menu.actions():
                if a.text() == "加入当前分组":
                    a.trigger()
            return None

        card = panel._cards[0]
        with patch.object(QMenu, "exec", fake_exec):
            card._on_jpg_context_menu(QPoint(0, 0))

        grouping = grouping_service.load_grouping(db, uid)
        assert len(grouping.groups) >= 1, "Expected at least one group after 加入当前分组"
        all_paths = [p for g in grouping.groups for p in g.jpg_paths]
        assert jpg_path in all_paths, f"{jpg_path} should be in grouping, got {all_paths}"

    def test_context_menu_add_to_group_no_active(self, qtbot, ctx_with_db):
        """加入当前分组 without active specimen shows a warning, no crash."""
        entry = _jpg_entry(path="/tmp/photo.jpg")
        panel = MonitorPanel(ctx_with_db)
        qtbot.addWidget(panel)
        panel.load_scan(_scan([entry]))

        warning_shown = []

        def fake_exec(self_menu, *args, **kwargs):
            for a in self_menu.actions():
                if a.text() == "加入当前分组":
                    a.trigger()
            return None

        def fake_warning(parent, title, msg, *args, **kwargs):
            warning_shown.append((title, msg))
            return None

        from PyQt6.QtWidgets import QMessageBox
        card = panel._cards[0]
        with patch.object(QMenu, "exec", fake_exec), \
             patch.object(QMessageBox, "warning", staticmethod(fake_warning)):
            card._on_jpg_context_menu(QPoint(0, 0))

        assert warning_shown, "Expected a warning when no active specimen"


class TestContextMenuUnassign:
    """2-A: 取消归属 — removes jpg_path from any grouping record."""

    def test_context_menu_has_unassign_action(self, qtbot, ctx_with_db):
        """Context menu must contain '取消归属' item for JPG cards."""
        entry = _jpg_entry(path="/tmp/myfile.jpg")
        card = _FileCard(entry)
        qtbot.addWidget(card)

        actions_seen = []

        def fake_exec(self_menu, *args, **kwargs):
            actions_seen.extend([a.text() for a in self_menu.actions()])
            return None

        with patch.object(QMenu, "exec", fake_exec):
            card._on_jpg_context_menu(QPoint(0, 0))

        assert "取消归属" in actions_seen, \
            f"Expected '取消归属' in context menu, got: {actions_seen}"

    def test_context_menu_unassign(self, qtbot, ctx_with_db, db, tmp_path):
        """取消归属: removes jpg_path from whatever group it belongs to."""
        jpg = tmp_path / "photo.jpg"
        jpg.write_bytes(b"\xff\xd8" * 10)
        jpg_path = str(jpg)

        uid = "ZJ-TMW-B2-001"
        # Pre-populate a group containing jpg_path
        from app.services.grouping_service import Group, save_grouping
        grp = Group(group_index=0, angle_label="A", jpg_paths=[jpg_path])
        save_grouping(db, uid, [grp], clean_phantoms=False)

        entry = _jpg_entry(path=jpg_path)
        panel = MonitorPanel(ctx_with_db)
        qtbot.addWidget(panel)
        panel.load_scan(_scan([entry]))

        def fake_exec(self_menu, *args, **kwargs):
            for a in self_menu.actions():
                if a.text() == "取消归属":
                    a.trigger()
            return None

        card = panel._cards[0]
        with patch.object(QMenu, "exec", fake_exec):
            card._on_jpg_context_menu(QPoint(0, 0))

        grouping = grouping_service.load_grouping(db, uid)
        all_paths = [p for g in grouping.groups for p in g.jpg_paths]
        assert jpg_path not in all_paths, \
            f"{jpg_path} should be removed after 取消归属, got {all_paths}"

    def test_unassign_adds_to_blacklist(self, qtbot, ctx_with_db, db, tmp_path):
        """取消归属 = 加入 P0 黑名单(变无主)，连拍摄期自动归属的照片也能取消。"""
        from app.services.grouping_service import get_explicit_unassigns
        p = str(tmp_path / "auto.jpg")
        panel = MonitorPanel(ctx_with_db)
        qtbot.addWidget(panel)
        panel._on_ctx_unassign(p)
        assert any(s.endswith("auto.jpg") for s in get_explicit_unassigns(db))

    def test_unassign_still_removes_from_group(self, qtbot, ctx_with_db, db, tmp_path):
        """变无主同时踢出合成组(用户选定行为)。"""
        from app.services.grouping_service import (
            Group, save_grouping, load_grouping, get_explicit_unassigns,
        )
        p = str(tmp_path / "g.jpg")
        save_grouping(db, "ZJ-TMW-B2-001",
                      [Group(group_index=0, jpg_paths=[p])], clean_phantoms=False)
        panel = MonitorPanel(ctx_with_db)
        qtbot.addWidget(panel)
        panel._on_ctx_unassign(p)
        all_paths = [x for g in load_grouping(db, "ZJ-TMW-B2-001").groups for x in g.jpg_paths]
        assert p not in all_paths                                  # 踢出组
        assert any(s.endswith("g.jpg") for s in get_explicit_unassigns(db))  # + 黑名单

    def test_assign_uid_clears_blacklist(self, qtbot, ctx_with_db, db, tmp_path):
        """指定归属 = 主动归属 → 解除黑名单(否则取消后归不回)。"""
        from app.services.grouping_service import (
            add_explicit_unassign, get_explicit_unassigns,
        )
        p = str(tmp_path / "back.jpg")
        add_explicit_unassign(db, p)
        panel = MonitorPanel(ctx_with_db)
        qtbot.addWidget(panel)
        panel._on_ctx_assign_uid(p, "ZJ-TMW-B2-001")
        assert not any(s.endswith("back.jpg") for s in get_explicit_unassigns(db))

    def test_add_to_group_clears_blacklist(self, qtbot, ctx_with_db, db, tmp_path):
        from app.services.grouping_service import (
            add_explicit_unassign, get_explicit_unassigns,
        )
        p = str(tmp_path / "back2.jpg")
        add_explicit_unassign(db, p)
        db.execute("INSERT INTO tasks(uid, is_active) VALUES(?, 1)", ("ZJ-TMW-B2-001",))
        db.commit()
        panel = MonitorPanel(ctx_with_db)
        qtbot.addWidget(panel)
        panel._on_ctx_add_to_group(p)
        assert not any(s.endswith("back2.jpg") for s in get_explicit_unassigns(db))

    def test_context_menu_has_assign_uid_action(self, qtbot, ctx_with_db):
        """Context menu must contain '指定归属标本' item for JPG cards."""
        entry = _jpg_entry(path="/tmp/myfile.jpg")
        card = _FileCard(entry)
        qtbot.addWidget(card)

        actions_seen = []

        def fake_exec(self_menu, *args, **kwargs):
            actions_seen.extend([a.text() for a in self_menu.actions()])
            return None

        with patch.object(QMenu, "exec", fake_exec):
            card._on_jpg_context_menu(QPoint(0, 0))

        assert "指定归属标本" in actions_seen, \
            f"Expected '指定归属标本' in context menu, got: {actions_seen}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _visible_card_names(panel: MonitorPanel) -> set:
    """Return names of visible _FileCard widgets in the panel."""
    from PyQt6.QtWidgets import QLabel
    names = set()
    for card in panel._cards:
        if not card.isHidden():
            # Card name is derived from entry name
            entry_name = getattr(card._entry, "name", None)
            if entry_name:
                names.add(entry_name)
    return names


# ── 补处理: selection accessors ───────────────────────────────────────────────

def _tiff_entry(name="FJ-XM-B2-DLC001-1-T95E-20260601.tif",
                path="/tmp/FJ-XM-B2-DLC001-1-T95E-20260601.tif",
                has_zip=False, detail="results/ · TIFF"):
    return FileEntry(name=name, path=path, kind="tiff", size=2000,
                     mtime="2026-01-01T00:00:00+00:00",
                     has_zip=has_zip, detail=detail)


class TestTiffDelete:
    """用户主权：TIFF 可手动删除（带确认框），覆盖旧「TIFF 永不删」UI 封锁。
    自动整理/归档仍绝不删 TIFF（见 test_archive_service.test_tiff_never_deleted）。"""

    def test_tiff_card_has_delete_action(self, qtbot):
        card = _FileCard(_tiff_entry(path="/fake/r.tif"))
        qtbot.addWidget(card)
        seen = []

        def fake_exec(self_menu, *a, **k):
            seen.extend(x.text() for x in self_menu.actions())
            return None

        with patch.object(QMenu, "exec", fake_exec):
            card._show_context_menu(QPoint(0, 0))
        assert "删除此文件" in seen

    def test_tiff_delete_confirmed_unlinks(self, qtbot, ctx, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        tif = tmp_path / "r.tif"
        tif.write_bytes(b"II*\x00")
        panel = MonitorPanel(ctx)
        qtbot.addWidget(panel)
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        panel._delete_paths([str(tif)], clear_selection=False)
        assert not tif.exists()                       # 确认→真删

    def test_tiff_delete_cancelled_keeps_file(self, qtbot, ctx, tmp_path, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        tif = tmp_path / "r.tif"
        tif.write_bytes(b"II*\x00")
        panel = MonitorPanel(ctx)
        qtbot.addWidget(panel)
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.No)
        panel._delete_paths([str(tif)], clear_selection=False)
        assert tif.exists()                           # 取消→保留


class TestSelectionAccessors:
    def test_selected_tiff_paths_returns_only_tiffs(self, panel):
        panel.load_scan(_scan(
            [_jpg_entry(path="/tmp/a.jpg")],
            [_tiff_entry(path="/tmp/t.tif")],
        ))
        for card in panel._cards:
            card.set_selected(True)
        assert panel.selected_tiff_paths() == ["/tmp/t.tif"]
        assert panel.selected_jpg_paths() == ["/tmp/a.jpg"]

    def test_selected_all_paths_mixed(self, panel):
        panel.load_scan(_scan(
            [_jpg_entry(path="/tmp/a.jpg"), _jpg_entry(name="b.jpg", path="/tmp/b.jpg")],
            [_tiff_entry(path="/tmp/t.tif")],
        ))
        for card in panel._cards:
            card.set_selected(True)
        assert sorted(panel.selected_all_paths()) == ["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/t.tif"]

    def test_accessors_empty_when_nothing_selected(self, panel):
        panel.load_scan(_scan([_jpg_entry(path="/tmp/a.jpg")], [_tiff_entry()]))
        assert panel.selected_all_paths() == []
        assert panel.selected_tiff_paths() == []


# ── 已归档 TIFF 不进待处理 feed (oracle app.js:3574-3586) ─────────────────────

class TestArchivedTiffFilter:
    """results/ 里已有同名 ZIP 的 TIFF 属已归档成果,不应出现在待处理照片区。

    Oracle app.js:3577: if (f.hasZip && f.detail && f.detail.indexOf("incoming") < 0) return;
    """

    def test_archived_tiff_hidden_from_pending(self, panel):
        archived = _tiff_entry(name="a.tif", path="/tmp/results/a.tif", has_zip=True)
        pending = _tiff_entry(name="b.tif", path="/tmp/results/b.tif", has_zip=False)
        panel.load_scan(_scan([], [archived, pending]))
        names = _visible_card_names(panel)
        assert "b.tif" in names
        assert "a.tif" not in names

    def test_incoming_tiff_with_zip_still_shown(self, panel):
        e = _tiff_entry(name="c.tif", path="/tmp/in/c.tif", has_zip=True,
                        detail="incoming-jpg/ · TIFF")
        panel.load_scan(_scan([], [e]))
        assert "c.tif" in _visible_card_names(panel)

    def test_untidy_count_excludes_archived_tiff(self, panel):
        jpg = _jpg_entry()
        archived = _tiff_entry(name="a.tif", path="/tmp/results/a.tif", has_zip=True)
        pending = _tiff_entry(name="b.tif", path="/tmp/results/b.tif", has_zip=False)
        panel.load_scan(_scan([jpg], [archived, pending]))
        assert panel._stat_untidy.text() == "未整理 2"
        assert panel._stat_recent.text() == "TIFF 1"

    def test_tiff_disappears_when_zip_appears_mid_session(self, panel):
        """归档动作只改变 has_zip — 扫描指纹必须包含它,否则卡片不消失。"""
        before = _tiff_entry(name="a.tif", path="/tmp/results/a.tif", has_zip=False)
        panel.load_scan(_scan([], [before]))
        assert "a.tif" in _visible_card_names(panel)

        after = _tiff_entry(name="a.tif", path="/tmp/results/a.tif", has_zip=True)
        panel.load_scan(_scan([], [after]))
        assert "a.tif" not in _visible_card_names(panel)


# ── 阶段按钮(拍摄中/已拍完/整理中/完成)接线 (oracle app.js:8357-8383) ───────

class TestPhasePills:
    """Pills 发 status code 信号;checked 互斥、只由 set_phase 驱动;
    永不禁用——无激活 UID 时点击由工作台给状态栏反馈(零反馈=bug)。"""

    def test_pills_always_enabled(self, panel):
        panel.set_batch("X", None)
        assert all(b.isEnabled() for b in panel._phase_pills.values())
        panel.set_batch("X", "X")
        assert all(b.isEnabled() for b in panel._phase_pills.values())

    def test_pills_keyed_by_status_code(self, panel):
        assert set(panel._phase_pills.keys()) == {
            "shooting", "shot_done", "organizing", "done"}

    def test_click_emits_status_code_without_self_check(self, panel, qtbot):
        panel.set_batch("X", "X")
        received = []
        panel.phase_clicked.connect(received.append)
        btn = panel._phase_pills["shooting"]
        qtbot.mouseClick(btn, Qt.MouseButton.LeftButton)
        assert received == ["shooting"]
        assert not btn.isChecked(), "checked 只能由 set_phase 驱动"

    def test_set_phase_checks_exactly_one(self, panel):
        panel.set_batch("X", "X")
        panel.set_phase("organizing")
        checked = [c for c, b in panel._phase_pills.items() if b.isChecked()]
        assert checked == ["organizing"]

    def test_set_phase_none_unchecks_all(self, panel):
        panel.set_batch("X", "X")
        panel.set_phase("shooting")
        panel.set_phase(None)
        assert all(not b.isChecked() for b in panel._phase_pills.values())

    def test_deactivate_clears_phase(self, panel):
        panel.set_batch("X", "X")
        panel.set_phase("shooting")
        panel.set_batch("X", None)
        assert all(not b.isChecked() for b in panel._phase_pills.values())
