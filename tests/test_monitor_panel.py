"""test_monitor_panel.py — Tests for 1-C (clipboard copy) and 1-D (filter archived).

1-C: Right-click context menu on JPG cards has "复制路径" action.
1-D: "隐藏已归档" checkbox hides cards where is_grouped=True.
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
from app.widgets.monitor_panel import MonitorPanel, _FileCard


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def ctx():
    """Minimal AppContext stub."""
    c = MagicMock()
    c.get_db.return_value = None
    return c


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
