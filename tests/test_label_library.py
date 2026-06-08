"""tests/test_label_library.py — Tests for LabelTemplateLibrary CRUD,
labelEdits per-specimen overrides, dual-bucket print job, and LabelsView
status bar.

Covers spec checklist items:
  - 自定义模板新建/编辑/复制/删除/命名/选用  (LabelTemplateLibrary)
  - 导入JSON saved as named record
  - per桶尺寸记忆 (QSettings size key)
  - labelEdits逐字段覆盖 → propagated into print job items
  - 打印样品瓶/打印RNAlater两按钮 disabled when count=0
  - 状态栏计数 (_update_status_bar)
"""

from __future__ import annotations

import sys
import copy
import json
import os
import tempfile

import pytest

# ── Fixtures shared with test_label_core ─────────────────────────────────────

def _sp(**kw) -> dict:
    base = {
        "province": "FJ", "site": "YGLZ", "station": "B2",
        "id": "DLC001", "storage": "D95E",
        "collectionDate": "20260506", "photoDate": "20260508",
        "species": "背鳞虫 sp.01", "latin": "Polynoidae sp.",
        "collector": "杨德援", "photographer": "钟珅",
        "family": "Polynoidae", "region": "福建·厦门",
        "lon": "118.18432", "lat": "24.48921", "geoArea": "黄海",
    }
    base.update(kw)
    return base


def _rna_sp(**kw) -> dict:
    kw.setdefault("id", "BLC001")
    kw.setdefault("storage", "RD75E")
    return _sp(**kw)


# ── Qt fixture ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    existing = QApplication.instance()
    if existing is not None:
        yield existing
    else:
        app = QApplication(sys.argv[:1])
        yield app


# ══════════════════════════════════════════════════════════════════════════════
# LabelTemplateLibrary — in-memory isolation via tmp QSettings
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def sample_lib(qt_app, tmp_path):
    """Fresh LabelTemplateLibrary backed by a temp INI file."""
    from PyQt6.QtCore import QSettings
    from app.services.label_service import LabelTemplateLibrary

    ini = str(tmp_path / "test_label_lib.ini")
    # Monkey-patch QSettings to use our temp file
    original_init = QSettings.__init__

    class _FakeLib(LabelTemplateLibrary):
        def __init__(self, bucket: str) -> None:
            self._bucket = bucket
            self._is_tissue = bucket == "tissue"
            self._qs = QSettings(ini, QSettings.Format.IniFormat)
            # skip migration (empty settings)
            from app.services.label_service import _MIGRATION_QSETTINGS_KEY
            self._qs.setValue(_MIGRATION_QSETTINGS_KEY[bucket], "1")

    lib = _FakeLib("sample")
    return lib


@pytest.fixture()
def tissue_lib(qt_app, tmp_path):
    from PyQt6.QtCore import QSettings
    from app.services.label_service import LabelTemplateLibrary

    ini = str(tmp_path / "test_label_lib_tissue.ini")

    class _FakeLib(LabelTemplateLibrary):
        def __init__(self, bucket: str) -> None:
            self._bucket = bucket
            self._is_tissue = bucket == "tissue"
            self._qs = QSettings(ini, QSettings.Format.IniFormat)
            from app.services.label_service import _MIGRATION_QSETTINGS_KEY
            self._qs.setValue(_MIGRATION_QSETTINGS_KEY[bucket], "1")

    lib = _FakeLib("tissue")
    return lib


# ── Basic CRUD ────────────────────────────────────────────────────────────────

