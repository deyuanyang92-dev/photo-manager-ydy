"""Declarative schema for the free-form label designer.

This module is intentionally Qt-free.  It describes the design surface that the
template picker, designer dialog, renderer tests, and future Claude Code tasks
can share without importing widgets.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ElementTool:
    """One tool the free-form label designer can place on the canvas."""

    key: str
    label: str
    category: str
    description: str
    binds_data: bool = False


@dataclass(frozen=True)
class FieldOption:
    """One specimen/label field that can be shown as text or encoded in QR."""

    key: str
    label: str
    group: str
    recommended_qr: bool = False


@dataclass(frozen=True)
class ShapeOption:
    """Outer label shape supported by normalize_template/render_label_onto."""

    key: str
    label: str
    description: str


LABEL_SHAPES: tuple[ShapeOption, ...] = (
    ShapeOption("rect", "矩形", "普通样品瓶、玻片、采集信息标签"),
    ShapeOption("roundrect", "圆角矩形", "小管侧贴、需要柔和边角的标签"),
    ShapeOption("circle", "圆形", "冻存管盖、圆形贴纸、顶盖编号标签"),
)


ELEMENT_TOOLS: tuple[ElementTool, ...] = (
    ElementTool("text", "文字", "content", "固定文字，例如项目名、批次说明"),
    ElementTool("field", "绑定字段", "content", "绑定标本字段，打印时按样品替换", True),
    ElementTool("line", "直线", "shape", "分隔线、引导线、表格线"),
    ElementTool("rect", "矩形", "shape", "边框、色块、信息区背景"),
    ElementTool("ellipse", "椭圆/圆", "shape", "圆点、圆形底纹、圆形标签内元素"),
    ElementTool("shape", "多边形", "shape", "三角/星形/箭头/多边形，自由顶点"),
    ElementTool("image", "图片", "media", "logo、图标、机构标识"),
    ElementTool("barcode", "条码", "code", "一维条码，通常绑定唯一编号", True),
)


FIELD_OPTIONS: tuple[FieldOption, ...] = (
    FieldOption("uniqueId", "唯一编号", "identity", True),
    FieldOption("headerId", "编号头", "identity", True),
    FieldOption("speciesName", "物种名称", "taxonomy"),
    FieldOption("latin", "拉丁名", "taxonomy"),
    FieldOption("family", "科", "taxonomy"),
    FieldOption("storage", "保存方式", "collection"),
    FieldOption("rnaPreservative", "RNA保存液", "collection"),
    FieldOption("shortDate", "日期段", "collection", True),
    FieldOption("fullDate", "完整日期段", "collection"),
    FieldOption("collectionDate", "采集日期", "collection"),
    FieldOption("photoDate", "拍摄日期", "collection"),
    FieldOption("province", "省份", "location"),
    FieldOption("site", "样点", "location"),
    FieldOption("station", "站位", "location"),
    FieldOption("region", "地点", "location"),
    FieldOption("geoArea", "采集地理区", "location"),
    FieldOption("lon", "经度", "location"),
    FieldOption("lat", "纬度", "location"),
    FieldOption("collector", "采集人", "people"),
    FieldOption("collectorLabel", "采集人(带'采集')", "people"),
    FieldOption("photographer", "拍摄者", "people"),
    FieldOption("photoNotes", "拍摄备注", "notes"),
)


QR_CONTENT_KEYS: tuple[str, ...] = (
    "uniqueId",
    "headerId",
    "shortDate",
    "storage",
    "speciesName",
    "latin",
    "region",
    "collector",
)


DESIGN_CAPABILITIES: dict[str, object] = {
    "canvas_units": "mm",
    "z_order": "elements list order; last item is topmost",
    "single_renderer": "app.utils.label_render.render_label_onto",
    "supports_outer_shapes": tuple(s.key for s in LABEL_SHAPES),
    "supports_element_tools": tuple(t.key for t in ELEMENT_TOOLS),
    "supports_bound_fields": tuple(f.key for f in FIELD_OPTIONS),
    "supports_qr_content": QR_CONTENT_KEYS,
}


def element_tool_keys() -> tuple[str, ...]:
    return tuple(t.key for t in ELEMENT_TOOLS)


def field_option_keys() -> tuple[str, ...]:
    return tuple(f.key for f in FIELD_OPTIONS)


def qr_content_keys() -> tuple[str, ...]:
    return QR_CONTENT_KEYS


def shape_keys() -> tuple[str, ...]:
    return tuple(s.key for s in LABEL_SHAPES)
