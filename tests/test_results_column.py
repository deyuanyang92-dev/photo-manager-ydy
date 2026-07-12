"""tests/test_results_column.py — TDD for _TiffLightboxDialog."""
from __future__ import annotations

import sys
import os

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication, QMenu

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


def test_lightbox_wheel_zoom_helper_changes_zoom(qtbot, tmp_path):
    """Mouse-wheel zoom path leaves fit mode and changes the zoom percentage."""
    from PIL import Image
    from app.widgets.results_column import _TiffLightboxDialog

    tif = tmp_path / "real.tif"
    Image.new("RGB", (200, 100), "white").save(tif)
    dlg = _TiffLightboxDialog([tif], initial_index=0)
    qtbot.addWidget(dlg)

    start = dlg._zoom_slider.value()
    dlg._zoom_by_wheel_delta(120)

    assert dlg._fit_to_window is False
    assert dlg._zoom_slider.value() == min(400, start + 10)
    assert dlg._zoom_value.text().endswith("%")


def test_lightbox_uses_shared_preview_decoder(qtbot, tmp_path, monkeypatch):
    """Preview uses the shared image decoder instead of a separate TIFF path."""
    from PyQt6.QtGui import QPixmap
    import app.widgets.results_column as rc_mod

    tif = tmp_path / "fallback-needed.tif"
    tif.write_bytes(b"not a qt-readable tif")
    calls = []

    def fake_decode(path):
        calls.append(path)
        pixmap = QPixmap(120, 80)
        pixmap.fill(Qt.GlobalColor.white)
        return pixmap

    monkeypatch.setattr(rc_mod, "_decode_preview_pixmap", fake_decode)

    dlg = rc_mod._TiffLightboxDialog([tif], initial_index=0)
    qtbot.addWidget(dlg)

    assert calls == [str(tif)]
    assert dlg._image_label.pixmap() is not None
    assert "无法预览" not in dlg._image_label.text()


def test_lightbox_preview_uses_bounded_tiff_proxy(qtbot, tmp_path):
    """Large TIFF previews use the configured bounded proxy, not the master."""
    from PIL import Image
    from app.widgets.results_column import _TiffLightboxDialog

    tif = tmp_path / "native-resolution.tif"
    Image.new("RGB", (3600, 1200), "white").save(tif)

    dlg = _TiffLightboxDialog([tif], initial_index=0)
    qtbot.addWidget(dlg)

    from app.config.preview_profile import current_preview_master_size
    assert dlg._base_pixmap.width() <= current_preview_master_size()
    assert dlg._base_pixmap.height() <= current_preview_master_size()
    assert dlg._base_pixmap.width() / dlg._base_pixmap.height() == pytest.approx(3.0, rel=0.02)


def test_lightbox_scales_preview_for_screen_pixel_ratio(qtbot, tmp_path, monkeypatch):
    """100% preview maps source pixels to screen pixels on high-DPI displays."""
    from PIL import Image
    from app.widgets.results_column import _TiffLightboxDialog

    tif = tmp_path / "hidpi.tif"
    Image.new("RGB", (2000, 1200), "white").save(tif)

    dlg = _TiffLightboxDialog([tif], initial_index=0)
    qtbot.addWidget(dlg)
    monkeypatch.setattr(dlg, "_preview_device_pixel_ratio", lambda: 2.0)

    dlg._actual_size()

    pixmap = dlg._image_label.pixmap()
    assert pixmap is not None
    assert pixmap.width() == dlg._base_pixmap.width()
    assert pixmap.height() == dlg._base_pixmap.height()
    assert dlg._image_label.width() == round(dlg._base_pixmap.width() / 2.0)
    assert dlg._image_label.height() == round(dlg._base_pixmap.height() / 2.0)


def test_lightbox_sharpens_downscaled_preview_by_default(qtbot, tmp_path, monkeypatch):
    """Downscaled previews get display-only sharpening unless the toggle is off."""
    from PIL import Image
    from app.widgets.results_column import _TiffLightboxDialog

    tif = tmp_path / "sharp-preview.tif"
    Image.new("RGB", (1000, 500), "white").save(tif)

    dlg = _TiffLightboxDialog([tif], initial_index=0)
    qtbot.addWidget(dlg)
    calls = []

    def fake_sharpen(pixmap):
        calls.append((pixmap.width(), pixmap.height()))
        return pixmap

    monkeypatch.setattr(dlg, "_sharpen_preview_pixmap", fake_sharpen)

    dlg._set_zoom_percent(50)
    assert calls == [(
        round(dlg._base_pixmap.width() * 0.5),
        round(dlg._base_pixmap.height() * 0.5),
    )]

    dlg._sharpen_btn.setChecked(False)
    calls.clear()
    dlg._set_zoom_percent(40)
    assert calls == []


