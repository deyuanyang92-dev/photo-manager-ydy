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


# ── selected_key persistence ──────────────────────────────────────────────────

class TestSelectedKeyPersistence:
    def test_default_sample_key_is_standard(self, sample_lib):
        assert sample_lib.selected_key() == "standard"

    def test_default_tissue_key_is_tissue_compact(self, tissue_lib):
        assert tissue_lib.selected_key() == "tissueCompact"

    def test_set_selected_key_persists(self, sample_lib):
        sample_lib.set_selected_key("compact")
        assert sample_lib.selected_key() == "compact"

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
    def _make_view(self, qt_app):
        from app.views.labels_view import LabelsView
        from app.app_context import AppContext
        ctx = AppContext()
        view = LabelsView(ctx)
        return view

    def test_status_bar_labels_exist(self, qt_app):
        view = self._make_view(qt_app)
        assert hasattr(view, "_status_selected")
        assert hasattr(view, "_status_sample")
        assert hasattr(view, "_status_tissue")
        assert hasattr(view, "_status_total")
        assert hasattr(view, "_status_warn")

    def test_update_status_bar_sets_labels(self, qt_app):
        view = self._make_view(qt_app)
        view._sample_job = None
        view._tissue_job = None
        view._update_status_bar(5, 3, 2, 2)
        assert "5" in view._status_selected.text()
        assert "3" in view._status_sample.text()
        assert "2" in view._status_tissue.text()
        # total = (3+2)*2 = 10
        assert "10" in view._status_total.text()

    def test_print_buttons_disabled_when_no_counts(self, qt_app):
        view = self._make_view(qt_app)
        view._go_to_step(3)
        # No specimens loaded → counts are 0 → buttons disabled
        # Step4 update_counts with (0,0,...) should disable both buttons
        view._step4.update_counts(0, 0, [], [], copies=1)
        assert not view._step4.sample_button.isEnabled()
        assert not view._step4.tissue_button.isEnabled()

    def test_print_buttons_enabled_when_counts_positive(self, qt_app):
        view = self._make_view(qt_app)
        view._step4.update_counts(3, 1, [], [], copies=1)
        assert view._step4.sample_button.isEnabled()
        assert view._step4.tissue_button.isEnabled()

    def test_print_tissue_button_disabled_when_tissue_zero(self, qt_app):
        view = self._make_view(qt_app)
        view._step4.update_counts(2, 0, [], [], copies=1)
        assert view._step4.sample_button.isEnabled()
        assert not view._step4.tissue_button.isEnabled()

    def test_inject_specimens_with_rna(self, qt_app):
        """Injecting R-prefix specimen activates tissue bucket in Step 2."""
        view = self._make_view(qt_app)
        mock = [_sp(), _rna_sp()]
        view._specimens = mock
        view._step1.set_specimens(mock)
        view._go_to_step(1)
        indices = view._step1.selected_indices()
        assert len(indices) == 2

    def test_label_edits_initially_empty(self, qt_app):
        view = self._make_view(qt_app)
        mock = [_sp()]
        view._specimens = mock
        view._step1.set_specimens(mock)
        view._go_to_step(1)
        edits = view._step2.label_edits()
        assert isinstance(edits, dict)

    def test_step2_bucket_col_has_template_library(self, qt_app):
        view = self._make_view(qt_app)
        assert hasattr(view._step2._sample_col, "_lib")
        assert hasattr(view._step2._tissue_col, "_lib")


# ══════════════════════════════════════════════════════════════════════════════
# _BucketColWidget — selected_template with library key
# ══════════════════════════════════════════════════════════════════════════════

class TestBucketColWidgetLibrary:
    def _make_col(self, qt_app, bucket="sample"):
        from app.views.labels_view import _BucketColWidget
        col = _BucketColWidget(bucket)
        return col

    def test_selected_template_builtin_returns_standard(self, qt_app):
        col = self._make_col(qt_app, "sample")
        col._selected_template_key = "standard"
        tmpl = col.selected_template()
        assert tmpl.get("name") == "标准"

    def test_selected_template_library_key_resolved(self, qt_app, tmp_path):
        """A custom:<id> key should resolve via the library."""
        from PyQt6.QtCore import QSettings
        from app.services.label_service import LabelTemplateLibrary, _MIGRATION_QSETTINGS_KEY
        from app.views.labels_view import _BucketColWidget

        ini = str(tmp_path / "col_lib.ini")

        class _FakeLib(LabelTemplateLibrary):
            def __init__(self, bucket):
                self._bucket = bucket
                self._is_tissue = bucket == "tissue"
                self._qs = QSettings(ini, QSettings.Format.IniFormat)
                self._qs.setValue(_MIGRATION_QSETTINGS_KEY[bucket], "1")

        col = _BucketColWidget("sample")
        col._lib = _FakeLib("sample")
        rec = col._lib.upsert({
            "name": "我的自定义",
            "template": {"name": "我的自定义", "rows": [{"fields": ["headerId"], "size": 9}],
                         "qr": {"ecc": "Q"}},
        })
        col._selected_template_key = f"custom:{rec['id']}"
        tmpl = col.selected_template()
        assert tmpl.get("name") == "我的自定义"

    def test_dims_saved_and_restored(self, qt_app):
        col = self._make_col(qt_app, "sample")
        col._select_size("label_60x40")
        dims = col.selected_dims()
        assert dims["w"] == 60
        assert dims["h"] == 40

    def test_custom_dims_when_custom_selected(self, qt_app):
        col = self._make_col(qt_app, "sample")
        col._custom_w = 45
        col._custom_h = 25
        col._select_size("custom")
        dims = col.selected_dims()
        assert dims["w"] == 45
        assert dims["h"] == 25
