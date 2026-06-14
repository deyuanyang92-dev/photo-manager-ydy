"""tests/test_label_core.py — Golden-vector + integration tests for label_core.py.

Covers:
  - normalized_date / date_segment / unique_id    (mirrors JS oracle)
  - has_rna_tissue / rna_preservative
  - specimen_to_label_data
  - unique_specimen_indices (de-dup by uniqueId)
  - bucket_specimens (double-bucket: R-prefix enters BOTH buckets)
  - normalize_field / normalize_template
  - calculate_grid
  - estimate_text_scale
  - qr_metrics (positions + free mode)
  - validate_print_job (all warning codes)
  - create_print_job
  - QR error-correction level Q (verified via qrcode library if available)
  - LabelService.build_print_job (service layer)
  - LabelEditorWidget / LabelsView smoke-tests (offscreen Qt)
"""

from __future__ import annotations

import sys
import pytest

# ── label_core imports ────────────────────────────────────────────────────────
from app.utils.label_core import (
    DEFAULT_LINE_HEIGHT,
    DEFAULT_PRINTER_MARGIN_MM,
    GRID_GAP_MM,
    SHEET_MARGIN_MM,
    normalized_date,
    date_segment,
    unique_id,
    has_rna_tissue,
    rna_preservative,
    specimen_to_label_data,
    unique_specimen_indices,
    bucket_specimens,
    normalize_field,
    normalize_template,
    resolve_line_height,
    resolve_wrap,
    calculate_grid,
    estimate_text_scale,
    qr_metrics,
    validate_print_job,
    create_print_job,
    label_data_text,
    apply_field_visibility,
    plan_label_pages,
)
from app.services.label_service import LabelService, BUILTIN_TEMPLATES


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _sp(**kw) -> dict:
    """Build a minimal specimen dict with defaults + overrides."""
    base = {
        "province": "FJ",
        "site": "YGLZ",
        "station": "B2",
        "id": "DLC001",
        "storage": "D95E",
        "collectionDate": "20260506",
        "photoDate": "20260508",
        "species": "背鳞虫 sp.01",
        "latin": "Polynoidae sp.",
        "collector": "杨德援",
        "photographer": "钟珅",
        "family": "Polynoidae",
        "region": "福建·厦门",
        "lon": "118.18432",
        "lat": "24.48921",
        "geoArea": "黄海",
    }
    base.update(kw)
    return base


def _rna_sp(**kw) -> dict:
    """R-prefix storage specimen."""
    kw.setdefault("id", "BLC001")
    kw.setdefault("storage", "RD75E")
    return _sp(**kw)


# ══════════════════════════════════════════════════════════════════════════════
# normalized_date
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizedDate:
    def test_strips_hyphens(self):
        assert normalized_date("2026-05-08") == "20260508"

    def test_strips_slashes(self):
        assert normalized_date("2026/05/08") == "20260508"

    def test_truncates_to_8(self):
        assert normalized_date("202605081234") == "20260508"

    def test_none_returns_empty(self):
        assert normalized_date(None) == ""

    def test_empty_returns_empty(self):
        assert normalized_date("") == ""

    def test_pure_digits_passthrough(self):
        assert normalized_date("20260508") == "20260508"

    def test_non_numeric_string(self):
        assert normalized_date("abc") == ""


# ══════════════════════════════════════════════════════════════════════════════
# date_segment — golden vectors (mirrors JS oracle exactly)
# ══════════════════════════════════════════════════════════════════════════════

class TestDateSegment:
    def test_same_dates_returns_collection(self):
        assert date_segment({"collectionDate": "20260508", "photoDate": "20260508"}) == "20260508"

    def test_no_collection_returns_photo(self):
        assert date_segment({"collectionDate": None, "photoDate": "20260508"}) == "20260508"

    def test_no_photo_returns_collection(self):
        assert date_segment({"collectionDate": "20260506", "photoDate": None}) == "20260506"

    def test_same_year_short_form(self):
        # Oracle: collection + "-" + photo[4:]
        assert date_segment({"collectionDate": "20260506", "photoDate": "20260508"}) == "20260506-0508"

    def test_different_year_full_form(self):
        assert date_segment({"collectionDate": "20250601", "photoDate": "20260601"}) == "20250601-20260601"

    def test_none_specimen(self):
        assert date_segment(None) == ""

    def test_empty_dict(self):
        assert date_segment({}) == ""

    def test_strips_non_digits_in_dates(self):
        assert date_segment({"collectionDate": "2026-05-06", "photoDate": "2026-05-08"}) == "20260506-0508"


# ══════════════════════════════════════════════════════════════════════════════
# unique_id — golden vector
# ══════════════════════════════════════════════════════════════════════════════

class TestUniqueId:
    def test_golden_vector(self):
        sp = _sp()
        # FJ-YGLZ-B2-DLC001-D95E-20260506-0508
        uid = unique_id(sp)
        assert uid == "FJ-YGLZ-B2-DLC001-D95E-20260506-0508"

    def test_same_dates_no_suffix(self):
        sp = _sp(collectionDate="20260508", photoDate="20260508")
        uid = unique_id(sp)
        assert uid == "FJ-YGLZ-B2-DLC001-D95E-20260508"

    def test_none_returns_empty(self):
        assert unique_id(None) == ""

    def test_empty_dict_has_dashes(self):
        # empty fields produce dashes but no crash
        uid = unique_id({})
        assert isinstance(uid, str)

    def test_station_included(self):
        """station is a required component of uniqueId."""
        sp = _sp(station="A1")
        assert "A1" in unique_id(sp)


# ══════════════════════════════════════════════════════════════════════════════
# has_rna_tissue / rna_preservative
# ══════════════════════════════════════════════════════════════════════════════

class TestRnaTissue:
    def test_r_prefix_upper(self):
        assert has_rna_tissue({"storage": "RD75E"}) is True

    def test_r_prefix_lower(self):
        assert has_rna_tissue({"storage": "rd75e"}) is True

    def test_non_r_prefix(self):
        assert has_rna_tissue({"storage": "D95E"}) is False
        assert has_rna_tissue({"storage": "T95E"}) is False

    def test_none_storage(self):
        assert has_rna_tissue({"storage": None}) is False

    def test_none_specimen(self):
        assert has_rna_tissue(None) is False

    def test_rna_preservative_r(self):
        assert rna_preservative({"storage": "RD75E"}) == "RNAlater"

    def test_rna_preservative_non_r(self):
        assert rna_preservative({"storage": "D95E"}) == ""


# ══════════════════════════════════════════════════════════════════════════════
# specimen_to_label_data
# ══════════════════════════════════════════════════════════════════════════════

class TestSpecimenToLabelData:
    def test_unique_id_field_present(self):
        data = specimen_to_label_data(_sp())
        assert "uniqueId" in data
        assert data["uniqueId"] == unique_id(_sp())

    def test_header_id_excludes_storage_and_date(self):
        data = specimen_to_label_data(_sp())
        # headerId = province-site-station-id
        assert data["headerId"] == "FJ-YGLZ-B2-DLC001"

    def test_collector_label(self):
        data = specimen_to_label_data(_sp(collector="张三"))
        assert data["collectorLabel"] == "张三采集"

    def test_transcriptome_flag_true_for_r(self):
        data = specimen_to_label_data(_rna_sp())
        assert data["transcriptome"] is True
        assert data["rnaPreservative"] == "RNAlater"

    def test_transcriptome_flag_false_for_non_r(self):
        data = specimen_to_label_data(_sp())
        assert data["transcriptome"] is False
        assert data["rnaPreservative"] == ""

    def test_photo_notes_defaults_empty(self):
        data = specimen_to_label_data(_sp())
        assert data["photoNotes"] == ""

    def test_all_expected_keys_present(self):
        data = specimen_to_label_data(_sp())
        expected_keys = {
            "province", "site", "station", "speciesId", "storage",
            "date", "collectionDate", "photoDate", "photoNotes",
            "speciesName", "latin", "collector", "photographer",
            "region", "lon", "lat", "geoArea", "family",
            "uniqueId", "headerId", "shortDate", "fullDate",
            "collectorLabel", "transcriptome", "rnaPreservative",
        }
        assert expected_keys.issubset(set(data.keys()))


