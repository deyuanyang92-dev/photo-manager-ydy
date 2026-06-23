"""test_specimen_sidebar.py — per-编号 phase dots in the workbench sidebar.

The left specimen list shows, under each UID, a row of 4 clickable phase dots
(拍摄中/已拍完/整理中/完成).  Clicking a dot marks that 编号's phase via the
``phase_mark_requested(uid, code)`` signal — no activation required.  The
current phase reads from the project DB (tasks.raw_json.status) when collab is
off, so dots work for single-user offline use too.
"""
import sqlite3

from unittest.mock import MagicMock

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

from app.db import db_manager
from app.services import activation_service
from app.widgets.specimen_sidebar import SpecimenSidebar

_APP = QApplication.instance() or QApplication([])

_PROJ = "/tmp/proj-sidebar-test"


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    db_manager.ensure_schema(conn)
    return conn


@pytest.fixture
def ctx(db):
    c = MagicMock()
    c.get_db.return_value = db
    c.current_project_dir = _PROJ
    c.collab_service = None          # collab OFF — dots must still work via DB
    return c


def _add_specimen(db, uid, name="", storage=""):
    db.execute(
        """
        INSERT INTO specimens (uid, scientific_name, storage, owner_project_dir)
        VALUES (?, ?, ?, ?)
        """,
        (uid, name, storage, _PROJ),
    )
    db.commit()


def _add_complete_specimen(db, uid, name="Marphysa sp.", storage="D95E"):
    db.execute(
        """
        INSERT INTO specimens (
            uid, scientific_name, storage,
            collection_date, photo_date, owner_project_dir
        )
        VALUES (?, ?, ?, '20260618', '20260618', ?)
        """,
        (uid, name, storage, _PROJ),
    )
    db.commit()


def _add_specimen_with_fields(db, *, uid, species_id, storage):
    db.execute(
        """
        INSERT INTO specimens (
            uid, province, site, station, id, storage,
            collection_date, photo_date, owner_project_dir
        )
        VALUES (?, 'GXFCG', 'BLW', '', ?, ?, '20260618', '20260618', ?)
        """,
        (uid, species_id, storage, _PROJ),
    )
    db.commit()


def _dots(sidebar, uid):
    return sidebar._phase_dots.get(uid, {})


def test_each_specimen_renders_four_phase_dots(ctx, db):
    _add_specimen(db, "ZJ-TMW-B2-001", "Marphysa sp.")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    dots = _dots(sb, "ZJ-TMW-B2-001")
    assert set(dots.keys()) == {"shooting", "shot_done", "organizing", "done"}
    assert all(isinstance(b, QPushButton) and b.isCheckable() for b in dots.values())


def test_current_phase_dot_is_checked_from_db(ctx, db):
    _add_specimen(db, "U-1")
    activation_service.set_collab_status(db, "U-1", "organizing")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    dots = _dots(sb, "U-1")
    assert dots["organizing"].isChecked() is True
    assert dots["shooting"].isChecked() is False
    assert dots["done"].isChecked() is False


def test_activate_emits_for_single_visible_row_without_prior_selection(ctx, db, qtbot):
    uid = "GXFCG-BLW-SC001-D79-20260618"
    _add_specimen(db, uid)
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    assert sb.current_uid() is None or sb._list.currentItem() is not None
    sb._list.setCurrentItem(None)
    seen: list[str] = []
    sb.activate_requested.connect(seen.append)
    sb._on_activate_clicked()
    assert seen == [uid]
    _add_specimen(db, "U-2")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    dots = _dots(sb, "U-2")
    assert not any(b.isChecked() for b in dots.values())


def test_dot_click_emits_phase_mark_requested(ctx, db):
    _add_specimen(db, "U-3")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    seen = []
    sb.phase_mark_requested.connect(lambda u, c: seen.append((u, c)))
    _dots(sb, "U-3")["shooting"].click()
    assert seen == [("U-3", "shooting")]


def test_dot_click_does_not_self_persist(ctx, db):
    """Click only requests; without a handler writing back the truth is unchanged
    and the auto-toggle is rolled back to the persisted phase (here: none)."""
    _add_specimen(db, "U-4")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    _dots(sb, "U-4")["done"].click()
    # No workbench wired → refresh_phases not called → dot rolled back to truth.
    assert _dots(sb, "U-4")["done"].isChecked() is False
    assert activation_service.get_collab_status(db, "U-4") is None


def test_edit_request_only_emits_uid(ctx, db):
    """Left sidebar edit entry delegates to the right rail; it must not save."""
    _add_specimen(db, "U-EDIT", "Marphysa sp.")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    sb.select_uid("U-EDIT")
    seen = []
    sb.edit_specimen_requested.connect(seen.append)

    assert sb.edit_current_specimen() is True

    assert seen == ["U-EDIT"]
    assert db.execute(
        "SELECT uid FROM specimens WHERE uid = ?",
        ("U-EDIT",),
    ).fetchone()