def test_lightbox_windows_shortcuts(qtbot, tmp_path):
    """Common Windows-style shortcuts navigate and control zoom."""
    from PIL import Image
    from PyQt6.QtCore import Qt
    from app.widgets.results_column import _TiffLightboxDialog

    paths = []
    for i in range(3):
        tif = tmp_path / f"real_{i}.tif"
        Image.new("RGB", (200, 100), "white").save(tif)
        paths.append(tif)
    dlg = _TiffLightboxDialog(paths, initial_index=1)
    qtbot.addWidget(dlg)

    qtbot.keyClick(dlg, Qt.Key.Key_PageDown)
    assert dlg._index == 2
    qtbot.keyClick(dlg, Qt.Key.Key_Home)
    assert dlg._index == 0
    qtbot.keyClick(dlg, Qt.Key.Key_End)
    assert dlg._index == 2
    qtbot.keyClick(dlg, Qt.Key.Key_PageUp)
    assert dlg._index == 1

    qtbot.keyClick(dlg, Qt.Key.Key_1, modifier=Qt.KeyboardModifier.ControlModifier)
    assert dlg._zoom_value.text() == "100%"
    qtbot.keyClick(dlg, Qt.Key.Key_0, modifier=Qt.KeyboardModifier.ControlModifier)
    assert dlg._fit_to_window is True
    assert dlg._zoom_value.text().startswith("适合窗口")


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
    cards = [c for c in col._cards if isinstance(c, _TiffCard)]
    assert len(cards) == 2

    # Simulate double-click on second card
    from PyQt6.QtCore import Qt
    qtbot.mouseDClick(cards[1], Qt.MouseButton.LeftButton)

    assert len(opened) == 1
    paths, idx = opened[0]
    assert paths[idx] == p2
    assert len(paths) == 2


# ── Paired rows (同编号关联显示) ───────────────────────────────────────────────

def test_tiff_and_zip_render_as_two_list_rows(qtbot):
    """A TIFF+ZIP sharing seq render in ONE group: two compact file rows —
    a ``_TiffCard`` (thumbnail icon) and an ``_ArchiveCard`` (zip icon)."""
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


def test_result_files_are_flat_rows_with_context_menu_actions(qtbot):
    from PyQt6.QtWidgets import QPushButton

    from app.widgets.results_column import ResultsColumn, _ArchiveCard, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
        [{"path": "/fake/a.zip", "name": "a.zip", "size": 99, "seq": 1}],
    )

    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_card = next(c for c in col._cards if isinstance(c, _ArchiveCard))
    assert tiff_card.objectName() == "ResultFile"
    assert zip_card.objectName() == "ResultFile"
    assert not tiff_card.findChildren(QPushButton)
    assert not zip_card.findChildren(QPushButton)


def test_result_row_uses_compact_sequence_badge(qtbot):
    from PyQt6.QtWidgets import QLabel

    from app.widgets.results_column import ResultsColumn, _ResultRow

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif", "seq": 3}],
        [{"path": "/fake/a.zip", "name": "a.zip", "size": 99, "seq": 3}],
    )

    row = col.findChild(_ResultRow)
    badges = [
        lbl for lbl in row.findChildren(QLabel)
        if lbl.objectName() == "ResultSeqBadge"
    ]
    assert len(badges) == 1
    assert badges[0].text() == "3"
    assert badges[0].toolTip() == "成果 3"