# ══════════════════════════════════════════════════════════════════════════════
# unique_specimen_indices
# ══════════════════════════════════════════════════════════════════════════════

class TestUniqueSpecimenIndices:
    def test_removes_duplicates(self):
        sp = _sp()
        result = unique_specimen_indices([0, 1, 2], [sp, sp, sp])
        assert len(result) == 1

    def test_keeps_distinct(self):
        sp1 = _sp(id="DLC001")
        sp2 = _sp(id="BLC001")
        result = unique_specimen_indices([0, 1], [sp1, sp2])
        assert len(result) == 2

    def test_first_index_wins(self):
        sp1 = _sp(id="DLC001")
        sp2 = _sp(id="DLC001")  # same uid
        result = unique_specimen_indices([0, 1], [sp1, sp2])
        assert result == [0]

    def test_empty_indices(self):
        assert unique_specimen_indices([], [_sp()]) == []

    def test_none_indices(self):
        assert unique_specimen_indices(None, [_sp()]) == []  # type: ignore[arg-type]

    def test_out_of_range_index_skipped(self):
        result = unique_specimen_indices([0, 99], [_sp()])
        assert result == [0]


# ══════════════════════════════════════════════════════════════════════════════
# bucket_specimens — DOUBLE-BUCKET RULE
# ══════════════════════════════════════════════════════════════════════════════

class TestBucketSpecimens:
    def test_non_r_in_samples_only(self):
        sp = _sp(storage="D95E")
        result = bucket_specimens([0], [sp])
        assert len(result["samples"]) == 1
        assert len(result["tissues"]) == 0

    def test_r_prefix_in_both_buckets(self):
        """Hard rule: R-prefix specimen enters BOTH sample and tissue buckets."""
        sp = _rna_sp()
        result = bucket_specimens([0], [sp])
        assert len(result["samples"]) == 1
        assert len(result["tissues"]) == 1
        # Same item object
        assert result["samples"][0] is result["tissues"][0]

    def test_mixed_specimens(self):
        sp_normal = _sp(id="DLC001", storage="D95E")
        sp_rna = _rna_sp(id="BLC001", storage="RD75E")
        result = bucket_specimens([0, 1], [sp_normal, sp_rna])
        assert len(result["samples"]) == 2
        assert len(result["tissues"]) == 1
        assert result["tissues"][0]["data"]["speciesId"] == "BLC001"

    def test_de_duplicates_by_unique_id(self):
        sp = _sp()
        result = bucket_specimens([0, 0, 0], [sp, sp, sp])
        # De-dup removes all but first occurrence
        assert len(result["samples"]) == 1

    def test_edits_applied(self):
        sp = _sp(collector="原始")
        edits = {0: {"collector": "修改后"}}
        result = bucket_specimens([0], [sp], edits)
        assert result["samples"][0]["data"]["collector"] == "修改后"

    def test_edits_none_value_not_applied(self):
        sp = _sp(collector="原始")
        edits = {0: {"collector": None}}
        result = bucket_specimens([0], [sp], edits)
        # None edits are skipped per oracle
        assert result["samples"][0]["data"]["collector"] == "原始"

    def test_multiple_r_prefix_all_in_tissues(self):
        sp1 = _rna_sp(id="BLC001", storage="RD75E")
        sp2 = _rna_sp(id="BLC002", storage="RT95E")
        result = bucket_specimens([0, 1], [sp1, sp2])
        assert len(result["tissues"]) == 2


# ══════════════════════════════════════════════════════════════════════════════
# normalize_field
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeField:
    def test_string_input(self):
        f = normalize_field("storage")
        assert f == {"key": "storage", "style": "", "size": None, "offsetX": 0, "offsetY": 0}

    def test_dict_input(self):
        f = normalize_field({"key": "species", "style": "bold", "size": 10, "offsetX": 1})
        assert f["key"] == "species"
        assert f["style"] == "bold"
        assert f["size"] == 10
        assert f["offsetX"] == 1

    def test_dict_defaults(self):
        f = normalize_field({"key": "species"})
        assert f["style"] == ""
        assert f["size"] is None
        assert f["offsetX"] == 0
        assert f["offsetY"] == 0

    def test_none_input(self):
        f = normalize_field(None)
        assert f["key"] == ""

    def test_non_string_non_dict(self):
        f = normalize_field(42)
        assert f["key"] == ""


# ══════════════════════════════════════════════════════════════════════════════
# apply_field_visibility — field-level print on/off + blank styles
# ══════════════════════════════════════════════════════════════════════════════

class TestApplyFieldVisibility:
    @staticmethod
    def _tmpl() -> dict:
        # 3 single-field rows + 1 two-field row + a QR config.
        return {
            "name": "T",
            "rows": [
                {"fields": ["headerId"], "style": "bold", "size": 10},
                {"fields": ["shortDate"], "size": 9},
                {"fields": ["speciesName"], "size": 8},
                {"fields": ["lon", "lat"], "size": 8, "sep": "/"},
            ],
            "qr": {"content": "uniqueId", "position": "right"},
        }

    def test_empty_hidden_returns_equal(self):
        # Red-line: not using the feature must leave the template byte-identical.
        import copy
        t = self._tmpl()
        snap = copy.deepcopy(t)
        out = apply_field_visibility(t, set(), "placeholder", {})
        assert out == snap
        # input must not be mutated either
        assert t == snap

    def test_collapse_empties_row_no_prefix(self):
        out = apply_field_visibility(t := self._tmpl(), {"shortDate"}, "collapse",
                                     {"shortDate": "日期段"})
        row = out["rows"][1]
        assert row["fields"] == []
        assert not row.get("prefix")

    def test_blank_keeps_space_prefix(self):
        out = apply_field_visibility(self._tmpl(), {"shortDate"}, "blank",
                                     {"shortDate": "日期段"})
        row = out["rows"][1]
        assert row["fields"] == []
        assert row["prefix"] == " "

    def test_placeholder_prints_field_name(self):
        out = apply_field_visibility(self._tmpl(), {"speciesName"}, "placeholder",
                                     {"speciesName": "物种名称"})
        row = out["rows"][2]
        assert row["fields"] == []
        assert "物种名称" in row["prefix"]
        assert "：" in row["prefix"]

    def test_placeholder_falls_back_to_key(self):
        out = apply_field_visibility(self._tmpl(), {"shortDate"}, "placeholder", {})
        assert "shortDate" in out["rows"][1]["prefix"]

    def test_partial_row_keeps_visible_fields(self):
        # Hide one of two fields in the lon/lat row → keep the other, no prefix.
        out = apply_field_visibility(self._tmpl(), {"lon"}, "placeholder",
                                     {"lon": "经度", "lat": "纬度"})
        row = out["rows"][3]
        keys = [normalize_field(f)["key"] for f in row["fields"]]
        assert keys == ["lat"]
        assert not row.get("prefix")

    def test_qr_preserved(self):
        out = apply_field_visibility(self._tmpl(), {"headerId", "shortDate"},
                                     "placeholder", {})
        assert out["qr"] == {"content": "uniqueId", "position": "right"}

    def test_visible_rows_untouched(self):
        out = apply_field_visibility(self._tmpl(), {"shortDate"}, "collapse",
                                     {"shortDate": "日期段"})
        # row 0 (headerId) stays as-is
        assert normalize_field(out["rows"][0]["fields"][0])["key"] == "headerId"


