from __future__ import annotations

import os
from pathlib import Path


def test_inspect_tiff_names_marks_nonconforming_files_and_suggests_names(tmp_path):
    """A legacy folder reports valid and invalid TIFF names in time order."""
    from app.services.tiff_naming_service import inspect_tiff_names

    uid = "FJ-XM-B2-DLC001-T95E-20260601"
    invalid = tmp_path / "HeliconFocus.tif"
    valid = tmp_path / "FJ-XM-B2-DLC001-2-T95E-20260601.tif"
    invalid.write_bytes(b"tif")
    valid.write_bytes(b"tif")
    os.utime(invalid, ns=(1_700_000_000_000_000_000,) * 2)
    os.utime(valid, ns=(1_700_000_001_000_000_000,) * 2)

    result = inspect_tiff_names(str(tmp_path), current_uid=uid)

    assert result.total == 2
    assert result.valid_count == 1
    assert result.invalid_count == 1
    assert [item.name for item in result.items] == [invalid.name, valid.name]
    assert result.items[0].valid is False
    assert result.items[0].reason.startswith("未识别到标本唯一编号")
    assert result.items[0].suggested_name == (
        "FJ-XM-B2-DLC001-3-T95E-20260601.tif"
    )
    assert result.items[1].valid is True
    assert result.items[1].uid == uid
    assert result.items[1].sequence == 2


def test_inspect_tiff_paths_checks_single_file(tmp_path):
    """The naming audit can inspect an explicit single TIFF path."""
    from app.services.tiff_naming_service import inspect_tiff_paths

    tiff = tmp_path / "GXFCG-BLW-JinSC003-2-R-20260618-广西防城港-白龙尾.tif"
    tiff.write_bytes(b"tif")

    result = inspect_tiff_paths([str(tiff)])

    assert result.total == 1
    assert result.valid_count == 1
    assert result.items[0].name == tiff.name
    assert result.items[0].uid == "GXFCG-BLW-JINSC003-R-20260618"
    assert result.items[0].sequence == 2


def test_tiff_naming_audit_dialog_marks_invalid_row(qtbot, tmp_path):
    """The independent audit dialog visibly marks nonconforming TIFFs."""
    from app.services.tiff_naming_service import inspect_tiff_names
    from app.widgets.tiff_naming_audit_dialog import TiffNamingAuditDialog

    (tmp_path / "HeliconFocus.tif").write_bytes(b"tif")
    audit = inspect_tiff_names(str(tmp_path), current_uid="SPECIMEN-001")
    dialog = TiffNamingAuditDialog(audit)
    qtbot.addWidget(dialog)

    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 2).text() == "不符合"
    assert dialog._table.item(0, 4).text().endswith(".tif")


