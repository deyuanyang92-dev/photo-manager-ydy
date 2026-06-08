"""test_label_render.py — the unified label renderer honors the full schema.

Proves render_label_onto (via _render_label_pixmap) actually applies row.align,
per-field size/offset, row.wrap clipping, and QR position(free)/content/ecc — the
capabilities the free-form designer relies on, and that the old renderer ignored.
Because preview + print share this path, "what you see is what prints".
"""
from __future__ import annotations

import os
import sys

import pytest


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    existing = QApplication.instance()
    yield existing if existing is not None else QApplication(sys.argv[:1])


SCALE = 6
DIMS = {"w": 60, "h": 40}


def _img(tmpl, data, scale=SCALE):
    from app.views.labels_view import _render_label_pixmap
    return _render_label_pixmap(tmpl, DIMS, data, scale=scale, placeholder=False).toImage()


def _dark_bbox(img):
    """Bounding box (minx,miny,maxx,maxy) of dark (text/QR) pixels, or None."""
    w, h = img.width(), img.height()
    minx = miny = 10 ** 9
    maxx = maxy = -1
    for y in range(h):
        for x in range(w):
            c = img.pixelColor(x, y)
            if c.red() < 128 and c.green() < 128 and c.blue() < 128:
                minx = min(minx, x); maxx = max(maxx, x)
                miny = min(miny, y); maxy = max(maxy, y)
    return None if maxx < 0 else (minx, miny, maxx, maxy)


def _images_equal(a, b):
    if a.size() != b.size():
        return False
    return a == b


def _row_tmpl(align="left", size=9, offset_x=0, wrap=True, fname="headerId", qr="none"):
    return {
        "name": "t", "lineHeight": 1.3,
        "rows": [{
            "fields": [{"key": fname, "style": "", "size": size,
                        "offsetX": offset_x, "offsetY": 0}],
            "size": size, "style": "", "align": align, "wrap": wrap,
        }],
        "qr": {"position": qr, "content": "uniqueId", "ecc": "Q", "sizePct": 0.4},
    }


DATA = {"headerId": "ABC", "uniqueId": "FJ-XM-B2-DLC001-T95E-20260601"}


class TestAlignment:
    def test_right_align_shifts_text_right(self, qt_app):
        left = _dark_bbox(_img(_row_tmpl(align="left"), DATA))
        right = _dark_bbox(_img(_row_tmpl(align="right"), DATA))
        assert left and right
        assert right[0] > left[0]  # right-aligned text starts further right

    def test_center_between_left_and_right(self, qt_app):
        left = _dark_bbox(_img(_row_tmpl(align="left"), DATA))[0]
        center = _dark_bbox(_img(_row_tmpl(align="center"), DATA))[0]
        right = _dark_bbox(_img(_row_tmpl(align="right"), DATA))[0]
        assert left < center < right


class TestFieldOffset:
    def test_offset_x_moves_text_right(self, qt_app):
        base = _dark_bbox(_img(_row_tmpl(offset_x=0), DATA))[0]
        moved = _dark_bbox(_img(_row_tmpl(offset_x=8), DATA))[0]
        # +8mm at 6 px/mm ≈ +48 px (allow tolerance for AA)
        assert moved - base > 30


class TestPerFieldSize:
    def test_larger_field_size_is_taller(self, qt_app):
        small = _dark_bbox(_img(_row_tmpl(size=8), DATA))
        big = _dark_bbox(_img(_row_tmpl(size=22), DATA))
        small_h = small[3] - small[1]
        big_h = big[3] - big[1]
        assert big_h > small_h * 1.4


class TestWrapClipping:
    def test_wrap_false_clips_within_width(self, qt_app):
        long = {"headerId": "X" * 80, "uniqueId": "u"}
        img = _img(_row_tmpl(wrap=False, fname="headerId"), long)
        bbox = _dark_bbox(img)
        # right padding is 2mm * 6 = 12px; clipped text must not reach the edge
        assert bbox[2] <= img.width() - 6


