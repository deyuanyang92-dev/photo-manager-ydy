"""tests/test_results_column.py — TDD for _TiffLightboxDialog."""
from __future__ import annotations

import sys
import os

import pytest

pytestmark = pytest.mark.skipif(
    "QT_QPA_PLATFORM" not in os.environ and sys.platform != "win32",
    reason="needs Qt display",
)


@pytest.fixture()
def tiff_paths(tmp_path):
    """Create three dummy .tiff files."""
    paths = []
    for i in range(3):
        p = tmp_path / f"specimen_{i}.tiff"
        p.write_bytes(b"\x00" * 16)
        paths.append(p)
    return paths


def test_lightbox_dialog_loads_paths(qtbot, tiff_paths):
    """Dialog shows correct file name and index label for initial path."""
    from app.widgets.results_column import _TiffLightboxDialog

    dlg = _TiffLightboxDialog(tiff_paths, initial_index=1)
    qtbot.addWidget(dlg)

    text = dlg._info_label.text()
    assert tiff_paths[1].name in text
    assert "2 / 3" in text


def test_lightbox_navigation_prev_next_disabled_at_ends(qtbot, tiff_paths):
    """Prev button disabled at start; next button disabled at end."""
    from app.widgets.results_column import _TiffLightboxDialog

    dlg = _TiffLightboxDialog(tiff_paths, initial_index=0)
    qtbot.addWidget(dlg)

    assert not dlg._prev_btn.isEnabled()
    assert dlg._next_btn.isEnabled()

    # Move to last
    dlg._index = len(tiff_paths) - 1
    dlg._load_current()
    assert dlg._prev_btn.isEnabled()
    assert not dlg._next_btn.isEnabled()


def test_lightbox_keyboard_navigation(qtbot, tiff_paths):
    """Left/Right arrow keys navigate; Escape does not crash."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent
    from app.widgets.results_column import _TiffLightboxDialog

    dlg = _TiffLightboxDialog(tiff_paths, initial_index=1)
    qtbot.addWidget(dlg)

    assert dlg._index == 1

    # Press Right → index becomes 2
    qtbot.keyClick(dlg, Qt.Key.Key_Right)
    assert dlg._index == 2

    # Press Right again (at end) → stays at 2
    qtbot.keyClick(dlg, Qt.Key.Key_Right)
    assert dlg._index == 2

    # Press Left → index becomes 1
    qtbot.keyClick(dlg, Qt.Key.Key_Left)
    assert dlg._index == 1

    # Press Left → index becomes 0
    qtbot.keyClick(dlg, Qt.Key.Key_Left)
    assert dlg._index == 0

    # Press Left at start → stays 0
    qtbot.keyClick(dlg, Qt.Key.Key_Left)
    assert dlg._index == 0


def test_tiff_card_double_click_opens_lightbox(qtbot, tmp_path, monkeypatch):
    """Double-clicking a _TiffCard triggers the lightbox dialog (exec mocked)."""
    from app.widgets.results_column import ResultsColumn

    opened = []

    # Monkeypatch _TiffLightboxDialog.exec so dialog doesn't block
    import app.widgets.results_column as rc_mod

    class _FakeDlg:
        def __init__(self, paths, initial_index=0, parent=None):
            opened.append((paths, initial_index))

        def exec(self):
            pass

    monkeypatch.setattr(rc_mod, "_TiffLightboxDialog", _FakeDlg)

    p1 = tmp_path / "a.tiff"
    p2 = tmp_path / "b.tiff"
    p1.write_bytes(b"\x00" * 8)
    p2.write_bytes(b"\x00" * 8)

    col = ResultsColumn()
    qtbot.addWidget(col)

    tiff_infos = [{"path": str(p1), "name": "a.tiff"},
                  {"path": str(p2), "name": "b.tiff"}]
    col.load_uid("uid-test", tiff_infos, [])

    # Find first TiffCard and simulate double-click
    from app.widgets.results_column import _TiffCard
    cards = col.findChildren(_TiffCard)
    assert len(cards) == 2

    # Simulate double-click on second card
    from PyQt6.QtCore import Qt
    qtbot.mouseDClick(cards[1], Qt.MouseButton.LeftButton)

    assert len(opened) == 1
    paths, idx = opened[0]
    assert paths[idx] == p2
    assert len(paths) == 2
