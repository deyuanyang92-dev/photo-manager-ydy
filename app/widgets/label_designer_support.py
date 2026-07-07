"""Shared constants and helpers for the label designer dialog."""
from __future__ import annotations

from PyQt6.QtWidgets import QPushButton

from app.utils.label_core import normalize_element

MIN_EL_MM = 2.0

# Style keys the format painter copies between elements. Deliberately excludes
# geometry (x/y/w/h/points/rotation) and content (text/key/content/data).
_STYLE_KEYS = (
    "color", "stroke", "fill", "strokeWidth", "dash", "opacity", "font",
    "style", "align", "gradient", "shadow", "cornerRadius",
)

# element_* ops that should fan out to the whole multi-selection (style/appearance
# only — geometry/structural ops stay single-target).
_BATCH_OPS = frozenset({
    "element_color", "element_stroke", "element_fill", "element_strokeWidth",
    "element_dash", "element_opacity", "element_font", "element_bold",
    "element_italic", "element_align", "element_size", "element_cornerRadius",
    "element_gradient", "element_shadow", "element_arrowStart",
    "element_arrowEnd", "element_wrap", "element_hidden", "element_locked",
    "element_rotation",
})

# Edits that change which row/field/element is selected or mutate the list
# structure. These need a full property-panel rebuild so the inspector shows
# the new selection / new controls. Every OTHER edit (slider drags, spinbox
# steps, text typed into a field) is a *value* edit: the source widget already
# reflects the new value, so the panel must be left intact — see _refresh_live.
_STRUCTURAL_OPS = frozenset({
    "field_add", "field_del", "row_dup", "row_del", "row_move",
    "element_dup", "element_del", "element_z",
})

# Free-form element types offered by the "+元素" toolbar menu (type → label).
ELEMENT_TYPE_LABELS: dict[str, str] = {
    "text": "文字", "field": "绑定字段", "line": "直线", "rect": "矩形",
    "ellipse": "椭圆", "shape": "多边形", "image": "图片", "barcode": "条码",
}

# Polygon presets for the "+元素 ▸ 多边形" submenu (name → unit-square points).
SHAPE_PRESETS: dict[str, list] = {
    "三角形": [[0.5, 0.0], [1.0, 1.0], [0.0, 1.0]],
    "菱形": [[0.5, 0.0], [1.0, 0.5], [0.5, 1.0], [0.0, 0.5]],
    "五角星": [[0.5, 0.0], [0.62, 0.38], [1.0, 0.38], [0.69, 0.61],
              [0.81, 1.0], [0.5, 0.75], [0.19, 1.0], [0.31, 0.61],
              [0.0, 0.38], [0.38, 0.38]],
    "六边形": [[0.25, 0.0], [0.75, 0.0], [1.0, 0.5],
              [0.75, 1.0], [0.25, 1.0], [0.0, 0.5]],
    "右箭头": [[0.0, 0.3], [0.6, 0.3], [0.6, 0.0], [1.0, 0.5],
              [0.6, 1.0], [0.6, 0.7], [0.0, 0.7]],
}


def _default_element(etype: str, dims: dict) -> dict:
    """Build a normalized default element of *etype*, centered on the label."""
    el = normalize_element({"type": etype})
    if el is None:
        el = normalize_element({"type": "rect"})
    w_mm = float(dims.get("w", 60))
    h_mm = float(dims.get("h", 40))
    if etype == "line":
        el["x1"] = round(w_mm * 0.25, 1)
        el["y1"] = round(h_mm * 0.5, 1)
        el["x2"] = round(w_mm * 0.75, 1)
        el["y2"] = round(h_mm * 0.5, 1)
    else:
        ew = float(el.get("w") or 20.0)
        eh = float(el.get("h") or 10.0)
        el["x"] = round(max(0.0, (w_mm - ew) / 2.0), 1)
        el["y"] = round(max(0.0, (h_mm - eh) / 2.0), 1)
        if etype == "text":
            el["text"] = "文字"
    return el


# Placeable fields (key → Chinese label), ordered for the picker.
FIELD_LABELS: dict[str, str] = {
    "uniqueId": "唯一编号", "headerId": "编号头",
    "province": "省份", "site": "样点", "station": "站位",
    "storage": "保存方式", "shortDate": "日期段", "fullDate": "完整日期段",
    "collectionDate": "采集日期", "photoDate": "拍摄日期",
    "speciesName": "物种名称", "latin": "拉丁名", "family": "科",
    "region": "地点", "geoArea": "采集地理区", "lon": "经度", "lat": "纬度",
    "collector": "采集人", "collectorLabel": "采集人(带'采集')",
    "photographer": "拍摄者", "photoNotes": "拍摄备注",
    "rnaPreservative": "RNA保存液",
}


def _field_name(key: str) -> str:
    return FIELD_LABELS.get(key, key)


def _make_designer_button(text: str, checkable: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setCheckable(checkable)
    button.setStyleSheet(
        "QPushButton { background:#0f2127; border:1px solid rgba(145,182,181,0.22);"
        " border-radius:4px; color:#cfe0db; padding:3px 9px; font-size:12px; }"
        "QPushButton:checked { background:rgba(41,185,171,0.20); border-color:#29b9ab; color:#29b9ab; }"
        "QPushButton:hover { border-color:#29b9ab; }"
    )
    return button