def test_load_many_groups_results_by_specimen_uid(qtbot):
    from PyQt6.QtWidgets import QLabel

    from app.widgets.results_column import (
        ResultsColumn, _ResultRow, _SpecimenResultHeader,
    )
    col = ResultsColumn()
    qtbot.addWidget(col)

    col.load_many([
        {
            "uid": "UID-1",
            "tiffs": [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
            "zips": [{"path": "/fake/a.zip", "name": "a.zip", "seq": 1}],
        },
        {
            "uid": "UID-2",
            "tiffs": [{"path": "/fake/b.tif", "name": "b.tif", "seq": 2}],
            "zips": [],
        },
    ])

    headers = col.findChildren(_SpecimenResultHeader)
    assert len(headers) == 2
    assert "UID-1" in headers[0].findChildren(QLabel)[0].text()
    assert "UID-2" in headers[1].findChildren(QLabel)[0].text()
    assert len(col.findChildren(_ResultRow)) == 2
    assert col._count.text() == "2 编号 / 2 项"
    assert col._title.text() == "全部成果"


def test_all_results_header_click_requests_specimen(qtbot):
    from PyQt6.QtCore import Qt

    from app.widgets.results_column import ResultsColumn, _SpecimenResultHeader
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_many([
        {
            "uid": "UID-1",
            "tiffs": [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
            "zips": [],
        },
    ])
    seen = []
    col.specimen_requested.connect(seen.append)

    header = col.findChild(_SpecimenResultHeader)
    qtbot.mouseClick(header, Qt.MouseButton.LeftButton)

    assert seen == ["UID-1"]


def test_no_zip_only_tiff_row(qtbot):
    """A TIFF with no paired ZIP yields one row with only a ``_TiffCard``."""
    from app.widgets.results_column import ResultsColumn, _ResultRow, _TiffCard, _ArchiveCard
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif"}],
        [],
    )
    row = col.findChildren(_ResultRow)[0]
    assert len(row.findChildren(_TiffCard)) == 1
    assert len(row.findChildren(_ArchiveCard)) == 0


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
    cards = [c for c in col._cards if isinstance(c, _TiffCard)]
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
    col._set_zoom(80)
    assert col._thumb_size == 80
    card = col.findChildren(_TiffCard)[0]
    assert card._thumb_size == 80


def test_results_ctrl_wheel_changes_thumb_size(qtbot):
    """Ctrl+wheel follows Windows zoom convention for result thumbnails."""
    from app.widgets.results_column import ResultsColumn

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("UID", [{"path": "/fake/a.tif", "name": "a.tif"}], [])
    initial = col._thumb_size

    event = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(col._body.viewport(), event)

    assert col._thumb_size > initial


def test_thumb_guard_on_fake_path(qtbot):
    """A non-existent path must not raise and falls back to an icon card."""
    from app.widgets.results_column import ResultsColumn, _TiffCard
    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("UID", [{"path": "/fake/missing.tif", "name": "missing.tif"}], [])
    assert len(col.findChildren(_TiffCard)) == 1


def test_tiff_card_uses_real_thumbnail_when_decodable(qtbot, tmp_path):
    """A readable TIFF should render as an image thumbnail, not only a file icon."""
    from PIL import Image
    from app.widgets.results_column import ResultsColumn, _TiffCard

    tif = tmp_path / "thumb.tif"
    Image.new("RGB", (120, 80), "green").save(tif)

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("UID", [{"path": str(tif), "name": tif.name}], [])

    card = col.findChildren(_TiffCard)[0]
    qtbot.waitUntil(lambda: card._icon.property("hasThumbnail") is True, timeout=1000)
    assert card._icon.property("hasThumbnail") is True
    pixmap = card._icon.pixmap()
    assert pixmap is not None
    assert not pixmap.isNull()


def test_results_column_loads_tiff_thumbnail_without_user_action(qtbot, monkeypatch):
    """成果 TIFF 缩略图必须自动出现(PROJECT_MEMORY 不可回归项:不得图标占位)。

    §7 旧断言 `assert calls == ["/fake/visible.tif"]` —— 冻结的是「load_uid 内
    **同步**解码」这一实现细节, 而同步解码正是「点击反应迟钝」的主因
    (2026-07-10 用户报障)。改为延迟 + 20ms 分批队列后真需求不变:
    无需用户任何操作, 缩略图随后自动填充(下方 waitUntil 即证)。

    2026-07-12: 解码整体搬到 GridThumbnailWorker 线程(TIFF 母图冷解码最坏十几秒,
    在 GUI 线程做 = 未响应)。monkeypatch 的接缝随之从主线程的同步解码器
    `results_column._decode_thumb` 移到 worker 线程真正调用的
    `thumbnail_worker.decode_image_data`。**需求不变**: 无需任何用户操作, 该路径
    必须被自动解码。
    §7 旧接缝: monkeypatch.setattr("app.widgets.results_column._decode_thumb", ...)
    """
    from app.widgets.results_column import ResultsColumn

    calls = []
    monkeypatch.setattr(
        "app.workers.thumbnail_worker.decode_image_data",
        lambda path, max_size=280, **kw: calls.append(path) or None,
    )

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid("UID", [{"path": "/fake/visible.tif", "name": "visible.tif"}], [])

    assert calls == [], "构造卡片时不得同步解码(会卡住点击)"
    qtbot.waitUntil(lambda: calls == ["/fake/visible.tif"], timeout=2000)


def test_results_column_has_windows_folder_actions(qtbot, tmp_path):
    """The results header exposes Windows-Explorer-style folder and sort actions."""
    from PyQt6.QtCore import Qt
    from app.widgets.results_column import ResultsColumn

    tif = tmp_path / "b.tif"
    zipf = tmp_path / "a.zip"
    tif.write_bytes(b"tif")
    zipf.write_bytes(b"zip")

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": str(tif), "name": "b.tif", "seq": 1}],
        [{"path": str(zipf), "name": "a.zip", "size": 3, "seq": 1}],
    )

    assert col._current_mode_btn.text() == "当前"
    assert col._all_mode_btn.text() == "全部"
    assert col._paired_selection_btn.text() == "联选"
    assert col._paired_selection_btn.isCheckable()
    assert not col._paired_selection_btn.isChecked()
    assert col._options_btn.text() == ""
    assert not hasattr(col, "_link_selected_btn")
    assert col._paired_columns_enabled is True
    assert col._paired_selection_enabled is False
    assert col._body.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert col._body.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert col._results_dir == str(tmp_path)