def test_tiff_naming_audit_dialog_only_applies_selected_valid_tiff(qtbot, tmp_path):
    """Only a recognized result can be sent to the editable right rail."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDialog

    from app.services.tiff_naming_service import inspect_tiff_names
    from app.widgets.tiff_naming_audit_dialog import TiffNamingAuditDialog

    invalid = tmp_path / "HeliconFocus.tif"
    valid = tmp_path / "GXFCG-BLW-SC003-2-R-20260618.tif"
    invalid.write_bytes(b"tif")
    valid.write_bytes(b"tif")
    os.utime(invalid, ns=(1_700_000_000_000_000_000,) * 2)
    os.utime(valid, ns=(1_700_000_001_000_000_000,) * 2)
    dialog = TiffNamingAuditDialog(inspect_tiff_names(str(tmp_path)))
    qtbot.addWidget(dialog)

    dialog._table.selectRow(0)
    assert dialog._apply_btn.isEnabled() is False

    dialog._table.selectRow(1)
    assert dialog._apply_btn.isEnabled() is True
    qtbot.mouseClick(dialog._apply_btn, Qt.MouseButton.LeftButton)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_tiff_path() == str(valid.resolve())


def test_tiff_naming_check_auto_applies_one_valid_result(tmp_path, monkeypatch):
    """A single recognized TIFF pre-fills the right rail without another click."""
    from PyQt6.QtWidgets import QDialog

    from app.views.workbench_monitor_workflow import WorkbenchMonitorWorkflowMixin

    tiff = tmp_path / "GXFCG-BLW-SC003-2-R-20260618.tif"
    tiff.write_bytes(b"tif")
    captured = {}

    class FakeDialog:
        def __init__(self, audit, parent=None):
            captured["audit"] = audit

        def mark_auto_applied(self, path):
            captured["auto_applied_path"] = path

        def exec(self):
            return QDialog.DialogCode.Rejected

        def selected_tiff_path(self):
            return None

    monkeypatch.setattr(
        "app.widgets.tiff_naming_audit_dialog.TiffNamingAuditDialog",
        FakeDialog,
    )

    class Context:
        current_project_dir = str(tmp_path)

        @staticmethod
        def get_db():
            return None

    class Grouping:
        _uid = None

    class Harness(WorkbenchMonitorWorkflowMixin):
        ctx = Context()
        _grouping = Grouping()

        def __init__(self):
            self.applied = []

        def _apply_tiff_filename_recognition(self, path, *, overwrite=False):
            self.applied.append((path, overwrite))

    harness = Harness()
    harness._run_tiff_naming_check(paths=[str(tiff)])

    expected = str(tiff.resolve())
    assert harness.applied == [(expected, False)]
    assert captured["auto_applied_path"] == expected


def test_tiff_naming_check_applies_selected_result_from_many(tmp_path, monkeypatch):
    """Multiple recognized TIFFs require an explicit row choice."""
    from PyQt6.QtWidgets import QDialog

    from app.views.workbench_monitor_workflow import WorkbenchMonitorWorkflowMixin

    first = tmp_path / "GXFCG-BLW-SC003-1-R-20260618.tif"
    second = tmp_path / "GXFCG-BLW-SC004-1-R-20260618.tif"
    first.write_bytes(b"tif")
    second.write_bytes(b"tif")
    selected = str(second.resolve())

    class FakeDialog:
        def __init__(self, audit, parent=None):
            assert audit.valid_count == 2

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_tiff_path(self):
            return selected

    monkeypatch.setattr(
        "app.widgets.tiff_naming_audit_dialog.TiffNamingAuditDialog",
        FakeDialog,
    )

    class Context:
        current_project_dir = str(tmp_path)

        @staticmethod
        def get_db():
            return None

    class Grouping:
        _uid = None

    class Harness(WorkbenchMonitorWorkflowMixin):
        ctx = Context()
        _grouping = Grouping()

        def __init__(self):
            self.applied = []

        def _apply_tiff_filename_recognition(self, path, *, overwrite=False):
            self.applied.append((path, overwrite))

    harness = Harness()
    harness._run_tiff_naming_check(paths=[str(first), str(second)])

    assert harness.applied == [(selected, False)]


def test_inspect_tiff_names_honors_custom_naming_components(tmp_path):
    """Custom project naming rules accept stems built without station."""
    from app.services.tiff_naming_service import inspect_tiff_names
    from app.utils.naming import build_configured_result_id

    components = ["province", "site", "species_id", "storage", "date_seg"]
    values = {
        "province": "FJ",
        "site": "XM",
        "species_id": "DLC001",
        "storage": "T95E",
        "date_seg": "20260601",
    }
    uid = "FJ-XM-DLC001-T95E-20260601"
    valid = tmp_path / f"{build_configured_result_id(components, values, seq=2)}.tif"
    invalid = tmp_path / "HeliconFocus.tif"
    valid.write_bytes(b"tif")
    invalid.write_bytes(b"tif")
    os.utime(invalid, ns=(1_700_000_000_000_000_000,) * 2)
    os.utime(valid, ns=(1_700_000_001_000_000_000,) * 2)

    result = inspect_tiff_names(
        str(tmp_path),
        current_uid=uid,
        naming_components=components,
        specimen_values=values,
    )

    assert result.valid_count == 1
    assert result.invalid_count == 1
    assert result.items[0].valid is False
    assert result.items[1].valid is True
    assert result.items[1].uid == uid
    assert result.items[1].sequence == 2
    assert result.items[0].suggested_name == (
        f"{build_configured_result_id(components, values, seq=3)}.tif"
    )


def test_export_tiff_naming_audit_csv_writes_uid_column(tmp_path):
    from app.services.tiff_naming_service import (
        TiffNameItem,
        TiffNamingAudit,
        export_tiff_naming_audit_csv,
    )

    audit = TiffNamingAudit(
        folder=str(tmp_path),
        rules_summary="项目命名规则：测试",
        items=[
            TiffNameItem(
                path=str(tmp_path / "a.tif"),
                name="a.tif",
                modified_at="2026-06-01T00:00:00+00:00",
                valid=True,
                uid="FJ-XM-DLC001-T95E-20260601",
                sequence=1,
            ),
        ],
    )
    out = tmp_path / "audit.csv"
    export_tiff_naming_audit_csv(audit, str(out))
    text = out.read_text(encoding="utf-8-sig")
    assert "解析标本编号" in text
    assert "FJ-XM-DLC001-T95E-20260601" in text


def test_parse_tiff_result_stem_ignores_trailing_collector_photographer_suffix():
    """Core result name + 采集人-拍摄人 suffix still yields uid + sequence."""
    from app.utils.naming import parse_tiff_result_stem

    stem = "gxhp-sl-dlc001-1-r-20260712-杨等采集-杨拍"
    components = ["province", "site", "species_id", "storage", "date_seg"]
    uid, seq = parse_tiff_result_stem(stem, components)

    assert uid == "GXHP-SL-DLC001-R-20260712"
    assert seq == 1


def test_inspect_tiff_names_accepts_collector_photographer_suffix(tmp_path):
    from app.services.tiff_naming_service import inspect_tiff_names

    name = "gxhp-sl-dlc001-1-r-20260712-杨等采集-杨拍.tif"
    (tmp_path / name).write_bytes(b"tif")
    components = ["province", "site", "species_id", "storage", "date_seg"]

    result = inspect_tiff_names(
        str(tmp_path),
        naming_components=components,
    )

    assert result.valid_count == 1
    assert result.items[0].uid == "GXHP-SL-DLC001-R-20260712"
    assert result.items[0].sequence == 1


def test_parse_tiff_result_detail_allows_arbitrary_suffix_after_core():
    from app.utils.naming import parse_tiff_result_detail

    stem = "gxhp-sl-dlc001-1-r-20260712-杨等采集-杨拍-临时备注-foo"
    components = ["province", "site", "species_id", "storage", "date_seg"]
    detail = parse_tiff_result_detail(stem, components)

    assert detail is not None
    assert detail.uid == "GXHP-SL-DLC001-R-20260712"
    assert detail.sequence == 1
    assert detail.has_extra_suffix is True
    assert detail.core_stem == "gxhp-sl-dlc001-1-r-20260712"


def test_parse_legacy_inline_chinese_descriptor_before_date():
    from app.utils.naming import recognize_tiff_filename, tiff_stem_is_recognizable

    stem = "GXFCG-BLW-SC001-1-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    components = [
        "province", "site", "station", "species_id", "storage", "date_seg",
    ]
    assert tiff_stem_is_recognizable(stem, components)
    rec = recognize_tiff_filename(stem, components)
    assert rec is not None
    assert rec.sequence == 1
    assert rec.field_values["species_id"] == "SC001"
    assert rec.field_values["storage"] == "260618"
    assert rec.inline_labels == ("广西防城港", "白龙尾", "独齿沙蚕")


def test_parse_inline_descriptor_with_existing_core_date_does_not_duplicate_date():
    from app.utils.naming import recognize_tiff_filename

    stem = "GXFCG-BLW-SC004-2-R-20260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    components = [
        "province", "site", "station", "species_id", "storage", "date_seg",
    ]
    rec = recognize_tiff_filename(stem, components)

    assert rec is not None
    assert rec.uid == "GXFCG-BLW-SC004-R-20260618"
    assert rec.field_values["date_seg"] == "20260618"


def test_parse_inline_descriptor_with_storage_and_legacy_short_date():
    from app.utils.naming import recognize_tiff_filename

    stem = "GXFCG-BLW-SC002-2-R-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    components = [
        "province", "site", "station", "species_id", "storage", "date_seg",
    ]
    rec = recognize_tiff_filename(stem, components)

    assert rec is not None
    assert rec.uid == "GXFCG-BLW-SC002-R-20260618"
    assert rec.sequence == 2
    assert rec.field_values["storage"] == "R"
    assert rec.field_values["date_seg"] == "20260618"
    assert rec.inline_labels == ("260618", "广西防城港", "白龙尾", "独齿沙蚕")


def test_tiff_stem_fully_conforms_requires_exact_core():
    from app.utils.naming import tiff_stem_fully_conforms, tiff_stem_is_recognizable

    components = [
        "province", "site", "station", "species_id", "storage", "date_seg",
    ]
    legacy = "GXFCG-BLW-SC001-1-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    core = "GXFCG-BLW-SC001-1-260618-20260618"
    standard = "FJ-XM-B2-DLC001-1-T95E-20260601"

    assert tiff_stem_is_recognizable(legacy, components)
    assert not tiff_stem_fully_conforms(legacy, components)
    assert tiff_stem_fully_conforms(core, components)
    assert tiff_stem_fully_conforms(standard, components)


def test_tiff_stem_needs_rename_only_when_storage_must_be_patched():
    from app.utils.naming import tiff_stem_needs_rename_for_organize

    components = [
        "province", "site", "station", "species_id", "storage", "date_seg",
    ]
    legacy = "GXFCG-BLW-SC001-1-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    uid = "GXFCG-BLW-SC001-20260618"

    assert not tiff_stem_needs_rename_for_organize(
        legacy, components, panel_uid=uid, panel_storage="",
    )
    assert tiff_stem_needs_rename_for_organize(
        legacy, components, panel_uid=uid, panel_storage="D79",
    )
    assert tiff_stem_needs_rename_for_organize(
        "HeliconFocus", components, panel_uid=uid, panel_storage="D79",
    )


def test_suggest_tiff_filename_preserve_legacy_inserts_storage():
    from app.utils.naming import suggest_tiff_filename_preserve_legacy

    components = [
        "province", "site", "station", "species_id", "storage", "date_seg",
    ]
    stem = "GXFCG-BLW-SC001-1-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    suggested = suggest_tiff_filename_preserve_legacy(
        stem,
        components,
        {
            "province": "GXFCG",
            "site": "BLW",
            "species_id": "SC001",
            "storage": "D79",
            "date_seg": "20260618",
        },
        seq=1,
    )
    assert suggested == (
        "GXFCG-BLW-SC001-1-D79-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    )


def test_suggest_tiff_filename_preserve_legacy_replaces_old_storage():
    from app.utils.naming import suggest_tiff_filename_preserve_legacy

    components = [
        "province", "site", "station", "species_id", "storage", "date_seg",
    ]
    stem = "GXFCG-BLW-SC002-2-R-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    suggested = suggest_tiff_filename_preserve_legacy(
        stem,
        components,
        {
            "province": "GXFCG",
            "site": "BLW",
            "species_id": "SC002",
            "storage": "RD79",
            "date_seg": "20260618",
        },
        seq=2,
    )
    assert suggested == (
        "GXFCG-BLW-SC002-2-RD79-260618-广西防城港-白龙尾-独齿沙蚕-20260618"
    )


def test_legacy_filename_photo_notes_archives_unmapped_segments():
    from app.utils.naming import legacy_filename_photo_notes

    text = legacy_filename_photo_notes(
        ["广西防城港", "白龙尾", "独齿沙蚕"],
        date_suffix="20260618",
    )
    assert text == "广西防城港-白龙尾-独齿沙蚕-20260618"


def test_apply_recognized_fields_single_date_fills_both(qtbot):
    from unittest.mock import MagicMock

    from app.widgets.naming_panel import NamingPanel

    panel = NamingPanel(MagicMock())
    qtbot.addWidget(panel)
    panel.apply_recognized_fields(
        {"province": "GXFCG", "site": "BLW", "species_id": "SC001", "storage": "260618"},
        collection_date="20260618",
        photo_date="",
        sequence=1,
        inline_labels=("广西防城港", "白龙尾", "独齿沙蚕"),
        source_filename="demo.tif",
    )
    assert panel._collection_date.text() == "20260618"
    assert panel._photo_date.text() == "20260618"
    assert panel._photo_notes.toPlainText() == "广西防城港-白龙尾-独齿沙蚕-20260618"


def test_apply_recognized_fields_preserves_manual_values_and_stays_editable(qtbot):
    """Recognition is an editable suggestion and never locks or replaces input."""
    from unittest.mock import MagicMock

    from app.widgets.naming_panel import NamingPanel

    panel = NamingPanel(MagicMock())
    qtbot.addWidget(panel)
    panel._province.setText("MANUAL")

    panel.apply_recognized_fields(
        {"province": "GXFCG", "site": "BLW", "species_id": "SC001", "storage": "R"},
        collection_date="20260618",
        photo_date="20260618",
        sequence=1,
        source_filename="demo.tif",
    )

    assert panel._province.text() == "MANUAL"
    assert panel._site.text() == "BLW"
    panel._site.setText("CORRECTED")
    assert panel._site.text() == "CORRECTED"
    assert panel._site.isReadOnly() is False


def test_parse_tiff_result_detail_accepts_dual_date_segment(tmp_path):
    from app.services.tiff_naming_service import inspect_tiff_names

    name = "gxhp-sl-dlc001-1-r-20250601-20261203-备注.tif"
    (tmp_path / name).write_bytes(b"tif")
    components = ["province", "site", "species_id", "storage", "date_seg"]

    result = inspect_tiff_names(str(tmp_path), naming_components=components)

    assert result.valid_count == 1
    assert result.items[0].uid == "GXHP-SL-DLC001-R-20250601-20261203"
    assert result.items[0].sequence == 1


def test_coalesce_specimen_dates_single_field_means_same_day():
    from app.utils.naming import coalesce_specimen_dates, specimen_date_seg

    assert coalesce_specimen_dates("20261203", "") == ("20261203", "20261203")
    assert coalesce_specimen_dates("", "20261203") == ("20261203", "20261203")
    col, photo = coalesce_specimen_dates("20260501", "20260601")
    assert specimen_date_seg(col, photo) == "20260501-0601"