def test_delete_signal_emits_uid(ctx, db):
    _add_specimen(db, "U-DELETE", "Marphysa sp.")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    seen = []
    sb.delete_specimen_requested.connect(seen.append)

    sb.delete_specimen_requested.emit("U-DELETE")

    assert seen == ["U-DELETE"]


def test_edit_current_specimen_returns_false_without_selection(ctx, db):
    sb = SpecimenSidebar(ctx)
    assert sb.edit_current_specimen() is False


def test_refresh_phases_resyncs_after_external_change(ctx, db):
    _add_specimen(db, "U-5")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    assert not any(b.isChecked() for b in _dots(sb, "U-5").values())
    # Simulate the workbench persisting a phase, then re-syncing dots.
    activation_service.set_collab_status(db, "U-5", "shot_done")
    sb.refresh_phases()
    assert _dots(sb, "U-5")["shot_done"].isChecked() is True


def test_refresh_canonicalizes_lowercase_uid_and_references(ctx, db):
    _add_specimen(db, "fj-d-f-dd001")
    db.execute(
        "INSERT INTO tasks(uid, is_active) VALUES(?, 1)",
        ("fj-d-f-dd001",),
    )
    db.commit()

    sb = SpecimenSidebar(ctx)
    sb.refresh()

    assert db.execute(
        "SELECT 1 FROM specimens WHERE uid = ?",
        ("FJ-D-F-DD001",),
    ).fetchone()
    assert db.execute(
        "SELECT 1 FROM tasks WHERE uid = ? AND is_active = 1",
        ("FJ-D-F-DD001",),
    ).fetchone()
    assert sb._list.item(0).data(Qt.ItemDataRole.UserRole) == "FJ-D-F-DD001"


def test_active_specimen_row_has_active_style_and_badge(ctx, db):
    _add_specimen(db, "FJ-D-F-DD001")
    db.execute(
        "INSERT INTO tasks(uid, is_active) VALUES(?, 1)",
        ("FJ-D-F-DD001",),
    )
    db.commit()

    sb = SpecimenSidebar(ctx)
    sb.refresh()

    row = sb._list.itemWidget(sb._list.item(0))
    assert row.objectName() == "SpecimenRowActive"
    badges = [
        w for w in row.findChildren(QLabel)
        if w.objectName() == "SpecimenActivePill"
    ]
    assert badges and badges[0].text() == "当前激活"


def test_inactive_specimen_row_has_explicit_inactive_badge(ctx, db):
    _add_specimen(db, "FJ-D-F-DD001")

    sb = SpecimenSidebar(ctx)
    sb.refresh()

    row = sb._list.itemWidget(sb._list.item(0))
    badges = [
        w for w in row.findChildren(QLabel)
        if w.objectName() == "SpecimenInactivePill"
    ]
    assert badges and badges[0].text() == "未激活"


def test_action_buttons_reflect_selected_activation_state(ctx, db):
    active_uid = "FJ-D-F-DD001"
    inactive_uid = "FJ-D-F-DD002"
    _add_specimen(db, active_uid)
    _add_specimen(db, inactive_uid)
    db.execute(
        "INSERT INTO tasks(uid, is_active) VALUES(?, 1)",
        (active_uid,),
    )
    db.commit()

    sb = SpecimenSidebar(ctx)
    sb.refresh()

    sb.select_uid(active_uid)
    assert sb._activate_btn.text() == "已激活"
    assert sb._activate_btn.isEnabled() is False
    assert sb._deactivate_btn.text() == "取消激活"
    assert sb._deactivate_btn.isEnabled() is True

    sb.select_uid(inactive_uid)
    assert sb._activate_btn.text() == "激活此编号"
    assert sb._activate_btn.isEnabled() is True
    assert sb._deactivate_btn.text() == "未激活"
    assert sb._deactivate_btn.isEnabled() is False


def test_row_shows_organize_progress_from_grouping(ctx, db):
    uid = "GXFCG-BLW-SC001-D79-20260618"
    _add_specimen(db, uid)
    db.execute(
        """
        INSERT INTO grouping
          (uid, group_index, jpg_paths, composed_tiff_path, status, archive_zip)
        VALUES
          (?, 0, '["a.jpg","b.jpg"]', '/p/results/one.tif', 'organized',
           '/p/results/one.zip')
        """,
        (uid,),
    )
    db.commit()

    sb = SpecimenSidebar(ctx)
    sb.refresh()

    row = sb._list.itemWidget(sb._list.item(0))
    badges = [
        w for w in row.findChildren(QLabel)
        if w.objectName() == "SpecimenProgressBadge"
    ]
    assert badges and badges[0].text() == "已整理 1角度"


