"""test_calibration_dialog.py — 控制点校准对话框冒烟.

用户点击底图上已知经纬网交点 + 输入经纬度 → 累积控制点 → 拟合 → 显示 RMS → 保存 sidecar。
本测试直接调逻辑方法（add_control_point / refit / save），不模拟鼠标点击。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_calibration_dialog.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.services import basemap_registry as br

_APP = QApplication.instance() or QApplication([])


def _make_image(tmp_path: Path) -> Path:
    from PIL import Image
    Image.new("RGB", (200, 120), (230, 240, 240)).save(tmp_path / "w.png")
    return tmp_path / "w.png"


def _dialog(img: Path):
    from app.widgets.calibration_dialog import CalibrationDialog
    return CalibrationDialog(str(img))


# 仿射真值：px = 2*lon, py = 120 - 1.2*lat
def _affine(lon, lat):
    return 2 * lon, 120 - 1.2 * lat


class TestCalibration:
    def test_instantiates(self, tmp_path):
        d = _dialog(_make_image(tmp_path))
        assert d is not None

    def test_add_points_and_refit(self, tmp_path):
        d = _dialog(_make_image(tmp_path))
        for lon, lat in [(0, 0), (100, 0), (0, 100), (50, 50)]:
            px, py = _affine(lon, lat)
            d.add_control_point(lon, lat, px, py)
        d.refit()
        assert d.model is not None
        assert d.model["order"] == 1
        assert d.model["rms_px"] < 1e-6

    def test_too_few_points_no_model(self, tmp_path):
        d = _dialog(_make_image(tmp_path))
        d.add_control_point(0, 0, 0, 120)
        d.refit()                 # 仅 1 点，不足
        assert d.model is None

    def test_save_writes_sidecar(self, tmp_path):
        img = _make_image(tmp_path)
        d = _dialog(img)
        for lon, lat in [(0, 0), (100, 0), (0, 100), (50, 50)]:
            px, py = _affine(lon, lat)
            d.add_control_point(lon, lat, px, py)
        d.refit()
        d.save()
        loaded = br.load_calibration(img)
        assert loaded is not None
        assert loaded["model"]["order"] == 1
        assert len(loaded["control_points"]) == 4
