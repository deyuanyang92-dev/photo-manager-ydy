"""test_activation_service.py — Unit tests for activation_service.

Tests cover:
  - activate() mutual exclusion: only one specimen active at a time.
  - activate() writes log event to state.json.
  - deactivate() clears is_active in DB, does NOT write log event.
  - manual_assign() appends manual-assign event to state.json.
  - read_activations() reconstructs AttributionCtx (activations, assign_to_uid).
  - get_active_uid() returns the active specimen from tasks table.

These tests are pure-logic (no Qt, no Helicon, no real filesystem beyond tmp dirs).
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.services.activation_service import (
    activate,
    deactivate,
    get_active_uid,
    manual_assign,
    read_activations,
    _state_json_path,
    _read_state,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    """In-memory SQLite with the tasks table schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            uid TEXT PRIMARY KEY,
            is_active INTEGER DEFAULT 0,
            activated_at TEXT,
            last_organized_at TEXT,
            next_result_sequence_hint INTEGER,
            raw_json TEXT
        );
    """)
    conn.commit()
    return conn


@pytest.fixture
def tmp_project(tmp_path):
    """Return a temp project_dir string with the _data/ subdir created."""
    proj = str(tmp_path / "project")
    os.makedirs(os.path.join(proj, "_data"), exist_ok=True)
    return proj


@pytest.fixture
def db():
    conn = _make_db()
    yield conn
    conn.close()


# ── activate() ────────────────────────────────────────────────────────────────

class TestActivate:
    def test_activates_uid_in_db(self, tmp_project, db):
        """activate() sets is_active=1 for the target uid."""
        result = activate(tmp_project, db, "UID-A")
        assert result["ok"] is True
        row = db.execute("SELECT is_active FROM tasks WHERE uid='UID-A'").fetchone()
        assert row is not None
        assert row["is_active"] == 1

    def test_mutual_exclusion_deactivates_previous(self, tmp_project, db):
        """Activating UID-B must deactivate UID-A (global mutual exclusion)."""
        activate(tmp_project, db, "UID-A")
        activate(tmp_project, db, "UID-B")

        row_a = db.execute("SELECT is_active FROM tasks WHERE uid='UID-A'").fetchone()
        row_b = db.execute("SELECT is_active FROM tasks WHERE uid='UID-B'").fetchone()
        assert row_a["is_active"] == 0
        assert row_b["is_active"] == 1

    def test_returns_previous_uid(self, tmp_project, db):
        """activate() returns the previously active uid in the result dict."""
        activate(tmp_project, db, "UID-A")
        result = activate(tmp_project, db, "UID-B")
        assert result["previous_uid"] == "UID-A"
        assert result["new_uid"] == "UID-B"

    def test_previous_uid_none_when_first_activation(self, tmp_project, db):
        """No previous activation → previous_uid is None."""
        result = activate(tmp_project, db, "UID-A")
        assert result["previous_uid"] is None

    def test_writes_activation_event_to_state_json(self, tmp_project, db):
        """activate() appends a source=activation event to state.json."""
        activate(tmp_project, db, "UID-A")
        state = _read_state(tmp_project)
        events = state.get("events", [])
        assert len(events) == 1
        assert events[0]["source"] == "activation"
        assert events[0]["specimenUniqueId"] == "UID-A"

    def test_multiple_activations_append_events(self, tmp_project, db):
        """Each activate() call appends a new event; old events are preserved."""
        activate(tmp_project, db, "UID-A")
        activate(tmp_project, db, "UID-B")
        events = _read_state(tmp_project).get("events", [])
        assert len(events) == 2
        assert events[0]["specimenUniqueId"] == "UID-A"
        assert events[1]["specimenUniqueId"] == "UID-B"

    def test_raises_on_empty_uid(self, tmp_project, db):
        with pytest.raises(ValueError):
            activate(tmp_project, db, "")

    def test_only_one_active_after_multiple_activations(self, tmp_project, db):
        """After three activations, only the last uid should be active."""
        activate(tmp_project, db, "UID-A")
        activate(tmp_project, db, "UID-B")
        activate(tmp_project, db, "UID-C")
        rows = db.execute("SELECT uid, is_active FROM tasks").fetchall()
        active = [r["uid"] for r in rows if r["is_active"] == 1]
        assert active == ["UID-C"]


# ── deactivate() ─────────────────────────────────────────────────────────────

class TestDeactivate:
    def test_deactivate_clears_is_active(self, tmp_project, db):
        """deactivate() sets is_active=0 for the given uid."""
        activate(tmp_project, db, "UID-A")
        deactivate(tmp_project, db, "UID-A")
        row = db.execute("SELECT is_active FROM tasks WHERE uid='UID-A'").fetchone()
        assert row["is_active"] == 0

    def test_deactivate_does_not_write_log_event(self, tmp_project, db):
        """deactivate() must NOT append a new event to state.json.

        Oracle: server.js:3857-3861 — only clears isActive, no log written.
        """
        activate(tmp_project, db, "UID-A")
        initial_event_count = len(_read_state(tmp_project).get("events", []))
        deactivate(tmp_project, db, "UID-A")
        final_event_count = len(_read_state(tmp_project).get("events", []))
        # No new event should have been appended
        assert final_event_count == initial_event_count

    def test_deactivate_returns_ok(self, tmp_project, db):
        result = deactivate(tmp_project, db, "UID-A")
        assert result["ok"] is True
        assert result["previous_uid"] == "UID-A"
        assert result["new_uid"] is None

    def test_deactivate_raises_on_empty_uid(self, tmp_project, db):
        with pytest.raises(ValueError):
            deactivate(tmp_project, db, "")


# ── manual_assign() ───────────────────────────────────────────────────────────

class TestManualAssign:
    def test_appends_manual_assign_event(self, tmp_project):
        """manual_assign() appends a source=manual-assign event."""
        result = manual_assign(tmp_project, "UID-A", ["/fake/img.jpg"])
        assert result["ok"] is True
        assert result["count"] == 1

        events = _read_state(tmp_project).get("events", [])
        assert len(events) == 1
        assert events[0]["source"] == "manual-assign"
        assert events[0]["specimenUniqueId"] == "UID-A"

    def test_resolves_paths(self, tmp_project):
        """manual_assign() stores resolved (absolute) paths."""
        manual_assign(tmp_project, "UID-A", ["/some/path/img.jpg"])
        events = _read_state(tmp_project).get("events", [])
        paths = events[0].get("jpgPaths", [])
        assert all(os.path.isabs(p) for p in paths)

    def test_empty_jpg_list_returns_ok_count_zero(self, tmp_project):
        """manual_assign() with empty list returns ok without writing events."""
        result = manual_assign(tmp_project, "UID-A", [])
        assert result["ok"] is True
        assert result["count"] == 0
        # No events written
        assert _read_state(tmp_project).get("events") is None

    def test_raises_on_empty_uid(self, tmp_project):
        with pytest.raises(ValueError):
            manual_assign(tmp_project, "", ["/fake/img.jpg"])


# ── read_activations() ────────────────────────────────────────────────────────

class TestReadActivations:
    def test_empty_project_returns_empty_ctx(self, tmp_project):
        """read_activations on a project with no events returns empty ctx."""
        ctx = read_activations(tmp_project)
        assert ctx.activations == []
        assert ctx.assign_to_uid == {}

    def test_activations_are_sorted_ascending(self, tmp_project, db):
        """Activations must be sorted by eventAt ascending for P3 to work.

        Oracle: monitor-service.js:76-78 — sorted ascending.
        """
        # Activate B before A (to verify sorting is by eventAt, not insertion order)
        activate(tmp_project, db, "UID-B")
        activate(tmp_project, db, "UID-A")

        ctx = read_activations(tmp_project)
        times = [e["eventAt"] for e in ctx.activations]
        assert times == sorted(times), "activations must be sorted ascending"

    def test_manual_assign_populates_assign_to_uid(self, tmp_project):
        """manual-assign events are reflected in AttributionCtx.assign_to_uid."""
        manual_assign(tmp_project, "UID-A", ["/project/incoming/img001.jpg"])
        ctx = read_activations(tmp_project)
        # The path is resolved by manual_assign; look up by resolved path
        resolved = str(Path("/project/incoming/img001.jpg").resolve())
        assert resolved in ctx.assign_to_uid
        assert ctx.assign_to_uid[resolved] == "UID-A"

    def test_later_manual_assign_overwrites_earlier(self, tmp_project):
        """If a JPG is assigned twice, the last assignment wins."""
        path = "/project/incoming/img001.jpg"
        manual_assign(tmp_project, "UID-A", [path])
        manual_assign(tmp_project, "UID-B", [path])
        ctx = read_activations(tmp_project)
        resolved = str(Path(path).resolve())
        assert ctx.assign_to_uid[resolved] == "UID-B"

    def test_activation_events_not_in_assign_to_uid(self, tmp_project, db):
        """Activation events must not pollute assign_to_uid."""
        activate(tmp_project, db, "UID-A")
        ctx = read_activations(tmp_project)
        assert ctx.assign_to_uid == {}

    def test_mixed_events_separated_correctly(self, tmp_project, db):
        """A mix of activation + manual-assign events are correctly partitioned."""
        activate(tmp_project, db, "UID-A")
        manual_assign(tmp_project, "UID-A", ["/project/incoming/img001.jpg"])
        activate(tmp_project, db, "UID-B")

        ctx = read_activations(tmp_project)
        assert len(ctx.activations) == 2
        assert all(e["source"] == "activation" for e in ctx.activations)
        resolved = str(Path("/project/incoming/img001.jpg").resolve())
        assert ctx.assign_to_uid.get(resolved) == "UID-A"


# ── get_active_uid() ──────────────────────────────────────────────────────────

class TestGetActiveUid:
    def test_returns_none_when_no_active(self, db):
        assert get_active_uid(db) is None

    def test_returns_active_uid(self, tmp_project, db):
        activate(tmp_project, db, "UID-A")
        assert get_active_uid(db) == "UID-A"

    def test_returns_new_active_after_switch(self, tmp_project, db):
        activate(tmp_project, db, "UID-A")
        activate(tmp_project, db, "UID-B")
        assert get_active_uid(db) == "UID-B"

    def test_returns_none_after_deactivate(self, tmp_project, db):
        activate(tmp_project, db, "UID-A")
        deactivate(tmp_project, db, "UID-A")
        assert get_active_uid(db) is None


# ── 协作阶段状态持久化(工作台阶段按钮跨重启回读)────────────────────────────

class TestCollabStatusPersistence:
    """阶段状态写 tasks.raw_json(镜像 oracle specimen_tasks.json 持久化,
    server.js:3117);TaskStore 纯内存,重启后由 DB 回读。"""

    def test_set_collab_status_roundtrip(self):
        from app.services.activation_service import set_collab_status, get_collab_status
        db = _make_db()
        set_collab_status(db, "ZJ-TMW-B2-001", "shooting")
        assert get_collab_status(db, "ZJ-TMW-B2-001") == "shooting"

    def test_merge_preserves_existing_raw_json_keys(self):
        from app.services.activation_service import set_collab_status
        db = _make_db()
        db.execute("INSERT INTO tasks (uid, raw_json) VALUES (?, ?)",
                   ("U1", json.dumps({"foo": 1})))
        db.commit()
        set_collab_status(db, "U1", "organizing")
        raw = json.loads(db.execute(
            "SELECT raw_json FROM tasks WHERE uid='U1'").fetchone()[0])
        assert raw["foo"] == 1
        assert raw["status"] == "organizing"

    def test_set_does_not_touch_activation_columns(self, tmp_project):
        from app.services.activation_service import set_collab_status
        db = _make_db()
        activate(tmp_project, db, "U2")
        set_collab_status(db, "U2", "shooting")
        row = db.execute(
            "SELECT is_active, activated_at FROM tasks WHERE uid='U2'").fetchone()
        assert row["is_active"] == 1
        assert row["activated_at"]

    def test_get_missing_returns_none(self):
        from app.services.activation_service import get_collab_status
        db = _make_db()
        assert get_collab_status(db, "nope") is None

    def test_get_tolerates_corrupt_raw_json(self):
        from app.services.activation_service import get_collab_status
        db = _make_db()
        db.execute("INSERT INTO tasks (uid, raw_json) VALUES ('U3', 'not json')")
        db.commit()
        assert get_collab_status(db, "U3") is None
