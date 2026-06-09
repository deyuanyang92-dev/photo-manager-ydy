"""test_grouping_panel_supp.py — 补处理 drop button on the grouping panel.

The button must:
  - emit supp_process_requested on click,
  - emit supp_files_dropped with local paths on an OS file drop,
  - be enabled with NO active specimen (independent of the gate).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QMimeData, QUrl, Qt  # noqa: E402

from app.widgets.grouping_panel import GroupingPanel  # noqa: E402


@pytest.fixture
def ctx():
    c = MagicMock()
    c.get_db.return_value = None
    return c


@pytest.fixture
def panel(qtbot, ctx):
    w = GroupingPanel(ctx)
    qtbot.addWidget(w)
    w.show()
    return w


def test_supp_button_emits_process_signal_on_click(qtbot, panel):
    with qtbot.waitSignal(panel.supp_process_requested, timeout=1000):
        panel._supp_btn.click()


def test_supp_button_enabled_without_active_specimen(panel):
    # No specimen loaded → grouping body shows empty state, but the 补处理
    # button is still usable (gate independence).
    assert panel._uid is None
    assert panel._supp_btn.isEnabled()


def test_supp_button_accepts_drop_emits_paths(qtbot, panel, tmp_path):
    f1 = tmp_path / "a.jpg"
    f2 = tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
    f1.write_bytes(b"x")
    f2.write_bytes(b"x")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f1)), QUrl.fromLocalFile(str(f2))])
    # Mock the event — dropEvent only uses mimeData() and acceptProposedAction().
    event = MagicMock()
    event.mimeData.return_value = mime
    with qtbot.waitSignal(panel.supp_files_dropped, timeout=1000) as blocker:
        panel._supp_btn.dropEvent(event)
    assert sorted(blocker.args[0]) == sorted([str(f1), str(f2)])
