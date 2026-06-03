"""Tests for app/utils/naming.py — full 7-segment spec (TDD).

Covers:
- parse_uid: three formats (full with sequence, uniqueId without, v002 legacy no station)
- build_uid / build_result_id
- extract_unique_id (remove sequence segment at index 4)
- validate_uid
- suggested_tiff_name
- Chinese site does not crash
- legacy graceful degradation
"""
import pytest
from app.utils.naming import (
    parse_uid,
    build_uid,
    build_result_id,
    extract_unique_id,
    validate_uid,
    suggested_tiff_name,
    # existing (must still pass)
    specimen_date_seg,
    derive_uid,
)


# ── parse_uid — full 7-segment with sequence ──────────────────────────────

class TestParseUidFull:
    """Full format: province-site-station-speciesId-seq-storage-dateSeg"""

    def test_standard_full(self):
        uid = "FJ-YGLZ-B2-DLC001-1-RD75E-20260506-0508"
        r = parse_uid(uid)
        assert r is not None
        assert r["province"] == "FJ"
        assert r["site"] == "YGLZ"
        assert r["station"] == "B2"
        assert r["speciesId"] == "DLC001"
        assert r["resultSequence"] == "1"
        assert r["storage"] == "RD75E"
        assert r["dateSegment"] == "20260506-0508"

    def test_single_date(self):
        uid = "FJ-XM-B2-DLC001-2-T95E-20260601"
        r = parse_uid(uid)
        assert r is not None
        assert r["resultSequence"] == "2"
        assert r["dateSegment"] == "20260601"

    def test_seq_multi_digit(self):
        uid = "FJ-XM-B2-DLC001-12-T95E-20260601"
        r = parse_uid(uid)
        assert r is not None
        assert r["resultSequence"] == "12"

    def test_returns_dict_not_none(self):
        r = parse_uid("FJ-XM-B2-DLC001-1-T95E-20260601")
        assert isinstance(r, dict)


# ── parse_uid — uniqueId (no sequence segment) ───────────────────────────

class TestParseUidUniqueId:
    """UniqueId format: province-site-station-speciesId-storage-dateSeg"""

    def test_unique_id_no_sequence(self):
        uid = "FJ-YGLZ-B2-DLC001-RD75E-20260506-0508"
        r = parse_uid(uid)
        assert r is not None
        # No sequence in this format
        assert r.get("resultSequence") is None
        assert r["province"] == "FJ"
        assert r["site"] == "YGLZ"
        assert r["station"] == "B2"
        assert r["speciesId"] == "DLC001"
        assert r["storage"] == "RD75E"
        assert r["dateSegment"] == "20260506-0508"

    def test_unique_id_single_date(self):
        uid = "FJ-XM-B2-DLC001-T95E-20260601"
        r = parse_uid(uid)
        assert r is not None
        assert r.get("resultSequence") is None
        assert r["dateSegment"] == "20260601"


# ── parse_uid — v002 legacy (no station) ─────────────────────────────────

class TestParseUidLegacy:
    """Legacy v002 format: province-site-speciesId-storage-dateSeg (no station)."""

    def test_legacy_no_station(self):
        uid = "FJ-XM-DLC001-T95E-20260601"
        r = parse_uid(uid)
        assert r is not None
        assert r["province"] == "FJ"
        assert r["site"] == "XM"
        assert r.get("station") is None or r.get("station") == ""
        assert r["speciesId"] == "DLC001"
        assert r["storage"] == "T95E"

    def test_legacy_does_not_crash(self):
        # Should return something (dict), not raise
        assert parse_uid("FJ-XM-DLC001-T95E-20260601") is not None

    def test_invalid_returns_none(self):
        assert parse_uid("not-valid") is None

    def test_empty_returns_none(self):
        assert parse_uid("") is None

    def test_none_returns_none(self):
        assert parse_uid(None) is None


# ── parse_uid — Chinese site doesn't crash ───────────────────────────────

class TestParseUidChinese:
    def test_chinese_site_no_crash(self):
        # Chinese characters in site — must not crash (may or may not parse fully)
        result = parse_uid("浙江-三门湾-B2-DLC001-1-T95E-20260601")
        # Just verify it doesn't raise
        assert result is None or isinstance(result, dict)

    def test_chinese_province_site_uniqueid(self):
        uid = "浙江-三门湾-B2-DLC001-T95E-20260601"
        result = parse_uid(uid)
        assert result is None or isinstance(result, dict)


# ── build_uid ─────────────────────────────────────────────────────────────

