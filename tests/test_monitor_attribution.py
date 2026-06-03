"""test_monitor_attribution.py — TDD tests for attribution logic.

Tests the 4-level priority:
  P0: explicitUnassigns  → None  (blacklist, highest priority)
  P1: grouping pathToUid → uid
  P2: manual-assign assignToUid → uid
  P3: activation time window, using firstSeenAt NOT mtime
"""

import os
import sqlite3
from pathlib import Path
import pytest

from app.services.monitor_service import (
    attribute_jpg,
    AttributionCtx,
    FileEntry,
    ensure_seen_files_table,
    get_first_seen_at,
    set_first_seen_at,
    scan_project,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry(path: str, first_seen_at: str = "", mtime: str = "") -> FileEntry:
    """Make a minimal FileEntry for attribution testing."""
    return FileEntry(
        name=Path(path).name,
        path=path,
        kind="jpg",
        size=1000,
        mtime=mtime or "2026-01-01T00:00:00+00:00",
        first_seen_at=first_seen_at or None,
    )


def _ctx(**kwargs) -> AttributionCtx:
    defaults = dict(
        explicit_unassigns=set(),
        path_to_uid={},
        assign_to_uid={},
        activations=[],
    )
    defaults.update(kwargs)
    return AttributionCtx(**defaults)


# ── P0: explicitUnassigns ─────────────────────────────────────────────────────

class TestExplicitUnassigns:
    def test_blacklisted_path_returns_none(self):
        path = "/project/incoming-jpg/img001.jpg"
        ctx = _ctx(
            explicit_unassigns={str(Path(path).resolve())},
            path_to_uid={str(Path(path).resolve()): "UID001"},
            assign_to_uid={str(Path(path).resolve()): "UID002"},
            activations=[{"specimenUniqueId": "UID003", "eventAt": "2026-01-01T00:00:00+00:00"}],
        )
        e = _entry(path, first_seen_at="2026-01-02T00:00:00+00:00")
        assert attribute_jpg(e, ctx) is None

    def test_blacklist_overrides_all_other_sources(self):
        """P0 must override P1/P2/P3 simultaneously."""
        path = "/project/incoming-jpg/test.jpg"
        resolved = str(Path(path).resolve())
        ctx = _ctx(
            explicit_unassigns={resolved},
            path_to_uid={resolved: "GROUPING_UID"},
            assign_to_uid={resolved: "ASSIGN_UID"},
            activations=[{"specimenUniqueId": "ACTIVE_UID", "eventAt": "2026-01-01T00:00:00+00:00"}],
        )
        e = _entry(path, first_seen_at="2026-06-01T12:00:00+00:00")
        result = attribute_jpg(e, ctx)
        assert result is None

    def test_non_blacklisted_path_not_affected(self):
        """Different path in blacklist → should proceed to P1."""
        path = "/project/incoming-jpg/img001.jpg"
        other = "/project/incoming-jpg/other.jpg"
        resolved = str(Path(path).resolve())
        ctx = _ctx(
            explicit_unassigns={str(Path(other).resolve())},
            path_to_uid={resolved: "GROUP_UID"},
        )
        e = _entry(path)
        assert attribute_jpg(e, ctx) == "GROUP_UID"


# ── P1: grouping pathToUid ────────────────────────────────────────────────────

class TestGroupingPathToUid:
    def test_path_in_grouping_returns_uid(self):
        path = "/project/incoming-jpg/img_001.jpg"
        resolved = str(Path(path).resolve())
        ctx = _ctx(path_to_uid={resolved: "SPEC001"})
        e = _entry(path)
        assert attribute_jpg(e, ctx) == "SPEC001"

    def test_grouping_takes_priority_over_activation(self):
        """P1 > P3."""
        path = "/project/incoming-jpg/img_002.jpg"
        resolved = str(Path(path).resolve())
        ctx = _ctx(
            path_to_uid={resolved: "GROUP_UID"},
            activations=[{"specimenUniqueId": "ACTIVE_UID", "eventAt": "2026-01-01T00:00:00+00:00"}],
        )
        e = _entry(path, first_seen_at="2026-06-01T00:00:00+00:00")
        assert attribute_jpg(e, ctx) == "GROUP_UID"

    def test_grouping_takes_priority_over_manual_assign(self):
        """P1 > P2."""
        path = "/project/incoming-jpg/img_003.jpg"
        resolved = str(Path(path).resolve())
        ctx = _ctx(
            path_to_uid={resolved: "GROUP_UID"},
            assign_to_uid={resolved: "ASSIGN_UID"},
        )
        e = _entry(path)
        assert attribute_jpg(e, ctx) == "GROUP_UID"


# ── P2: manual-assign assignToUid ────────────────────────────────────────────

class TestManualAssign:
    def test_manual_assign_returns_uid(self):
        path = "/project/incoming-jpg/assign_me.jpg"
        resolved = str(Path(path).resolve())
        ctx = _ctx(assign_to_uid={resolved: "MANUAL_UID"})
        e = _entry(path)
        assert attribute_jpg(e, ctx) == "MANUAL_UID"

    def test_manual_assign_over_activation(self):
        """P2 > P3."""
        path = "/project/incoming-jpg/assign_me2.jpg"
        resolved = str(Path(path).resolve())
        ctx = _ctx(
            assign_to_uid={resolved: "MANUAL_UID"},
            activations=[{"specimenUniqueId": "ACTIVE_UID", "eventAt": "2026-01-01T00:00:00+00:00"}],
        )
        e = _entry(path, first_seen_at="2026-06-01T00:00:00+00:00")
        assert attribute_jpg(e, ctx) == "MANUAL_UID"

    def test_no_manual_assign_falls_through(self):
        """If P2 map is empty, attribute_jpg falls through to P3."""
        path = "/project/incoming-jpg/fallthrough.jpg"
        ctx = _ctx(
            assign_to_uid={},
            activations=[{"specimenUniqueId": "ACTIVE_UID", "eventAt": "2026-01-01T00:00:00+00:00"}],
        )
        e = _entry(path, first_seen_at="2026-06-01T00:00:00+00:00")
        result = attribute_jpg(e, ctx)
        assert result == "ACTIVE_UID"


# ── P3: activation time window ────────────────────────────────────────────────

class TestActivationWindow:
    def test_single_activation_before_arrival(self):
        """Activation before firstSeenAt → attributed."""
        ctx = _ctx(
            activations=[{"specimenUniqueId": "SP1", "eventAt": "2026-05-01T10:00:00+00:00"}]
        )
        e = _entry("/p/a.jpg", first_seen_at="2026-05-01T12:00:00+00:00")
        assert attribute_jpg(e, ctx) == "SP1"

    def test_single_activation_after_arrival(self):
        """Activation after firstSeenAt → NOT attributed."""
        ctx = _ctx(
            activations=[{"specimenUniqueId": "SP1", "eventAt": "2026-05-02T10:00:00+00:00"}]
        )
        e = _entry("/p/a.jpg", first_seen_at="2026-05-01T08:00:00+00:00")
        assert attribute_jpg(e, ctx) is None

    def test_multiple_activations_last_wins(self):
        """Takes the LAST activation whose eventAt ≤ firstSeenAt."""
        ctx = _ctx(
            activations=[
                {"specimenUniqueId": "SP_OLD", "eventAt": "2026-05-01T00:00:00+00:00"},
                {"specimenUniqueId": "SP_NEW", "eventAt": "2026-05-03T00:00:00+00:00"},
                {"specimenUniqueId": "SP_FUTURE", "eventAt": "2026-05-10T00:00:00+00:00"},
            ]
        )
        e = _entry("/p/a.jpg", first_seen_at="2026-05-05T00:00:00+00:00")
        assert attribute_jpg(e, ctx) == "SP_NEW"

    def test_no_activations_returns_none(self):
        ctx = _ctx(activations=[])
        e = _entry("/p/a.jpg", first_seen_at="2026-05-05T00:00:00+00:00")
        assert attribute_jpg(e, ctx) is None

    def test_uses_first_seen_at_not_mtime(self):
        """CRITICAL: uses firstSeenAt, NOT mtime.

        Old photo: mtime is before activation (2025).
        firstSeenAt: after activation (2026-06).
        Must be attributed because firstSeenAt > eventAt.
        If mtime were used, the photo would NOT be attributed.
        """
        ctx = _ctx(
            activations=[{"specimenUniqueId": "SP_ACTIVE", "eventAt": "2026-05-01T00:00:00+00:00"}]
        )
        e = _entry(
            "/p/old_photo.jpg",
            mtime="2025-01-01T00:00:00+00:00",       # OLD: before activation
            first_seen_at="2026-06-01T00:00:00+00:00", # NEW: arrived after activation
        )
        result = attribute_jpg(e, ctx)
        # Must be attributed via firstSeenAt (not blocked by old mtime)
        assert result == "SP_ACTIVE"

    def test_uses_mtime_fallback_when_no_first_seen(self):
        """If firstSeenAt is None, fall back to mtime for comparison.

        Oracle: monitor-service.js:110 — `const arrival = entry.firstSeenAt || entry.mtime`
        """
        ctx = _ctx(
            activations=[{"specimenUniqueId": "SP1", "eventAt": "2026-01-01T00:00:00+00:00"}]
        )
        e = _entry(
            "/p/a.jpg",
            mtime="2026-06-01T00:00:00+00:00",
            first_seen_at=None,  # no firstSeenAt recorded yet
        )
        e.first_seen_at = None
        result = attribute_jpg(e, ctx)
        assert result == "SP1"


# ── firstSeenAt DB persistence ────────────────────────────────────────────────

class TestFirstSeenAtPersistence:
    def setup_method(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        ensure_seen_files_table(self.db)

    def test_first_seen_at_recorded_on_first_scan(self):
        """Once recorded, firstSeenAt is never overwritten."""
        set_first_seen_at(self.db, "img001.jpg", "2026-05-01T10:00:00+00:00")
        ts = get_first_seen_at(self.db, "img001.jpg")
        assert ts == "2026-05-01T10:00:00+00:00"

    def test_first_seen_at_not_overwritten(self):
        """INSERT OR IGNORE — subsequent writes are silently ignored."""
        set_first_seen_at(self.db, "img002.jpg", "2026-05-01T10:00:00+00:00")
        set_first_seen_at(self.db, "img002.jpg", "2026-06-01T10:00:00+00:00")  # should be ignored
        ts = get_first_seen_at(self.db, "img002.jpg")
        assert ts == "2026-05-01T10:00:00+00:00"

    def test_unknown_file_returns_none(self):
        assert get_first_seen_at(self.db, "nonexistent.jpg") is None

    def test_keyed_by_filename_not_full_path(self):
        """DB key is the bare filename, matching Oracle: monitor-service.js schema seen_files."""
        set_first_seen_at(self.db, "photo.jpg", "2026-05-01T00:00:00+00:00")
        # Same name, different directory — same key in DB
        assert get_first_seen_at(self.db, "photo.jpg") == "2026-05-01T00:00:00+00:00"
        # Full path should NOT match
        assert get_first_seen_at(self.db, "/some/dir/photo.jpg") is None


# ── scan_project integration ──────────────────────────────────────────────────

class TestScanProject:
    def setup_method(self, tmp_path_factory):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_jpg(self, name: str, subdir: str = "incoming-jpg") -> str:
        d = os.path.join(self.tmpdir, subdir)
        os.makedirs(d, exist_ok=True)
        full = os.path.join(d, name)
        with open(full, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # minimal JPEG header
        return full

    def _make_tiff(self, name: str, subdir: str = "results") -> str:
        d = os.path.join(self.tmpdir, subdir)
        os.makedirs(d, exist_ok=True)
        full = os.path.join(d, name)
        with open(full, "wb") as f:
            f.write(b"II*\x00" + b"\x00" * 100)  # minimal TIFF header (little-endian)
        return full

    def test_scan_returns_jpg_files(self):
        import os
        self._make_jpg("img001.jpg")
        self._make_jpg("img002.jpg")
        result = scan_project(self.tmpdir, self.db)
        names = {f.name for f in result.jpg_files}
        assert "img001.jpg" in names
        assert "img002.jpg" in names

    def test_scan_records_first_seen_at_in_db(self):
        import os
        self._make_jpg("new001.jpg")
        scan_project(self.tmpdir, self.db)
        ts = get_first_seen_at(self.db, "new001.jpg")
        assert ts is not None

    def test_first_seen_at_not_updated_on_rescan(self):
        import time
        self._make_jpg("stable.jpg")
        scan_project(self.tmpdir, self.db)
        ts1 = get_first_seen_at(self.db, "stable.jpg")
        # Small wait; rescan should NOT update firstSeenAt
        time.sleep(0.05)
        scan_project(self.tmpdir, self.db)
        ts2 = get_first_seen_at(self.db, "stable.jpg")
        assert ts1 == ts2

    def test_scan_returns_tiff_files(self):
        self._make_tiff("result001.tif")
        result = scan_project(self.tmpdir, self.db)
        names = {f.name for f in result.tiff_files}
        assert "result001.tif" in names

    def test_nonexistent_dir_raises(self):
        import pytest
        with pytest.raises(FileNotFoundError):
            scan_project("/nonexistent/path/12345", self.db)

    def test_attribution_applied_when_ctx_provided(self):
        import os
        jpg_path = self._make_jpg("attr_test.jpg")
        resolved = str(Path(jpg_path).resolve())
        ctx = AttributionCtx(
            explicit_unassigns=set(),
            path_to_uid={resolved: "SP_GROUPED"},
            assign_to_uid={},
            activations=[],
        )
        result = scan_project(self.tmpdir, self.db, attr=ctx)
        found = next((f for f in result.jpg_files if f.name == "attr_test.jpg"), None)
        assert found is not None
        assert found.attributed_specimen_id == "SP_GROUPED"

    def test_hidden_files_excluded(self):
        """Files starting with '.' are excluded by default."""
        self._make_jpg(".hidden.jpg")
        self._make_jpg("visible.jpg")
        result = scan_project(self.tmpdir, self.db)
        names = {f.name for f in result.jpg_files}
        assert ".hidden.jpg" not in names
        assert "visible.jpg" in names