class TestApplyFieldVisibilityRenderContract:
    """At print time (placeholder=False): collapse → row skipped (no ink);
    blank/placeholder → row preserved (prefix draws ink)."""

    @staticmethod
    def _ink(qt_app, tmpl: dict) -> int:
        from PyQt6.QtGui import QPixmap, QPainter, QColor
        from app.utils.label_render import render_label_onto
        dims = {"w": 50, "h": 30}
        scale = 6.0
        pm = QPixmap(int(dims["w"] * scale), int(dims["h"] * scale))
        pm.fill(QColor("white"))
        p = QPainter(pm)
        # printing path: empty rows w/o prefix are skipped
        render_label_onto(p, normalize_template(tmpl), dims, {}, px_per_mm=scale,
                          placeholder=False)
        p.end()
        img = pm.toImage()
        return sum(
            1
            for y in range(0, img.height(), 2)
            for x in range(0, img.width(), 2)
            if img.pixel(x, y) & 0xFFFFFF != 0xFFFFFF
        )

    @staticmethod
    def _single_hidden(style: str) -> dict:
        t = {"rows": [{"fields": ["speciesName"], "size": 10}],
             "qr": {"position": "none"}}
        return apply_field_visibility(t, {"speciesName"}, style,
                                      {"speciesName": "物种名称"})

    def test_collapse_row_skipped_no_ink(self, qt_app):
        assert self._ink(qt_app, self._single_hidden("collapse")) == 0

    def test_placeholder_row_draws_ink(self, qt_app):
        # "物种名称：" prefix must render even with blank value.
        assert self._ink(qt_app, self._single_hidden("placeholder")) > 0

    def test_blank_row_not_skipped(self, qt_app):
        # A space prefix keeps the row alive (not skipped) — may draw little/no
        # visible ink, but the template must still carry the prefix.
        t = self._single_hidden("blank")
        assert normalize_template(t)["rows"][0]["prefix"] == " "


# ══════════════════════════════════════════════════════════════════════════════
# normalize_template
# ══════════════════════════════════════════════════════════════════════════════

class TestNormalizeTemplate:
    def test_none_returns_fallback(self):
        t = normalize_template(None)
        assert t["name"] == "标签"
        assert isinstance(t["rows"], list)
        assert t["qr"]["ecc"] == "Q"

    def test_qr_ecc_defaults_to_q(self):
        t = normalize_template({"rows": [], "qr": {}})
        assert t["qr"]["ecc"] == "Q"

    def test_qr_position_defaults_to_right(self):
        t = normalize_template({"rows": [], "qr": {}})
        assert t["qr"]["position"] == "right"

    def test_qr_size_pct_default(self):
        t = normalize_template({"rows": [], "qr": {}})
        assert t["qr"]["sizePct"] == 0.4

    def test_line_height_default(self):
        t = normalize_template({"rows": []})
        assert t["lineHeight"] == DEFAULT_LINE_HEIGHT

    def test_rows_normalized(self):
        t = normalize_template({"rows": [{"fields": ["storage"]}]})
        row = t["rows"][0]
        assert row["wrap"] is True
        assert row["align"] == "left"
        # fields normalized to dict form
        assert row["fields"][0]["key"] == "storage"

    def test_preserves_existing_qr_ecc(self):
        t = normalize_template({"rows": [], "qr": {"ecc": "H"}})
        assert t["qr"]["ecc"] == "H"

    def test_custom_fallback(self):
        fallback = {"name": "TEST", "rows": [], "qr": {"ecc": "L"}}
        t = normalize_template(None, opts={"fallback": fallback})
        assert t["name"] == "TEST"
        assert t["qr"]["ecc"] == "L"


# ══════════════════════════════════════════════════════════════════════════════
# resolve_line_height / resolve_wrap
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveHelpers:
    def test_line_height_row_override(self):
        assert resolve_line_height({"lineHeight": 1.3}, {"lineHeight": 2.0}) == 2.0

    def test_line_height_template_fallback(self):
        assert resolve_line_height({"lineHeight": 1.5}, {}) == 1.5

    def test_line_height_global_default(self):
        assert resolve_line_height({}, {}) == DEFAULT_LINE_HEIGHT

    def test_resolve_wrap_default_true(self):
        assert resolve_wrap({}) is True
        assert resolve_wrap(None) is True

    def test_resolve_wrap_explicit_false(self):
        assert resolve_wrap({"wrap": False}) is False


# ══════════════════════════════════════════════════════════════════════════════
# calculate_grid
# ══════════════════════════════════════════════════════════════════════════════

class TestCalculateGrid:
    def test_standard_a4_60x40(self):
        # A4: 210×297, margin=8, gap=2
        # usableW = 194, usableH = 281
        # cols = floor((194+2)/(60+2)) = floor(196/62) = 3
        # rows = floor((281+2)/(40+2)) = floor(283/42) = 6
        g = calculate_grid(60, 40, 210, 297)
        assert g["cols"] == 3
        assert g["rows"] == 6
        assert g["perPage"] == 18

    def test_standard_a4_50x30(self):
        # cols = floor((194+2)/(50+2)) = floor(196/52) = 3
        # rows = floor((281+2)/(30+2)) = floor(283/32) = 8
        g = calculate_grid(50, 30, 210, 297)
        assert g["cols"] == 3
        assert g["rows"] == 8
        assert g["perPage"] == 24

    def test_minimum_one_col_row(self):
        g = calculate_grid(300, 300, 210, 297)
        assert g["cols"] == 1
        assert g["rows"] == 1

    def test_custom_margin_override(self):
        g = calculate_grid(60, 40, 210, 297, opts={"marginMm": 0})
        # usableW = 210, usableH = 297
        g2 = calculate_grid(60, 40, 210, 297, opts={"marginMm": 8})
        assert g["cols"] >= g2["cols"]

    def test_usable_dimensions_correct(self):
        g = calculate_grid(60, 40, 210, 297)
        assert g["usableW"] == 210 - 2 * SHEET_MARGIN_MM
        assert g["usableH"] == 297 - 2 * SHEET_MARGIN_MM

    def test_force_cols_rows_override_auto(self):
        g = calculate_grid(50, 30, 210, 297, opts={"forceCols": 2, "forceRows": 5})
        assert g["cols"] == 2
        assert g["rows"] == 5
        assert g["perPage"] == 10

    def test_force_cols_only(self):
        auto = calculate_grid(50, 30, 210, 297)
        g = calculate_grid(50, 30, 210, 297, opts={"forceCols": 1})
        assert g["cols"] == 1
        assert g["rows"] == auto["rows"]   # rows still auto

    def test_force_values_clamped_to_one_minimum(self):
        g = calculate_grid(50, 30, 210, 297, opts={"forceCols": 0, "forceRows": -3})
        # non-positive forces are ignored → fall back to auto
        auto = calculate_grid(50, 30, 210, 297)
        assert g["cols"] == auto["cols"] and g["rows"] == auto["rows"]

    def test_custom_gap_changes_grid(self):
        tight = calculate_grid(50, 30, 210, 297, opts={"gapMm": 0})
        loose = calculate_grid(50, 30, 210, 297, opts={"gapMm": 20})
        assert tight["perPage"] >= loose["perPage"]


# ══════════════════════════════════════════════════════════════════════════════
# plan_label_pages — pure page/slot geometry (no Qt); must mirror the painter
# loop in labels_view._paint_labels exactly (incl. blank-item placeholders).
# ══════════════════════════════════════════════════════════════════════════════

