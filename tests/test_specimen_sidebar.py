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

from PyQt6.QtWidgets import QApplication, QPushButton

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


def _add_specimen(db, uid, name=""):
    db.execute(
        "INSERT INTO specimens (uid, scientific_name, owner_project_dir) VALUES (?, ?, ?)",
        (uid, name, _PROJ),
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


def test_no_phase_means_no_dot_checked(ctx, db):
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


def test_refresh_phases_resyncs_after_external_change(ctx, db):
    _add_specimen(db, "U-5")
    sb = SpecimenSidebar(ctx)
    sb.refresh()
    assert not any(b.isChecked() for b in _dots(sb, "U-5").values())
    # Simulate the workbench persisting a phase, then re-syncing dots.
    activation_service.set_collab_status(db, "U-5", "shot_done")
    sb.refresh_phases()
    assert _dots(sb, "U-5")["shot_done"].isChecked() is True
