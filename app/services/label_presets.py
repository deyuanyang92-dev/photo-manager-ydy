"""label_presets.py — starter templates for the free-form label designer.

These are *designer starting points*, deliberately kept OUT of
``label_service.BUILTIN_TEMPLATES`` so they don't appear in the Step-2 print
card grid. Each value is a full (un-normalized) template dict — the designer
runs it through ``normalize_template`` on apply, so missing keys self-default.

Presets may carry a free-form ``elements`` layer (text/field/line/rect/
ellipse/image/barcode), which the unified renderer draws above the rows.
"""
from __future__ import annotations


STARTER_PRESETS: dict[str, dict] = {
    "specimen": {
        "name": "标本签（编号+物种+QR）",
        "shape": "rect",
        "lineHeight": 1.3,
        "rows": [
            {"fields": [{"key": "headerId", "style": "bold", "size": 10}],
             "size": 10, "style": "bold", "align": "left", "wrap": False},
            {"fields": [{"key": "speciesName", "style": "", "size": 9}],
             "size": 9, "align": "left", "wrap": False},
            {"fields": [{"key": "latin", "style": "italic", "size": 8}],
             "size": 8, "style": "italic", "align": "left", "wrap": False},
            {"fields": [{"key": "collectionDate", "size": 7},
                        {"key": "storage", "size": 7}],
             "size": 7, "align": "left", "sep": " · ", "wrap": False},
        ],
        "qr": {"content": "uniqueId", "position": "right", "sizePct": 0.4, "ecc": "Q"},
        "elements": [],
    },
    "tube": {
        "name": "管签（小号竖排）",
        "shape": "rect",
        "lineHeight": 1.2,
        "rows": [
            {"fields": [{"key": "headerId", "style": "bold", "size": 7}],
             "size": 7, "style": "bold", "align": "center", "wrap": False},
            {"fields": [{"key": "storage", "size": 6}],
             "size": 6, "align": "center", "wrap": False},
        ],
        "qr": {"content": "uniqueId", "position": "top", "sizePct": 0.5, "ecc": "M"},
        "elements": [],
    },
    "logo": {
        "name": "Logo 签（图片+标题+条码）",
        "shape": "rect",
        "lineHeight": 1.3,
        "rows": [],
        "qr": {"content": "uniqueId", "position": "none", "sizePct": 0.4, "ecc": "Q"},
        "elements": [
            {"type": "rect", "x": 1, "y": 1, "w": 58, "h": 38,
             "stroke": "#000000", "strokeWidth": 0.3, "fill": None},
            {"type": "text", "x": 4, "y": 3, "w": 40, "h": 7,
             "text": "标题", "size": 12, "style": "bold", "align": "left"},
            {"type": "image", "x": 44, "y": 3, "w": 12, "h": 12,
             "data": None, "keepAspect": True},
            {"type": "field", "x": 4, "y": 12, "w": 52, "h": 6,
             "key": "headerId", "size": 8, "align": "left"},
            {"type": "barcode", "x": 4, "y": 22, "w": 52, "h": 14,
             "content": "uniqueId", "showText": True},
        ],
    },
    "blank": {
        "name": "空白（自由设计）",
        "shape": "rect",
        "lineHeight": 1.3,
        "rows": [],
        "qr": {"content": "uniqueId", "position": "none", "sizePct": 0.4, "ecc": "Q"},
        "elements": [],
    },
}
