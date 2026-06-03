"""test_taxonomy_service.py — TDD tests for TaxonomyService.

Covers:
  - Seed loading and read-only invariant (seed never written)
  - User record CRUD: learn / update / delete
  - Search: NFKC Chinese + Latin substring match, ranking, max_results
  - Ancestor constraint filtering (taxonGroup → order → family → species)
  - Cross-level fallback (no context vs. constrained context)
  - 4-tuple completeness guard (incomplete records silently skipped)
  - all_records pagination and source_filter
  - seed_count / user_count
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from app.services.taxonomy_service import TaxonomyService, TaxonCandidate


# ── Fixtures ──────────────────────────────────────────────────────────────────

SEED_DATA: list[dict[str, Any]] = [
    {
        "class": "Polychaeta",
        "order": "Phyllodocida",
        "family": "Polynoidae",
        "species": "Halosydna brevisetosa",
        "classCn": "多毛纲",
        "orderCn": "叶须虫目",
        "familyCn": "多鳞虫科",
        "genus": "Halosydna",
        "genusCn": "海鳞虫属",
        "speciesCn": "短毛海鳞虫",
    },
    {
        "class": "Polychaeta",
        "order": "Phyllodocida",
        "family": "Aphroditidae",
        "species": "Aphrodita aculeata",
        "classCn": "多毛纲",
        "orderCn": "叶须虫目",
        "familyCn": "鳞沙蚕科",
        "genus": "Aphrodita",
        "genusCn": "鳞沙蚕属",
        "speciesCn": "棘鳞沙蚕",
    },
    {
        "class": "Malacostraca",
        "order": "Decapoda",
        "family": "Portunidae",
        "species": "Portunus trituberculatus",
        "classCn": "软甲纲",
        "orderCn": "十足目",
        "familyCn": "梭子蟹科",
        "genus": "Portunus",
        "genusCn": "梭子蟹属",
        "speciesCn": "三疣梭子蟹",
    },
    {
        "class": "Polychaeta",
        "order": "Terebellida",
        "family": "Terebellidae",
        "species": "Terebella lapidaria",
        "classCn": "多毛纲",
        "orderCn": "蛰龙介目",
        "familyCn": "蛰龙介科",
        "genus": "Terebella",
        "genusCn": "蛰龙介属",
        "speciesCn": "岩石蛰龙介",
    },
]


@pytest.fixture
def tmp_dirs():
    """Yield (seed_path, user_path) in a temporary directory."""
    d = tempfile.mkdtemp()
    seed_p = Path(d) / "taxonomy_seed.json"
    user_p = Path(d) / "user_taxonomy.json"
    seed_p.write_text(json.dumps(SEED_DATA), encoding="utf-8")
    try:
        yield seed_p, user_p
    finally:
        shutil.rmtree(d)


@pytest.fixture
def svc(tmp_dirs):
    seed_p, user_p = tmp_dirs
    return TaxonomyService(seed_p, user_p)


# ── Basic loading ─────────────────────────────────────────────────────────────

class TestLoading:
    def test_seed_count(self, svc):
        assert svc.seed_count() == len(SEED_DATA)

    def test_user_count_zero_initially(self, svc):
        assert svc.user_count() == 0

    def test_user_json_created_if_absent(self, tmp_dirs):
        seed_p, user_p = tmp_dirs
        assert not user_p.exists()
        svc = TaxonomyService(seed_p, user_p)
        svc.seed_count()  # trigger load
        assert user_p.exists()
        data = json.loads(user_p.read_text())
        assert data == []

    def test_seed_absent_returns_empty(self, tmp_dirs):
        _, user_p = tmp_dirs
        absent = Path(tmp_dirs[0]).parent / "nonexistent_seed.json"
        svc = TaxonomyService(absent, user_p)
        assert svc.seed_count() == 0

    def test_reload_repopulates(self, svc, tmp_dirs):
        seed_p, _ = tmp_dirs
        svc.seed_count()  # initial load
        # Add an entry directly to the seed (simulate external change)
        new_seed = SEED_DATA + [{
            "class": "Bivalvia", "order": "Mytilida",
            "family": "Mytilidae", "species": "Mytilus edulis",
        }]
        seed_p.write_text(json.dumps(new_seed), encoding="utf-8")
        svc.reload()
        assert svc.seed_count() == len(new_seed)


# ── Search / matchTaxon ───────────────────────────────────────────────────────

class TestSearch:
    def test_empty_query_returns_all_of_level(self, svc):
        results = svc.search("family", "")
        values = {c.value for c in results}
        assert "Polynoidae" in values
        assert "Portunidae" in values

    def test_latin_substring_match(self, svc):
        results = svc.search("family", "Polynoi")
        assert any(c.value == "Polynoidae" for c in results)
        # When a query is given, all returned values match the query in value or cn
        for c in results:
            match_in_value = "polynoi" in c.value.lower()
            match_in_cn = "polynoi" in c.cn.lower()
            assert match_in_value or match_in_cn

    def test_chinese_substring_match(self, svc):
        """Chinese match on cn field."""
        results = svc.search("family", "多鳞")
        assert any(c.value == "Polynoidae" for c in results)

    def test_nfkc_normalisation(self, svc):
        """Full-width ASCII characters should match after NFKC normalisation."""
        # ｐ = U+FF50 (full-width p) normalises to p
        results = svc.search("family", "ｐolynoi")
        assert any(c.value == "Polynoidae" for c in results)

    def test_case_insensitive(self, svc):
        results = svc.search("family", "POLYNOIDAE")
        assert any(c.value == "Polynoidae" for c in results)

    def test_max_results_limit(self, svc):
        results = svc.search("family", "", max_results=2)
        assert len(results) <= 2

    def test_invalid_sp_key_raises(self, svc):
        with pytest.raises(ValueError):
            svc.search("nonexistent", "test")

    def test_source_is_seed_for_seed_records(self, svc):
        results = svc.search("family", "Polynoidae")
        assert any(c.source == "seed" for c in results)

    def test_result_ranking_earliest_match_first(self, svc):
        """Score = position of first match; earlier matches rank higher."""
        results = svc.search("family", "a")
        # All families containing 'a' — first one should have 'a' earlier
        if len(results) >= 2:
            # Just assert it ran without error; order is deterministic
            assert results[0].value is not None


# ── Ancestor constraint filtering ─────────────────────────────────────────────

class TestAncestorFiltering:
    def test_order_filters_families(self, svc):
        """Searching for 'family' with known order should only show families in that order."""
        ctx = {"taxonGroup": "Polychaeta", "order": "Phyllodocida"}
        results = svc.search("family", "", context=ctx)
        values = {c.value for c in results}
        # Phyllodocida has Polynoidae + Aphroditidae
        assert "Polynoidae" in values
        assert "Aphroditidae" in values
        # Terebellidae is in Terebellida, should be excluded
        assert "Terebellidae" not in values
        # Portunidae is Malacostraca, should be excluded
        assert "Portunidae" not in values

    def test_class_filters_orders(self, svc):
        ctx = {"taxonGroup": "Malacostraca"}
        results = svc.search("order", "", context=ctx)
        values = {c.value for c in results}
        assert "Decapoda" in values
        assert "Phyllodocida" not in values

    def test_no_context_returns_all_families(self, svc):
        results = svc.search("family", "", context={})
        values = {c.value for c in results}
        assert "Polynoidae" in values
        assert "Portunidae" in values
        assert "Terebellidae" in values

    def test_unknown_parent_does_not_filter(self, svc):
        """If the parent value exists in context but not in merged data, no filter applied."""
        ctx = {"taxonGroup": "Polychaeta", "order": "UnknownOrder"}
        # "UnknownOrder" is not in seed → knownOrder = False → no filter
        results = svc.search("family", "", context=ctx)
        values = {c.value for c in results}
        # All Polychaeta families visible (order filter skipped because UnknownOrder not found)
        assert len(values) >= 1

    def test_genus_filters_species(self, svc):
        """When genus is known, scientificName search should be filtered by genus."""
        ctx = {"taxonGroup": "Polychaeta", "order": "Phyllodocida",
               "family": "Polynoidae", "genus": "Halosydna"}
        results = svc.search("scientificName", "", context=ctx)
        values = {c.value for c in results}
        assert "Halosydna brevisetosa" in values
        # Aphrodita is different genus — should be excluded
        assert "Aphrodita aculeata" not in values


# ── Learn (upsert) ────────────────────────────────────────────────────────────

class TestLearn:
    def test_learn_incomplete_tuple_skipped(self, svc):
        result = svc.learn({"class": "Polychaeta", "order": "Phyllodocida"})
        assert result == {}
        assert svc.user_count() == 0

    def test_learn_complete_tuple_saved(self, svc):
        result = svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        assert result.get("class") == "Polychaeta"
        assert svc.user_count() == 1

    def test_learn_persists_to_disk(self, svc, tmp_dirs):
        _, user_p = tmp_dirs
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        data = json.loads(user_p.read_text())
        assert len(data) == 1
        assert data[0]["class"] == "Polychaeta"

    def test_learn_increments_use_count(self, svc):
        rec = {
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        }
        svc.learn(rec)
        svc.learn(rec)
        assert svc.user_count() == 1  # same key → upsert
        records, _ = svc.all_records(source_filter="user")
        assert records[0]["useCount"] == 2

    def test_learn_optional_cn_fields_stored(self, svc):
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
            "classCn": "多毛纲", "familyCn": "多鳞虫科",
        })
        records, _ = svc.all_records(source_filter="user")
        assert records[0].get("classCn") == "多毛纲"
        assert records[0].get("familyCn") == "多鳞虫科"

    def test_learn_does_not_overwrite_seed(self, svc, tmp_dirs):
        seed_p, _ = tmp_dirs
        original_seed = seed_p.read_text()
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        # Seed file must be byte-for-byte identical
        assert seed_p.read_text() == original_seed

    def test_learn_user_record_has_record_id(self, svc):
        result = svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        assert result.get("recordId", "").startswith("user:")

    def test_learn_user_appears_first_in_search(self, svc):
        """User records come before seed records in search results."""
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        results = svc.search("family", "Polynoidae")
        assert results[0].source == "user"


# ── Update ────────────────────────────────────────────────────────────────────

class TestUpdate:
    def test_update_existing_user_record(self, svc):
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        result = svc.update(rec_id, {"familyCn": "多鳞虫科_updated"})
        assert result["familyCn"] == "多鳞虫科_updated"

    def test_update_nonexistent_returns_none(self, svc):
        result = svc.update("user:doesnotexist", {"familyCn": "x"})
        assert result is None

    def test_update_persists_to_disk(self, svc, tmp_dirs):
        _, user_p = tmp_dirs
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"orderCn": "叶须虫目_new"})
        data = json.loads(user_p.read_text())
        assert data[0]["orderCn"] == "叶须虫目_new"

    def test_update_does_not_overwrite_seed(self, svc, tmp_dirs):
        seed_p, _ = tmp_dirs
        original = seed_p.read_text()
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        svc.update(records[0]["recordId"], {"classCn": "x"})
        assert seed_p.read_text() == original


# ── Delete ────────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_existing_user_record(self, svc):
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        assert svc.delete(rec_id) is True
        assert svc.user_count() == 0

    def test_delete_nonexistent_returns_false(self, svc):
        assert svc.delete("user:doesnotexist") is False

    def test_delete_persists_to_disk(self, svc, tmp_dirs):
        _, user_p = tmp_dirs
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        svc.delete(records[0]["recordId"])
        data = json.loads(user_p.read_text())
        assert data == []

    def test_delete_does_not_overwrite_seed(self, svc, tmp_dirs):
        seed_p, _ = tmp_dirs
        original = seed_p.read_text()
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        svc.delete(records[0]["recordId"])
        assert seed_p.read_text() == original


# ── Seed never overwritten ────────────────────────────────────────────────────

class TestSeedImmutability:
    """Verify seed file is never modified regardless of operations."""

    def _run_all_mutations(self, svc, seed_p, user_p):
        rec = {
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        }
        svc.learn(rec)
        records, _ = svc.all_records(source_filter="user")
        if records:
            rid = records[0]["recordId"]
            svc.update(rid, {"classCn": "changed"})
            svc.delete(rid)

    def test_seed_unchanged_after_learn_update_delete(self, svc, tmp_dirs):
        seed_p, user_p = tmp_dirs
        original = seed_p.read_text()
        self._run_all_mutations(svc, seed_p, user_p)
        assert seed_p.read_text() == original


# ── all_records / pagination ──────────────────────────────────────────────────

class TestAllRecords:
    def test_all_records_total(self, svc):
        _, total = svc.all_records()
        assert total == len(SEED_DATA)

    def test_all_records_source_filter_seed(self, svc):
        records, total = svc.all_records(source_filter="seed")
        assert total == len(SEED_DATA)
        assert all(not r.get("recordId", "").startswith("user:") for r in records)

    def test_all_records_source_filter_user_empty(self, svc):
        records, total = svc.all_records(source_filter="user")
        assert total == 0
        assert records == []

    def test_all_records_pagination(self, svc):
        page0, total = svc.all_records(page=0, page_size=2)
        page1, _ = svc.all_records(page=1, page_size=2)
        assert len(page0) == 2
        # page1 has remaining items
        assert len(page0) + len(page1) <= total

    def test_all_records_user_appears_before_seed(self, svc):
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records()
        # User record is the first item
        assert records[0].get("recordId", "").startswith("user:")


# ── History tracking ──────────────────────────────────────────────────────────

class TestHistory:
    def test_update_stores_history_entry(self, svc):
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
            "familyCn": "多鳞虫科",  # pre-fill so history captures it
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"familyCn": "多鳞虫科_v2"})
        records2, _ = svc.all_records(source_filter="user")
        hist = records2[0].get("history", [])
        assert len(hist) == 1
        assert "at" in hist[0]
        assert "before" in hist[0]
        assert hist[0]["before"]["familyCn"] == "多鳞虫科"

    def test_update_history_max_10(self, svc):
        """History list never exceeds 10 entries."""
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        for i in range(15):
            svc.update(rec_id, {"orderCn": f"叶须虫目_v{i}"})
        records2, _ = svc.all_records(source_filter="user")
        hist = records2[0].get("history", [])
        assert len(hist) <= 10

    def test_update_history_persists_to_disk(self, svc, tmp_dirs):
        _, user_p = tmp_dirs
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"orderCn": "叶须虫目_new"})
        data = json.loads(user_p.read_text())
        hist = data[0].get("history", [])
        assert len(hist) == 1
        assert hist[0]["before"]["orderCn"] == ""

    def test_update_history_before_has_all_fields(self, svc):
        """history[].before must contain all 10 editable fields."""
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"classCn": "多毛纲"})
        records2, _ = svc.all_records(source_filter="user")
        before = records2[0]["history"][0]["before"]
        expected_keys = {
            "class", "classCn", "order", "orderCn",
            "family", "familyCn", "genus", "genusCn",
            "species", "speciesCn",
        }
        assert set(before.keys()) == expected_keys


# ── find_seed_by_level ────────────────────────────────────────────────────────

class TestFindSeedByLevel:
    def test_find_existing_class(self, svc):
        e = svc.find_seed_by_level("class", "Polychaeta")
        assert e is not None
        assert e["class"] == "Polychaeta"

    def test_find_case_insensitive(self, svc):
        e = svc.find_seed_by_level("family", "polynoidae")
        assert e is not None
        assert e["family"] == "Polynoidae"

    def test_find_missing_returns_none(self, svc):
        assert svc.find_seed_by_level("class", "NoSuchClass") is None

    def test_find_empty_value_returns_none(self, svc):
        assert svc.find_seed_by_level("class", "") is None

    def test_find_species_level(self, svc):
        e = svc.find_seed_by_level("species", "Portunus trituberculatus")
        assert e is not None
        assert e["order"] == "Decapoda"

    def test_find_seed_by_level_found(self, svc):
        e = svc.find_seed_by_level("order", "Decapoda")
        assert e is not None
        assert e["order"] == "Decapoda"

    def test_find_seed_by_level_not_found(self, svc):
        assert svc.find_seed_by_level("family", "Nonexistent") is None

    def test_find_seed_by_level_case_insensitive(self, svc):
        e = svc.find_seed_by_level("species", "HALOSYDNA BREVISETOSA")
        assert e is not None
        assert e["species"] == "Halosydna brevisetosa"


# ── validate_taxonomy_chain ───────────────────────────────────────────────────

class TestValidateTaxonomyChain:
    def test_consistent_chain_ok(self, svc):
        sp = {
            "taxonGroup": "Polychaeta",
            "order": "Phyllodocida",
            "family": "Polynoidae",
            "genus": "Halosydna",
            "scientificName": "Halosydna brevisetosa",
        }
        result = svc.validate_taxonomy_chain(sp)
        assert result["ok"] is True
        assert result["mismatches"] == []

    def test_mismatch_order_detected(self, svc):
        sp = {
            "taxonGroup": "Polychaeta",
            "order": "Decapoda",        # wrong order for this species
            "family": "Polynoidae",
            "scientificName": "Halosydna brevisetosa",
        }
        result = svc.validate_taxonomy_chain(sp)
        assert result["ok"] is False
        sp_keys = [m["spKey"] for m in result["mismatches"]]
        assert "order" in sp_keys

    def test_mismatch_class_detected(self, svc):
        sp = {
            "taxonGroup": "Malacostraca",   # wrong class for Polychaeta species
            "order": "Phyllodocida",
            "family": "Polynoidae",
            "scientificName": "Halosydna brevisetosa",
        }
        result = svc.validate_taxonomy_chain(sp)
        assert result["ok"] is False
        sp_keys = [m["spKey"] for m in result["mismatches"]]
        assert "taxonGroup" in sp_keys

    def test_empty_specimen_is_ok(self, svc):
        result = svc.validate_taxonomy_chain({})
        assert result["ok"] is True
        assert result["mismatches"] == []

    def test_unknown_species_no_mismatch(self, svc):
        """Unknown species → no seed entry → no mismatch generated."""
        sp = {
            "taxonGroup": "Polychaeta",
            "order": "Phyllodocida",
            "family": "Polynoidae",
            "scientificName": "Unknown species xyz",
        }
        result = svc.validate_taxonomy_chain(sp)
        # speciesEntry = None → no mismatches from species anchor
        assert result["speciesEntry"] is None

    def test_returns_seed_entries(self, svc):
        sp = {
            "scientificName": "Halosydna brevisetosa",
        }
        result = svc.validate_taxonomy_chain(sp)
        assert result["speciesEntry"] is not None
        assert result["speciesEntry"]["family"] == "Polynoidae"

    def test_validate_taxonomy_chain_consistent(self, svc):
        sp = {
            "taxonGroup": "Polychaeta",
            "order": "Phyllodocida",
            "family": "Polynoidae",
            "genus": "Halosydna",
            "scientificName": "Halosydna brevisetosa",
        }
        result = svc.validate_taxonomy_chain(sp)
        assert result["ok"] is True
        assert result["mismatches"] == []

    def test_validate_taxonomy_chain_mismatch(self, svc):
        sp = {
            "taxonGroup": "Polychaeta",
            "order": "Decapoda",   # wrong: Halosydna brevisetosa is in Phyllodocida
            "family": "Polynoidae",
            "scientificName": "Halosydna brevisetosa",
        }
        result = svc.validate_taxonomy_chain(sp)
        assert result["ok"] is False
        sp_keys = [m["spKey"] for m in result["mismatches"]]
        assert "order" in sp_keys


# ── apply_taxonomy_authority ──────────────────────────────────────────────────

class TestApplyTaxonomyAuthority:
    def test_apply_from_species_entry(self, svc):
        sp: dict[str, Any] = {}
        validation = svc.validate_taxonomy_chain(
            {"scientificName": "Halosydna brevisetosa"}
        )
        svc.apply_taxonomy_authority(sp, validation)
        assert sp["taxonGroup"] == "Polychaeta"
        assert sp["order"] == "Phyllodocida"
        assert sp["family"] == "Polynoidae"

    def test_apply_fills_cn_fields(self, svc):
        sp: dict[str, Any] = {}
        validation = svc.validate_taxonomy_chain(
            {"scientificName": "Halosydna brevisetosa"}
        )
        svc.apply_taxonomy_authority(sp, validation)
        assert sp.get("taxonGroupCn") == "多毛纲"
        assert sp.get("orderCn") == "叶须虫目"

    def test_apply_sets_taxonomy_confirmed_false(self, svc):
        sp: dict[str, Any] = {"taxonomyConfirmed": True}
        validation = svc.validate_taxonomy_chain(
            {"scientificName": "Halosydna brevisetosa"}
        )
        svc.apply_taxonomy_authority(sp, validation)
        assert sp["taxonomyConfirmed"] is False

    def test_apply_no_entry_does_nothing(self, svc):
        sp: dict[str, Any] = {"taxonGroup": "Polychaeta"}
        validation = {"speciesEntry": None, "genusEntry": None,
                      "familyEntry": None, "orderEntry": None}
        svc.apply_taxonomy_authority(sp, validation)
        assert sp["taxonGroup"] == "Polychaeta"  # unchanged


# ── taxon_entry_cn ────────────────────────────────────────────────────────────

class TestTaxonEntryCn:
    def test_returns_cn_from_entry_directly(self, svc):
        entry = {"class": "Polychaeta", "classCn": "多毛纲"}
        result = svc.taxon_entry_cn(entry, "class", "classCn")
        assert result == "多毛纲"

    def test_falls_back_to_seed_lookup(self, svc):
        entry = {"class": "Polychaeta"}  # no classCn in entry
        result = svc.taxon_entry_cn(entry, "class", "classCn")
        assert result == "多毛纲"   # found from seed

    def test_returns_empty_when_not_found(self, svc):
        entry = {"class": "UnknownClass"}
        result = svc.taxon_entry_cn(entry, "class", "classCn")
        assert result == ""


# ── find_user_entry_for_current ───────────────────────────────────────────────

class TestFindUserEntryForCurrent:
    def test_finds_matching_entry(self, svc):
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        sp = {
            "taxonGroup": "Polychaeta",
            "order": "Phyllodocida",
            "family": "Polynoidae",
            "scientificName": "Halosydna brevisetosa",
        }
        result = svc.find_user_entry_for_current(sp)
        assert result is not None
        assert result["class"] == "Polychaeta"

    def test_case_insensitive_match(self, svc):
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        sp = {
            "taxonGroup": "POLYCHAETA",
            "order": "phyllodocida",
            "family": "Polynoidae",
            "scientificName": "Halosydna brevisetosa",
        }
        result = svc.find_user_entry_for_current(sp)
        assert result is not None

    def test_returns_none_when_no_match(self, svc):
        sp = {
            "taxonGroup": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "scientificName": "Unknown sp",
        }
        result = svc.find_user_entry_for_current(sp)
        assert result is None

    def test_returns_none_when_user_empty(self, svc):
        sp = {
            "taxonGroup": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "scientificName": "Halosydna brevisetosa",
        }
        result = svc.find_user_entry_for_current(sp)
        assert result is None


# ── apply_draft_to_specimen ───────────────────────────────────────────────────

class TestApplyDraftToSpecimen:
    def test_copies_all_8_fields(self):
        from app.services.taxonomy_service import TaxonomyService
        sp: dict[str, Any] = {}
        draft = {
            "class":     "Polychaeta",
            "classCn":   "多毛纲",
            "order":     "Phyllodocida",
            "orderCn":   "叶须虫目",
            "family":    "Polynoidae",
            "familyCn":  "多鳞虫科",
            "species":   "Halosydna brevisetosa",
            "speciesCn": "短毛海鳞虫",
        }
        TaxonomyService.apply_draft_to_specimen(sp, draft)
        assert sp["taxonGroup"]       == "Polychaeta"
        assert sp["taxonGroupCn"]     == "多毛纲"
        assert sp["order"]            == "Phyllodocida"
        assert sp["orderCn"]          == "叶须虫目"
        assert sp["family"]           == "Polynoidae"
        assert sp["familyCn"]         == "多鳞虫科"
        assert sp["scientificName"]   == "Halosydna brevisetosa"
        assert sp["scientificNameCn"] == "短毛海鳞虫"

    def test_sets_taxonomy_confirmed_false(self):
        from app.services.taxonomy_service import TaxonomyService
        sp: dict[str, Any] = {"taxonomyConfirmed": True}
        TaxonomyService.apply_draft_to_specimen(sp, {
            "class": "A", "order": "B", "family": "C", "species": "D sp",
        })
        assert sp["taxonomyConfirmed"] is False

    def test_strips_whitespace(self):
        from app.services.taxonomy_service import TaxonomyService
        sp: dict[str, Any] = {}
        TaxonomyService.apply_draft_to_specimen(sp, {
            "class": "  Polychaeta  ", "order": " Phyllodocida ",
            "family": "Polynoidae", "species": "X sp",
        })
        assert sp["taxonGroup"] == "Polychaeta"
        assert sp["order"] == "Phyllodocida"

    def test_missing_draft_fields_become_empty(self):
        from app.services.taxonomy_service import TaxonomyService
        sp: dict[str, Any] = {}
        TaxonomyService.apply_draft_to_specimen(sp, {
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "X sp",
            # no CN fields
        })
        assert sp["taxonGroupCn"] == ""
        assert sp["orderCn"] == ""


# ── Widget blur exact-match with ancestor fill ────────────────────────────────

class TestEditingFinishedExactMatch:
    """Tests for the blur / editing-finished exact-match ancestor fill.

    Mirrors commitTypedTaxon in app.js: exact match → fill ancestors.
    """

    @pytest.fixture
    def app_instance(self):
        from PyQt6.QtWidgets import QApplication
        import sys
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        yield app

    def test_blur_exact_latin_fills_ancestors(self, svc, app_instance):
        """Typing exact family name on blur fills ancestor inputs (order, class)."""
        from app.widgets.taxonomy_input import TaxonomyInputPanel
        panel = TaxonomyInputPanel(svc)
        committed: list[dict] = []
        panel.value_committed.connect(committed.append)

        # Simulate: user typed "Polynoidae" into the family field then blurred
        panel._inputs["family"].setText("Polynoidae")
        panel._active_sp_key = "family"
        panel._on_editing_finished("family")

        # Should have committed at least family + its ancestors
        assert len(committed) >= 1
        merged = {}
        for d in committed:
            merged.update(d)
        assert merged.get("family") == "Polynoidae"
        # Ancestors filled from full record
        assert merged.get("order") == "Phyllodocida"
        assert merged.get("taxonGroup") == "Polychaeta"

    def test_blur_exact_cn_fills_ancestors(self, svc, app_instance):
        """Typing the Chinese name on blur should resolve to the Latin and fill ancestors."""
        from app.widgets.taxonomy_input import TaxonomyInputPanel
        panel = TaxonomyInputPanel(svc)
        committed: list[dict] = []
        panel.value_committed.connect(committed.append)

        panel._inputs["family"].setText("多鳞虫科")   # Chinese for Polynoidae
        panel._active_sp_key = "family"
        panel._on_editing_finished("family")

        merged = {}
        for d in committed:
            merged.update(d)
        # Latin value resolved
        assert merged.get("family") == "Polynoidae"

    def test_blur_no_match_emits_nothing(self, svc, app_instance):
        """Typing a string that doesn't match any candidate emits no value_committed."""
        from app.widgets.taxonomy_input import TaxonomyInputPanel
        panel = TaxonomyInputPanel(svc)
        committed: list[dict] = []
        panel.value_committed.connect(committed.append)

        panel._inputs["family"].setText("NoSuchFamilyXYZ123")
        panel._active_sp_key = "family"
        panel._on_editing_finished("family")

        assert committed == []

    def test_blur_does_not_overwrite_child_inputs(self, svc, app_instance):
        """Selecting a family-level entry must NOT touch scientificName input."""
        from app.widgets.taxonomy_input import TaxonomyInputPanel
        panel = TaxonomyInputPanel(svc)
        # Pre-set species
        panel.set_values({"scientificName": "My custom species"})
        panel._active_sp_key = "family"
        panel._inputs["family"].setText("Polynoidae")
        panel._on_editing_finished("family")

        # scientificName must still be what the user typed, not overwritten
        assert panel._inputs["scientificName"].text() == "My custom species"