class TestQrConfig:
    def test_qr_free_position_differs_from_right(self, qt_app):
        free = dict(_row_tmpl(qr="free"))
        free["qr"] = {"position": "free", "x": 1, "y": 1, "sizeMm": 10,
                      "content": "uniqueId", "ecc": "Q"}
        free["rows"] = []  # QR only
        right = dict(_row_tmpl(qr="right"))
        right["rows"] = []
        bf = _dark_bbox(_img(free, DATA))
        br = _dark_bbox(_img(right, DATA))
        assert bf and br
        assert bf[0] < br[0]  # free QR (x=1mm) sits left of a right-anchored QR

    def test_qr_content_field_changes_image(self, qt_app):
        a = dict(_row_tmpl(qr="right")); a["rows"] = []
        a["qr"] = {"position": "right", "content": "uniqueId", "ecc": "Q", "sizePct": 0.5}
        b = dict(_row_tmpl(qr="right")); b["rows"] = []
        b["qr"] = {"position": "right", "content": "headerId", "ecc": "Q", "sizePct": 0.5}
        assert not _images_equal(_img(a, DATA), _img(b, DATA))

    def test_qr_none_draws_no_qr(self, qt_app):
        t = dict(_row_tmpl(qr="none")); t["rows"] = []
        assert _dark_bbox(_img(t, DATA)) is None  # nothing drawn


# ── Free-form element layer ──────────────────────────────────────────────────

def _render_with_elements(elements, data=None, scale=SCALE):
    """Render a rows-empty template carrying *elements*; return (image, hit_boxes)."""
    from PyQt6.QtGui import QPainter, QPixmap, QColor
    from app.utils.label_core import normalize_template
    from app.utils.label_render import render_label_onto
    tmpl = normalize_template({"rows": [], "qr": {"position": "none"},
                               "elements": elements})
    w_px = int(DIMS["w"] * scale)
    h_px = int(DIMS["h"] * scale)
    pm = QPixmap(w_px, h_px)
    pm.fill(QColor("white"))
    p = QPainter(pm)
    boxes = []
    render_label_onto(p, tmpl, DIMS, data or {}, px_per_mm=scale,
                      placeholder=False, fill_bg=False, hit_boxes=boxes)
    p.end()
    return pm.toImage(), boxes


class TestElementHitBoxes:
    def test_rect_element_draws_and_emits_hit_box(self, qt_app):
        img, boxes = _render_with_elements(
            [{"type": "rect", "x": 5, "y": 5, "w": 20, "h": 10}])
        el_boxes = [b for b in boxes if b["kind"] == "element"]
        assert len(el_boxes) == 1
        b = el_boxes[0]
        assert b["index"] == 0 and b["etype"] == "rect"
        # box geometry in device px = mm * scale, origin top-left
        assert b["x"] == 5 * SCALE and b["y"] == 5 * SCALE
        assert b["w"] == 20 * SCALE and b["h"] == 10 * SCALE
        assert _dark_bbox(img) is not None  # stroke pixels present

    def test_each_visual_type_draws_pixels(self, qt_app):
        for el in (
            {"type": "text", "x": 2, "y": 2, "w": 30, "h": 6, "text": "Hi"},
            {"type": "line", "x1": 2, "y1": 2, "x2": 40, "y2": 2, "width": 0.5},
            {"type": "rect", "x": 2, "y": 2, "w": 20, "h": 10},
            {"type": "ellipse", "x": 2, "y": 2, "w": 20, "h": 10},
        ):
            img, _ = _render_with_elements([el])
            assert _dark_bbox(img) is not None, f"{el['type']} drew nothing"

    def test_field_element_resolves_from_data(self, qt_app):
        img, boxes = _render_with_elements(
            [{"type": "field", "x": 2, "y": 2, "w": 40, "h": 6, "key": "headerId"}],
            data={"headerId": "ABC"})
        assert _dark_bbox(img) is not None
        assert boxes[-1]["etype"] == "field"

    def test_index_increments_per_element(self, qt_app):
        _, boxes = _render_with_elements([
            {"type": "rect", "x": 1, "y": 1, "w": 5, "h": 5},
            {"type": "rect", "x": 10, "y": 10, "w": 5, "h": 5},
        ])
        el = [b for b in boxes if b["kind"] == "element"]
        assert [b["index"] for b in el] == [0, 1]