def test_organized_progress_badge_does_not_show_total_groups(ctx, db):
    uid = "GXFCG-BLW-SC001-D79-20260618"
    _add_specimen(db, uid)
    db.executemany(
        """
        INSERT INTO grouping
          (uid, group_index, jpg_paths, composed_tiff_path, status, archive_zip)
        VALUES
          (?, ?, ?, ?, ?, ?)
        """,
        [
            (uid, 0, '["a.jpg"]', "/p/results/one.tif", "organized", "/p/results/one.zip"),
            (uid, 1, '["b.jpg"]', "/p/results/two.tif", "organized", "/p/results/two.zip"),
            (uid, 2, '["c.jpg"]', "/p/results/three.tif", "composed", None),
        ],
    )
    db.commit()

    sb = SpecimenSidebar(ctx)
    sb.refresh()

    row = sb._list.itemWidget(sb._list.item(0))
    badges = [
        w for w in row.findChildren(QLabel)
        if w.objectName() == "SpecimenProgressBadge"
    ]
    assert badges and badges[0].text() == "已整理 2角度"


def test_selected_specimen_row_gets_explicit_selected_property(ctx, db):
    _add_specimen(db, "FJ-D-F-DD001")
    _add_specimen(db, "FJ-D-F-DD002")
    sb = SpecimenSidebar(ctx)
    sb.refresh()

    first = sb._list.item(0)
    second = sb._list.item(1)
    sb._list.setCurrentItem(first)
    first_row = sb._list.itemWidget(first)
    second_row = sb._list.itemWidget(second)
    assert first_row.property("selected") is True
    assert second_row.property("selected") is False

    sb._list.setCurrentItem(second)
    assert first_row.property("selected") is False
    assert second_row.property("selected") is True


def test_rna_filter_button_shows_count_and_filters_r_prefix(ctx, db):
    _add_specimen(db, "RNA-1", storage="RD75E")
    _add_specimen(db, "NONRNA-1", storage="D95E")
    sb = SpecimenSidebar(ctx)
    sb.refresh()

    assert sb._filter_all_btn.text() == "全部 2"
    assert sb._filter_rna_btn.text() == "RNA编号 1"

    sb._filter_rna_btn.click()

    assert sb._list.count() == 1
    assert sb._list.item(0).data(Qt.ItemDataRole.UserRole) == "RNA-1"
    assert sb._filter_rna_btn.text() == "RNA编号 1"


def test_all_filter_button_requests_all_results(ctx, db, qtbot):
    _add_specimen(db, "RNA-1", storage="RD75E")
    _add_specimen(db, "NONRNA-1", storage="D95E")
    sb = SpecimenSidebar(ctx)
    qtbot.addWidget(sb)
    sb.refresh()

    received = []
    sb.show_all_requested.connect(lambda: received.append(True))

    sb._filter_all_btn.click()

    assert received == [True]
    assert sb._list.count() == 2


def test_attention_filter_shows_incomplete_specimens(ctx, db):
    _add_complete_specimen(db, "COMPLETE-1")
    _add_specimen(db, "MISSING-1", storage="D95E")
    sb = SpecimenSidebar(ctx)
    sb.refresh()

    assert sb._filter_attention_btn.text() == "资料待补 1"
    assert "缺物种名、保存方式、采集日期或拍照日期" in sb._filter_attention_btn.toolTip()

    sb._filter_attention_btn.click()

    assert sb._list.count() == 1
    assert sb._list.item(0).data(Qt.ItemDataRole.UserRole) == "MISSING-1"
    assert sb._filter_attention_btn.text() == "资料待补 1"


def test_rna_badge_and_missing_species_are_visible_on_row(ctx, db):
    _add_specimen(db, "RNA-2", storage="RT95E")
    sb = SpecimenSidebar(ctx)
    sb.refresh()

    row = sb._list.itemWidget(sb._list.item(0))
    rna_badges = [
        w for w in row.findChildren(QLabel)
        if w.objectName() == "SpecimenRnaBadge"
    ]
    missing = [
        w for w in row.findChildren(QLabel)
        if w.objectName() == "SpecimenMissingText"
    ]

    assert rna_badges and "已取 RNA" in rna_badges[0].text()
    assert "RT95E" in rna_badges[0].text()
    assert missing and missing[0].text() == "未填写物种信息"


def test_refresh_repairs_malformed_uid_from_naming_fields(ctx, db):
    _add_specimen_with_fields(db, uid="RD79", species_id="SC002", storage="RD79")
    sb = SpecimenSidebar(ctx)
    sb.refresh()

    fixed_uid = "GXFCG-BLW-SC002-RD79-20260618"
    assert sb._list.item(0).data(Qt.ItemDataRole.UserRole) == fixed_uid
    assert db.execute("SELECT 1 FROM specimens WHERE uid=?", (fixed_uid,)).fetchone()
    assert not db.execute("SELECT 1 FROM specimens WHERE uid='RD79'").fetchone()
