"""geo_calibration.py — 控制点校准：经纬度 → 底图像素 的变换拟合.

采集地图的出版底图（官方审图号世界/中国图）是投影图，经纬网弯曲、非线性。用户在
校准对话框点几个已知经纬网交点并输入其经纬度，得到控制点 (lon, lat, px, py)；本模块
用最小二乘拟合一个变换，把任意经纬度映射到该底图的像素坐标，并报残差 RMS（像素）供
用户判断落点精度。

变换阶数：
    order=1  仿射（线性，≥3 点）—— 适合等距圆柱(PlateCarree)底图。
    order=2  二次多项式（≥6 点）—— 吸收投影弯曲，适合官方投影图。

模型 dict 可 JSON 序列化，存为图片旁 sidecar（basemap_registry）。纯 numpy，无 Qt。
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

# 每阶所需最少控制点数（= 设计矩阵列数）。
_MIN_POINTS = {1: 3, 2: 6}


def _design_row(lon: float, lat: float, order: int) -> list[float]:
    """单点设计行（多项式基）。"""
    if order == 1:
        return [1.0, lon, lat]
    if order == 2:
        return [1.0, lon, lat, lon * lon, lon * lat, lat * lat]
    raise ValueError(f"未知阶数 order={order}，应为 1 或 2")


def _design_matrix(lons, lats, order: int) -> np.ndarray:
    lons = np.asarray(lons, dtype=float)
    lats = np.asarray(lats, dtype=float)
    if order == 1:
        cols = [np.ones_like(lons), lons, lats]
    elif order == 2:
        cols = [np.ones_like(lons), lons, lats, lons * lons, lons * lats, lats * lats]
    else:
        raise ValueError(f"未知阶数 order={order}，应为 1 或 2")
    return np.column_stack(cols)


def fit(control_points: Sequence[tuple], order: int = 1) -> dict:
    """拟合 经纬度→像素 变换。

    control_points: 序列 of (lon, lat, px, py)。
    返回模型 dict ``{order, cx, cy, rms_px}``：cx/cy 为 px/py 的多项式系数（list），
    rms_px 为控制点上的预测像素与真值的欧氏距离均方根。

    点数不足该阶所需 → ValueError。
    """
    min_pts = _MIN_POINTS.get(order)
    if min_pts is None:
        raise ValueError(f"未知阶数 order={order}，应为 1 或 2")
    pts = list(control_points)
    if len(pts) < min_pts:
        raise ValueError(
            f"order={order} 需至少 {min_pts} 个控制点，仅提供 {len(pts)} 个"
        )

    lons = np.array([p[0] for p in pts], dtype=float)
    lats = np.array([p[1] for p in pts], dtype=float)
    pxs = np.array([p[2] for p in pts], dtype=float)
    pys = np.array([p[3] for p in pts], dtype=float)

    A = _design_matrix(lons, lats, order)
    cx, *_ = np.linalg.lstsq(A, pxs, rcond=None)
    cy, *_ = np.linalg.lstsq(A, pys, rcond=None)

    pred_x = A @ cx
    pred_y = A @ cy
    dist = np.hypot(pred_x - pxs, pred_y - pys)
    rms = float(np.sqrt(np.mean(dist ** 2))) if len(dist) else 0.0

    return {"order": int(order), "cx": cx.tolist(), "cy": cy.tolist(), "rms_px": rms}


def project(model: dict, lon: float, lat: float) -> tuple[float, float]:
    """把单个经纬度映射到底图像素 (px, py)。"""
    order = int(model["order"])
    row = np.array(_design_row(lon, lat, order), dtype=float)
    px = float(row @ np.array(model["cx"], dtype=float))
    py = float(row @ np.array(model["cy"], dtype=float))
    return px, py


def project_many(model: dict, lons, lats) -> tuple[np.ndarray, np.ndarray]:
    """向量化映射；返回 (px_array, py_array)。"""
    order = int(model["order"])
    A = _design_matrix(lons, lats, order)
    px = A @ np.array(model["cx"], dtype=float)
    py = A @ np.array(model["cy"], dtype=float)
    return px, py