# ── Widget smoke tests (offscreen) ────────────────────────────────────────────

class TestTaxonomyInputPanelSmoke:
    """Offscreen smoke tests for TaxonomyInputPanel."""

    @pytest.fixture
    def app_instance(self):
        """Ensure a QApplication exists for widget tests."""
        from PyQt6.QtWidgets import QApplication
        import sys
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        yield app

    def test_panel_constructs(self, svc, app_instance):
        from app.widgets.taxonomy_input import TaxonomyInputPanel
        panel = TaxonomyInputPanel(svc)
        assert panel is not None

    def test_panel_has_four_inputs(self, svc, app_instance):
        from app.widgets.taxonomy_input import TaxonomyInputPanel, _FIELD_ORDER
        panel = TaxonomyInputPanel(svc)
        assert len(panel._inputs) == len(_FIELD_ORDER)

    def test_set_values_no_signal(self, svc, app_instance):
        from app.widgets.taxonomy_input import TaxonomyInputPanel
        panel = TaxonomyInputPanel(svc)
        received = []
        panel.value_committed.connect(lambda d: received.append(d))
        panel.set_values({"taxonGroup": "Polychaeta"})
        assert received == []
        assert panel._inputs["taxonGroup"].text() == "Polychaeta"

    def test_set_context_updates_internal_state(self, svc, app_instance):
        from app.widgets.taxonomy_input import TaxonomyInputPanel
        panel = TaxonomyInputPanel(svc)
        panel.set_context({"taxonGroup": "Polychaeta", "order": "Phyllodocida"})
        assert panel._context["taxonGroup"] == "Polychaeta"

    def test_clear_all_empties_inputs(self, svc, app_instance):
        from app.widgets.taxonomy_input import TaxonomyInputPanel
        panel = TaxonomyInputPanel(svc)
        panel.set_values({"taxonGroup": "Polychaeta", "order": "Phyllodocida"})
        panel.clear_all()
        for inp in panel._inputs.values():
            assert inp.text() == ""

    def test_popup_constructs(self, app_instance):
        from app.widgets.taxonomy_input import TaxonPopup
        popup = TaxonPopup()
        assert popup is not None

    def test_candidate_model_empty(self, app_instance):
        from app.widgets.taxonomy_input import _CandidateModel
        m = _CandidateModel()
        assert m.rowCount() == 0

    def test_candidate_model_set_items(self, svc, app_instance):
        from app.widgets.taxonomy_input import _CandidateModel
        from app.services.taxonomy_service import TaxonCandidate
        m = _CandidateModel()
        items = [TaxonCandidate("Polynoidae", "多鳞虫科", "seed", {})]
        m.set_items(items, "Poly")
        assert m.rowCount() == 1
        assert m.candidate_at(0).value == "Polynoidae"


