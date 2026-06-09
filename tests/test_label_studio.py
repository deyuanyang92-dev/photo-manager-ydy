"""test_label_studio.py — the master-detail Label Print Studio widgets.

Covers the three new panels of the redesigned 标签打印 page:
  * LabelListPanel    — left master (list / grid, RNA marker, selection)
  * LabelDetailPanel  — right detail (bucket toggle, template/size resolution)
  * LabelEditorDialog — modal template editor

Bucketing / print-job / rendering behavior is exercised by test_label_core.py
and test_label_library.py; here we test the new UI plumbing only.
"""
from __future__ import annotations

import os
import sys

import pytest


def _sp(**kw) -> dict:
    base = {
        "province": "FJ", "site": "YGLZ", "station": "B2",
        "id": "DLC001", "storage": "D95E",
        "collectionDate": "20260506", "photoDate": "20260508",
        "species": "背鳞虫 sp.01", "latin": "Polynoidae sp.",
        "collector": "杨德援", "family": "Polynoidae",
    }
    base.update(kw)
    return base


def _rna_sp(**kw) -> dict:
    kw.setdefault("id", "BLC001")
    kw.setdefault("storage", "RD75E")  # R-prefix → RNAlater bucket
    return _sp(**kw)


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    existing = QApplication.instance()
    if existing is not None:
        yield existing
    else:
        yield QApplication(sys.argv[:1])


# ── LabelListPanel ─────────────────────────────────────────────────────────────

class TestLabelListPanel:
    def _panel(self, qt_app, specs):
        from app.widgets.label_list_panel import LabelListPanel
        p = LabelListPanel()
        p.set_specimens(specs)
        return p

    def test_defaults_all_checked(self, qt_app):
        p = self._panel(qt_app, [_sp(), _rna_sp()])
        assert p.selected_indices() == [0, 1]

    def test_rna_marker_only_for_r_prefix(self, qt_app):
        p = self._panel(qt_app, [_sp(), _rna_sp()])
        assert 0 not in p._rna_badges
        assert 1 in p._rna_badges

    def test_select_rna_only(self, qt_app):
        p = self._panel(qt_app, [_sp(), _rna_sp()])
        p.select_rna_only()
        assert p.selected_indices() == [1]

    def test_select_sample_only(self, qt_app):
        p = self._panel(qt_app, [_sp(), _rna_sp()])
        p.select_sample_only()
        assert p.selected_indices() == [0]

    def test_clear_and_all(self, qt_app):
        p = self._panel(qt_app, [_sp(), _rna_sp()])
        p.clear_selection()
        assert p.selected_indices() == []
        p.select_all()
        assert p.selected_indices() == [0, 1]

    def test_rna_marker_click_switches_to_tissue(self, qt_app):
        p = self._panel(qt_app, [_sp(), _rna_sp()])
        seen = []
        p.current_changed.connect(lambda i, b: seen.append((i, b)))
        p._rna_badges[1].click()
        assert seen and seen[-1] == (1, "tissue")
        assert p.current() == (1, "tissue")

    def test_grid_tiles_count_matches_print_set(self, qt_app):
        p = self._panel(qt_app, [_sp(), _rna_sp()])
        p.set_view_mode("grid")
        # 2 sample + 1 RNAlater = 3
        assert len(p._tile_frames) == 3

    def test_select_only_uid(self, qt_app):
        from app.utils.label_core import unique_id
        specs = [_sp(), _rna_sp()]
        p = self._panel(qt_app, specs)
        uid = unique_id(specs[1])
        assert p.select_only_uid(uid) is True
        assert p.selected_indices() == [1]
        assert p.select_only_uid("NOPE-404") is False


# ── LabelDetailPanel ───────────────────────────────────────────────────────────

class TestLabelDetailPanel:
    def _panel(self, qt_app):
        from app.widgets.label_detail_panel import LabelDetailPanel
        return LabelDetailPanel()

    def test_tissue_toggle_disabled_without_rna(self, qt_app):
        d = self._panel(qt_app)
        d.set_context({"uniqueId": "X"}, has_rna=False, bucket="sample")
        assert not d._btn_tissue.isEnabled()

    def test_tissue_toggle_enabled_with_rna(self, qt_app):
        d = self._panel(qt_app)
        d.set_context({"uniqueId": "X"}, has_rna=True, bucket="sample")
        assert d._btn_tissue.isEnabled()

    def test_set_context_tissue_falls_back_when_no_rna(self, qt_app):
        d = self._panel(qt_app)
        d.set_context({"uniqueId": "X"}, has_rna=False, bucket="tissue")
        assert d.current_bucket() == "sample"

    def test_bucket_switch_emits(self, qt_app):
        d = self._panel(qt_app)
        d.set_context({"uniqueId": "X"}, has_rna=True, bucket="sample")
        seen = []
        d.bucket_changed.connect(lambda b: seen.append(b))
        d._switch_bucket("tissue")
        assert seen == ["tissue"]
        assert d.current_bucket() == "tissue"

    def test_size_change_emits_config(self, qt_app):
        d = self._panel(qt_app)
        seen = []
        d.config_changed.connect(lambda: seen.append(1))
        d._on_size("label_60x40")
        assert seen
        dims = d.selected_dims("sample")
        assert dims["w"] == 60.0 and dims["h"] == 40.0


# ── LabelEditorDialog ──────────────────────────────────────────────────────────

class TestLabelEditorDialog:
    def _dlg(self, qt_app):
        from app.widgets.label_editor_dialog import LabelEditorDialog
        tmpl = {"name": "标准", "rows": [{"fields": ["headerId"], "size": 9}],
                "qr": {"ecc": "Q"}}
        return LabelEditorDialog(tmpl, {"w": 50, "h": 30}, {"headerId": "T95E"})

    def test_edited_template_defaults_to_input(self, qt_app):
        dlg = self._dlg(qt_app)
        out = dlg.edited_template()
        assert out.get("name") == "标准"
        assert out.get("rows")

    def test_template_changed_is_captured(self, qt_app):
        dlg = self._dlg(qt_app)
        dlg._on_template_changed({"name": "改过的", "rows": [{"fields": ["uniqueId"]}],
                                  "qr": {"ecc": "Q"}})
        out = dlg.edited_template()
        assert out.get("name") == "改过的"

    def test_edited_template_is_a_copy(self, qt_app):
        dlg = self._dlg(qt_app)
        a = dlg.edited_template()
        a["name"] = "MUTATED"
        b = dlg.edited_template()
        assert b.get("name") != "MUTATED"