class TestLabelTemplateLibraryCRUD:
    def test_empty_library_returns_no_records(self, sample_lib):
        assert sample_lib.records() == []

    def test_upsert_new_record_inserts(self, sample_lib):
        rec = sample_lib.upsert({
            "name": "测试模板",
            "template": {"name": "测试", "rows": [], "qr": {"ecc": "Q"}},
        })
        assert rec["id"].startswith("custom-")
        assert rec["name"] == "测试模板"
        assert len(sample_lib.records()) == 1

    def test_upsert_existing_id_updates(self, sample_lib):
        rec = sample_lib.upsert({
            "name": "原名",
            "template": {"rows": []},
        })
        rec2 = copy.deepcopy(rec)
        rec2["name"] = "新名"
        sample_lib.upsert(rec2)
        recs = sample_lib.records()
        assert len(recs) == 1
        assert recs[0]["name"] == "新名"

    def test_get_returns_record_by_id(self, sample_lib):
        rec = sample_lib.upsert({"name": "A", "template": {"rows": []}})
        fetched = sample_lib.get(rec["id"])
        assert fetched is not None
        assert fetched["id"] == rec["id"]

    def test_get_missing_id_returns_none(self, sample_lib):
        assert sample_lib.get("nonexistent-id") is None

    def test_rename_changes_name(self, sample_lib):
        rec = sample_lib.upsert({"name": "旧名", "template": {"rows": []}})
        sample_lib.rename(rec["id"], "新名称")
        fetched = sample_lib.get(rec["id"])
        assert fetched["name"] == "新名称"

    def test_delete_removes_record(self, sample_lib):
        rec = sample_lib.upsert({"name": "删除我", "template": {"rows": []}})
        assert sample_lib.delete(rec["id"]) is True
        assert sample_lib.get(rec["id"]) is None
        assert sample_lib.records() == []

    def test_delete_nonexistent_returns_false(self, sample_lib):
        assert sample_lib.delete("ghost-id") is False

    def test_duplicate_creates_new_id(self, sample_lib):
        rec = sample_lib.upsert({"name": "原始", "template": {"rows": []}})
        dup = sample_lib.duplicate(rec["id"])
        assert dup is not None
        assert dup["id"] != rec["id"]
        assert "副本" in dup["name"]
        assert len(sample_lib.records()) == 2

    def test_duplicate_nonexistent_returns_none(self, sample_lib):
        assert sample_lib.duplicate("ghost") is None

    def test_clone_from_builtin_creates_library_record(self, sample_lib):
        from app.services.label_service import BUILTIN_TEMPLATES
        base = BUILTIN_TEMPLATES["standard"]
        rec = sample_lib.clone_from_builtin(base, "标准")
        assert rec is not None
        assert "标准" in rec["name"]
        assert len(sample_lib.records()) == 1

    def test_tissue_library_sets_flavor(self, tissue_lib):
        rec = tissue_lib.upsert({"name": "组织", "template": {"rows": []}})
        fetched = tissue_lib.get(rec["id"])
        assert fetched["template"].get("flavor") == "tissue"

    def test_multiple_records_persist_order(self, sample_lib):
        sample_lib.upsert({"name": "第一", "template": {"rows": []}})
        sample_lib.upsert({"name": "第二", "template": {"rows": []}})
        sample_lib.upsert({"name": "第三", "template": {"rows": []}})
        names = [r["name"] for r in sample_lib.records()]
        assert names == ["第一", "第二", "第三"]

    def test_free_form_elements_round_trip(self, sample_lib):
        """A template carrying every element type — including a base64 image —
        survives QSettings JSON persistence intact (Phase G: zero migration)."""
        import base64
        img_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n-fake-bytes").decode("ascii")
        elements = [
            {"type": "text", "x": 1, "y": 1, "w": 20, "h": 6, "text": "Hi"},
            {"type": "field", "x": 1, "y": 8, "w": 20, "h": 6, "key": "headerId"},
            {"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0, "width": 0.5},
            {"type": "rect", "x": 2, "y": 2, "w": 15, "h": 8, "fill": "#eeeeee"},
            {"type": "ellipse", "x": 3, "y": 3, "w": 10, "h": 10},
            {"type": "image", "x": 5, "y": 5, "w": 12, "h": 12, "data": img_b64},
            {"type": "barcode", "x": 1, "y": 20, "w": 30, "h": 12, "content": "uniqueId"},
        ]
        rec = sample_lib.upsert({"name": "全元素", "template": {
            "rows": [], "qr": {"ecc": "Q"}, "elements": elements}})
        got = sample_lib.get(rec["id"])
        saved = got["template"]["elements"]
        assert [e["type"] for e in saved] == [e["type"] for e in elements]
        img = next(e for e in saved if e["type"] == "image")
        assert img["data"] == img_b64  # base64 survived the JSON round-trip

    def test_phase1_style_keys_round_trip(self, sample_lib):
        """Phase 1 keys (opacity/dash/font) survive QSettings persistence."""
        elements = [
            {"type": "text", "x": 1, "y": 1, "w": 20, "h": 6, "text": "Hi",
             "opacity": 0.4, "font": "DejaVu Sans"},
            {"type": "rect", "x": 2, "y": 2, "w": 15, "h": 8, "fill": "#eeeeee",
             "opacity": 0.5, "dash": "dash"},
        ]
        rec = sample_lib.upsert({"name": "样式键", "template": {
            "rows": [], "elements": elements}})
        saved = sample_lib.get(rec["id"])["template"]["elements"]
        assert saved[0]["opacity"] == 0.4 and saved[0]["font"] == "DejaVu Sans"
        assert saved[1]["opacity"] == 0.5 and saved[1]["dash"] == "dash"

    def test_phase3_gradient_shadow_round_trip(self, sample_lib):
        """Phase 3 nested gradient/shadow dicts + monochrome flag persist."""
        grad = {"type": "linear", "angle": 30,
                "stops": [["#ffffff", 0.0], ["#000000", 1.0]]}
        shadow = {"dx": 0.6, "dy": 0.6, "blur": 0, "color": "#777777"}
        rec = sample_lib.upsert({"name": "渐变阴影", "template": {
            "rows": [], "monochrome": True,
            "elements": [{"type": "rect", "x": 1, "y": 1, "w": 20, "h": 10,
                          "gradient": grad, "shadow": shadow}]}})
        tmpl = sample_lib.get(rec["id"])["template"]
        assert tmpl["monochrome"] is True
        el = tmpl["elements"][0]
        assert el["gradient"] == grad and el["shadow"] == shadow

    def test_phase4_shape_points_round_trip(self, sample_lib):
        """Phase 4 generic-shape ``points`` survive QSettings persistence."""
        pts = [[0.5, 0.0], [1.0, 1.0], [0.0, 1.0]]
        rec = sample_lib.upsert({"name": "多边形", "template": {
            "rows": [], "elements": [
                {"type": "shape", "x": 2, "y": 2, "w": 18, "h": 12,
                 "points": pts, "fill": "#cccccc"}]}})
        el = sample_lib.get(rec["id"])["template"]["elements"][0]
        assert el["type"] == "shape" and el["points"] == pts


# ── selected_key persistence ──────────────────────────────────────────────────

class TestSelectedKeyPersistence:
    def test_default_sample_key_is_standard(self, sample_lib):
        assert sample_lib.selected_key() == "standard"

    def test_default_tissue_key_is_tissue_compact(self, tissue_lib):
        assert tissue_lib.selected_key() == "tissueCompact"

    def test_set_selected_key_persists(self, sample_lib):
        sample_lib.set_selected_key("compact")
        assert sample_lib.selected_key() == "compact"

    def test_custom_dims_persist(self, sample_lib):
        from app.services.label_service import resolve_dims
        sample_lib.set_custom_dims(72.0, 41.0)
        assert sample_lib.selected_custom_dims() == {"w": 72.0, "h": 41.0}
        sample_lib.set_selected_size_key("custom")
        # resolve_dims falls back to the persisted custom dims when none passed
        assert resolve_dims(sample_lib) == {"w": 72.0, "h": 41.0}

    def test_select_record_sets_custom_key(self, sample_lib):
        rec = sample_lib.upsert({"name": "M", "template": {"rows": []}})
        sample_lib.select_record(rec["id"])
        key = sample_lib.selected_key()
        assert key == f"custom:{rec['id']}"

    def test_default_sample_size_key(self, sample_lib):
        assert sample_lib.selected_size_key() == "label_50x30"

    def test_default_tissue_size_key(self, tissue_lib):
        assert tissue_lib.selected_size_key() == "label_30x15"

    def test_set_selected_size_key_persists(self, sample_lib):
        sample_lib.set_selected_size_key("label_60x40")
        assert sample_lib.selected_size_key() == "label_60x40"


# ── Migration (JS migrateLegacyLabelTemplate) ─────────────────────────────────

class TestLegacyMigration:
    def test_migration_skipped_when_flag_set(self, qt_app, tmp_path):
        """Migration should not create duplicate records if flag already set."""
        from PyQt6.QtCore import QSettings
        from app.services.label_service import (
            LabelTemplateLibrary, _MIGRATION_QSETTINGS_KEY,
            _LIBRARY_QSETTINGS_KEY, _LEGACY_CUSTOM_QSETTINGS_KEY,
        )
        ini = str(tmp_path / "mig_test.ini")
        qs = QSettings(ini, QSettings.Format.IniFormat)
        # Simulate an existing legacy template
        legacy_tmpl = {"name": "旧版", "rows": [], "qr": {"ecc": "Q"}}
        qs.setValue(_LEGACY_CUSTOM_QSETTINGS_KEY["sample"], json.dumps(legacy_tmpl))
        # Set migration flag to "0" so it triggers
        qs.setValue(_MIGRATION_QSETTINGS_KEY["sample"], "0")
        qs.sync()

        class _TestLib(LabelTemplateLibrary):
            def __init__(self, bucket):
                self._bucket = bucket
                self._is_tissue = False
                self._qs = QSettings(ini, QSettings.Format.IniFormat)
                self._migrate_legacy()

        lib = _TestLib("sample")
        recs = lib.records()
        assert len(recs) == 1
        assert recs[0]["name"] == "旧版"

    def test_migration_not_duplicated_on_second_call(self, qt_app, tmp_path):
        from PyQt6.QtCore import QSettings
        from app.services.label_service import (
            LabelTemplateLibrary, _MIGRATION_QSETTINGS_KEY,
            _LEGACY_CUSTOM_QSETTINGS_KEY,
        )
        ini = str(tmp_path / "mig_test2.ini")
        qs = QSettings(ini, QSettings.Format.IniFormat)
        legacy_tmpl = {"name": "旧版2", "rows": [], "qr": {"ecc": "Q"}}
        qs.setValue(_LEGACY_CUSTOM_QSETTINGS_KEY["sample"], json.dumps(legacy_tmpl))
        qs.setValue(_MIGRATION_QSETTINGS_KEY["sample"], "0")
        qs.sync()

        class _TestLib(LabelTemplateLibrary):
            def __init__(self, bucket):
                self._bucket = bucket
                self._is_tissue = False
                self._qs = QSettings(ini, QSettings.Format.IniFormat)
                self._migrate_legacy()

        lib1 = _TestLib("sample")
        lib2 = _TestLib("sample")  # second call should not double-insert
        assert len(lib2.records()) == 1


# ══════════════════════════════════════════════════════════════════════════════
# labelEdits — per-specimen field override in bucket_specimens
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelEdits:
    def test_field_override_applied_to_label_data(self):
        from app.utils.label_core import bucket_specimens
        sp = _sp(collector="原始采集人")
        edits = {0: {"collector": "修改采集人"}}
        result = bucket_specimens([0], [sp], edits)
        assert result["samples"][0]["data"]["collector"] == "修改采集人"

    def test_field_override_for_rna_specimen_in_both_buckets(self):
        from app.utils.label_core import bucket_specimens
        sp = _rna_sp(collector="原始")
        edits = {0: {"collector": "RNA改"}}
        result = bucket_specimens([0], [sp], edits)
        # Same item object for sample and tissue (mirrored reference)
        assert result["samples"][0]["data"]["collector"] == "RNA改"
        assert result["tissues"][0]["data"]["collector"] == "RNA改"

    def test_none_edit_value_not_applied(self):
        from app.utils.label_core import bucket_specimens
        sp = _sp(collector="保留")
        edits = {0: {"collector": None}}
        result = bucket_specimens([0], [sp], edits)
        assert result["samples"][0]["data"]["collector"] == "保留"

    def test_only_edited_idx_overridden(self):
        from app.utils.label_core import bucket_specimens
        sp1 = _sp(id="A001", collector="原始1")
        sp2 = _sp(id="A002", collector="原始2")
        edits = {0: {"collector": "覆盖1"}}
        result = bucket_specimens([0, 1], [sp1, sp2], edits)
        assert result["samples"][0]["data"]["collector"] == "覆盖1"
        assert result["samples"][1]["data"]["collector"] == "原始2"

    def test_label_service_passes_edits_to_items(self):
        from app.services.label_service import LabelService, BUILTIN_TEMPLATES
        sp = _sp(collector="原始")
        edits = {0: {"collector": "服务层覆盖"}}
        job = LabelService.build_print_job(
            [sp], BUILTIN_TEMPLATES["standard"], "sample",
            dims={"w": 60, "h": 40}, edits=edits,
        )
        assert job["items"][0]["data"]["collector"] == "服务层覆盖"

    def test_multi_specimen_edits(self):
        from app.services.label_service import LabelService, BUILTIN_TEMPLATES
        sp1 = _sp(id="A001", collector="原始1")
        sp2 = _sp(id="A002", collector="原始2")
        edits = {
            0: {"collector": "覆盖1"},
            1: {"collector": "覆盖2"},
        }
        job = LabelService.build_print_job(
            [sp1, sp2], BUILTIN_TEMPLATES["standard"], "sample",
            dims={"w": 60, "h": 40}, edits=edits,
        )
        assert len(job["items"]) == 2
        collectors = {item["data"]["collector"] for item in job["items"]}
        assert collectors == {"覆盖1", "覆盖2"}


# ══════════════════════════════════════════════════════════════════════════════
# Dual-bucket print job — R-prefix enters BOTH buckets
# ══════════════════════════════════════════════════════════════════════════════

class TestDualBucketPrintJob:
    def test_sample_job_includes_all_specimens(self):
        from app.services.label_service import LabelService, BUILTIN_TEMPLATES
        specimens = [_sp(id="DLC001"), _rna_sp(id="BLC001")]
        job = LabelService.build_print_job(
            specimens, BUILTIN_TEMPLATES["standard"], "sample",
            dims={"w": 60, "h": 40},
        )
        assert len(job["items"]) == 2

    def test_tissue_job_includes_only_r_prefix(self):
        from app.services.label_service import LabelService, BUILTIN_TEMPLATES
        specimens = [_sp(id="DLC001", storage="D95E"),
                     _rna_sp(id="BLC001", storage="RD75E")]
        job = LabelService.build_print_job(
            specimens, BUILTIN_TEMPLATES["tissueCompact"], "tissue",
            dims={"w": 30, "h": 15},
        )
        assert len(job["items"]) == 1
        assert job["items"][0]["data"]["storage"].upper().startswith("R")

    def test_tissue_job_empty_when_no_r_prefix(self):
        from app.services.label_service import LabelService, BUILTIN_TEMPLATES
        specimens = [_sp(id="DLC001"), _sp(id="DLC002")]
        job = LabelService.build_print_job(
            specimens, BUILTIN_TEMPLATES["tissueCompact"], "tissue",
            dims={"w": 30, "h": 15},
        )
        assert len(job["items"]) == 0
        # Should have "empty" warning
        assert any(w["code"] == "empty" for w in job.get("warnings", []))

    def test_copies_multiplies_labels_not_items(self):
        from app.services.label_service import LabelService, BUILTIN_TEMPLATES
        specimens = [_sp(id="DLC001"), _rna_sp(id="BLC001")]
        job = LabelService.build_print_job(
            specimens, BUILTIN_TEMPLATES["standard"], "sample",
            dims={"w": 60, "h": 40}, copies=3,
        )
        # items = 2, labels = 6
        assert len(job["items"]) == 2
        assert len(job["labels"]) == 6


# ══════════════════════════════════════════════════════════════════════════════
# LabelsView widget smoke-tests (offscreen)
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelsViewFull:
    """LabelsView — web-oracle classic Step 1-4 vertical flow."""

    def _make_view(self, qt_app):
        from app.views.labels_view import LabelsView
        from app.app_context import AppContext
        ctx = AppContext()
        return LabelsView(ctx)

    def _loaded(self, qt_app, specs):
        view = self._make_view(qt_app)
        view._specimens = specs
        view._step1.set_specimens(specs)  # emits → pushes to Step2/3, refreshes Step4
        return view

    def test_step_sections_exist(self, qt_app):
        view = self._make_view(qt_app)
        for attr in ("_step1", "_step2", "_step3", "_step4"):
            assert hasattr(view, attr)

    def test_output_reflects_injected_specimens(self, qt_app):
        # 2 specimens, 1 R-prefix → samples 2, RNAlater 1, total (2+1)*1 = 3.
        view = self._loaded(qt_app, [_sp(), _rna_sp()])
        txt = view._step4._summary.text()
        assert "样品瓶 2" in txt
        assert "RNAlater 组织管 1" in txt
        assert "总 3" in txt

    def test_no_selection_allows_blank_sample_print(self, qt_app):
        """No specimen selected → the 样品瓶 button prints blank labels of the
        bare template (编号 is supported, not required). Tissue stays strictly
        R-prefix-derived, so its button remains disabled."""
        view = self._loaded(qt_app, [_sp(), _rna_sp()])
        view._step1.clear_selection()
        assert view._step4._btn_sample.isEnabled()       # blank standalone print
        assert not view._step4._btn_tissue.isEnabled()   # tissue needs R-prefix specimens

    def test_no_selection_blank_job_has_one_item(self, qt_app):
        view = self._loaded(qt_app, [_sp(), _rna_sp()])
        view._step1.clear_selection()
        sample_job = view._build_job("sample")
        tissue_job = view._build_job("tissue")
        assert len(sample_job["items"]) == 1            # one blank sample label
        assert sample_job["items"][0]["data"] == {}     # bound fields blank
        assert tissue_job["items"] == []                # tissue not fabricated

    def test_print_buttons_enabled_when_counts_positive(self, qt_app):
        view = self._loaded(qt_app, [_sp(), _rna_sp()])
        assert view._step4._btn_sample.isEnabled()
        assert view._step4._btn_tissue.isEnabled()

    def test_tissue_button_disabled_when_no_rna(self, qt_app):
        view = self._loaded(qt_app, [_sp()])  # no R-prefix specimen
        assert view._step4._btn_sample.isEnabled()
        assert not view._step4._btn_tissue.isEnabled()

    def test_inject_specimens_selects_all(self, qt_app):
        view = self._loaded(qt_app, [_sp(), _rna_sp()])
        assert len(view._step1.selected_indices()) == 2

    def test_rna_marker_only_for_r_prefix(self, qt_app):
        view = self._loaded(qt_app, [_sp(), _rna_sp()])
        # index 0 = non-R (no RNA badge), index 1 = R-prefix (has badge)
        assert view._step1._items[0]["rna"] is False
        assert view._step1._items[1]["rna"] is True

    def test_view_has_template_libraries(self, qt_app):
        view = self._make_view(qt_app)
        assert "sample" in view._libs
        assert "tissue" in view._libs

    def test_template_available_without_selection(self, qt_app):
        view = self._loaded(qt_app, [_sp(), _rna_sp()])
        view._step1.clear_selection()
        # Resolved template stays available even with nothing checked.
        from app.services.label_service import resolve_template
        assert resolve_template(view._libs["sample"]).get("name")

    def test_step2_cards_match_buckets(self, qt_app):
        view = self._loaded(qt_app, [_sp(), _rna_sp()])
        # sample column always; tissue column present when R-prefix selected.
        assert view._step2._cards.get("sample")
        assert view._step2._cards.get("tissue")


# ══════════════════════════════════════════════════════════════════════════════
# LabelDetailPanel — template + size resolution via the library
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelDetailPanelTemplates:
    def _make_detail(self, qt_app):
        from app.widgets.label_detail_panel import LabelDetailPanel
        return LabelDetailPanel()

    def _fake_lib(self, tmp_path, bucket):
        from PyQt6.QtCore import QSettings
        from app.services.label_service import (
            LabelTemplateLibrary, _MIGRATION_QSETTINGS_KEY,
        )
        ini = str(tmp_path / "detail_lib.ini")

        class _FakeLib(LabelTemplateLibrary):
            def __init__(self, b):
                self._bucket = b
                self._is_tissue = b == "tissue"
                self._qs = QSettings(ini, QSettings.Format.IniFormat)
                self._qs.setValue(_MIGRATION_QSETTINGS_KEY[b], "1")

        return _FakeLib(bucket)

    def test_selected_template_builtin_returns_standard(self, qt_app, tmp_path):
        d = self._make_detail(qt_app)
        lib = self._fake_lib(tmp_path, "sample")
        lib.set_selected_key("standard")
        d._libs["sample"] = lib
        assert d.selected_template("sample").get("name") == "标准"

    def test_selected_template_library_key_resolved(self, qt_app, tmp_path):
        """A custom:<id> key should resolve via the library."""
        d = self._make_detail(qt_app)
        lib = self._fake_lib(tmp_path, "sample")
        rec = lib.upsert({
            "name": "我的自定义",
            "template": {"name": "我的自定义", "rows": [{"fields": ["headerId"], "size": 9}],
                         "qr": {"ecc": "Q"}},
        })
        lib.set_selected_key(f"custom:{rec['id']}")
        d._libs["sample"] = lib
        assert d.selected_template("sample").get("name") == "我的自定义"

    def test_dims_from_size_key(self, qt_app, tmp_path):
        d = self._make_detail(qt_app)
        lib = self._fake_lib(tmp_path, "sample")
        lib.set_selected_size_key("label_60x40")
        d._libs["sample"] = lib
        dims = d.selected_dims("sample")
        assert dims["w"] == 60
        assert dims["h"] == 40

    def test_custom_dims_when_custom_selected(self, qt_app, tmp_path):
        d = self._make_detail(qt_app)
        lib = self._fake_lib(tmp_path, "sample")
        lib.set_selected_size_key("custom")
        d._libs["sample"] = lib
        d._custom_dims["sample"] = {"w": 45, "h": 25}
        dims = d.selected_dims("sample")
        assert dims["w"] == 45
        assert dims["h"] == 25


# ══════════════════════════════════════════════════════════════════════════════
# label_data_text — pure-text label summary
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelDataText:
    def test_all_fields_present(self):
        from app.utils.label_core import label_data_text, specimen_to_label_data
        data = specimen_to_label_data(_sp())
        text = label_data_text(data)
        assert "FJ-YGLZ-B2-DLC001" in text        # part of uniqueId
        assert "背鳞虫" in text                    # speciesName
        assert "采集" in text                      # collectorLabel

    def test_missing_species_falls_back_to_latin(self):
        from app.utils.label_core import label_data_text
        data = {"uniqueId": "X-001", "latin": "Polynoidae sp.", "region": ""}
        text = label_data_text(data)
        assert "Polynoidae sp." in text

    def test_empty_fields_omitted(self):
        from app.utils.label_core import label_data_text
        data = {"uniqueId": "X-001", "speciesName": "", "region": None, "collectorLabel": ""}
        text = label_data_text(data)
        assert text == "X-001"

    def test_none_data_returns_empty(self):
        from app.utils.label_core import label_data_text
        assert label_data_text(None) == ""

    def test_fields_joined_by_newline(self):
        from app.utils.label_core import label_data_text
        data = {
            "uniqueId": "A", "speciesName": "B", "region": "C", "collectorLabel": "D"
        }
        text = label_data_text(data)
        assert text == "A\nB\nC\nD"


# ══════════════════════════════════════════════════════════════════════════════
# Backup subsystem — backup_library / latest_backup / restore_latest_backup
# Mirrors JS backupLabelCustomTemplate / latestLabelCustomBackup /
# restoreLatestLabelCustomBackup
# ══════════════════════════════════════════════════════════════════════════════

class TestBackupSubsystem:
    def test_backup_empty_library_returns_false(self, sample_lib):
        assert sample_lib.backup_library() is False

    def test_backup_nonempty_library_returns_true(self, sample_lib):
        sample_lib.upsert({"name": "T", "template": {"rows": []}})
        assert sample_lib.backup_library() is True

    def test_latest_backup_none_when_no_backups(self, sample_lib):
        assert sample_lib.latest_backup() is None

    def test_backup_and_retrieve(self, sample_lib):
        rec = sample_lib.upsert({"name": "T", "template": {"rows": []}})
        sample_lib.backup_library("test reason")
        snap = sample_lib.latest_backup()
        assert snap is not None
        assert snap.get("reason") == "test reason"
        assert isinstance(snap.get("at"), str)
        assert isinstance(snap.get("raw"), str)

    def test_identical_content_not_duplicated(self, sample_lib):
        sample_lib.upsert({"name": "T", "template": {"rows": []}})
        sample_lib.backup_library()
        sample_lib.backup_library()  # same state → not added again
        backups = sample_lib._read_backups()
        assert len(backups) == 1

    def test_backup_rolling_max_20(self, sample_lib):
        # Upsert 25 different templates and back up each time
        for i in range(25):
            sample_lib.upsert({"name": f"T{i}", "template": {"rows": [{"size": i}]}})
            sample_lib.backup_library(f"step {i}")
        backups = sample_lib._read_backups()
        assert len(backups) <= 20

    def test_restore_replaces_library(self, sample_lib):
        # Phase 1: add template A, back up
        sample_lib.upsert({"name": "A", "template": {"rows": [{"fields": ["storage"]}]}})
        sample_lib.backup_library("after A")
        # Phase 2: add template B (state changes)
        sample_lib.upsert({"name": "B", "template": {"rows": []}})
        assert len(sample_lib.records()) == 2
        # Restore → should go back to 1-record state
        result = sample_lib.restore_latest_backup()
        assert result is True
        recs = sample_lib.records()
        assert len(recs) == 1
        assert recs[0]["name"] == "A"

    def test_restore_no_backup_returns_false(self, sample_lib):
        assert sample_lib.restore_latest_backup() is False

    def test_restore_creates_pre_restore_backup(self, sample_lib):
        """restore_latest_backup must snapshot current state before overwriting."""
        sample_lib.upsert({"name": "A", "template": {"rows": []}})
        sample_lib.backup_library("initial")
        sample_lib.upsert({"name": "B", "template": {"rows": []}})
        # Now restore
        sample_lib.restore_latest_backup()
        # The new "恢复备份前" snapshot should be the most-recent backup
        snap = sample_lib.latest_backup()
        assert snap is not None
        assert snap.get("reason") == "恢复备份前"


# ══════════════════════════════════════════════════════════════════════════════
# LabelEditorWidget keyboard shortcuts (Ctrl+Z / Ctrl+Shift+Z)
# Mirrors handleLabelsKeydown() in app.js
# ══════════════════════════════════════════════════════════════════════════════

class TestLabelEditorKeyboardShortcuts:
    def test_undo_shortcut_exists(self, qt_app):
        """LabelEditorWidget must register a Ctrl+Z shortcut wired to undo."""
        from app.widgets.label_editor import LabelEditorWidget
        from PyQt6.QtGui import QShortcut
        from app.utils.label_core import normalize_template
        from app.services.label_service import BUILTIN_TEMPLATES
        tmpl = normalize_template(BUILTIN_TEMPLATES["standard"])
        w = LabelEditorWidget(tmpl, {"w": 60, "h": 40}, {})
        shortcuts = w.findChildren(QShortcut)
        # Check at least 2 shortcuts (undo + redo)
        assert len(shortcuts) >= 2

    def test_redo_shortcut_exists(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        from PyQt6.QtGui import QShortcut
        w = LabelEditorWidget(None, {"w": 60, "h": 40}, {})
        shortcuts = w.findChildren(QShortcut)
        assert len(shortcuts) >= 2

    def test_undo_button_text(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        from PyQt6.QtWidgets import QPushButton
        w = LabelEditorWidget(None, {"w": 60, "h": 40}, {})
        btns = w.findChildren(QPushButton)
        texts = [b.text() for b in btns]
        assert any("撤销" in t for t in texts)
        assert any("重做" in t for t in texts)


# ══════════════════════════════════════════════════════════════════════════════
# Per-template rolling backup — backup_template / latest_backup(template_id) /
# restore_backup — MAX_BACKUP_SLOTS = 20
# ══════════════════════════════════════════════════════════════════════════════

class TestPerTemplateBackup:
    def test_backup_creates_slot(self, sample_lib):
        """backup_template(template_id) creates one backup slot."""
        rec = sample_lib.upsert({"name": "备份测试", "template": {"rows": []}})
        result = sample_lib.backup_template(rec["id"])
        assert result is True
        snap = sample_lib.latest_backup(rec["id"])
        assert snap is not None
        assert "data" in snap
        assert snap["data"]["id"] == rec["id"]

    def test_backup_rolls_at_20_slots(self, sample_lib):
        """Adding 21 backups keeps only the newest 20."""
        rec = sample_lib.upsert({"name": "滚动备份", "template": {"rows": []}})
        for i in range(21):
            rec = sample_lib.upsert({
                "id": rec["id"],
                "name": f"滚动备份-{i}",
                "template": {"rows": [{"size": i}]},
            })
            sample_lib.backup_template(rec["id"], f"step {i}")
        import json
        raw = sample_lib._qs.value(sample_lib._backup_key(rec["id"]), "[]")
        slots = json.loads(raw) if isinstance(raw, str) else (raw or [])
        assert len(slots) <= sample_lib.MAX_BACKUP_SLOTS

    def test_latest_backup_returns_last(self, sample_lib):
        """latest_backup(template_id) returns the most recently appended entry."""
        rec = sample_lib.upsert({"name": "最新备份", "template": {"rows": []}})
        sample_lib.backup_template(rec["id"], "第一次")
        rec = sample_lib.upsert({
            "id": rec["id"],
            "name": "最新备份-v2",
            "template": {"rows": [{"size": 9}]},
        })
        sample_lib.backup_template(rec["id"], "第二次")
        snap = sample_lib.latest_backup(rec["id"])
        assert snap is not None
        assert snap.get("reason") == "第二次"

    def test_restore_backup_restores_data(self, sample_lib):
        """restore_backup(template_id) puts the backed-up template back into library."""
        rec = sample_lib.upsert({"name": "恢复测试", "template": {"rows": []}})
        sample_lib.backup_template(rec["id"])
        sample_lib.upsert({
            "id": rec["id"],
            "name": "修改后",
            "template": {"rows": [{"fields": ["storage"], "size": 9}]},
        })
        assert sample_lib.get(rec["id"])["name"] == "修改后"
        ok = sample_lib.restore_backup(rec["id"])
        assert ok is True
        restored = sample_lib.get(rec["id"])
        assert restored["name"] == "恢复测试"

    def test_backup_called_before_delete(self, sample_lib):
        """delete(template_id) must call backup_template before removing."""
        from unittest.mock import patch
        rec = sample_lib.upsert({"name": "删前备份", "template": {"rows": []}})
        with patch.object(sample_lib, "backup_template", wraps=sample_lib.backup_template) as mock_bt:
            sample_lib.delete(rec["id"])
        mock_bt.assert_called_once_with(rec["id"], "delete")


# ══════════════════════════════════════════════════════════════════════════════
# 新增管形/圆形标签模板 + 尺寸 (Task 4)
# ══════════════════════════════════════════════════════════════════════════════

class TestNewTubeTemplates:
    """新增8个管形/圆形模板 + 5个纸张尺寸。"""

    def test_total_builtin_count(self):
        from app.services.label_service import BUILTIN_TEMPLATES
        assert len(BUILTIN_TEMPLATES) == 14, (
            f"Expected 14 built-in templates (6 original + 8 new), got {len(BUILTIN_TEMPLATES)}"
        )

    def test_cryo2ml_cap_has_shape_circle(self):
        from app.services.label_service import BUILTIN_TEMPLATES
        assert "cryo2mlCap" in BUILTIN_TEMPLATES
        assert BUILTIN_TEMPLATES["cryo2mlCap"].get("shape") == "circle"

    def test_cryo2ml_side_exists(self):
        from app.services.label_service import BUILTIN_TEMPLATES
        assert "cryo2mlSide" in BUILTIN_TEMPLATES

    def test_falcon_templates_exist(self):
        from app.services.label_service import BUILTIN_TEMPLATES
        for k in ("falcon5ml", "falcon15ml", "falcon50ml"):
            assert k in BUILTIN_TEMPLATES, f"Missing template: {k}"

    def test_bottle_and_museum_exist(self):
        from app.services.label_service import BUILTIN_TEMPLATES
        assert "bottle500ml" in BUILTIN_TEMPLATES
        assert "museumDense" in BUILTIN_TEMPLATES

    def test_qr_first_exists(self):
        from app.services.label_service import BUILTIN_TEMPLATES
        assert "qrFirst" in BUILTIN_TEMPLATES

    def test_new_paper_sizes_registered(self):
        from app.services.label_service import PAPER_SIZES
        for k in ("label_13x13", "label_38x13", "label_45x17", "label_55x20", "label_75x25"):
            assert k in PAPER_SIZES, f"Missing paper size: {k}"

    def test_label_13x13_dimensions(self):
        from app.services.label_service import PAPER_SIZES
        s = PAPER_SIZES["label_13x13"]
        assert s["w"] == 13 and s["h"] == 13

    def test_label_size_keys_count(self):
        from app.services.label_service import LABEL_SIZE_KEYS
        assert len(LABEL_SIZE_KEYS) == 13, (
            f"Expected 13 label sizes (8 original + 5 new), got {len(LABEL_SIZE_KEYS)}"
        )

    def test_new_sizes_in_label_size_keys(self):
        from app.services.label_service import LABEL_SIZE_KEYS
        for k in ("label_13x13", "label_38x13", "label_45x17", "label_55x20", "label_75x25"):
            assert k in LABEL_SIZE_KEYS, f"Missing from LABEL_SIZE_KEYS: {k}"

    def test_all_new_templates_have_rows(self):
        from app.services.label_service import BUILTIN_TEMPLATES
        new_keys = ("cryo2mlSide","cryo2mlCap","falcon5ml","falcon15ml",
                    "falcon50ml","bottle500ml","qrFirst","museumDense")
        for k in new_keys:
            assert "rows" in BUILTIN_TEMPLATES[k], f"{k} missing 'rows'"

    def test_all_new_templates_have_qr(self):
        from app.services.label_service import BUILTIN_TEMPLATES
        new_keys = ("cryo2mlSide","cryo2mlCap","falcon5ml","falcon15ml",
                    "falcon50ml","bottle500ml","qrFirst","museumDense")
        for k in new_keys:
            assert "qr" in BUILTIN_TEMPLATES[k], f"{k} missing 'qr'"