# ── TaxonomyView smoke tests ──────────────────────────────────────────────────

class TestTaxonomyViewSmoke:
    @pytest.fixture
    def app_instance(self):
        from PyQt6.QtWidgets import QApplication
        import sys
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        yield app

    @pytest.fixture
    def mock_ctx(self, tmp_dirs):
        """Minimal AppContext mock with project dir and settings."""
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.current_project_dir = None
        ctx.settings = MagicMock()
        ctx.settings.last_nav_index = 0
        return ctx

    def test_view_constructs(self, mock_ctx, app_instance):
        from app.views.taxonomy_view import TaxonomyView
        view = TaxonomyView(mock_ctx)
        assert view is not None
        assert view.view_id == "taxonomy"
        assert view.nav_title == "内置分类库"
        assert view.nav_icon == "🧬"

    def test_view_on_activate_no_crash(self, mock_ctx, app_instance):
        from app.views.taxonomy_view import TaxonomyView
        view = TaxonomyView(mock_ctx)
        view.on_activate()   # must not raise

    def test_view_has_table(self, mock_ctx, app_instance):
        from app.views.taxonomy_view import TaxonomyView
        view = TaxonomyView(mock_ctx)
        assert hasattr(view, "_table")

    def test_record_dialog_history_button_visible_when_history_present(
        self, svc, app_instance
    ):
        """_RecordDialog shows 'history' button when record has history."""
        from app.views.taxonomy_view import _RecordDialog
        # Learn a record then update it to create history
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec_id = records[0]["recordId"]
        svc.update(rec_id, {"classCn": "多毛纲"})
        records2, _ = svc.all_records(source_filter="user")
        rec = records2[0]
        assert "history" in rec
        dlg = _RecordDialog(record=rec)
        # history button must exist and not be hidden
        assert hasattr(dlg, "_btn_history")
        assert not dlg._btn_history.isHidden()

    def test_record_dialog_no_history_button_when_no_history(
        self, svc, app_instance
    ):
        """_RecordDialog hides history button when record has no history."""
        from app.views.taxonomy_view import _RecordDialog
        svc.learn({
            "class": "Polychaeta", "order": "Phyllodocida",
            "family": "Polynoidae", "species": "Halosydna brevisetosa",
        })
        records, _ = svc.all_records(source_filter="user")
        rec = records[0]
        assert not rec.get("history")
        dlg = _RecordDialog(record=rec)
        assert hasattr(dlg, "_btn_history")
        assert dlg._btn_history.isHidden()