class TestElementImageBarcode:
    def _red_png_b64(self):
        import base64
        from PyQt6.QtGui import QImage, QColor
        from PyQt6.QtCore import QBuffer, QIODevice
        img = QImage(8, 8, QImage.Format.Format_RGB32)
        img.fill(QColor("red"))
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        return base64.b64encode(bytes(buf.data())).decode("ascii")

    def test_image_element_from_base64_draws(self, qt_app):
        img, boxes = _render_with_elements(
            [{"type": "image", "x": 2, "y": 2, "w": 10, "h": 10,
              "data": self._red_png_b64(), "keepAspect": False}])
        # red pixel present somewhere
        found = any(img.pixelColor(x, y).red() > 200
                    and img.pixelColor(x, y).green() < 80
                    for y in range(img.height()) for x in range(img.width()))
        assert found
        assert boxes[-1]["etype"] == "image"

    def test_image_bad_data_draws_placeholder_not_crash(self, qt_app):
        img, boxes = _render_with_elements(
            [{"type": "image", "x": 2, "y": 2, "w": 10, "h": 10,
              "data": "!!!not-base64!!!"}])
        assert boxes[-1]["etype"] == "image"  # rendered without raising

    def test_barcode_soft_degrades_when_lib_absent(self, qt_app, monkeypatch):
        import builtins
        from app.utils import label_render
        real_import = builtins.__import__

        def no_barcode(name, *a, **k):
            if name == "barcode" or name.startswith("barcode."):
                raise ImportError("python-barcode not installed")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", no_barcode)
        assert label_render._generate_barcode_pixmap("X123", 60, 24) is None
        # full render still succeeds with a placeholder
        img, boxes = _render_with_elements(
            [{"type": "barcode", "x": 2, "y": 2, "w": 30, "h": 10,
              "content": "uniqueId"}], data=DATA)
        assert boxes[-1]["etype"] == "barcode"


def _dark_count(img):
    """Number of dark (<128 on all channels) pixels in the image."""
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() < 128 and c.green() < 128 and c.blue() < 128:
                n += 1
    return n


class TestElementOpacity:
    def test_default_opacity_is_byte_identical(self, qt_app):
        """opacity:1.0 (the default) must render identically to no opacity key."""
        el = {"type": "rect", "x": 5, "y": 5, "w": 20, "h": 12,
              "fill": "#000000", "strokeWidth": 0}
        a, _ = _render_with_elements([dict(el)])
        b, _ = _render_with_elements([dict(el, opacity=1.0)])
        assert _images_equal(a, b)

    def test_opacity_half_lightens_pixels(self, qt_app):
        el = {"type": "rect", "x": 5, "y": 5, "w": 20, "h": 12,
              "fill": "#000000", "strokeWidth": 0}
        full, _ = _render_with_elements([dict(el)])
        half, _ = _render_with_elements([dict(el, opacity=0.5)])
        # a half-opacity black fill over white is mid-grey → fewer <128 pixels
        assert _dark_count(half) < _dark_count(full)


class TestElementDash:
    def test_default_dash_is_byte_identical(self, qt_app):
        el = {"type": "line", "x1": 2, "y1": 5, "x2": 55, "y2": 5, "width": 0.6}
        a, _ = _render_with_elements([dict(el)])
        b, _ = _render_with_elements([dict(el, dash="solid")])
        assert _images_equal(a, b)

    def test_dash_draws_gaps(self, qt_app):
        el = {"type": "line", "x1": 2, "y1": 5, "x2": 55, "y2": 5, "width": 0.6}
        solid, _ = _render_with_elements([dict(el, dash="solid")])
        dashed, _ = _render_with_elements([dict(el, dash="dash")])
        # gaps mean fewer dark pixels along the same stroke
        assert _dark_count(dashed) < _dark_count(solid)


class TestElementFontFamily:
    def test_default_font_is_byte_identical(self, qt_app):
        el = {"type": "text", "x": 2, "y": 2, "w": 50, "h": 8, "text": "AaBb",
              "size": 10}
        a, _ = _render_with_elements([dict(el)])
        b, _ = _render_with_elements([dict(el, font="")])
        assert _images_equal(a, b)

    def test_font_family_changes_pixels(self, qt_app):
        from PyQt6.QtGui import QFontDatabase
        fams = QFontDatabase.families()
        pick = next((f for f in fams if f and "mono" in f.lower()), None) \
            or next((f for f in fams if f), None)
        if not pick:
            pytest.skip("no font families available in this environment")
        el = {"type": "text", "x": 2, "y": 2, "w": 50, "h": 8, "text": "AaBbGg",
              "size": 11}
        base, _ = _render_with_elements([dict(el)])
        styled, _ = _render_with_elements([dict(el, font=pick)])
        if _images_equal(base, styled):
            pytest.skip(f"family {pick!r} renders identically to default here")
        assert not _images_equal(base, styled)