class TestPlanLabelPages:
    def _items(self, n, blanks=()):
        out = []
        for i in range(n):
            data = {} if i in blanks else {"uniqueId": f"X{i}"}
            out.append({"idx": i, "data": data})
        return out

    def test_label_paper_one_per_page(self):
        items = self._items(3)
        plan = plan_label_pages(items, {"w": 50, "h": 30}, "label", None, None)
        assert [p["page"] for p in plan] == [0, 1, 2]
        assert all(p["x_mm"] == 0 and p["y_mm"] == 0 for p in plan)
        assert [p["data"] for p in plan] == [it["data"] for it in items]

    def test_label_paper_blank_still_consumes_page(self):
        # mirrors _paint_labels: label paper advances one page per item even
        # when the item is blank (newPage fires before the blank-skip).
        items = self._items(3, blanks=(1,))
        plan = plan_label_pages(items, {"w": 50, "h": 30}, "label", None, None)
        assert len(plan) == 3
        assert [p["page"] for p in plan] == [0, 1, 2]
        assert plan[1]["data"] == {}

    def test_a4_grid_offsets_match_calculate_grid(self):
        dims = {"w": 50, "h": 30}
        paper = {"w": 210, "h": 297}
        g = calculate_grid(50, 30, 210, 297)
        cols, per_page = g["cols"], g["perPage"]
        margin, gap = g["margin"], g["gap"]
        items = self._items(per_page + 2)
        plan = plan_label_pages(items, dims, "a4", paper, None)
        for i, p in enumerate(plan):
            slot = i % per_page
            col, row = slot % cols, slot // cols
            assert p["page"] == i // per_page
            assert p["x_mm"] == margin + col * (50 + gap)
            assert p["y_mm"] == margin + row * (30 + gap)

    def test_grid_blank_item_keeps_slot(self):
        # blank items occupy a slot (skip at paint) — length & slots preserved.
        dims = {"w": 50, "h": 30}
        paper = {"w": 210, "h": 297}
        items = self._items(4, blanks=(0, 2))
        plan = plan_label_pages(items, dims, "a4", paper, None)
        assert len(plan) == 4
        assert plan[0]["data"] == {} and plan[2]["data"] == {}

    def test_grid_opts_force_cols_respected(self):
        dims = {"w": 50, "h": 30}
        paper = {"w": 210, "h": 297}
        items = self._items(4)
        plan = plan_label_pages(items, dims, "a4", paper, {"forceCols": 2})
        # forceCols=2 → row 0: cols 0,1 ; row 1: cols 0,1
        gap = calculate_grid(50, 30, 210, 297, opts={"forceCols": 2})["gap"]
        margin = calculate_grid(50, 30, 210, 297, opts={"forceCols": 2})["margin"]
        assert plan[2]["x_mm"] == margin                       # wraps to col 0
        assert plan[2]["y_mm"] == margin + 1 * (30 + gap)       # second row

    def test_a4_default_paper_when_none(self):
        # a4/a5 with paper=None falls back to a sane default, no crash.
        plan = plan_label_pages(self._items(1), {"w": 50, "h": 30}, "a4", None, None)
        assert len(plan) == 1 and plan[0]["page"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# estimate_text_scale
# ══════════════════════════════════════════════════════════════════════════════

class TestEstimateTextScale:
    def test_scale_one_for_adequate_space(self):
        # small template, big label
        tmpl = {"rows": [{"size": 8}], "qr": {"position": "none"}}
        scale = estimate_text_scale(tmpl, {"w": 100, "h": 100})
        assert scale == 1.0

    def test_scale_below_one_for_overflow(self):
        # many rows in a tiny label
        rows = [{"size": 12}] * 20
        tmpl = {"rows": rows, "qr": {"position": "none"}}
        scale = estimate_text_scale(tmpl, {"w": 30, "h": 15})
        assert scale < 1.0

    def test_scale_minimum_0_4(self):
        rows = [{"size": 20}] * 50
        tmpl = {"rows": rows, "qr": {"position": "none"}}
        scale = estimate_text_scale(tmpl, {"w": 10, "h": 5})
        assert scale >= 0.4

    def test_scale_accounts_for_top_qr(self):
        rows = [{"size": 9}] * 3
        tmpl = {"rows": rows, "qr": {"position": "top", "sizePct": 0.55}}
        scale_no_qr = estimate_text_scale({"rows": rows, "qr": {"position": "none"}}, {"w": 60, "h": 40})
        scale_top_qr = estimate_text_scale(tmpl, {"w": 60, "h": 40})
        # Top QR subtracts vertical space → scale should be smaller or equal
        assert scale_top_qr <= scale_no_qr


# ══════════════════════════════════════════════════════════════════════════════
# qr_metrics
# ══════════════════════════════════════════════════════════════════════════════

class TestQrMetrics:
    def test_position_none_returns_none(self):
        tmpl = normalize_template({"rows": [], "qr": {"position": "none"}})
        assert qr_metrics(tmpl, {"w": 60, "h": 40}) is None

    def test_position_right(self):
        tmpl = normalize_template({"rows": [], "qr": {"position": "right", "sizePct": 0.4}})
        m = qr_metrics(tmpl, {"w": 60, "h": 40})
        assert m is not None
        size_mm = 40 * 0.4   # h * sizePct
        assert abs(m["sizeMm"] - size_mm) < 1e-6
        assert abs(m["x"] - (60 - size_mm)) < 1e-6

    def test_position_left(self):
        tmpl = normalize_template({"rows": [], "qr": {"position": "left", "sizePct": 0.4}})
        m = qr_metrics(tmpl, {"w": 60, "h": 40})
        assert m is not None
        assert m["x"] == 0.0

    def test_position_bottom(self):
        tmpl = normalize_template({"rows": [], "qr": {"position": "bottom", "sizePct": 0.55}})
        m = qr_metrics(tmpl, {"w": 60, "h": 40})
        assert m is not None
        size_mm = 60 * 0.55
        assert abs(m["y"] - (40 - size_mm)) < 1e-6

    def test_position_free(self):
        tmpl = normalize_template({
            "rows": [],
            "qr": {"position": "free", "x": 5.0, "y": 3.0, "sizeMm": 12.0}
        })
        m = qr_metrics(tmpl, {"w": 60, "h": 40})
        assert m is not None
        assert m["x"] == 5.0
        assert m["y"] == 3.0
        assert m["sizeMm"] == 12.0

    def test_dist_fields_correct(self):
        tmpl = normalize_template({"rows": [], "qr": {"position": "right", "sizePct": 0.4}})
        dims = {"w": 60, "h": 40}
        m = qr_metrics(tmpl, dims)
        assert m is not None
        assert abs(m["distLeft"] - m["x"]) < 1e-6
        assert abs(m["distRight"] - (60 - m["x"] - m["sizeMm"])) < 1e-6
        assert abs(m["distBottom"] - (40 - m["y"] - m["sizeMm"])) < 1e-6

    def test_default_ecc_q_in_normalized_template(self):
        """QR template must default to ECC Q (hard rule)."""
        tmpl = normalize_template({"rows": []})
        assert tmpl["qr"]["ecc"] == "Q"


# ══════════════════════════════════════════════════════════════════════════════
# validate_print_job
# ══════════════════════════════════════════════════════════════════════════════

class TestValidatePrintJob:
    def _base_job(self, **kw) -> dict:
        job: dict = {
            "bucket": "sample",
            "items": [{"data": specimen_to_label_data(_sp())}],
            "template": normalize_template({
                "rows": [{"fields": ["headerId"], "size": 9}],
                "qr": {"position": "right", "sizePct": 0.3, "ecc": "Q"},
            }),
            "dims": {"w": 60, "h": 40},
            "printerMargin": DEFAULT_PRINTER_MARGIN_MM,
        }
        job.update(kw)
        return job

    def test_no_warnings_for_valid_job(self):
        warnings = validate_print_job(self._base_job())
        codes = [w["code"] for w in warnings]
        # Should not have error codes
        assert "empty" not in codes
        assert "bad-size" not in codes

    def test_empty_items_raises_error(self):
        warnings = validate_print_job(self._base_job(items=[]))
        assert any(w["code"] == "empty" for w in warnings)

    def test_bad_size_dims_zero(self):
        warnings = validate_print_job(self._base_job(dims={"w": 0, "h": 0}))
        assert any(w["code"] == "bad-size" for w in warnings)

    def test_tiny_label_warning(self):
        warnings = validate_print_job(self._base_job(dims={"w": 10, "h": 5}))
        assert any(w["code"] == "tiny-label" for w in warnings)

    def test_tissue_mini_warning_for_tissue_bucket(self):
        job = self._base_job(bucket="tissue", dims={"w": 25, "h": 10})
        warnings = validate_print_job(job)
        assert any(w["code"] == "tissue-mini" for w in warnings)

    def test_qr_none_warning_when_qr_disabled(self):
        job = self._base_job()
        job["template"] = normalize_template({
            "rows": [{"fields": ["headerId"], "size": 9}],
            "qr": {"position": "none"},
        })
        warnings = validate_print_job(job)
        assert any(w["code"] == "qr-none" for w in warnings)

    def test_qr_margin_warning_when_qr_near_edge(self):
        # QR right-aligned on a 20×8 label: distRight ≈ 0, triggering margin warn
        job = self._base_job(dims={"w": 20, "h": 8})
        warnings = validate_print_job(job)
        codes = {w["code"] for w in warnings}
        # Either qr-margin or tiny-label will fire for this size
        assert codes & {"qr-margin", "tiny-label", "tissue-mini"}

    def test_qr_small_warning(self):
        # QR size = 8 * 0.3 = 2.4 mm < 6 mm → qr-small
        job = self._base_job(dims={"w": 15, "h": 8})
        warnings = validate_print_job(job)
        assert any(w["code"] == "qr-small" for w in warnings)


# ══════════════════════════════════════════════════════════════════════════════
# create_print_job
# ══════════════════════════════════════════════════════════════════════════════

class TestCreatePrintJob:
    def test_returns_job_with_warnings(self):
        job = create_print_job({
            "template": BUILTIN_TEMPLATES["standard"],
            "items": [{"data": specimen_to_label_data(_sp())}],
            "dims": {"w": 60, "h": 40},
            "bucket": "sample",
        })
        assert "warnings" in job
        assert "labels" in job
        assert job["bucket"] == "sample"

    def test_copies_expands_labels(self):
        items = [{"data": specimen_to_label_data(_sp())}]
        job = create_print_job({
            "template": BUILTIN_TEMPLATES["standard"],
            "items": items,
            "dims": {"w": 60, "h": 40},
            "copies": 3,
        })
        assert len(job["labels"]) == 3

    def test_a4_layout_computed(self):
        job = create_print_job({
            "template": BUILTIN_TEMPLATES["standard"],
            "items": [{"data": specimen_to_label_data(_sp())}],
            "dims": {"w": 60, "h": 40},
            "paperType": "a4",
            "paper": {"w": 210, "h": 297},
        })
        assert job["layout"] is not None
        assert job["layout"]["perPage"] > 0

    def test_label_papertype_no_layout(self):
        job = create_print_job({
            "template": BUILTIN_TEMPLATES["compact"],
            "items": [{"data": specimen_to_label_data(_sp())}],
            "dims": {"w": 40, "h": 20},
            "paperType": "label",
        })
        assert job["layout"] is None

    def test_default_printer_margin(self):
        job = create_print_job({
            "items": [{"data": specimen_to_label_data(_sp())}],
            "dims": {"w": 60, "h": 40},
        })
        assert job["printerMargin"] == DEFAULT_PRINTER_MARGIN_MM

    def test_adapter_set_to_pyqt6(self):
        job = create_print_job()
        assert "pyqt6" in job["adapter"]


# ══════════════════════════════════════════════════════════════════════════════
# LabelService (service layer)
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelService:
    def test_build_print_job_sample_bucket(self):
        specimens = [_sp(id="DLC001"), _sp(id="BLC001")]
        job = LabelService.build_print_job(
            specimens,
            BUILTIN_TEMPLATES["standard"],
            "sample",
            dims={"w": 60, "h": 40},
        )
        assert len(job["items"]) == 2
        assert job["bucket"] == "sample"

    def test_build_print_job_tissue_bucket_only_r(self):
        """Tissue job must contain only R-prefix specimens."""
        specimens = [
            _sp(id="DLC001", storage="D95E"),    # non-RNA
            _rna_sp(id="BLC001", storage="RD75E"),  # RNA
        ]
        job = LabelService.build_print_job(
            specimens,
            BUILTIN_TEMPLATES["tissueCompact"],
            "tissue",
            dims={"w": 30, "h": 15},
        )
        assert len(job["items"]) == 1
        assert job["items"][0]["data"]["storage"].upper().startswith("R")

    def test_r_prefix_in_both_buckets(self):
        """R-prefix specimen must appear in BOTH sample and tissue builds."""
        specimens = [_rna_sp(id="BLC001", storage="RD75E")]
        sample_job = LabelService.build_print_job(
            specimens, BUILTIN_TEMPLATES["standard"], "sample",
            dims={"w": 60, "h": 40},
        )
        tissue_job = LabelService.build_print_job(
            specimens, BUILTIN_TEMPLATES["tissueCompact"], "tissue",
            dims={"w": 30, "h": 15},
        )
        assert len(sample_job["items"]) == 1
        assert len(tissue_job["items"]) == 1

    def test_selected_indices_subset(self):
        specimens = [_sp(id="DLC001"), _sp(id="BLC001"), _sp(id="CLC001")]
        job = LabelService.build_print_job(
            specimens, BUILTIN_TEMPLATES["standard"], "sample",
            selected_indices=[0, 2], dims={"w": 60, "h": 40},
        )
        ids = {item["data"]["speciesId"] for item in job["items"]}
        assert ids == {"DLC001", "CLC001"}

    def test_fill_blank_prints_without_specimens(self):
        """No specimen selected + fill_blank=True → one blank item so the user
        can print N copies of the bare template (bound fields blank)."""
        job = LabelService.build_print_job(
            [], BUILTIN_TEMPLATES["standard"], "sample",
            selected_indices=[], copies=3, dims={"w": 60, "h": 40},
            fill_blank=True,
        )
        assert len(job["items"]) == 1
        assert job["items"][0]["data"] == {}
        assert job["copies"] == 3

    def test_fill_blank_default_off_keeps_empty(self):
        """Default (fill_blank=False) is unchanged — no specimens → 0 items."""
        job = LabelService.build_print_job(
            [], BUILTIN_TEMPLATES["standard"], "sample",
            selected_indices=[], copies=3, dims={"w": 60, "h": 40},
        )
        assert job["items"] == []

    def test_fill_blank_not_triggered_when_specimens_selected(self):
        """fill_blank must NOT fabricate when the user selected specimens but a
        bucket is legitimately empty (e.g. tissue with no R-prefix)."""
        specimens = [_sp(id="DLC001", storage="D95E")]  # non-RNA
        job = LabelService.build_print_job(
            specimens, BUILTIN_TEMPLATES["tissueCompact"], "tissue",
            selected_indices=[0], copies=2, dims={"w": 30, "h": 15},
            fill_blank=True,
        )
        assert job["items"] == []  # tissue bucket stays empty, no blank fabricated

    def test_de_duplication_by_unique_id(self):
        sp = _sp(id="DLC001")
        # Pass same specimen three times
        job = LabelService.build_print_job(
            [sp, sp, sp], BUILTIN_TEMPLATES["standard"], "sample",
            dims={"w": 60, "h": 40},
        )
        assert len(job["items"]) == 1

    def test_specimen_dataclass_accepted(self):
        """LabelService must accept Specimen dataclass objects, not just dicts."""
        from app.models.specimen import Specimen
        sp = Specimen(
            uid="FJ-YGLZ-B2-DLC001-D95E-20260508",
            id="DLC001",
            province="FJ",
            site="YGLZ",
            station="B2",
            storage="D95E",
            collection_date="20260508",
            photo_date="20260508",
            scientific_name_cn="背鳞虫",
            collector="杨德援",
        )
        job = LabelService.build_print_job(
            [sp], BUILTIN_TEMPLATES["standard"], "sample",
            dims={"w": 60, "h": 40},
        )
        assert len(job["items"]) == 1


# ══════════════════════════════════════════════════════════════════════════════
# quick_print_jobs_for_specimen — workbench one-click (sample + tissue if RNA)
# ══════════════════════════════════════════════════════════════════════════════

class TestQuickPrintJobs:
    _PAPERS = {"sample": "label", "tissue": "label"}

    def test_non_rna_yields_one_sample_job(self):
        specs = [_sp()]
        jobs = LabelService.quick_print_jobs_for_specimen(
            specs, unique_id(specs[0]), copies=1, paper_types=self._PAPERS)
        assert [j["bucket"] for j in jobs] == ["sample"]
        assert len(jobs[0]["items"]) == 1

    def test_rna_yields_sample_and_tissue(self):
        specs = [_rna_sp()]
        jobs = LabelService.quick_print_jobs_for_specimen(
            specs, unique_id(specs[0]), copies=1, paper_types=self._PAPERS)
        assert [j["bucket"] for j in jobs] == ["sample", "tissue"]

    def test_unknown_uid_returns_empty(self):
        assert LabelService.quick_print_jobs_for_specimen(
            [_sp()], "NO-SUCH-UID", copies=1, paper_types=self._PAPERS) == []

    def test_copies_propagate_to_job(self):
        specs = [_sp()]
        jobs = LabelService.quick_print_jobs_for_specimen(
            specs, unique_id(specs[0]), copies=3, paper_types=self._PAPERS)
        assert jobs[0]["copies"] == 3
        assert len(jobs[0]["labels"]) == 3   # copies multiply the single item

    def test_picks_correct_specimen_among_many(self):
        specs = [_sp(id="A1"), _rna_sp(id="R2"), _sp(id="A3")]
        jobs = LabelService.quick_print_jobs_for_specimen(
            specs, unique_id(specs[1]), copies=1, paper_types=self._PAPERS)
        # the R2 specimen → sample + tissue, and only that specimen's data.
        assert [j["bucket"] for j in jobs] == ["sample", "tissue"]
        assert jobs[0]["items"][0]["data"]["speciesId"] == "R2"


# ══════════════════════════════════════════════════════════════════════════════
# QR error-correction level Q (hard rule)
# ══════════════════════════════════════════════════════════════════════════════

class TestQrEccQ:
    def test_default_template_ecc_is_q(self):
        """Hard rule: QR must default to ECC Q (25% recovery)."""
        tmpl = normalize_template(None)
        assert tmpl["qr"]["ecc"] == "Q"

    def test_builtin_templates_all_have_ecc_q(self):
        # Original oracle-derived templates must keep ECC=Q.
        # New tube/circle templates (Task 4) may use M/H for small-label suitability.
        _ORIGINAL_KEYS = ("standard", "compact", "detailed",
                          "tissueCompact", "tissueMini", "tissueCustom")
        for name in _ORIGINAL_KEYS:
            tmpl = BUILTIN_TEMPLATES[name]
            normalised = normalize_template(tmpl)
            assert normalised["qr"]["ecc"] == "Q", (
                f"Template '{name}' has ecc={normalised['qr']['ecc']!r}, expected 'Q'"
            )

    def test_qrcode_library_ecc_q_available(self):
        """qrcode library must be importable and expose ERROR_CORRECT_Q."""
        qrcode = pytest.importorskip("qrcode")
        from qrcode.constants import ERROR_CORRECT_Q  # type: ignore
        assert ERROR_CORRECT_Q is not None

    def test_generate_qr_pixmap_with_ecc_q(self, qt_app):
        """_generate_qr_pixmap must produce a non-null pixmap at ECC Q."""
        pytest.importorskip("qrcode")
        from app.widgets.label_editor import _generate_qr_pixmap
        pixmap = _generate_qr_pixmap("FJ-YGLZ-B2-DLC001-D95E-20260508", 60, "Q")
        assert pixmap is not None
        assert not pixmap.isNull()


# ══════════════════════════════════════════════════════════════════════════════
# Qt widget smoke-tests (offscreen, no display required)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def qt_app():
    """Module-scoped QApplication for offscreen tests."""
    from PyQt6.QtWidgets import QApplication
    existing = QApplication.instance()
    if existing is not None:
        yield existing
    else:
        app = QApplication(sys.argv[:1])
        yield app
        # Do not call app.quit() — let pytest-qt clean up


class TestLabelEditorWidget:
    def test_instantiates_without_crash(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        template = normalize_template(BUILTIN_TEMPLATES["standard"])
        dims = {"w": 60, "h": 40}
        data = specimen_to_label_data(_sp())
        w = LabelEditorWidget(template, dims, data)
        assert w is not None

    def test_has_undo_stack(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        w = LabelEditorWidget(None, {"w": 60, "h": 40}, {})
        assert w.undo_stack is not None
        assert w.undo_stack.undoLimit() == 30

    def test_has_scene(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        w = LabelEditorWidget(None, {"w": 60, "h": 40}, {})
        assert w.scene is not None

    def test_qr_item_present_for_default_template(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        template = normalize_template(BUILTIN_TEMPLATES["standard"])
        w = LabelEditorWidget(template, {"w": 60, "h": 40}, specimen_to_label_data(_sp()))
        # QR item should exist (standard template has QR enabled)
        assert w.scene.qr_item is not None

    def test_update_label_does_not_crash(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        w = LabelEditorWidget(None, {"w": 60, "h": 40}, {})
        w.update_label(dims={"w": 50, "h": 30}, label_data=specimen_to_label_data(_sp()))

    def test_2mm_safety_margin_in_scene(self, qt_app):
        """Safety margin rectangle must be present in the scene."""
        from app.widgets.label_editor import LabelEditorWidget, _SAFETY_MARGIN_MM
        from PyQt6.QtWidgets import QGraphicsRectItem
        w = LabelEditorWidget(None, {"w": 60, "h": 40}, {})
        rects = [item for item in w.scene.items()
                 if isinstance(item, QGraphicsRectItem)]
        assert len(rects) >= 2   # background + safety margin


class TestLabelsView:
    def test_instantiates_without_crash(self, qt_app):
        from app.views.labels_view import LabelsView
        from app.app_context import AppContext
        ctx = AppContext()
        view = LabelsView(ctx)
        assert view is not None
        assert view.view_id == "labels"
        assert view.nav_title == "标签打印"
        assert view.nav_icon == "🏷️"

    def test_on_activate_with_no_project(self, qt_app):
        from app.views.labels_view import LabelsView
        from app.app_context import AppContext
        ctx = AppContext()
        view = LabelsView(ctx)
        # Should not crash with no project loaded
        view.on_activate()

    def test_step_sections_present(self, qt_app):
        from app.views.labels_view import LabelsView
        from app.app_context import AppContext
        ctx = AppContext()
        view = LabelsView(ctx)
        # Web classic Step 1-4 vertical flow.
        assert view._step1 is not None
        assert view._step2 is not None
        assert view._step3 is not None
        assert view._step4 is not None


# ══════════════════════════════════════════════════════════════════════════════
# label_data_text
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelDataText:
    def test_label_data_text_all_fields(self):
        """label_data_text mirrors JS labelDataText(): uniqueId + speciesName + region +
        collectorLabel joined by newline, values only (no field-name prefixes)."""
        data = {
            "uniqueId": "FJ-YGLZ-B2-001",
            "speciesName": "背鳞虫 sp.01",
            "region": "福建·厦门",
            "collectorLabel": "杨德援采集",
        }
        result = label_data_text(data)
        assert "FJ-YGLZ-B2-001" in result
        assert "背鳞虫 sp.01" in result
        assert "福建·厦门" in result
        assert "杨德援采集" in result
        lines = result.split("\n")
        assert len(lines) == 4

    def test_label_data_text_skips_empty(self):
        data = {
            "uniqueId": "FJ-001",
            "speciesName": "",
            "region": None,
            "collectorLabel": "",
        }
        result = label_data_text(data)
        assert "FJ-001" in result
        assert result == "FJ-001"

    def test_label_data_text_empty_dict(self):
        assert label_data_text({}) == ""


# ══════════════════════════════════════════════════════════════════════════════
# render_label_onto: shape / bgColor / field color / cornerRadius (Task 4)
# ══════════════════════════════════════════════════════════════════════════════

class TestRenderShape:
    """Tests for render_label_onto shape/bgColor/field-color/cornerRadius (Task 4)."""

    def _render(self, qt_app, tmpl: dict, dims: dict, data: dict = None, scale: float = 4.0):
        from PyQt6.QtGui import QPixmap, QPainter, QColor
        from app.utils.label_render import render_label_onto
        w_px = max(1, int(dims["w"] * scale))
        h_px = max(1, int(dims["h"] * scale))
        pm = QPixmap(w_px, h_px)
        pm.fill(QColor("white"))
        p = QPainter(pm)
        render_label_onto(p, tmpl, dims, data or {}, px_per_mm=scale)
        p.end()
        return pm.toImage()

    def test_circle_shape_no_crash(self, qt_app):
        self._render(qt_app, {"shape": "circle", "rows": [], "lineHeight": 1.0}, {"w": 13, "h": 13})

    def test_roundrect_shape_no_crash(self, qt_app):
        self._render(qt_app,
                     {"shape": "roundrect", "cornerRadius": 2.0, "rows": [], "lineHeight": 1.0},
                     {"w": 40, "h": 20})

    def test_default_shape_rect_no_crash(self, qt_app):
        self._render(qt_app, {"rows": [], "lineHeight": 1.0}, {"w": 30, "h": 15})

    def test_bgColor_red_produces_non_white_pixels(self, qt_app):
        img = self._render(qt_app, {"bgColor": "#ff0000", "rows": [], "lineHeight": 1.0},
                           {"w": 30, "h": 15})
        has_red = any(
            (img.pixel(x, y) >> 16 & 0xFF) > 200
            and (img.pixel(x, y) >> 8 & 0xFF) < 50
            and (img.pixel(x, y) & 0xFF) < 50
            for y in range(0, img.height(), 2)
            for x in range(0, img.width(), 2)
        )
        assert has_red, "bgColor=#ff0000 should produce red pixels"

    def test_bgColor_default_white(self, qt_app):
        img = self._render(qt_app, {"rows": [], "lineHeight": 1.0}, {"w": 20, "h": 10})
        cx, cy = img.width() // 2, img.height() // 2
        assert img.pixel(cx, cy) & 0xFFFFFF == 0xFFFFFF, "Default bgColor should be white"

    def test_circle_bg_corners_stay_white(self, qt_app):
        """fillPath to circle: corners outside ellipse stay canvas bg color (white)."""
        img = self._render(
            qt_app,
            {"shape": "circle", "bgColor": "#0000ff", "rows": [], "lineHeight": 1.0},
            {"w": 20, "h": 20},
            scale=5.0,
        )
        c = img.pixel(0, 0)
        assert c & 0xFFFFFF == 0xFFFFFF, "Top-left corner outside circle should stay white"

    def test_field_color_blue_text(self, qt_app):
        from PyQt6.QtGui import QPixmap, QPainter, QColor
        from app.utils.label_render import render_label_onto
        scale = 8.0
        dims = {"w": 40, "h": 15}
        tmpl = {
            "rows": [{"fields": [{"key": "uniqueId", "style": "", "size": 9,
                                  "offsetX": 0, "offsetY": 0, "color": "#0000ff"}],
                      "align": "left"}],
            "lineHeight": 1.3,
        }
        data = {"uniqueId": "TEST-001"}
        pm = QPixmap(int(dims["w"] * scale), int(dims["h"] * scale))
        pm.fill(QColor("white"))
        p = QPainter(pm)
        render_label_onto(p, tmpl, dims, data, px_per_mm=scale)
        p.end()
        img = pm.toImage()
        has_blue = any(
            (img.pixel(x, y) & 0xFF) > 180
            and (img.pixel(x, y) >> 16 & 0xFF) < 50
            and (img.pixel(x, y) >> 8 & 0xFF) < 50
            for y in range(img.height())
            for x in range(img.width())
        )
        assert has_blue, "field color=#0000ff should produce blue pixels"

    def test_cornerRadius_rect_no_crash(self, qt_app):
        self._render(qt_app, {"cornerRadius": 3.0, "rows": [], "lineHeight": 1.0},
                     {"w": 50, "h": 30})


# ══════════════════════════════════════════════════════════════════════════════
# 排版设计 (imposition designer) extensions — per-side margins, axis gaps,
# shrink-to-fit, orientation, startSlot. HARD requirement: grid_opts WITHOUT
# any new key must produce byte-identical output to the legacy formulas
# (the renderer drives the printer — this is a print red line).
# ══════════════════════════════════════════════════════════════════════════════

class TestCalculateGridLegacyParity:
    """Legacy opts must hit the exact old formulas (no per-side code path)."""

    CASES = [
        (60, 40, 210, 297, None),
        (50, 30, 210, 297, {}),
        (50, 30, 210, 297, {"marginMm": 0}),
        (50, 30, 210, 297, {"marginMm": 8.0, "gapMm": 2.0}),
        (50, 30, 148, 210, {"gapMm": 0}),
        (50, 30, 210, 297, {"forceCols": 2, "forceRows": 5}),
        (33.3, 21.7, 210, 297, {"marginMm": 7.7, "gapMm": 1.3}),
    ]

    def test_legacy_opts_exact_values(self):
        for lw, lh, pw, ph, opts in self.CASES:
            g = calculate_grid(lw, lh, pw, ph, opts=opts)
            o = opts or {}
            margin = SHEET_MARGIN_MM if o.get("marginMm") is None else o["marginMm"]
            gap = GRID_GAP_MM if o.get("gapMm") is None else o["gapMm"]
            usable_w = max(0.0, pw - 2 * margin)
            usable_h = max(0.0, ph - 2 * margin)
            cols = max(1, int((usable_w + gap) // (lw + gap)))
            rows = max(1, int((usable_h + gap) // (lh + gap)))
            fc, fr = o.get("forceCols"), o.get("forceRows")
            if fc is not None and int(fc) > 0:
                cols = int(fc)
            if fr is not None and int(fr) > 0:
                rows = int(fr)
            assert g["cols"] == cols and g["rows"] == rows, (lw, lh, opts)
            assert g["perPage"] == cols * rows
            assert g["margin"] == margin and g["gap"] == gap
            assert g["usableW"] == usable_w and g["usableH"] == usable_h
            assert g["scale"] == 1.0

    def test_legacy_grid_carries_resolved_new_keys(self):
        g = calculate_grid(50, 30, 210, 297)
        assert g["marginLeft"] == g["marginRight"] == g["marginTop"] \
            == g["marginBottom"] == SHEET_MARGIN_MM
        assert g["gapX"] == g["gapY"] == GRID_GAP_MM
        assert g["labelW"] == 50 and g["labelH"] == 30


class TestCalculateGridPerSide:
    def test_fallback_chain_per_side_then_uniform_then_default(self):
        # per-side wins over uniform, uniform wins over SHEET_MARGIN_MM
        g = calculate_grid(50, 30, 210, 297,
                           opts={"marginMm": 5, "marginLeftMm": 12})
        assert g["marginLeft"] == 12
        assert g["marginRight"] == 5 and g["marginTop"] == 5
        g2 = calculate_grid(50, 30, 210, 297, opts={"marginTopMm": 3})
        assert g2["marginTop"] == 3
        assert g2["marginLeft"] == SHEET_MARGIN_MM

    def test_left_right_margins_change_cols_only(self):
        base = calculate_grid(50, 30, 210, 297)
        g = calculate_grid(50, 30, 210, 297,
                           opts={"marginLeftMm": 40, "marginRightMm": 40})
        # usableW = 210-80 = 130 → cols floor((130+2)/52)=2
        assert g["cols"] == 2 and g["cols"] < base["cols"]
        assert g["rows"] == base["rows"]
        assert g["usableW"] == 130 and g["usableH"] == base["usableH"]

    def test_gap_axis_split(self):
        g = calculate_grid(50, 30, 210, 297, opts={"gapXMm": 20, "gapYMm": 0})
        # cols: floor((194+20)/70)=3 ; rows: floor(281/30)=9
        assert g["gapX"] == 20 and g["gapY"] == 0
        assert g["cols"] == 3 and g["rows"] == 9

    def test_zero_gap_allowed(self):
        g = calculate_grid(50, 30, 210, 297,
                           opts={"marginMm": 0, "gapMm": 0})
        assert g["cols"] == 4 and g["rows"] == 9   # 210//50=4, 297//30=9


class TestCalculateGridShrink:
    def test_forced_overflow_scale(self):
        g = calculate_grid(50, 30, 210, 297,
                           opts={"forceCols": 5, "shrinkToFit": True})
        # usableW=194, needW=5*50+4*2=258 ; usableH=281, rows auto=8,
        # needH=8*30+7*2=254 → scale=min(1, 194/258, 281/254)=194/258
        assert g["scale"] == pytest.approx(194 / 258)

    def test_auto_grid_scale_is_one(self):
        g = calculate_grid(50, 30, 210, 297, opts={"shrinkToFit": True})
        assert g["scale"] == 1.0

    def test_overflow_without_flag_keeps_scale_one(self):
        g = calculate_grid(50, 30, 210, 297, opts={"forceCols": 5})
        assert g["scale"] == 1.0


class TestPlanLabelPagesNew:
    def _items(self, n):
        return [{"idx": i, "data": {"uniqueId": f"X{i}"}} for i in range(n)]

    def test_legacy_placements_byte_identical(self):
        # regression gate: no new keys, exact legacy values/key-set
        dims = {"w": 50, "h": 30}
        paper = {"w": 210, "h": 297}
        g = calculate_grid(50, 30, 210, 297)
        items = self._items(g["perPage"] + 2)
        plan = plan_label_pages(items, dims, "a4", paper, None)
        for i, p in enumerate(plan):
            assert set(p.keys()) == {"page", "x_mm", "y_mm", "data"}
            slot = i % g["perPage"]
            col, row = slot % g["cols"], slot // g["cols"]
            assert p["page"] == i // g["perPage"]
            assert p["x_mm"] == g["margin"] + col * (50 + g["gap"])
            assert p["y_mm"] == g["margin"] + row * (30 + g["gap"])

    def test_orientation_landscape_swaps_page_mm(self):
        from app.utils.label_core import effective_page_mm
        assert effective_page_mm(None, "a4", {"orientation": "landscape"}) == (297.0, 210.0)
        assert effective_page_mm(None, "a4", {}) == (210.0, 297.0)
        assert effective_page_mm({"w": 148, "h": 210}, "a4",
                                 {"orientation": "landscape"}) == (210.0, 148.0)
        # plan uses the swapped page: A4 landscape fits 5 cols of 50mm labels
        plan = plan_label_pages(self._items(6), {"w": 50, "h": 30}, "a4",
                                None, {"orientation": "landscape"})
        g = calculate_grid(50, 30, 297, 210)
        assert g["cols"] == 5
        assert plan[4]["x_mm"] == g["margin"] + 4 * (50 + g["gap"])
        assert plan[5]["x_mm"] == g["margin"] and plan[5]["y_mm"] > g["margin"]

    def test_start_slot_skips_first_page_only(self):
        dims = {"w": 50, "h": 30}
        g = calculate_grid(50, 30, 210, 297)        # 3×8 = 24/page
        items = self._items(g["perPage"] + 1)        # 25 items
        plan = plan_label_pages(items, dims, "a4", None, {"startSlot": 3})
        # item 0 → slot 3 (col 0, row 1)
        assert plan[0]["page"] == 0
        assert plan[0]["x_mm"] == g["margin"]
        assert plan[0]["y_mm"] == g["margin"] + 1 * (30 + g["gap"])
        # item 20 → slot 23, still page 0 ; item 21 → page 1 slot 0
        assert plan[20]["page"] == 0
        assert plan[21]["page"] == 1
        assert plan[21]["x_mm"] == g["margin"] and plan[21]["y_mm"] == g["margin"]
        # page 1 has NO offset: item 21+k sits at slot k
        assert plan[24]["page"] == 1

    def test_start_slot_modulo_per_page(self):
        dims = {"w": 50, "h": 30}
        a = plan_label_pages(self._items(4), dims, "a4", None, {"startSlot": 3})
        b = plan_label_pages(self._items(4), dims, "a4", None, {"startSlot": 27})
        assert a == b

    def test_start_slot_ignored_on_label_paper(self):
        plan = plan_label_pages(self._items(3), {"w": 50, "h": 30}, "label",
                                None, {"startSlot": 5})
        assert [p["page"] for p in plan] == [0, 1, 2]
        assert all(p["x_mm"] == 0 and p["y_mm"] == 0 for p in plan)

    def test_scale_key_only_when_shrunk(self):
        dims = {"w": 50, "h": 30}
        plain = plan_label_pages(self._items(2), dims, "a4", None,
                                 {"shrinkToFit": True})
        assert all("scale" not in p for p in plain)   # fits → scale 1 → no key
        shrunk = plan_label_pages(self._items(6), dims, "a4", None,
                                  {"forceCols": 5, "shrinkToFit": True})
        sc = 194 / 258
        assert shrunk[0]["scale"] == pytest.approx(sc)
        assert shrunk[1]["x_mm"] == pytest.approx(8 + 1 * (50 + 2) * sc)
        assert shrunk[0]["x_mm"] == 8                 # margin NOT scaled

    def test_slot_origin_mm_matches_plan(self):
        from app.utils.label_core import slot_origin_mm
        g = calculate_grid(50, 30, 210, 297,
                           opts={"marginLeftMm": 10, "marginTopMm": 4,
                                 "gapXMm": 1, "gapYMm": 3})
        plan = plan_label_pages(self._items(g["perPage"]), {"w": 50, "h": 30},
                                "a4", None,
                                {"marginLeftMm": 10, "marginTopMm": 4,
                                 "gapXMm": 1, "gapYMm": 3})
        for slot, p in enumerate(plan):
            x, y = slot_origin_mm(g, slot)
            assert p["x_mm"] == x and p["y_mm"] == y
        assert plan[1]["x_mm"] == 10 + (50 + 1)
        assert plan[g["cols"]]["y_mm"] == 4 + (30 + 3)