class TestBuildUid:
    def test_full_fields(self):
        uid = build_uid(
            province="FJ", site="XM", station="B2",
            species_id="DLC001", storage="T95E", date_seg="20260601"
        )
        assert uid == "FJ-XM-B2-DLC001-T95E-20260601"

    def test_missing_station_omitted(self):
        uid = build_uid(
            province="FJ", site="XM", station=None,
            species_id="DLC001", storage="T95E", date_seg="20260601"
        )
        assert "--" not in uid
        assert "DLC001" in uid

    def test_no_double_dash(self):
        uid = build_uid(
            province="FJ", site="XM", station="",
            species_id="DLC001", storage="T95E", date_seg="20260601"
        )
        assert "--" not in uid


# ── build_result_id ───────────────────────────────────────────────────────

class TestBuildResultId:
    def test_inserts_seq_at_index_4(self):
        rid = build_result_id(
            province="FJ", site="XM", station="B2",
            species_id="DLC001", storage="T95E", date_seg="20260601",
            seq=1
        )
        # Format: FJ-XM-B2-DLC001-1-T95E-20260601
        assert rid == "FJ-XM-B2-DLC001-1-T95E-20260601"

    def test_seq_as_string(self):
        rid = build_result_id(
            province="FJ", site="XM", station="B2",
            species_id="DLC001", storage="T95E", date_seg="20260601",
            seq="3"
        )
        assert "-3-" in rid

    def test_seq_2(self):
        rid = build_result_id(
            province="FJ", site="XM", station="B2",
            species_id="DLC001", storage="T95E", date_seg="20260601",
            seq=2
        )
        assert rid == "FJ-XM-B2-DLC001-2-T95E-20260601"


# ── extract_unique_id ─────────────────────────────────────────────────────

class TestExtractUniqueId:
    def test_removes_seq_segment(self):
        rid = "FJ-YGLZ-B2-DLC001-1-RD75E-20260506-0508"
        uid = extract_unique_id(rid)
        # seq "1" should be removed → FJ-YGLZ-B2-DLC001-RD75E-20260506-0508
        assert uid == "FJ-YGLZ-B2-DLC001-RD75E-20260506-0508"
        assert "-1-" not in uid

    def test_seq_2_removed(self):
        rid = "FJ-XM-B2-DLC001-2-T95E-20260601"
        uid = extract_unique_id(rid)
        assert uid == "FJ-XM-B2-DLC001-T95E-20260601"

    def test_already_unique_id_unchanged(self):
        """Input without numeric seq at index 4 → returned as-is."""
        uid_in = "FJ-XM-B2-DLC001-T95E-20260601"
        uid_out = extract_unique_id(uid_in)
        assert uid_out == uid_in

    def test_multi_digit_seq_removed(self):
        rid = "FJ-XM-B2-DLC001-12-T95E-20260601"
        uid = extract_unique_id(rid)
        assert uid == "FJ-XM-B2-DLC001-T95E-20260601"


# ── validate_uid ──────────────────────────────────────────────────────────

class TestValidateUid:
    def test_valid_full_with_seq(self):
        assert validate_uid("FJ-YGLZ-B2-DLC001-1-RD75E-20260506-0508") is True

    def test_valid_unique_id(self):
        assert validate_uid("FJ-XM-B2-DLC001-T95E-20260601") is True

    def test_invalid_empty(self):
        assert validate_uid("") is False

    def test_invalid_none(self):
        assert validate_uid(None) is False

    def test_invalid_too_few_segments(self):
        assert validate_uid("FJ-XM") is False

    def test_valid_legacy_no_station(self):
        # Legacy 5-segment (no station) is still valid
        assert validate_uid("FJ-XM-DLC001-T95E-20260601") is True


# ── suggested_tiff_name ───────────────────────────────────────────────────

class TestSuggestedTiffName:
    def _sp(self, **kw):
        base = {
            "province": "FJ", "site": "XM", "station": "B2",
            "id": "DLC001", "storage": "T95E",
            "collectionDate": "20260601", "photoDate": "20260601",
        }
        base.update(kw)
        return base

    def test_basic(self):
        sp = self._sp()
        name = suggested_tiff_name(sp, result_sequence=1)
        assert name == "FJ-XM-B2-DLC001-1-T95E-20260601.tif"

    def test_seq_2(self):
        sp = self._sp()
        name = suggested_tiff_name(sp, result_sequence=2)
        assert name.startswith("FJ-XM-B2-DLC001-2-")
        assert name.endswith(".tif")

    def test_two_date_segments(self):
        sp = self._sp(collectionDate="20260501", photoDate="20260601")
        name = suggested_tiff_name(sp, result_sequence=1)
        assert "20260501-0601" in name

    def test_none_sp_returns_none(self):
        assert suggested_tiff_name(None, result_sequence=1) is None

    def test_default_seq_is_1(self):
        sp = self._sp()
        # If result_sequence omitted / None, default to "1"
        name = suggested_tiff_name(sp)
        assert "-1-" in name
