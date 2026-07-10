"""Compatibility checks for gradual service-domain migrations."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize(
    ("legacy_module", "domain_module", "symbols"),
    (
        (
            "app.services.naming_field_catalog",
            "app.services.specimen.naming_field_catalog",
            ("NamingField", "NAMING_FIELDS", "normalize_required"),
        ),
        (
            "app.services.collab_types",
            "app.services.collab.collab_types",
            ("PeerInfo", "TaskStatus", "_now_iso", "get_httpx"),
        ),
        (
            "app.services.label_design_schema",
            "app.services.label.label_design_schema",
            ("ElementTool", "DESIGN_CAPABILITIES", "element_tool_keys"),
        ),
        (
            "app.services.cover_pick_service",
            "app.services.project.cover_pick_service",
            ("pick_project_cover_path", "set_project_cover_path"),
        ),
        (
            "app.services.survey_overview_service",
            "app.services.project.survey_overview_service",
            ("aggregate_survey_overview", "map_points_from_specimen_rows"),
        ),
    ),
)
def test_legacy_service_path_reexports_domain_implementation(
    legacy_module: str,
    domain_module: str,
    symbols: tuple[str, ...],
) -> None:
    legacy = importlib.import_module(legacy_module)
    domain = importlib.import_module(domain_module)

    assert legacy is domain
    for symbol in symbols:
        assert getattr(legacy, symbol) is getattr(domain, symbol)


def test_shared_attribution_type_keeps_legacy_monitor_import() -> None:
    from app.services.monitor_service import AttributionCtx as legacy
    from app.services.specimen.attribution_types import AttributionCtx as domain

    assert legacy is domain


def test_result_tif_schema_helper_keeps_service_import() -> None:
    from app.db.result_tif_schema import ensure_result_tif_index_table as db_helper
    from app.services.specimen_result_tif_service import (
        ensure_result_tif_index_table as service_helper,
    )

    assert service_helper is db_helper
