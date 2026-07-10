"""tests/test_summary_column_picker.py — 数据汇总列选择."""
from __future__ import annotations

import json

import pytest

from app.config.settings import AppSettings
from app.services import cross_workspace_query_service as cwq


def test_settings_persist_summary_visible_columns(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QSettings

    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    settings = AppSettings()
    settings.project_tree_summary_visible_columns = ["uid", "lon", "lat"]
    settings.flush_to_disk()
    again = AppSettings()
    assert again.project_tree_summary_visible_columns == ["uid", "lon", "lat"]


def test_column_picker_selected_keys_order(qtbot) -> None:
    from app.widgets.summary_column_picker_dialog import SummaryColumnPickerDialog

    all_cols = [
        ("uid", "编号"),
        ("_workspace_label", "工作区"),
        ("lon", "经度"),
        ("lat", "纬度"),
    ]
    dlg = SummaryColumnPickerDialog(all_cols, ["lat", "uid"])
    assert dlg.selected_keys() == ["lat", "uid"]
    dlg._checks["lon"].setChecked(True)
    assert dlg.selected_keys() == ["lat", "uid", "lon"]
    item = dlg._order_list.item(0)
    dlg._order_list.takeItem(0)
    dlg._order_list.insertItem(1, item)
    assert dlg.selected_keys() == ["uid", "lat", "lon"]


def test_default_visible_includes_tif_absolute_path() -> None:
    assert "photo_absolute_path" in cwq.DEFAULT_SUMMARY_VISIBLE_KEYS
    assert cwq.DEFAULT_SUMMARY_VISIBLE_KEYS.index("photo_absolute_path") == 1


def test_group_summary_columns_orders_by_category() -> None:
    from app.config.specimen_fields import group_summary_columns, summary_field_category

    cols = [
        ("scientific_name", "学名"),
        ("uid", "编号"),
        ("lon", "经度"),
        ("storage", "保存"),
        ("photographer", "拍摄人"),
        ("photo_absolute_path", "照片绝对路径"),
        ("camera_model", "相机型号"),
        ("custom_tag", "自定义"),
    ]
    groups = group_summary_columns(cols)
    assert [g["id"] for g in groups] == [
        "naming_identity",
        "voucher",
        "taxon",
        "metadata",
        "camera",
        "other",
    ]
    assert summary_field_category("storage") == "naming_identity"
    assert summary_field_category("photographer") == "metadata"
    assert summary_field_category("camera_model") == "camera"
    assert summary_field_category("photo_absolute_path") == "camera"
    assert summary_field_category("custom_tag") == "other"


def test_category_header_toggles_group(qtbot) -> None:
    from app.widgets.summary_column_picker_dialog import SummaryColumnPickerDialog

    all_cols = [
        ("uid", "编号"),
        ("lon", "经度"),
        ("lat", "纬度"),
        ("scientific_name", "学名"),
    ]
    dlg = SummaryColumnPickerDialog(all_cols, ["uid"])
    metadata_id = next(g["id"] for g in dlg._groups if g["id"] == "metadata")
    dlg._on_category_clicked(metadata_id)
    assert dlg._checks["lon"].isChecked()
    assert dlg._checks["lat"].isChecked()
    dlg._on_category_clicked(metadata_id)
    assert not dlg._checks["lon"].isChecked()
    assert not dlg._checks["lat"].isChecked()

