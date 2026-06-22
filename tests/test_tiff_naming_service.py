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
