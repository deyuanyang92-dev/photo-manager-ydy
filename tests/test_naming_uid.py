"""Tests for naming.py — specimen_date_seg + derive_uid.

TDD suite — covers spec invariants and edge cases.
"""
import pytest
from app.utils.naming import specimen_date_seg, derive_uid


# ── specimen_date_seg ──────────────────────────────────────────────────────

class TestSpecimenDateSeg:
    def test_both_equal(self):
        assert specimen_date_seg("20260601", "20260601") == "20260601"

    def test_no_collection(self):
        assert specimen_date_seg(None, "20260601") == "20260601"

    def test_no_photo(self):
        assert specimen_date_seg("20260601", None) == "20260601"

    def test_both_none(self):
        assert specimen_date_seg(None, None) == ""

    def test_same_year_different_month(self):
        # same year → c + "-" + p[4:]
        assert specimen_date_seg("20260501", "20260601") == "20260501-0601"

    def test_different_year(self):
        assert specimen_date_seg("20250601", "20260601") == "20250601-20260601"

    def test_strips_non_digits(self):
        # hyphens and slashes removed
        assert specimen_date_seg("2026-06-01", "2026-06-01") == "20260601"

    def test_truncates_to_8(self):
        assert specimen_date_seg("202606011234", "20260601") == "20260601"

    def test_empty_strings(self):
        assert specimen_date_seg("", "") == ""

    def test_collection_only_no_photo_empty(self):
        assert specimen_date_seg("20260601", "") == "20260601"


# ── derive_uid ─────────────────────────────────────────────────────────────

class TestDeriveUid:
    def _make_sp(self, **kwargs):
        base = {
            "province": "FJ",
            "site": "XM",
            "station": "B2",
            "id": "DLC001",
            "storage": "T95E",
            "collectionDate": "20260601",
            "photoDate": "20260601",
        }
        base.update(kwargs)
        return base

    def test_full_fields(self):
        sp = self._make_sp()
        assert derive_uid(sp) == "FJ-XM-B2-DLC001-T95E-20260601"

    def test_missing_station_degrades(self):
        """Spec invariant: missing station auto-degrades, no error."""
        sp = self._make_sp(station=None)
        uid = derive_uid(sp)
        assert uid == "FJ-XM-DLC001-T95E-20260601"
        # must not contain double dash
        assert "--" not in uid

    def test_chinese_province_site(self):
        sp = self._make_sp(province="浙江", site="三门湾", station="B2")
        uid = derive_uid(sp)
        assert uid.startswith("浙江-三门湾-B2-")

    def test_no_dates(self):
        sp = self._make_sp(collectionDate=None, photoDate=None)
        uid = derive_uid(sp)
        # date seg is empty → not included
        assert uid == "FJ-XM-B2-DLC001-T95E"

    def test_empty_dict_no_crash(self):
        uid = derive_uid({})
        assert uid == ""

    def test_lon_lat_not_in_uid(self):
        """lon/lat fields should not appear in uid (only 6 named fields used)."""
        sp = self._make_sp(lon="119.5", lat="88.1")
        uid = derive_uid(sp)
        # lon 119 and lat 88 must not appear in the uid
        assert "119" not in uid
        assert "88" not in uid
