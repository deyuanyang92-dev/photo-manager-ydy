"""test_supplementary_service.py — 补处理 (supplementary archival) pure-logic.

Mirrors web oracle finalCompositeTarget (app.js:3808-3824) +
validateSmartGroup (app.js:4097-4123). NO Qt, NO mocking of safety gates.

Core requirement under test: specimen identity is resolved from the TIFF
filename, NOT from the active specimen — 补处理 must work with no active task.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from app.db.db_manager import ensure_schema
from app.services.supplementary_service import (
    SuppGroup,
    SuppGroupError,
    resolve_specimen_for_tiff,
    validate_supp_group,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    yield conn
    conn.close()


def _insert_specimen(db: sqlite3.Connection, uid: str, **cols) -> None:
    db.execute("INSERT INTO specimens (uid) VALUES (?)", (uid,))
    if cols:
        sets = ", ".join(f"{k}=?" for k in cols)
        db.execute(f"UPDATE specimens SET {sets} WHERE uid=?", (*cols.values(), uid))
    db.commit()


# Canonical full result-name (with seq at index 4) and its uniqueId (no seq).
RESULT_NAME = "FJ-XM-B2-DLC001-1-T95E-20260601.tif"
UID = "FJ-XM-B2-DLC001-T95E-20260601"


def _touch(path: str, content: bytes = b"x") -> str:
    with open(path, "wb") as fh:
        fh.write(content)
    return path


# ── resolve_specimen_for_tiff ─────────────────────────────────────────────────

class TestResolveSpecimen:
    def test_matches_by_tiff_name(self, db):
        _insert_specimen(db, UID)
        sp = resolve_specimen_for_tiff(db, RESULT_NAME)
        assert sp is not None
        assert sp["uid"] == UID

    def test_case_insensitive(self, db):
        _insert_specimen(db, UID)
        sp = resolve_specimen_for_tiff(db, RESULT_NAME.lower())
        assert sp is not None
        assert sp["uid"] == UID

    def test_ignores_sequence_segment(self, db):
        """Different seq, same uniqueId → still matches (seq is stripped)."""
        _insert_specimen(db, UID)
        sp = resolve_specimen_for_tiff(db, "FJ-XM-B2-DLC001-7-T95E-20260601.tiff")
        assert sp is not None
        assert sp["uid"] == UID

    def test_no_active_task_required(self, db):
        """No tasks row at all (nothing activated) — lookup still succeeds.

        This is the whole point of 补处理: independent of activation state.
        """
        _insert_specimen(db, UID)
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        sp = resolve_specimen_for_tiff(db, RESULT_NAME)
        assert sp is not None and sp["uid"] == UID

    def test_returns_none_for_unparseable_tiff(self, db):
        _insert_specimen(db, UID)
        assert resolve_specimen_for_tiff(db, "random-photo.tif") is None

    def test_returns_none_when_no_specimen_row(self, db):
        # Name parses fine, but no matching specimen exists in DB.
        assert resolve_specimen_for_tiff(db, RESULT_NAME) is None


# ── validate_supp_group ───────────────────────────────────────────────────────

class TestValidateSuppGroup:
    def test_happy_path(self, db, tmp_path):
        _insert_specimen(db, UID)
        jpgs = [_touch(str(tmp_path / f"a{i}.jpg")) for i in range(3)]
        tiff = _touch(str(tmp_path / RESULT_NAME))
        grp = validate_supp_group(db, [*jpgs, tiff])
        assert isinstance(grp, SuppGroup)
        assert grp.uid == UID
        assert sorted(grp.jpg_paths) == sorted(jpgs)
        assert grp.tiff_path == tiff
        assert grp.specimen is not None

    def test_single_jpg_allowed(self, db, tmp_path):
        """minJpgs == 1 in real-monitor mode (oracle shouldUseRealMonitor)."""
        _insert_specimen(db, UID)
        jpg = _touch(str(tmp_path / "a.jpg"))
        tiff = _touch(str(tmp_path / RESULT_NAME))
        grp = validate_supp_group(db, [jpg, tiff])
        assert len(grp.jpg_paths) == 1

    def test_requires_at_least_one_jpg(self, db, tmp_path):
        _insert_specimen(db, UID)
        tiff = _touch(str(tmp_path / RESULT_NAME))
        with pytest.raises(SuppGroupError):
            validate_supp_group(db, [tiff])

    def test_requires_exactly_one_tiff(self, db, tmp_path):
        _insert_specimen(db, UID)
        jpg = _touch(str(tmp_path / "a.jpg"))
        t1 = _touch(str(tmp_path / RESULT_NAME))
        t2 = _touch(str(tmp_path / "FJ-XM-B2-DLC002-1-T95E-20260601.tif"))
        with pytest.raises(SuppGroupError):
            validate_supp_group(db, [jpg, t1, t2])

    def test_rejects_unsupported_file(self, db, tmp_path):
        _insert_specimen(db, UID)
        jpg = _touch(str(tmp_path / "a.jpg"))
        tiff = _touch(str(tmp_path / RESULT_NAME))
        zip_ = _touch(str(tmp_path / "x.zip"))
        with pytest.raises(SuppGroupError):
            validate_supp_group(db, [jpg, tiff, zip_])

    def test_unnamed_tiff_message(self, db, tmp_path):
        """TIFF doesn't resolve to a specimen → exact oracle pause message."""
        jpg = _touch(str(tmp_path / "a.jpg"))
        tiff = _touch(str(tmp_path / "FJ-XM-B2-DLC001-1-T95E-20260601.tif"))
        # No specimen inserted → resolve returns None.
        with pytest.raises(SuppGroupError) as ei:
            validate_supp_group(db, [jpg, tiff])
        assert str(ei.value) == "TIFF 未按完整成果文件名命名，已暂停该组"

    def test_empty_selection_errors(self, db):
        with pytest.raises(SuppGroupError):
            validate_supp_group(db, [])