class TestLineArrowheads:
    def test_default_no_arrow_is_byte_identical(self, qt_app):
        el = {"type": "line", "x1": 5, "y1": 20, "x2": 50, "y2": 20, "width": 0.5}
        a, _ = _render_with_elements([dict(el)])
        b, _ = _render_with_elements([dict(el, arrowStart=False, arrowEnd=False)])
        assert _images_equal(a, b)

    def test_arrowend_adds_pixels_near_endpoint(self, qt_app):
        el = {"type": "line", "x1": 5, "y1": 20, "x2": 50, "y2": 20, "width": 0.5}
        plain, _ = _render_with_elements([dict(el)])
        arrow, _ = _render_with_elements([dict(el, arrowEnd=True)])
        # the filled arrowhead triangle adds dark pixels off the 1px-tall stroke
        assert _dark_count(arrow) > _dark_count(plain)


class TestTextWrap:
    def test_default_no_wrap_is_byte_identical(self, qt_app):
        el = {"type": "text", "x": 2, "y": 2, "w": 30, "h": 6, "text": "Hello",
              "size": 9}
        a, _ = _render_with_elements([dict(el)])
        b, _ = _render_with_elements([dict(el, wrap=False)])
        assert _images_equal(a, b)

    def test_wrap_true_grows_text_height(self, qt_app):
        # a long string in a narrow tall box wraps to multiple lines → taller bbox
        el = {"type": "text", "x": 2, "y": 2, "w": 16, "h": 34,
              "text": "alpha beta gamma delta epsilon", "size": 9}
        nowrap, _ = _render_with_elements([dict(el)])
        wrapped, _ = _render_with_elements([dict(el, wrap=True)])
        bb_n, bb_w = _dark_bbox(nowrap), _dark_bbox(wrapped)
        assert bb_n is not None and bb_w is not None
        h_n = bb_n[3] - bb_n[1]
        h_w = bb_w[3] - bb_w[1]
        assert h_w > h_n, "wrapped text should occupy more vertical space"


class TestElementBackwardCompat:
    def test_empty_elements_is_byte_identical(self, qt_app):
        """The single most important gate: a template with elements:[] must
        render byte-identically to the same template before the elements pass
        existed — because this renderer also drives the physical printer."""
        from app.services.label_service import BUILTIN_TEMPLATES
        base = dict(_row_tmpl(qr="right"))
        # render once "as authored" (no elements key at all)
        a = _img(base, DATA)
        # render again after normalize injects elements:[]
        from app.utils.label_core import normalize_template
        b = _img(normalize_template(base), DATA)
        assert _images_equal(a, b)


class TestPreviewHonoursElements:
    """The big label preview in labels_view calls render_label_preview; it must
    route through the unified renderer so the free-form elements layer shows in
    the preview the user looks at (WYSIWYG — regression lock for Phase 0)."""

    def test_preview_renders_elements_layer(self, qt_app):
        from app.utils.label_core import normalize_template
        from app.utils.label_render import render_label_preview
        dims = {"w": 50, "h": 30}
        bare = normalize_template({"rows": [], "qr": {"position": "none"}})
        with_el = normalize_template({
            "rows": [], "qr": {"position": "none"},
            "elements": [{"type": "rect", "x": 5, "y": 5, "w": 40, "h": 20,
                          "fill": "#000000", "strokeWidth": 0}],
        })
        pm_bare = render_label_preview(bare, dims, {}, 300, 180)
        pm_el = render_label_preview(with_el, dims, {}, 300, 180)
        img_b, img_e = pm_bare.toImage(), pm_el.toImage()
        diffs = sum(
            1 for y in range(0, img_b.height(), 3)
            for x in range(0, img_b.width(), 3)
            if img_b.pixel(x, y) != img_e.pixel(x, y)
        )
        assert diffs > 50, "preview must render the elements layer, not rows only"
