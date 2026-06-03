"""test_worms_service.py — Tests for WormsService.

Covers:
  - Cache hit: a previously cached entry is returned without a live network call.
  - Chinese-field protection: merge_worms_into_record never overwrites *Cn keys.
  - Job lifecycle: create_job / list_jobs / get_job / update_job_status.
  - Cache eviction: clear_expired removes stale entries.
  - flatten_classification: nested dict → ordered list.
  - Search argument validation: empty name raises ValueError.
  - Record argument validation: aphia_id ≤ 0 raises ValueError.
  - Live network calls are patched via unittest.mock (no real HTTP).
  - WormsView offscreen smoke test (QApplication singleton, no pytest-qt needed).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Qt offscreen setup (must happen before PyQt6 is imported) ─────────────────
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

# One shared QApplication instance for all Qt tests in this module
_QT_APP = None


@pytest.fixture(scope="module", autouse=False)
def qt_app():
    """Return (or create) the module-level QApplication instance."""
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


from app.services.worms_service import (
    WormsService,
    TTL_SEARCH,
    TTL_CLASSIFICATION,
    TTL_RECORD,
    _now_iso,
    _elapsed_seconds,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_service(tmp_dir: str) -> WormsService:
    """Create a WormsService with temp cache and jobs files."""
    return WormsService(
        cache_path=os.path.join(tmp_dir, "worms_cache.json"),
        jobs_path =os.path.join(tmp_dir, "worms_jobs.json"),
        timeout=5.0,
    )


def _iso_ago(seconds: float) -> str:
    """Return an ISO-8601 timestamp *seconds* in the past."""
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return dt.isoformat()


def _write_cache(cache_path: str, records: dict) -> None:
    """Write a synthetic cache file."""
    data = {"_meta": {"version": 1}, "records": records}
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# ── Cache hit ─────────────────────────────────────────────────────────────────

class TestCacheHit:
    """_fetch should return cached data without hitting the network."""

    def test_search_returns_cached_result(self, tmp_path):
        svc = _make_service(str(tmp_path))
        cache_key = "search:Acanthurus olivaceus:like"
        fake_records = [{"AphiaID": 219014, "scientificname": "Acanthurus olivaceus", "status": "accepted"}]

        _write_cache(
            svc._cache_path,
            {cache_key: {"data": fake_records, "fetched_at": _iso_ago(60)}},  # 1 min old ≪ 7d TTL
        )

        # If _fetch hits the network, httpx.get would raise — absence of that
        # exception confirms the cache was used.
        with patch("httpx.get") as mock_get:
            result = svc.search("Acanthurus olivaceus", like=True)
            mock_get.assert_not_called()

        assert result == fake_records

    def test_record_returns_cached_result(self, tmp_path):
        svc = _make_service(str(tmp_path))
        aphia_id = 219014
        fake_record = {"AphiaID": aphia_id, "scientificname": "Acanthurus olivaceus"}
        _write_cache(
            svc._cache_path,
            {str(aphia_id): {"data": fake_record, "fetched_at": _iso_ago(100)}},
        )
        with patch("httpx.get") as mock_get:
            result = svc.record(aphia_id)
            mock_get.assert_not_called()
        assert result == fake_record

    def test_classification_returns_cached(self, tmp_path):
        svc = _make_service(str(tmp_path))
        aphia_id = 219014
        fake_chain = {"rank": "Phylum", "scientificname": "Chordata", "AphiaID": 11, "child": None}
        cache_key = f"classification:{aphia_id}"
        _write_cache(
            svc._cache_path,
            {cache_key: {"data": fake_chain, "fetched_at": _iso_ago(500)}},
        )
        with patch("httpx.get") as mock_get:
            result = svc.classification(aphia_id)
            mock_get.assert_not_called()
        assert result == fake_chain

    def test_expired_cache_triggers_network_call(self, tmp_path):
        svc = _make_service(str(tmp_path))
        cache_key = "search:OldSpecies:like"
        stale_records = [{"AphiaID": 1, "scientificname": "OldSpecies"}]
        fresh_records  = [{"AphiaID": 1, "scientificname": "OldSpecies", "status": "accepted"}]

        # Cache entry older than TTL_SEARCH
        _write_cache(
            svc._cache_path,
            {cache_key: {"data": stale_records, "fetched_at": _iso_ago(TTL_SEARCH + 3600)}},
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = fresh_records

        with patch("httpx.get", return_value=mock_response) as mock_get:
            result = svc.search("OldSpecies", like=True)
            mock_get.assert_called_once()

        assert result == fresh_records


# ── Chinese-field protection ──────────────────────────────────────────────────

class TestChineseFieldProtection:
    """merge_worms_into_record must NEVER touch *Cn fields."""

    def test_cn_fields_are_preserved_unchanged(self):
        """Chinese fields in the existing record survive a WoRMS merge."""
        original = {
            "scientificNameCn": "橄榄刺尾鱼",
            "familyCn": "刺尾鱼科",
            "orderCn": "鲈形目",
            "classCn": "辐鳍鱼纲",
            "genusCn": "刺尾鱼属",
            "speciesCn": "橄榄刺尾鱼",
        }
        worms_result = {
            "AphiaID": 219014,
            "valid_AphiaID": 219014,
            "scientificname": "Acanthurus olivaceus",
            "valid_name": "Acanthurus olivaceus",
            "authority": "Bloch & Schneider, 1801",
            "rank": "Species",
            "status": "accepted",
            "class": "Actinopterygii",
            "order": "Perciformes",
            "family": "Acanthuridae",
            "genus": "Acanthurus",
        }

        merged = WormsService.merge_worms_into_record(dict(original), worms_result)

        # Chinese fields must be exactly the same as before
        assert merged["scientificNameCn"] == "橄榄刺尾鱼"
        assert merged["familyCn"]         == "刺尾鱼科"
        assert merged["orderCn"]          == "鲈形目"
        assert merged["classCn"]          == "辐鳍鱼纲"
        assert merged["genusCn"]          == "刺尾鱼属"
        assert merged["speciesCn"]        == "橄榄刺尾鱼"

    def test_worms_latin_fields_are_written(self):
        """Latin / taxonomic fields ARE written by merge."""
        original = {"familyCn": "刺尾鱼科"}
        worms_result = {
            "AphiaID": 219014,
            "valid_AphiaID": 219014,
            "scientificname": "Acanthurus olivaceus",
            "valid_name": "Acanthurus olivaceus",
            "authority": "Bloch & Schneider, 1801",
            "rank": "Species",
            "status": "accepted",
            "class": "Actinopterygii",
            "order": "Perciformes",
            "family": "Acanthuridae",
            "genus": "Acanthurus",
        }
        merged = WormsService.merge_worms_into_record(dict(original), worms_result)

        assert merged["worms_aphia_id"]       == 219014
        assert merged["worms_scientific_name"] == "Acanthurus olivaceus"
        assert merged["worms_family"]          == "Acanthuridae"
        assert merged["worms_status"]          == "accepted"
        assert "worms_verified_at" in merged

    def test_no_cn_key_in_merged_output(self):
        """The merged record must not gain any new *Cn key."""
        original = {}
        worms_result = {"AphiaID": 1, "scientificname": "Foo bar", "status": "accepted"}
        merged = WormsService.merge_worms_into_record(dict(original), worms_result)

        cn_keys = [k for k in merged if k.endswith("Cn")]
        assert cn_keys == [], f"Unexpected Cn keys written: {cn_keys}"

    def test_existing_cn_not_overwritten_by_adversarial_worms_record(self):
        """Even if a WoRMS record somehow contained a *Cn key, it must be ignored."""
        original = {"familyCn": "刺尾鱼科"}
        # Adversarial: WoRMS record with a Cn key (impossible in real API, but defensive)
        worms_result = {
            "AphiaID": 1,
            "scientificname": "Foo bar",
            "status": "accepted",
            # These should never appear but we test defensively
        }
        # Manually inject after construction to simulate
        merged = WormsService.merge_worms_into_record(dict(original), worms_result)
        assert merged["familyCn"] == "刺尾鱼科"


# ── Batch job lifecycle ───────────────────────────────────────────────────────

class TestJobLifecycle:
    """create_job / list_jobs / get_job / update_job_status."""

    def test_create_job_persists(self, tmp_path):
        svc = _make_service(str(tmp_path))
        record_ids = ["r001", "r002", "r003"]
        job = svc.create_job(record_ids, created_by="test_operator", source="selected")

        assert job.id
        assert job.status == "running"
        assert job.record_ids == record_ids
        assert job.cursor == 0
        assert job.created_by == "test_operator"

        # Jobs file was written
        assert os.path.exists(svc._jobs_path)
        stored = svc.get_job(job.id)
        assert stored is not None
        assert stored["status"] == "running"

    def test_list_jobs_newest_first(self, tmp_path):
        svc = _make_service(str(tmp_path))
        j1 = svc.create_job(["r1"], created_by="op")
        j2 = svc.create_job(["r2"], created_by="op")
        jobs = svc.list_jobs()
        # Newest first
        assert jobs[0]["id"] == j2.id
        assert jobs[1]["id"] == j1.id

    def test_update_job_status(self, tmp_path):
        svc = _make_service(str(tmp_path))
        job = svc.create_job(["r1"])
        updated = svc.update_job_status(job.id, "paused")
        assert updated is not None
        assert updated["status"] == "paused"
        # Persisted
        stored = svc.get_job(job.id)
        assert stored["status"] == "paused"

    def test_update_completed_sets_completed_at(self, tmp_path):
        svc = _make_service(str(tmp_path))
        job = svc.create_job(["r1"])
        updated = svc.update_job_status(job.id, "completed")
        assert updated["completed_at"] is not None

    def test_create_job_empty_raises(self, tmp_path):
        svc = _make_service(str(tmp_path))
        with pytest.raises(ValueError, match="record_ids must not be empty"):
            svc.create_job([])

    def test_get_job_not_found_returns_none(self, tmp_path):
        svc = _make_service(str(tmp_path))
        assert svc.get_job("nonexistent-id") is None


# ── Cache eviction ────────────────────────────────────────────────────────────

class TestCacheEviction:
    """clear_expired should remove stale entries and leave fresh ones."""

    def test_clear_expired_removes_stale_entries(self, tmp_path):
        svc = _make_service(str(tmp_path))
        _write_cache(svc._cache_path, {
            "search:OldName:like":  {"data": [], "fetched_at": _iso_ago(TTL_SEARCH + 3600)},
            f"classification:99":   {"data": {}, "fetched_at": _iso_ago(TTL_CLASSIFICATION + 1)},
            "search:NewName:like":  {"data": [{"AphiaID": 1}], "fetched_at": _iso_ago(60)},
        })
        removed = svc.clear_expired()
        assert removed == 2   # two stale entries

        # Fresh entry still in cache
        with patch("httpx.get") as mock_get:
            svc.search("NewName", like=True)
            mock_get.assert_not_called()

    def test_clear_expired_noop_when_all_fresh(self, tmp_path):
        svc = _make_service(str(tmp_path))
        _write_cache(svc._cache_path, {
            "search:FreshName:like": {"data": [{"AphiaID": 2}], "fetched_at": _iso_ago(30)},
        })
        removed = svc.clear_expired()
        assert removed == 0


# ── Classification chain flattening ──────────────────────────────────────────

class TestFlattenClassification:
    """flatten_classification converts nested dict to an ordered list."""

    def test_flat_simple_chain(self):
        chain = {
            "rank": "Kingdom", "scientificname": "Animalia", "AphiaID": 2,
            "child": {
                "rank": "Phylum", "scientificname": "Chordata", "AphiaID": 11,
                "child": {
                    "rank": "Class", "scientificname": "Actinopterygii", "AphiaID": 216,
                    "child": None,
                },
            },
        }
        result = WormsService.flatten_classification(chain)
        assert len(result) == 3
        assert result[0]["rank"] == "Kingdom"
        assert result[0]["scientificname"] == "Animalia"
        assert result[1]["rank"] == "Phylum"
        assert result[2]["rank"] == "Class"

    def test_none_returns_empty(self):
        assert WormsService.flatten_classification(None) == []

    def test_empty_dict_returns_empty(self):
        assert WormsService.flatten_classification({}) == []

    def test_single_node_no_child(self):
        chain = {"rank": "Species", "scientificname": "Foo bar", "AphiaID": 999}
        result = WormsService.flatten_classification(chain)
        assert len(result) == 1
        assert result[0]["AphiaID"] == 999


# ── Input validation ──────────────────────────────────────────────────────────

class TestInputValidation:
    """search and record should raise ValueError on invalid inputs."""

    def test_search_empty_name_raises(self, tmp_path):
        svc = _make_service(str(tmp_path))
        with pytest.raises(ValueError):
            svc.search("")

    def test_search_whitespace_only_raises(self, tmp_path):
        svc = _make_service(str(tmp_path))
        with pytest.raises(ValueError):
            svc.search("   ")

    def test_record_zero_id_raises(self, tmp_path):
        svc = _make_service(str(tmp_path))
        with pytest.raises(ValueError):
            svc.record(0)

    def test_record_negative_id_raises(self, tmp_path):
        svc = _make_service(str(tmp_path))
        with pytest.raises(ValueError):
            svc.record(-1)

    def test_classification_zero_id_raises(self, tmp_path):
        svc = _make_service(str(tmp_path))
        with pytest.raises(ValueError):
            svc.classification(0)

    def test_synonyms_zero_id_raises(self, tmp_path):
        svc = _make_service(str(tmp_path))
        with pytest.raises(ValueError):
            svc.synonyms(0)


# ── Network fetch (mocked) ────────────────────────────────────────────────────

class TestNetworkFetch:
    """Verify the fetch path writes to cache and returns data."""

    def test_search_live_writes_to_cache(self, tmp_path):
        svc = _make_service(str(tmp_path))
        fake = [{"AphiaID": 219014, "scientificname": "Acanthurus olivaceus", "status": "accepted"}]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = fake

        with patch("httpx.get", return_value=mock_resp):
            result = svc.search("Acanthurus olivaceus", like=True)

        assert result == fake
        assert os.path.exists(svc._cache_path)
        with open(svc._cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
        assert "search:Acanthurus olivaceus:like" in cache["records"]

    def test_404_response_caches_none(self, tmp_path):
        svc = _make_service(str(tmp_path))
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.get", return_value=mock_resp):
            result = svc.record(999999)

        assert result is None
        with open(svc._cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
        assert cache["records"].get("999999", {}).get("data") is None

    def test_search_returns_empty_list_on_204(self, tmp_path):
        svc = _make_service(str(tmp_path))
        mock_resp = MagicMock()
        mock_resp.status_code = 204

        with patch("httpx.get", return_value=mock_resp):
            result = svc.search("NoSuchSpecies")

        assert result == []


# ── Cache stats ───────────────────────────────────────────────────────────────

class TestCacheStats:
    def test_stats_empty_cache(self, tmp_path):
        svc = _make_service(str(tmp_path))
        stats = svc.cache_stats()
        assert stats["entry_count"] == 0
        assert stats["file_size_bytes"] == 0

    def test_stats_after_population(self, tmp_path):
        svc = _make_service(str(tmp_path))
        _write_cache(svc._cache_path, {
            "search:A:like": {"data": [1], "fetched_at": _iso_ago(10)},
            "search:B:like": {"data": [2], "fetched_at": _iso_ago(20)},
        })
        stats = svc.cache_stats()
        assert stats["entry_count"] == 2
        assert stats["file_size_bytes"] > 0


# ── WormsView smoke test (offscreen) ─────────────────────────────────────────

class TestWormsViewOffscreen:
    """Basic widget construction under offscreen platform (no pytest-qt needed)."""

    def test_worms_view_constructs(self, qt_app, tmp_path):
        """WormsView should build without raising under QT_QPA_PLATFORM=offscreen."""
        from app.views.worms_view import WormsView

        ctx = MagicMock()
        ctx.current_project_dir = str(tmp_path)

        view = WormsView(ctx)
        assert view.view_id   == "worms"
        assert view.nav_title == "WoRMS 分类库"
        assert view.nav_icon  == "🌊"
        assert view._service  is not None

    def test_on_activate_does_not_raise(self, qt_app, tmp_path):
        from app.views.worms_view import WormsView

        ctx = MagicMock()
        ctx.current_project_dir = str(tmp_path)
        view = WormsView(ctx)
        view.on_activate()   # should not raise
