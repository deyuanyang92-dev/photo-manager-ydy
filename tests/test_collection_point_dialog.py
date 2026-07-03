"""test_collection_point_dialog.py — 采集地图 新增/绑定采集点对话框.

Headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import os

import pytest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from app.widgets.collection_point_dialog import CollectionPointDialog  # noqa: E402

_MSGBOX = "app.widgets.collection_point_dialog.QMessageBox"


def _stations():
    return [
        {"province": "ZJ", "site": "SMW", "station": "B2",
         "station_label": "北滩二区", "count": 2},
        {"province": "ZJ", "site": "SMW", "station": "H1",
         "station_label": "", "count": 1},
    ]


class TestCollectionPointDialog:
    def test_new_mode_produces_record_dict(self):
        dlg = CollectionPointDialog(
            lon=121.654321, lat=28.123456,
            province="ZJ", site="SMW", zone="intertidal",
            stations=_stations(), mode="new",
        )
        dlg._station_edit.setText("B5")
        dlg._date_edit.setText("20260622")
        dlg._accept_valid_collection_point()
        r = dlg.result_point()
        assert r is not None
        assert r["action"] == "new"
        assert r["province"] == "ZJ" and r["site"] == "SMW"
        assert r["station"] == "B5"
        assert r["collection_date"] == "20260622"
        assert r["zone"] == "intertidal"
        assert abs(r["lon"] - 121.654321) < 1e-6
        assert abs(r["lat"] - 28.123456) < 1e-6

    def test_bind_mode_produces_station_dict(self):
        dlg = CollectionPointDialog(
            lon=122.0, lat=30.0,
            province="ZJ", site="SMW", zone="intertidal",
            stations=_stations(), mode="bind",
        )
        assert dlg._rb_bind.isChecked()
        # 默认选第一站 B2
        dlg._accept_valid_collection_point()
        r = dlg.result_point()
        assert r["action"] == "bind"
        assert r["province"] == "ZJ" and r["site"] == "SMW" and r["station"] == "B2"
        assert abs(r["lon"] - 122.0) < 1e-6

    def test_new_mode_missing_station_blocks_accept(self):
        dlg = CollectionPointDialog(
            lon=121.0, lat=29.0, province="ZJ", site="SMW",
            zone="intertidal", stations=_stations(), mode="new",
        )
        dlg._station_edit.setText("")   # 缺站位
        with patch(_MSGBOX):
            dlg._accept_valid_collection_point()
        assert dlg.result_point() is None   # 未确认

    def test_invalid_coord_blocks_accept(self):
        dlg = CollectionPointDialog(
            lon=121.0, lat=29.0, province="ZJ", site="SMW",
            zone="intertidal", stations=_stations(), mode="new",
        )
        dlg._station_edit.setText("B5")
        dlg._date_edit.setText("20260622")
        dlg._lon_edit.setText("abc")    # 非数字
        with patch(_MSGBOX):
            dlg._accept_valid_collection_point()
        assert dlg.result_point() is None

    def test_coord_out_of_range_blocks_accept(self):
        dlg = CollectionPointDialog(
            lon=121.0, lat=29.0, province="ZJ", site="SMW",
            zone="intertidal", stations=_stations(), mode="new",
        )
        dlg._station_edit.setText("B5")
        dlg._date_edit.setText("20260622")
        dlg._lat_edit.setText("999")    # 越界
        with patch(_MSGBOX):
            dlg._accept_valid_collection_point()
        assert dlg.result_point() is None

    def test_bind_disabled_when_no_stations(self):
        dlg = CollectionPointDialog(
            lon=121.0, lat=29.0, province="ZJ", site="SMW",
            zone="intertidal", stations=[], mode="bind",
        )
        assert not dlg._rb_bind.isEnabled()
        assert dlg._rb_new.isChecked()   # 回退新建

    def test_coord_editable_fine_tune(self):
        dlg = CollectionPointDialog(
            lon=121.0, lat=29.0, province="ZJ", site="SMW",
            zone="intertidal", stations=_stations(), mode="new",
        )
        dlg._lon_edit.setText("121.55")
        dlg._station_edit.setText("B5")
        dlg._date_edit.setText("20260622")
        dlg._accept_valid_collection_point()
        r = dlg.result_point()
        assert abs(r["lon"] - 121.55) < 1e-6

    def test_station_combo_labels_carry_label_and_count(self):
        dlg = CollectionPointDialog(
            lon=121.0, lat=29.0, province="ZJ", site="SMW",
            zone="intertidal", stations=_stations(), mode="bind",
        )
        labels = [dlg._station_combo.itemText(i) for i in range(dlg._station_combo.count())]
        assert any("B2" in t and "北滩二区" in t and "2" in t for t in labels)
        assert any("H1" in t for t in labels)
