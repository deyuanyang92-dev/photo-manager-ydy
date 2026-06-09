from __future__ import annotations

from app.services.label_design_schema import (
    DESIGN_CAPABILITIES,
    element_tool_keys,
    field_option_keys,
    qr_content_keys,
    shape_keys,
)


def test_freeform_tool_registry_matches_element_normalizer():
    from app.utils.label_core import ELEMENT_DEFAULTS

    assert set(element_tool_keys()) == set(ELEMENT_DEFAULTS)


def test_shape_registry_matches_supported_template_shapes():
    assert set(shape_keys()) == {"rect", "roundrect", "circle"}
    assert "circle" in DESIGN_CAPABILITIES["supports_outer_shapes"]


def test_qr_content_defaults_to_unique_id_and_uses_known_fields():
    keys = qr_content_keys()

    assert keys[0] == "uniqueId"
    assert set(keys) <= set(field_option_keys())


def test_designer_field_and_element_constants_stay_in_sync():
    from app.widgets.label_designer_dialog import ELEMENT_TYPE_LABELS, FIELD_LABELS

    assert set(ELEMENT_TYPE_LABELS) == set(element_tool_keys())
    assert set(qr_content_keys()) <= set(FIELD_LABELS)
