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


def test_lightbox_has_zoom_and_pan_controls(qtbot, tiff_paths):
    """TIFF lightbox exposes scroll/pan area and zoom controls."""
    from app.widgets.results_column import _PanImageLabel, _TiffLightboxDialog

    dlg = _TiffLightboxDialog(tiff_paths, initial_index=0)
    qtbot.addWidget(dlg)

    assert hasattr(dlg, "_scroll")
    assert hasattr(dlg, "_zoom_slider")
    assert isinstance(dlg._image_label, _PanImageLabel)
    assert dlg._image_label._scroll_area is dlg._scroll


def test_lightbox_zoom_slider_switches_out_of_fit_mode(qtbot, tmp_path):
    """Manual zoom leaves fit-to-window mode and records the chosen percentage."""
    from PIL import Image
    from app.widgets.results_column import _TiffLightboxDialog

    tif = tmp_path / "real.tif"
    Image.new("RGB", (200, 100), "white").save(tif)
    dlg = _TiffLightboxDialog([tif], initial_index=0)
    qtbot.addWidget(dlg)

    dlg._zoom_slider.setValue(150)

    assert dlg._fit_to_window is False
    assert dlg._zoom_value.text() == "150%"
    assert dlg._image_label.pixmap() is not None


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


# ── Paired rows (同编号关联显示) ───────────────────────────────────────────────

def test_pairing_by_seq(qtbot):
    """A TIFF and its ZIP sharing the same seq render in ONE paired row."""
    from app.widgets.results_column import (
        ResultsColumn, _ResultRow, _TiffCard, _ArchiveCard,
    )
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
        [{"path": "/fake/a.zip", "name": "a.zip", "size": 99, "seq": 1}],
    )
    rows = col.findChildren(_ResultRow)
    assert len(rows) == 1
    assert len(rows[0].findChildren(_TiffCard)) == 1
    assert len(rows[0].findChildren(_ArchiveCard)) == 1


def test_pairing_by_stem_when_no_seq(qtbot):
    """No seq → TIFF and ZIP with matching filename stem pair into one row."""
    from app.widgets.results_column import ResultsColumn, _ResultRow
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/result.tif", "name": "result.tif"}],
        [{"path": "/fake/result.zip", "name": "result.zip", "size": 1}],
    )
    rows = col.findChildren(_ResultRow)
    assert len(rows) == 1


def test_two_unpaired_tiffs_keep_input_order(qtbot):
    """Two TIFFs, no zips → two rows, _TiffCard order == input order."""
    from app.widgets.results_column import ResultsColumn, _TiffCard, _ResultRow
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif"},
         {"path": "/fake/b.tif", "name": "b.tif"}],
        [],
    )
    assert len(col.findChildren(_ResultRow)) == 2
    cards = col.findChildren(_TiffCard)
    assert [c._info["name"] for c in cards] == ["a.tif", "b.tif"]


def test_collapse_toggle_hides_body(qtbot):
    """The whole results area collapses via a single toggle."""
    from app.widgets.results_column import ResultsColumn
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.show()
    assert not col._body.isHidden()
    col._set_collapsed(True)
    assert col._body.isHidden()
    col._set_collapsed(False)
    assert not col._body.isHidden()


def test_zoom_changes_thumb_size(qtbot):
    """The zoom control resizes the result display boxes."""
    from app.widgets.results_column import ResultsColumn, _TiffCard
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("UID", [{"path": "/fake/a.tif", "name": "a.tif"}], [])
    col._set_zoom(220)
    assert col._thumb_size == 220
    card = col.findChildren(_TiffCard)[0]
    assert card._thumb_size == 220


def test_thumb_guard_on_fake_path(qtbot):
    """A non-existent path must not raise and falls back to an icon card."""
    from app.widgets.results_column import ResultsColumn, _TiffCard
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("UID", [{"path": "/fake/missing.tif", "name": "missing.tif"}], [])
    assert len(col.findChildren(_TiffCard)) == 1
