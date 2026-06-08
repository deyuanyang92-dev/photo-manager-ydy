"""tests/test_label_elements.py — free-form element layer schema + normalization.

The free-form designer adds an optional ``elements`` list to a template: text,
field, line, rect, ellipse, image, barcode — each freely positioned in mm.
These tests pin the pure-Python schema/normalization (no Qt) and the
backward-compat guarantee that templates without ``elements`` are unaffected.
"""
from __future__ import annotations

from app.utils.label_core import normalize_elements, normalize_template


def test_normalize_elements_none_is_empty_list():
    assert normalize_elements(None) == []


def test_normalize_elements_non_list_is_empty_list():
    assert normalize_elements("nope") == []
    assert normalize_elements({"type": "text"}) == []


def test_text_element_gets_defaults():
    out = normalize_elements([{"type": "text", "text": "Hi", "x": 3, "y": 4}])
    assert len(out) == 1
    el = out[0]
    assert el["type"] == "text"
    assert el["text"] == "Hi"
    assert el["x"] == 3.0 and el["y"] == 4.0
    assert isinstance(el["x"], float)
    assert el["w"] > 0 and el["h"] > 0
    assert el["color"] == "#000000"
    assert el["align"] == "left"
    assert el["rotation"] == 0
    assert "size" in el and "style" in el


def test_unknown_type_dropped():
    out = normalize_elements([{"type": "wormhole", "x": 1},
                              {"type": "text", "text": "ok"},
                              {"no": "type"}])
    assert [e["type"] for e in out] == ["text"]


def test_extra_keys_preserved():
    out = normalize_elements([{"type": "rect", "x": 1, "_future": "keep"}])
    assert out[0]["_future"] == "keep"


def test_each_type_normalises():
    raw = [
        {"type": "field", "key": "speciesName"},
        {"type": "line"},
        {"type": "rect"},
        {"type": "ellipse"},
        {"type": "image"},
        {"type": "barcode"},
    ]
    out = normalize_elements(raw)
    assert [e["type"] for e in out] == ["field", "line", "rect", "ellipse",
                                        "image", "barcode"]
    line = out[1]
    assert {"x1", "y1", "x2", "y2", "width", "color"} <= set(line)
    assert isinstance(line["x2"], float)
    rect = out[2]
    assert rect["fill"] is None and rect["stroke"] == "#000000"
    img = out[4]
    assert img["data"] is None and img["keepAspect"] is True
    bc = out[5]
    assert bc["content"] == "uniqueId" and bc["showText"] is True


# ── Phase 1: opacity / dash / font ──────────────────────────────────────────

def test_opacity_default_is_one_on_all_types():
    raw = [{"type": t} for t in
           ("text", "field", "line", "rect", "ellipse", "image", "barcode")]
    for el in normalize_elements(raw):
        assert el["opacity"] == 1.0
        assert isinstance(el["opacity"], float)


def test_opacity_coerced_and_clamped():
    out = normalize_elements([
        {"type": "rect", "opacity": "0.5"},   # str → float
        {"type": "rect", "opacity": 2.5},     # > 1 → clamp to 1.0
        {"type": "rect", "opacity": -0.3},    # < 0 → clamp to 0.0
        {"type": "rect", "opacity": "junk"},  # bad → default 1.0
    ])
    assert out[0]["opacity"] == 0.5
    assert out[1]["opacity"] == 1.0
    assert out[2]["opacity"] == 0.0
    assert out[3]["opacity"] == 1.0


def test_dash_default_solid_on_stroke_types():
    out = normalize_elements([{"type": "line"}, {"type": "rect"},
                              {"type": "ellipse"}])
    assert all(el["dash"] == "solid" for el in out)
    # dash must stay a string, never coerced to float
    assert isinstance(out[0]["dash"], str)


def test_font_default_empty_on_text_field():
    out = normalize_elements([{"type": "text"}, {"type": "field"}])
    assert out[0]["font"] == "" and out[1]["font"] == ""


# ── Phase 2: arrowheads / wrapped text ──────────────────────────────────────

def test_line_arrow_defaults_false():
    el = normalize_elements([{"type": "line"}])[0]
    assert el["arrowStart"] is False and el["arrowEnd"] is False


def test_text_field_wrap_defaults_false():
    out = normalize_elements([{"type": "text"}, {"type": "field"}])
    assert out[0]["wrap"] is False and out[1]["wrap"] is False


def test_arrow_and_wrap_are_not_float_coerced():
    # booleans must survive normalization unchanged (never float())
    el = normalize_elements([{"type": "line", "arrowEnd": True}])[0]
    assert el["arrowEnd"] is True
    t = normalize_elements([{"type": "text", "wrap": True}])[0]
    assert t["wrap"] is True


# ── Phase 3: gradient / shadow / monochrome ─────────────────────────────────

def test_gradient_shadow_default_none():
    out = normalize_elements([{"type": "rect"}, {"type": "ellipse"}])
    assert out[0]["gradient"] is None and out[0]["shadow"] is None
    assert out[1]["gradient"] is None and out[1]["shadow"] is None


def test_gradient_nested_dict_preserved():
    grad = {"type": "linear", "angle": 45,
            "stops": [["#ffffff", 0.0], ["#000000", 1.0]]}
    shadow = {"dx": 0.5, "dy": 0.5, "blur": 0, "color": "#888888"}
    el = normalize_elements([{"type": "rect", "gradient": grad,
                              "shadow": shadow}])[0]
    assert el["gradient"] == grad      # nested dict rides the preserve path
    assert el["shadow"] == shadow
    # nested numeric values must NOT be float-coerced into the top-level
    assert isinstance(el["gradient"]["stops"], list)


def test_template_monochrome_default_false():
    tmpl = normalize_template({"rows": []})
    assert tmpl["monochrome"] is False


def test_normalize_template_adds_empty_elements():
    tmpl = normalize_template({"rows": [{"fields": [{"key": "uniqueId"}]}]})
    assert tmpl["elements"] == []


def test_normalize_template_normalises_existing_elements():
    tmpl = normalize_template({
        "rows": [],
        "elements": [{"type": "text", "text": "Hi"}, {"type": "bogus"}],
    })
    assert len(tmpl["elements"]) == 1
    assert tmpl["elements"][0]["type"] == "text"