def test_results_body_context_menu_holds_secondary_actions(qtbot, tmp_path, monkeypatch):
    from app.widgets.results_column import ResultsColumn

    tif = tmp_path / "b.tif"
    zipf = tmp_path / "a.zip"
    tif.write_bytes(b"tif")
    zipf.write_bytes(b"zip")

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": str(tif), "name": "b.tif", "seq": 1}],
        [{"path": str(zipf), "name": "a.zip", "size": 3, "seq": 1}],
    )

    captured = {}

    def fake_exec(menu, *_args, **_kwargs):
        captured["top"] = [a.text() for a in menu.actions()]
        captured["submenus"] = {
            a.text(): [sa.text() for sa in a.menu().actions()]
            for a in menu.actions()
            if a.menu() is not None
        }
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    col._show_body_menu(QPoint(0, 0))

    assert "打开 results 文件夹" in captured["top"]
    assert "显示方式" in captured["top"]
    assert "显示名称" in captured["top"]
    assert "排序" in captured["top"]
    assert "缩略图大小" in captured["top"]
    assert "双栏对照" in captured["top"]
    assert "联选 TIF/ZIP" in captured["top"]
    assert captured["submenus"]["显示方式"] == ["列表", "大缩略图"]
    assert captured["submenus"]["显示名称"] == ["完整文件名", "唯一编号"]
    assert captured["submenus"]["排序"] == ["顺序", "名称", "类型", "大小", "修改时间"]
    assert captured["submenus"]["缩略图大小"] == ["小", "中", "大", "最大"]


def test_results_large_thumbnail_view_keeps_list_view_as_default(qtbot):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout

    from app.widgets.results_column import ResultsColumn, _ArchiveCard, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
        [{"path": "/fake/a.zip", "name": "a.zip", "size": 99, "seq": 1}],
    )

    # 2026-07-11: 成果照片墙默认改为「大缩略图」网格(用户要求照片做大)。
    # 显式切到 list 再切回大图, 验证两种模式都工作。
    col._set_result_view_mode("list")
    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
        [{"path": "/fake/a.zip", "name": "a.zip", "size": 99, "seq": 1}],
    )
    default_tiff = next(c for c in col._cards if isinstance(c, _TiffCard))
    assert col._result_view_mode == "list"
    assert default_tiff.property("resultViewMode") == "list"

    col._set_result_view_mode("large_thumbnail")
    col.show()
    qtbot.wait(100)

    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_card = next(c for c in col._cards if isinstance(c, _ArchiveCard))
    name_label = next(
        lbl for lbl in tiff_card.findChildren(QLabel)
        if lbl.objectName() == "Mono"
    )
    assert col._result_view_mode == "large_thumbnail"
    assert tiff_card.property("resultViewMode") == "large_thumbnail"
    assert zip_card.property("resultViewMode") == "large_thumbnail"
    assert isinstance(tiff_card.layout(), QVBoxLayout)
    assert tiff_card._icon.width() >= 128
    assert tiff_card.minimumHeight() <= tiff_card._icon.height() + 64
    assert tiff_card._select_badge.geometry().bottom() >= tiff_card._icon.geometry().top()
    assert name_label.wordWrap()
    assert "大缩略图" in col._options_btn.toolTip()


def test_results_filename_mode_can_show_unique_id(qtbot):
    """Result cards can show the specimen UID while keeping full names in data."""
    from PyQt6.QtWidgets import QLabel

    from app.widgets.results_column import ResultsColumn, _ArchiveCard, _TiffCard

    result_name = "GXFCG-BLW-SC001-1-D79-260618-广西城港-白龙尾-独齿沙蚕-20260618.tif"
    uid = "GXFCG-BLW-SC001-D79-260618-广西城港-白龙尾-独齿沙蚕-20260618"

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": f"/fake/{result_name}", "name": result_name, "seq": 1}],
        [{"path": f"/fake/{result_name[:-4]}.zip", "name": f"{result_name[:-4]}.zip", "seq": 1}],
    )

    col._set_filename_mode("uid")

    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_card = next(c for c in col._cards if isinstance(c, _ArchiveCard))
    tiff_name = next(lbl for lbl in tiff_card.findChildren(QLabel) if lbl.objectName() == "Mono")
    zip_name = next(lbl for lbl in zip_card.findChildren(QLabel) if lbl.objectName() == "Mono")
    assert tiff_name.text() == uid
    assert zip_name.text() == uid
    assert tiff_card._info["name"] == result_name


def test_results_sort_by_name_reorders_cards(qtbot):
    """Sorting by name re-renders file cards in filename order."""
    from app.widgets.results_column import ResultsColumn, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/b.tif", "name": "b.tif"},
         {"path": "/fake/a.tif", "name": "a.tif"}],
        [],
    )

    col._set_sort_key("name")

    cards = [c for c in col._cards if isinstance(c, _TiffCard)]
    assert [c._info["name"] for c in cards] == ["a.tif", "b.tif"]


def test_results_paired_columns_align_tiff_left_zip_right(qtbot):
    """Paired columns keep matching TIFF and ZIP in one aligned two-column row."""
    from PyQt6.QtWidgets import QSizePolicy
    from app.widgets.results_column import (
        ResultsColumn, _ArchiveCard, _ResultPairIndicator, _ResultRow, _TiffCard,
    )

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
        [{"path": "/fake/a.zip", "name": "a.zip", "size": 10, "seq": 1}],
    )

    row = col.findChildren(_ResultRow)[0]
    assert col._paired_columns_enabled is True
    assert isinstance(row._rows[0], _TiffCard)
    assert isinstance(row._rows[1], _ArchiveCard)
    assert len(row.findChildren(_ResultPairIndicator)) == 1
    assert row.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed


