import sqlite3

from app.db.db_manager import ensure_schema
from app.services import rna_label_queue_service as q


def _db():
    db = sqlite3.connect(":memory:")
    ensure_schema(db)
    return db


def test_enqueue_deduplicates_pending_uid():
    db = _db()
    try:
        assert q.enqueue(db, ["UID-1"]) == 1
        assert q.enqueue(db, ["UID-1"]) == 0
        assert q.pending_uids(db) == ["UID-1"]
    finally:
        db.close()


def test_mark_printed_removes_uid_from_pending_count():
    db = _db()
    try:
        q.enqueue(db, ["UID-1", "UID-2"])
        assert q.mark_printed(db, ["UID-1"]) == 1
        assert q.pending_uids(db) == ["UID-2"]
        assert q.pending_count(db) == 1
    finally:
        db.close()


def test_clear_pending_marks_all_pending_as_done():
    db = _db()
    try:
        q.enqueue(db, ["UID-1", "UID-2"])
        assert q.clear_pending(db) == 2
        assert q.pending_uids(db) == []
        assert q.pending_count(db) == 0
    finally:
        db.close()
