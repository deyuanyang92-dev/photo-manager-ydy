"""test_monitor_panel.py — Tests for 1-C, 1-D, and 2-A context menu actions.

1-C: Right-click context menu on JPG cards has "复制路径" action.
1-D: "隐藏已归档" checkbox hides cards where is_grouped=True.
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
    def test_hide_archived_checkbox_exists(self, panel):
        """MonitorPanel must have a '隐藏已归档' QCheckBox."""
        from PyQt6.QtWidgets import QCheckBox
        checkboxes = panel.findChildren(QCheckBox)
        labels = [c.text() for c in checkboxes]
        assert "隐藏已归档" in labels, \
            f"Expected '隐藏已归档' checkbox, found: {labels}"

    def test_hide_archived_filter(self, panel, qtbot):
        """When '隐藏已归档' is checked, grouped files are hidden."""
        from PyQt6.QtWidgets import QCheckBox

        grouped_entry = _jpg_entry("grouped.jpg", "/tmp/grouped.jpg", is_grouped=True)
        solo_entry = _jpg_entry("solo.jpg", "/tmp/solo.jpg", is_grouped=False)

        panel.load_scan(_scan([grouped_entry, solo_entry]))

        # Initially both cards visible
        visible_names_before = _visible_card_names(panel)
        assert "grouped.jpg" in visible_names_before
        assert "solo.jpg" in visible_names_before

        # Check the checkbox
        cb = next(c for c in panel.findChildren(QCheckBox) if c.text() == "隐藏已归档")
        cb.setChecked(True)

        visible_names_after = _visible_card_names(panel)
        assert "grouped.jpg" not in visible_names_after, \
            "grouped.jpg should be hidden when '隐藏已归档' is checked"
        assert "solo.jpg" in visible_names_after

    def test_uncheck_restores_all_files(self, panel, qtbot):
        """Unchecking '隐藏已归档' restores all files."""
        from PyQt6.QtWidgets import QCheckBox

        grouped_entry = _jpg_entry("grouped.jpg", "/tmp/grouped.jpg", is_grouped=True)
        solo_entry = _jpg_entry("solo.jpg", "/tmp/solo.jpg", is_grouped=False)
        panel.load_scan(_scan([grouped_entry, solo_entry]))

        cb = next(c for c in panel.findChildren(QCheckBox) if c.text() == "隐藏已归档")
        cb.setChecked(True)
        cb.setChecked(False)

        visible_names = _visible_card_names(panel)
        assert "grouped.jpg" in visible_names
        assert "solo.jpg" in visible_names


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