def test_results_paired_columns_fall_back_when_narrow(qtbot):
    from app.widgets.results_column import (
        ResultsColumn, _ResultPairIndicator, _ResultRow,
    )

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.resize(560, 480)
    col.show()
    qtbot.wait(100)

    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
        [{"path": "/fake/a.zip", "name": "a.zip", "size": 10, "seq": 1}],
    )

    row = col.findChildren(_ResultRow)[0]
    assert col._paired_columns_enabled is True
    assert col._rendered_paired_columns is False
    assert row.property("pairedColumns") == "false"
    assert len(row.findChildren(_ResultPairIndicator)) == 0
    assert "宽度不足" in col._options_btn.toolTip()


def test_results_paired_columns_remain_visible_at_workbench_width(qtbot):
    from app.widgets.results_column import (
        ResultsColumn, _ResultPairIndicator, _ResultRow,
    )

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.resize(760, 480)
    col.show()
    qtbot.wait(100)

    col.load_uid(
        "UID",
        [{"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
        [{"path": "/fake/a.zip", "name": "a.zip", "size": 10, "seq": 1}],
    )

    row = col.findChildren(_ResultRow)[0]
    assert col._paired_columns_enabled is True
    assert col._rendered_paired_columns is True
    assert row.property("pairedColumns") == "true"
    assert len(row.findChildren(_ResultPairIndicator)) == 1


def test_results_column_link_result_signal_uses_tiff_zip_paths(qtbot):
    from app.widgets.results_column import ResultsColumn

    col = ResultsColumn()
    qtbot.addWidget(col)
    tiff = "/fake/a.tif"
    zipf = "/fake/a.zip"
    col.load_uid(
        "UID",
        [{"path": tiff, "name": "a.tif", "seq": 1}],
        [{"path": zipf, "name": "a.zip", "size": 10, "seq": 1}],
    )

    with qtbot.waitSignal(col.link_result_requested, timeout=1000) as blocker:
        col._emit_link_result(tiff, zipf)

    assert blocker.args == [tiff, zipf]


def test_tiff_card_context_menu_checks_and_deletes_tiff(qtbot, tmp_path, monkeypatch):
    from app.widgets.results_column import ResultsColumn, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    tif = tmp_path / "GXFCG-BLW-BZC003-1-R-20260618.tif"
    tif.write_bytes(b"tif")
    col.load_uid(
        "UID",
        [{"path": str(tif), "name": tif.name, "seq": 1}],
        [],
    )
    card = next(c for c in col._cards if isinstance(c, _TiffCard))

    def trigger_action(label):
        def fake_exec(menu, *_args, **_kwargs):
            next(a for a in menu.actions() if a.text() == label).trigger()
            return None
        return fake_exec

    monkeypatch.setattr(QMenu, "exec", trigger_action("检查 TIF 命名格式"))
    with qtbot.waitSignal(col.tiff_naming_check_requested, timeout=1000) as check:
        card._show_menu(QPoint(0, 0))
    assert check.args == [str(tif)]

    monkeypatch.setattr(QMenu, "exec", trigger_action("删除 TIF"))
    with qtbot.waitSignal(col.tiff_delete_requested, timeout=1000) as delete:
        card._show_menu(QPoint(0, 0))
    assert delete.args == [str(tif)]


def test_results_column_selection_highlights_only_clicked_file_by_default(qtbot):
    from app.widgets.results_column import ResultsColumn, _ArchiveCard, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    tiff = "/fake/a.tif"
    zipf = "/fake/a.zip"
    col.load_uid(
        "UID",
        [{"path": tiff, "name": "a.tif", "seq": 1}],
        [{"path": zipf, "name": "a.zip", "size": 10, "seq": 1}],
    )
    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_card = next(c for c in col._cards if isinstance(c, _ArchiveCard))

    col._toggle_result_selection(tiff, tiff_card)

    assert tiff_card.property("resultSelected") == "true"
    assert zip_card.property("resultSelected") == "false"
    assert col.selected_result_paths() == [tiff]

    col._toggle_result_selection(zipf, zip_card)

    assert zip_card.property("resultSelected") == "true"
    assert len(col.selected_result_paths()) == 2


def test_selected_zip_button_emits_only_selected_archives(qtbot):
    from app.widgets.results_column import ResultsColumn, _ArchiveCard, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    tiff = "/fake/a.tif"
    zip_a = "/fake/a.zip"
    zip_b = "/fake/b.zip"
    col.load_uid(
        "UID",
        [{"path": tiff, "name": "a.tif", "seq": 1}],
        [
            {"path": zip_a, "name": "a.zip", "size": 10, "seq": 1},
            {"path": zip_b, "name": "b.zip", "size": 11, "seq": 2},
        ],
    )
    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_cards = [c for c in col._cards if isinstance(c, _ArchiveCard)]

    col._toggle_result_selection(tiff, tiff_card)
    col._toggle_result_selection(zip_a, zip_cards[0])
    col._toggle_result_selection(zip_b, zip_cards[1])

    assert col.selected_zip_paths() == [zip_a, zip_b]
    assert not col._restore_selected_btn.isHidden()
    assert "2" in col._restore_selected_btn.text()
    with qtbot.waitSignal(col.restore_many_requested, timeout=1000) as emitted:
        col._restore_selected_btn.click()
    assert emitted.args == [[zip_a, zip_b]]


def test_selected_actions_separate_tiffs_and_zips(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QMenu
    from app.widgets.results_column import ResultsColumn, _ArchiveCard, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    tiff = "/fake/a.tif"
    zipf = "/fake/a.zip"
    col.load_uid(
        "UID",
        [{"path": tiff, "name": "a.tif", "seq": 1}],
        [{"path": zipf, "name": "a.zip", "size": 10, "seq": 1}],
    )
    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_card = next(c for c in col._cards if isinstance(c, _ArchiveCard))
    col._toggle_result_selection(tiff, tiff_card)
    col._toggle_result_selection(zipf, zip_card)

    assert col.selected_tiff_paths() == [tiff]
    assert col.selected_zip_paths() == [zipf]
    assert col.visible_selected_paths() == [tiff, zipf]
    assert not col._selected_actions_btn.isHidden()
    captured = {}

    def fake_exec(menu, *_args, **_kwargs):
        captured["labels"] = [action.text() for action in menu.actions()]
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    col._show_selected_actions_menu()
    assert "还原所选 ZIP（1）" in captured["labels"]
    assert "检查所选 TIF 命名（1）" in captured["labels"]
    assert "复制所选路径（2）" in captured["labels"]


def test_clicking_result_card_selects_only_that_file(qtbot):
    from PyQt6.QtCore import Qt
    from app.widgets.results_column import (
        ResultsColumn, _ArchiveCard, _ResultPairIndicator, _TiffCard,
    )

    col = ResultsColumn()
    qtbot.addWidget(col)
    tiff = "/fake/a.tif"
    zipf = "/fake/a.zip"
    col.load_uid(
        "UID",
        [{"path": tiff, "name": "a.tif", "seq": 1}],
        [{"path": zipf, "name": "a.zip", "size": 10, "seq": 1}],
    )
    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_card = next(c for c in col._cards if isinstance(c, _ArchiveCard))
    pair_indicator = col.findChild(_ResultPairIndicator)

    qtbot.mouseClick(tiff_card, Qt.MouseButton.LeftButton)

    assert tiff_card.property("resultSelected") == "true"
    assert zip_card.property("resultSelected") == "false"
    assert pair_indicator.property("selected") == "false"
    assert col.selected_result_paths() == [tiff]


def test_paired_selection_mode_clicks_visible_pair(qtbot):
    from PyQt6.QtCore import Qt
    from app.widgets.results_column import (
        ResultsColumn, _ArchiveCard, _ResultPairIndicator, _TiffCard,
    )

    col = ResultsColumn()
    qtbot.addWidget(col)
    tiff = "/fake/a.tif"
    zipf = "/fake/a.zip"
    col.load_uid(
        "UID",
        [{"path": tiff, "name": "a.tif", "seq": 1}],
        [{"path": zipf, "name": "a.zip", "size": 10, "seq": 1}],
    )
    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_card = next(c for c in col._cards if isinstance(c, _ArchiveCard))
    pair_indicator = col.findChild(_ResultPairIndicator)

    col._paired_selection_btn.click()
    qtbot.mouseClick(tiff_card, Qt.MouseButton.LeftButton)

    assert col._paired_selection_enabled is True
    assert col._paired_selection_btn.isChecked()
    assert tiff_card.property("resultSelected") == "true"
    assert zip_card.property("resultSelected") == "true"
    assert pair_indicator.property("selected") == "true"
    assert col.selected_result_paths() == sorted([tiff, zipf])


def test_tiff_card_context_menu_links_visible_zip(qtbot, monkeypatch):
    from app.widgets.results_column import ResultsColumn, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    tiff = "/fake/a.tif"
    zipf = "/fake/a.zip"
    col.load_uid(
        "UID",
        [{"path": tiff, "name": "a.tif", "seq": 1}],
        [{"path": zipf, "name": "a.zip", "size": 10, "seq": 1}],
    )
    card = next(c for c in col._cards if isinstance(c, _TiffCard))

    def trigger_link(menu, *_args, **_kwargs):
        action = next(a for a in menu.actions() if a.text() == "关联到右侧编号")
        assert action.isEnabled()
        action.trigger()
        return None

    monkeypatch.setattr(QMenu, "exec", trigger_link)
    with qtbot.waitSignal(col.link_result_requested, timeout=1000) as blocker:
        card._show_menu(QPoint(0, 0))

    assert blocker.args == [tiff, zipf]


def test_archive_card_context_menu_links_visible_tiff(qtbot, monkeypatch):
    from app.widgets.results_column import ResultsColumn, _ArchiveCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    tiff = "/fake/a.tif"
    zipf = "/fake/a.zip"
    col.load_uid(
        "UID",
        [{"path": tiff, "name": "a.tif", "seq": 1}],
        [{"path": zipf, "name": "a.zip", "size": 10, "seq": 1}],
    )
    card = next(c for c in col._cards if isinstance(c, _ArchiveCard))

    def trigger_link(menu, *_args, **_kwargs):
        action = next(a for a in menu.actions() if a.text() == "关联到右侧编号")
        assert action.isEnabled()
        action.trigger()
        return None

    monkeypatch.setattr(QMenu, "exec", trigger_link)
    with qtbot.waitSignal(col.link_result_requested, timeout=1000) as blocker:
        card._show_menu(QPoint(0, 0))

    assert blocker.args == [tiff, zipf]


def test_results_cards_show_registry_status(qtbot):
    from PyQt6.QtWidgets import QLabel
    from app.widgets.results_column import ResultsColumn, _ArchiveCard, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{
            "path": "/fake/a.tif",
            "name": "a.tif",
            "seq": 1,
            "owner_uid": "UID",
            "group_index": 0,
            "registered": True,
        }],
        [{
            "path": "/fake/a.zip",
            "name": "a.zip",
            "size": 10,
            "seq": 1,
            "owner_uid": "UID",
            "group_index": 0,
            "registered": True,
        }],
    )

    tiff_card = next(c for c in col._cards if isinstance(c, _TiffCard))
    zip_card = next(c for c in col._cards if isinstance(c, _ArchiveCard))
    tiff_text = " ".join(lbl.text() for lbl in tiff_card.findChildren(QLabel))
    zip_text = " ".join(lbl.text() for lbl in zip_card.findChildren(QLabel))
    assert "已入库: UID" in tiff_text
    assert "已配 ZIP" in tiff_text
    assert "已入库: UID" in zip_text
    assert "已配 TIF" in zip_text


def test_results_sort_by_sequence_orders_pairs(qtbot):
    """The explicit sequence sort orders paired rows by seq before filename."""
    from app.widgets.results_column import ResultsColumn, _TiffCard

    col = ResultsColumn()
    qtbot.addWidget(col)
    col.load_uid(
        "UID",
        [{"path": "/fake/b.tif", "name": "b.tif", "seq": 2},
         {"path": "/fake/a.tif", "name": "a.tif", "seq": 1}],
        [{"path": "/fake/b.zip", "name": "b.zip", "size": 2, "seq": 2},
         {"path": "/fake/a.zip", "name": "a.zip", "size": 1, "seq": 1}],
    )

    col._set_sort_key("seq")

    cards = [c for c in col._cards if isinstance(c, _TiffCard)]
    assert [c._info["seq"] for c in cards] == [1, 2]


# ── 用户报障 2026-07-10: 预览全部编号 / 多选编号预览 / 点击迟钝 ──────────────


def _mk_group(tmp_path, uid: str, n: int) -> dict:
    d = tmp_path / uid
    d.mkdir(parents=True, exist_ok=True)
    tiffs = []
    for i in range(1, n + 1):
        p = d / f"{uid}-{i}-20260618.tif"
        p.write_bytes(b"\x00" * 16)
        tiffs.append({"path": str(p), "name": p.name})
    return {"uid": uid, "tiffs": tiffs, "zips": []}


class TestAllModeLightboxSpansEveryUid:
    """「全部」模式打开大图后, 上/下一张必须能跨编号连续翻页。

    旧实现把 all_tiff_paths 在分组循环内按 group 重建 → 灯箱锁死在单个编号内。
    """

    def test_lightbox_paths_cover_all_groups(self, qtbot, tmp_path, monkeypatch):
        from app.widgets.results_column import ResultsColumn

        col = ResultsColumn()
        qtbot.addWidget(col)
        groups = [_mk_group(tmp_path, "AAA-1-R-20260618", 2),
                  _mk_group(tmp_path, "BBB-2-R-20260618", 3)]

        captured: list = []
        monkeypatch.setattr(
            col, "_open_tiff_lightbox",
            lambda clicked, paths: captured.append((clicked, list(paths))),
        )
        col.load_many(groups)

        # 点第一个编号的第一张 → 翻页范围必须含两个编号的全部 5 张
        first = next(c for c in col._cards if hasattr(c, "_lightbox_fn"))
        first._lightbox_fn(first._info["path"])
        assert captured, "sanity: 灯箱应被调用"
        _clicked, paths = captured[0]
        assert len(paths) == 5, f"翻页范围只有 {len(paths)} 张, 应跨全部编号共 5 张"


class TestSelectedResultsScopeLightbox:
    """勾选多个编号的成片后, 大图翻页只在勾选集合内 —— 即「选择多个编号预览」."""

    def test_selection_scopes_paging(self, qtbot, tmp_path):
        from app.widgets.results_column import ResultsColumn

        col = ResultsColumn()
        qtbot.addWidget(col)
        g1 = _mk_group(tmp_path, "AAA-1-R-20260618", 2)
        g2 = _mk_group(tmp_path, "BBB-2-R-20260618", 3)
        col.load_many([g1, g2])

        pick = [g1["tiffs"][0]["path"], g2["tiffs"][2]["path"]]  # 跨两个编号各挑一张
        for p in pick:
            col._toggle_result_selection(p)

        from pathlib import Path
        scoped = col._lightbox_scope(Path(pick[0]))
        assert {str(p) for p in scoped} == set(pick), (
            "勾选 2 张(跨编号)后, 翻页范围应只含这 2 张"
        )

    def test_no_selection_uses_full_scope(self, qtbot, tmp_path):
        from pathlib import Path

        from app.widgets.results_column import ResultsColumn

        col = ResultsColumn()
        qtbot.addWidget(col)
        g1 = _mk_group(tmp_path, "AAA-1-R-20260618", 2)
        col.load_many([g1])
        scoped = col._lightbox_scope(Path(g1["tiffs"][0]["path"]))
        assert len(scoped) == 2, "无勾选时翻页范围 = 当前全部成片"


class TestThumbnailsAreDeferred:
    """点击编号不得在主线程同步解码全部 TIFF 缩略图(卡顿主因)。

    卡片必须 defer + 进 20ms 分批队列, 点击立即返回, 缩略图随后填充。
    """

    def test_load_uid_defers_thumbnails(self, qtbot, tmp_path):
        from app.widgets.results_column import ResultsColumn

        col = ResultsColumn()
        qtbot.addWidget(col)
        g = _mk_group(tmp_path, "AAA-1-R-20260618", 3)
        col.load_uid("AAA-1-R-20260618", g["tiffs"], [])

        tiff_cards = [c for c in col._cards if hasattr(c, "_lightbox_fn")]
        assert tiff_cards, "sanity: 应建出 TIFF 卡片"
        assert all(c._defer_thumbnail for c in tiff_cards), "TIFF 卡片必须延迟解码"
        assert not any(c._thumbnail_loaded for c in tiff_cards), "构造时不得已解码"
        assert len(col._thumb_queue) == len(tiff_cards), "全部卡片应进异步队列"

    def test_load_many_defers_thumbnails(self, qtbot, tmp_path):
        from app.widgets.results_column import ResultsColumn

        col = ResultsColumn()
        qtbot.addWidget(col)
        col.load_many([_mk_group(tmp_path, "AAA-1-R-20260618", 2),
                       _mk_group(tmp_path, "BBB-2-R-20260618", 2)])
        tiff_cards = [c for c in col._cards if hasattr(c, "_lightbox_fn")]
        assert all(c._defer_thumbnail for c in tiff_cards)
        assert len(col._thumb_queue) == len(tiff_cards)


def test_results_default_view_is_large_thumbnail(qtbot, monkeypatch, tmp_path):
    """成果照片墙默认「大缩略图」(2026-07-11 用户要求照片做大), 且持久化。"""
    from PyQt6.QtCore import QSettings
    from app.widgets import results_column as results_column_module

    settings = QSettings(
        str(tmp_path / "results-view.ini"), QSettings.Format.IniFormat
    )
    monkeypatch.setattr(results_column_module, "QSettings", lambda: settings)
    ResultsColumn = results_column_module.ResultsColumn

    settings.remove("ui/results_view_mode")   # 干净默认
    col = ResultsColumn()
    qtbot.addWidget(col)
    assert col._result_view_mode == "large_thumbnail"

    # 切到 list → 持久化 → 新实例应记住
    col._set_result_view_mode("list")
    assert settings.value("ui/results_view_mode") == "list"
    col2 = ResultsColumn()
    qtbot.addWidget(col2)
    assert col2._result_view_mode == "list"
    settings.remove("ui/results_view_mode")


class TestPairedColumnsHysteresis:
    """v0.57 修「疯狂闪屏」: 单/双列阈值加 ±24px 死区——重建引起的滚动条
    出没(viewport 宽 ±十几px)不得再触发判定翻转→全量重建→自激振荡。"""

    def _col(self, qtbot, monkeypatch, width, rendered_paired):
        from app.widgets import results_column as rc

        col = rc.ResultsColumn()
        qtbot.addWidget(col)
        col.show()
        monkeypatch.setattr(col, "_layout_probe_width", lambda: width)
        col._paired_columns_enabled = True
        col._rendered_paired_columns = rendered_paired
        return col

    def test_scrollbar_jitter_inside_deadzone_never_flips(self, qtbot, monkeypatch):
        from app.widgets.results_column_cards import _MIN_PAIRED_COLUMNS_WIDTH as MIN

        # 已是双列, 宽度掉到 MIN-10(死区内, 相当于滚动条出现) → 保持双列
        col = self._col(qtbot, monkeypatch, MIN - 10, rendered_paired=True)
        assert col._should_show_paired_columns() is True

        # 已是单列, 宽度升到 MIN+10(死区内, 滚动条消失) → 保持单列
        col2 = self._col(qtbot, monkeypatch, MIN + 10, rendered_paired=False)
        assert col2._should_show_paired_columns() is False

    def test_beyond_deadzone_still_switches(self, qtbot, monkeypatch):
        from app.widgets.results_column_cards import _MIN_PAIRED_COLUMNS_WIDTH as MIN

        # 真正变窄(越过死区下缘) → 双列退单列
        col = self._col(qtbot, monkeypatch, MIN - 60, rendered_paired=True)
        assert col._should_show_paired_columns() is False

        # 真正变宽(越过死区上缘) → 单列升双列
        col2 = self._col(qtbot, monkeypatch, MIN + 60, rendered_paired=False)
        assert col2._should_show_paired_columns() is True
