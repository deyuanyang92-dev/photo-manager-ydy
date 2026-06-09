"""test_collab_claim_on_save.py — UID claim is wired into the save flow.

When collaboration is active (running + a non-empty group code), saving a NEW
specimen UID must claim it across the LAN via CollabService.create_task.  A 409
(someone else already holds the UID) blocks the save.  Re-saving a UID that is
already a local specimen must NOT re-claim.

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_collab_claim_on_save.py -v
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.views.workbench_view import WorkbenchView


_APP = None


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    global _APP
    if _APP is None:
        _APP = QApplication.instance() or QApplication([])
    return _APP


def _make_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS specimens (
            uid TEXT PRIMARY KEY,
            id TEXT, province TEXT, site TEXT, station TEXT,
            storage TEXT, collection_date TEXT, photo_date TEXT,
            photo_notes TEXT, owner_project_dir TEXT
        );
        """
    )
    return conn


def _make_ctx(project_dir, db, svc):
    ctx = MagicMock()
    ctx.has_project = True
    ctx.current_project_dir = project_dir
    ctx.get_db.return_value = db
    ctx.collab_service = svc
    return ctx


def _fill_naming(w: WorkbenchView) -> str:
    n = w._naming
    n._province.setText("FJ")
    n._site.setText("XM")
    n._station.setText("B2")
    n._species_id.setText("DLC001")
    n._storage.setText("T95E")
    n._collection_date.setText("20260601")
    return n.current_uid()


def _setup(tmp_path, svc):
    project_dir = str(tmp_path / "proj")
    Path(project_dir, "_data").mkdir(parents=True)
    db = _make_db(str(tmp_path / "proj" / "_data" / "project.db"))
    ctx = _make_ctx(project_dir, db, svc)
    w = WorkbenchView(ctx)
    return w, db


def _row_count(db, uid):
    return db.execute("SELECT COUNT(*) FROM specimens WHERE uid=?", (uid,)).fetchone()[0]


class TestClaimOnSave:
    def test_new_uid_is_claimed_and_saved(self, tmp_path):
        svc = MagicMock()
        svc.is_running.return_value = True
        svc.group_code = "G1"
        svc.create_task.return_value = (True, "ok")
        w, db = _setup(tmp_path, svc)
        uid = _fill_naming(w)
        assert uid
        w._on_naming_save()
        svc.create_task.assert_called_once()
        assert svc.create_task.call_args.args[0] == uid
        assert _row_count(db, uid) == 1

    def test_409_blocks_save(self, tmp_path, monkeypatch):
        warned = []
        monkeypatch.setattr(QMessageBox, "warning",
                            lambda *a, **k: warned.append(a))
        svc = MagicMock()
        svc.is_running.return_value = True
        svc.group_code = "G1"
        svc.create_task.return_value = (False, "409: UID taken (peer host-b)")
        w, db = _setup(tmp_path, svc)
        uid = _fill_naming(w)
        w._on_naming_save()
        svc.create_task.assert_called_once()
        assert _row_count(db, uid) == 0  # save was blocked
        assert warned  # user was warned

    def test_resave_existing_local_uid_not_reclaimed(self, tmp_path):
        svc = MagicMock()
        svc.is_running.return_value = True
        svc.group_code = "G1"
        svc.create_task.return_value = (True, "ok")
        w, db = _setup(tmp_path, svc)
        uid = _fill_naming(w)
        # Pre-insert the UID as an existing local specimen.
        db.execute("INSERT INTO specimens (uid, owner_project_dir) VALUES (?,?)",
                   (uid, w.ctx.current_project_dir))
        db.commit()
        w._on_naming_save()
        svc.create_task.assert_not_called()
        assert _row_count(db, uid) == 1

    def test_no_service_saves_normally(self, tmp_path):
        w, db = _setup(tmp_path, None)
        uid = _fill_naming(w)
        w._on_naming_save()
        assert _row_count(db, uid) == 1

    def test_disabled_service_saves_without_claim(self, tmp_path):
        svc = MagicMock()
        svc.is_running.return_value = False
        svc.group_code = "G1"
        w, db = _setup(tmp_path, svc)
        uid = _fill_naming(w)
        w._on_naming_save()
        svc.create_task.assert_not_called()
        assert _row_count(db, uid) == 1

    def test_no_group_code_saves_without_claim(self, tmp_path):
        svc = MagicMock()
        svc.is_running.return_value = True
        svc.group_code = ""
        w, db = _setup(tmp_path, svc)
        uid = _fill_naming(w)
        w._on_naming_save()
        svc.create_task.assert_not_called()
        assert _row_count(db, uid) == 1
