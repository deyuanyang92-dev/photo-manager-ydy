"""test_worms_view.py — Tests for WoRMS view and taxonomy-filter import feature.

Covers:
  - TaxonomyView.get_filtered_uids: returns recordIds of currently displayed records
  - WormsView._build_jobs_section: "从分类库筛选导入" button exists
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_QT_APP = None


@pytest.fixture(scope="module")
def qapp():
    global _QT_APP
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.current_project_dir = None
    ctx.settings = MagicMock()
    ctx.settings.last_nav_index = 0
    return ctx


# ── Seed data for TaxonomyService ────────────────────────────────────────────

SEED = [
    {
        "class": "Polychaeta", "order": "Phyllodocida",
        "family": "Polynoidae", "species": "Halosydna brevisetosa",
        "classCn": "多毛纲", "orderCn": "叶须虫目",
        "familyCn": "多鳞虫科", "genus": "Halosydna",
        "genusCn": "海鳞虫属", "speciesCn": "短毛海鳞虫",
    },
    {
        "class": "Malacostraca", "order": "Decapoda",
        "family": "Portunidae", "species": "Portunus trituberculatus",
        "classCn": "软甲纲", "orderCn": "十足目",
        "familyCn": "梭子蟹科", "genus": "Portunus",
        "genusCn": "梭子蟹属", "speciesCn": "三疣梭子蟹",
    },
]


@pytest.fixture
def tmp_svc():
    import shutil
    from app.services.taxonomy_service import TaxonomyService
    d = Path(tempfile.mkdtemp())
    seed_p = d / "taxonomy_seed.json"
    user_p = d / "user_taxonomy.json"
    seed_p.write_text(json.dumps(SEED), encoding="utf-8")
    svc = TaxonomyService(seed_p, user_p)
    try:
        yield svc
    finally:
        shutil.rmtree(d)


# ── TaxonomyView.get_filtered_uids ─────────────────────────────────────────────

class TestGetFilteredUids:
    def test_returns_record_ids_for_all_visible_rows(self, qapp, mock_ctx, tmp_svc):
        """get_filtered_uids returns recordId for every row currently in the model."""
        from app.views.taxonomy_view import TaxonomyView
        v = TaxonomyView(mock_ctx)
        v._svc = tmp_svc
        v._load_page()

        uids = v.get_filtered_uids()
        # Two seed records are loaded; each should produce a recordId
        assert len(uids) == 2
        assert all(isinstance(u, str) and u for u in uids)

    def test_returns_empty_list_when_no_records(self, qapp, mock_ctx, tmp_svc):
        """get_filtered_uids returns [] when the table shows no rows."""
        from app.views.taxonomy_view import TaxonomyView
        v = TaxonomyView(mock_ctx)
        v._svc = tmp_svc
        # Apply a filter that matches nothing
        v._filter_text = "ZZZNOMATCH"
        v._load_page()

        uids = v.get_filtered_uids()
        assert uids == []

    def test_filtered_rows_produce_matching_uids(self, qapp, mock_ctx, tmp_svc):
        """After a filter, only records matching the filter appear in get_filtered_uids."""
        from app.views.taxonomy_view import TaxonomyView
        v = TaxonomyView(mock_ctx)
        v._svc = tmp_svc
        v._filter_text = "Decapoda"
        v._filter_col = "order"
        v._load_page()

        uids = v.get_filtered_uids()
        assert len(uids) == 1


# ── WormsView: "从分类库筛选导入" button ──────────────────────────────────────

class TestWormsImportFromFilterButton:
    def test_worms_import_from_filter_button_exists(self, qapp, mock_ctx):
        """WormsView jobs section has a '从分类库筛选导入' QPushButton."""
        from PyQt6.QtWidgets import QPushButton
        from app.views.worms_view import WormsView
        v = WormsView(mock_ctx)

        btn = v.findChild(QPushButton, "BtnImportFromTaxonFilter")
        assert btn is not None, "Expected QPushButton named 'BtnImportFromTaxonFilter'"

    def test_worms_import_button_text(self, qapp, mock_ctx):
        """The import button shows the expected Chinese label."""
        from PyQt6.QtWidgets import QPushButton
        from app.views.worms_view import WormsView
        v = WormsView(mock_ctx)

        btn = v.findChild(QPushButton, "BtnImportFromTaxonFilter")
        assert btn is not None
        assert btn.text() == "从分类库筛选导入"
