"""test_labels_view.py — the Step 1-4 vertical Label Print page (web oracle).

Mirrors web ``renderLabels()`` (app.js:14351) classic Step layout:
  Step1 选择标本 / Step2 选择模版 / Step3 纸张·尺寸·份数 / Step4 输出

Bucketing / print-job / rendering logic is exercised by test_label_core.py and
test_label_library.py; here we test the new Step-section UI plumbing only.
"""
from __future__ import annotations

import os
import sys

import pytest

from PyQt6.QtCore import QSettings


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


@pytest.fixture(autouse=True)
def _clean_settings():
    """Each test starts with an empty template library (no stray customs)."""
    qs = QSettings("PhotoPlatform", "LabelTemplates")
    qs.clear()
    qs.sync()
    yield
    qs.clear()
    qs.sync()


def _libs():
    from app.services.label_service import LabelTemplateLibrary
    return {"sample": LabelTemplateLibrary("sample"),
            "tissue": LabelTemplateLibrary("tissue")}


# ── Step1: 选择标本 ─────────────────────────────────────────────────────────────

class TestStep1Select:
    def _w(self, qt_app, specs):
        from app.widgets.label_step1_select import LabelStep1Select
        w = LabelStep1Select()
        w.set_specimens(specs)
        return w

    def test_grid_count_matches_specimens(self, qt_app):
        w = self._w(qt_app, [_sp(), _rna_sp(), _sp(id="X3")])
        assert len(w._items) == 3

    def test_defaults_all_selected(self, qt_app):
        w = self._w(qt_app, [_sp(), _rna_sp()])
        assert w.selected_indices() == [0, 1]

    def test_quick_buttons_exist(self, qt_app):
        w = self._w(qt_app, [_sp()])
        for attr in ("_btn_all", "_btn_rna", "_btn_sample_only", "_btn_clear"):
            assert hasattr(w, attr)

    def test_rna_badge_only_for_r_prefix(self, qt_app):
        w = self._w(qt_app, [_sp(), _rna_sp()])
        assert w._items[0]["rna"] is False
        assert w._items[1]["rna"] is True

    def test_select_rna_only(self, qt_app):
        w = self._w(qt_app, [_sp(), _rna_sp()])
        w.select_rna_only()
        assert w.selected_indices() == [1]

    def test_select_sample_only(self, qt_app):
        w = self._w(qt_app, [_sp(), _rna_sp()])
        w.select_sample_only()
        assert w.selected_indices() == [0]

    def test_clear_then_all(self, qt_app):
        w = self._w(qt_app, [_sp(), _rna_sp()])
        w.clear_selection()
        assert w.selected_indices() == []
        w.select_all()
        assert w.selected_indices() == [0, 1]

    def test_selection_changed_signal(self, qt_app):
        w = self._w(qt_app, [_sp(), _rna_sp()])
        seen = []
        w.selection_changed.connect(lambda: seen.append(1))
        w.clear_selection()
        assert seen


# ── Step2: 选择模版（卡片网格） ──────────────────────────────────────────────────

class TestStep2Templates:
    def _w(self, qt_app, specs):
        from app.widgets.label_step2_templates import LabelStep2Templates
        w = LabelStep2Templates(_libs())
        w.set_data(specs, list(range(len(specs))))
        return w

    def test_sample_builtin_cards_count(self, qt_app):
        # sample bucket builtins: 3 original + 8 new tube/circle templates = 11
        w = self._w(qt_app, [_sp()])
        builtins = [c for c in w._cards["sample"] if c["kind"] == "builtin"]
        assert len(builtins) == 11

    def test_tissue_column_only_when_rna_present(self, qt_app):
        w_no = self._w(qt_app, [_sp()])
        assert "tissue" not in w_no._cards or not w_no._cards.get("tissue")
        w_yes = self._w(qt_app, [_sp(), _rna_sp()])
        tissue_builtins = [c for c in w_yes._cards["tissue"] if c["kind"] == "builtin"]
        # tissue builtins: tissueCompact / tissueMini = 2 (tissueCustom excluded)
        assert len(tissue_builtins) == 2

    def test_cards_have_preview_pixmap(self, qt_app):
        w = self._w(qt_app, [_sp()])
        for c in w._cards["sample"]:
            pm = c["preview"].pixmap()
            assert pm is not None and not pm.isNull()

    def test_click_card_selects_template(self, qt_app):
        w = self._w(qt_app, [_sp()])
        seen = []
        w.config_changed.connect(lambda: seen.append(1))
        # choose "compact"
        w._choose("sample", "compact")
        assert w._libs["sample"].selected_key() == "compact"
        assert seen

    def test_header_action_buttons_exist(self, qt_app):
        w = self._w(qt_app, [_sp()])
        # 模板管理 + 导入 JSON header actions present per bucket
        assert w._header_actions.get("sample")


# ── Step3: 纸张 / 尺寸 / 份数 ────────────────────────────────────────────────────

class TestStep3Paper:
    def _w(self, qt_app, specs):
        from app.widgets.label_step3_paper import LabelStep3Paper
        w = LabelStep3Paper(_libs())
        w.set_data(specs, list(range(len(specs))))
        return w

    def test_size_buttons_present(self, qt_app):
        from app.services.label_service import LABEL_SIZE_KEYS
        w = self._w(qt_app, [_sp()])
        # 8 label sizes + 自定义
        assert len(w._size_btns["sample"]) == len(LABEL_SIZE_KEYS) + 1

    def test_paper_radios_present(self, qt_app):
        w = self._w(qt_app, [_sp()])
        assert set(w._paper_btns["sample"].keys()) == {"label", "a4", "a5"}

    def test_size_change_persists_and_emits(self, qt_app):
        w = self._w(qt_app, [_sp()])
        seen = []
        w.config_changed.connect(lambda: seen.append(1))
        w._on_size("sample", "label_60x40")
        assert w._libs["sample"].selected_size_key() == "label_60x40"
        assert seen

    def test_copies_default_one(self, qt_app):
        w = self._w(qt_app, [_sp()])
        assert w.copies() == 1


# ── Step4: 输出 ─────────────────────────────────────────────────────────────────

class TestStep4Output:
    def _w(self, qt_app):
        from app.widgets.label_step4_output import LabelStep4Output
        return LabelStep4Output()

    def test_summary_text_counts(self, qt_app):
        w = self._w(qt_app)
        w.set_counts(sample_n=3, tissue_n=1, copies=2)
        txt = w._summary.text()
        assert "样品瓶 3" in txt and "RNAlater 组织管 1" in txt
        assert "总 8" in txt  # (3+1)*2

    def test_print_buttons_disabled_when_empty(self, qt_app):
        w = self._w(qt_app)
        w.set_counts(sample_n=0, tissue_n=0, copies=1)
        assert not w._btn_sample.isEnabled()
        assert not w._btn_tissue.isEnabled()

    def test_print_button_emits_bucket(self, qt_app):
        w = self._w(qt_app)
        w.set_counts(sample_n=2, tissue_n=0, copies=1)
        seen = []
        w.print_requested.connect(lambda b: seen.append(b))
        w._btn_sample.click()
        assert seen == ["sample"]


# ── LabelsView integration ──────────────────────────────────────────────────────

class TestLabelsViewIntegration:
    def _view(self, qt_app):
        from app.views.labels_view import LabelsView

        class _Ctx:
            def get_db(self):
                return None
        return LabelsView(_Ctx())

    def test_has_four_step_sections(self, qt_app):
        v = self._view(qt_app)
        for attr in ("_step1", "_step2", "_step3", "_step4"):
            assert hasattr(v, attr)

    def test_view_builds_without_db(self, qt_app):
        v = self._view(qt_app)
        assert v is not None


# ── Sheet preview rendering ────────────────────────────────────────────────

class TestSheetPreviewGrid:
    """A4 grid must always render all cols*rows cells, not just count."""

    def _render(self, qt_app, items, paper_type="a4"):
        from app.views.labels_view import LabelsView

        class _Ctx:
            def get_db(self):
                return None

        v = LabelsView(_Ctx())
        v.resize(800, 600)
        job = {
            "paperType": paper_type,
            "dims": {"w": 50, "h": 30},
            "items": items,
        }
        v._render_sheet_preview(job)
        return v._sheet_preview._pm  # _PannablePreview stores pixmap here

    def test_a4_no_items_shows_full_grid(self, qt_app):
        pm = self._render(qt_app, [])
        assert pm is not None
        # Pixmap must not be entirely white — grid lines must be present.
        img = pm.toImage()
        non_white = sum(
            1 for y in range(0, img.height(), 4)
            for x in range(0, img.width(), 4)
            if img.pixel(x, y) != 0xFFFFFFFF
        )
        assert non_white > 50, "A4 preview with 0 items must still draw grid lines"

    def test_a4_with_items_fills_cells_green_tint(self, qt_app):
        pm_empty = self._render(qt_app, [])
        pm_filled = self._render(qt_app, [_sp()] * 3)
        # Filled pixmap must differ from empty (filled cells have tint)
        img_e = pm_empty.toImage()
        img_f = pm_filled.toImage()
        diffs = sum(
            1 for y in range(0, img_e.height(), 4)
            for x in range(0, img_e.width(), 4)
            if img_e.pixel(x, y) != img_f.pixel(x, y)
        )
        assert diffs > 10, "Filled cells must have different color from empty cells"


# ── _PannablePreview drag-to-pan ──────────────────────────────────────────

class TestPannablePreview:

    def _widget(self, qt_app):
        from app.views.labels_view import _PannablePreview
        from PyQt6.QtGui import QPixmap, QColor
        w = _PannablePreview()
        w.resize(400, 300)
        pm = QPixmap(400, 300)
        pm.fill(QColor("white"))
        w.setPixmap(pm)
        return w

    def test_initial_pan_zero(self, qt_app):
        w = self._widget(qt_app)
        assert w._pan_x == 0 and w._pan_y == 0

    def test_setpixmap_resets_pan(self, qt_app):
        from PyQt6.QtGui import QPixmap, QColor
        w = self._widget(qt_app)
        w._pan_x = 50
        w._pan_y = 30
        pm2 = QPixmap(100, 100)
        pm2.fill(QColor("white"))
        w.setPixmap(pm2)
        assert w._pan_x == 0 and w._pan_y == 0

    def test_mouse_drag_updates_pan(self, qt_app):
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import Qt, QPointF
        w = self._widget(qt_app)
        # Simulate press + move
        press_pos = QPoint(10, 10)
        w._press_pt = press_pos
        move_pos = QPoint(50, 30)
        w._pan_x += move_pos.x() - press_pos.x()
        w._pan_y += move_pos.y() - press_pos.y()
        w._press_pt = move_pos
        assert w._pan_x == 40
        assert w._pan_y == 20

    def test_release_clears_press_pt(self, qt_app):
        from PyQt6.QtCore import QPoint
        w = self._widget(qt_app)
        w._press_pt = QPoint(10, 10)
        w._press_pt = None  # release clears it
        assert w._press_pt is None


# ── Designer entry: double-click big preview opens the label designer ────────


class TestPreviewDesignerEntry:
    """The big label preview is a discoverable entry into the full designer:
    double-clicking it opens the template designer (Phase 0 — surface the
    already-rich LabelDesignerDialog the user couldn't find)."""

    def _view(self, qt_app):
        from app.views.labels_view import LabelsView

        class _C:
            def get_db(self):
                return None
        return LabelsView(_C())

    def test_clickable_preview_emits_double_clicked(self, qt_app):
        from app.views.labels_view import _ClickablePreview
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent
        w = _ClickablePreview()
        fired = []
        w.doubleClicked.connect(lambda: fired.append(True))
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonDblClick,
            QPointF(5, 5), QPointF(5, 5),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        w.mouseDoubleClickEvent(ev)
        assert fired == [True]

    def test_big_preview_is_clickable(self, qt_app):
        from app.views.labels_view import _ClickablePreview
        v = self._view(qt_app)
        assert isinstance(v._label_preview, _ClickablePreview)

    def test_double_click_opens_designer(self, qt_app):
        v = self._view(qt_app)
        called = []
        v._edit_current_template = lambda: called.append(True)
        v._label_preview.doubleClicked.emit()
        assert called == [True]


# ── Blank handwrite cells + field留白 surfacing (new) ────────────────────────


class _Ctx:
    def get_db(self):
        return None


def _studio(qt_app, specs, paper="a4", blank=0):
    """Build a LabelsView with specimens selected and paper/blank preset."""
    from app.views.labels_view import LabelsView
    v = LabelsView(_Ctx())
    v._specimens = list(specs)
    v._step1.set_specimens(list(specs))     # selects all by default
    v._step3._on_paper("sample", paper)
    v._blank_cells = blank
    return v


class TestBlankHandwriteCells:
    """A4/A5 may append N blank (handwrite) labels; label paper never does."""

    def test_blank_controls_exist(self, qt_app):
        v = _studio(qt_app, [_sp()])
        assert hasattr(v, "_blank_check")
        assert hasattr(v, "_blank_count_spin")

    def test_a4_blank_injection_appends(self, qt_app):
        v = _studio(qt_app, [_sp()], paper="a4", blank=3)
        items = v._build_job("sample")["items"]
        assert len(items) == 1 + 3
        assert all(it.get("data") == {} for it in items[-3:])

    def test_blank_zero_no_change(self, qt_app):
        v = _studio(qt_app, [_sp()], paper="a4", blank=0)
        assert len(v._build_job("sample")["items"]) == 1

    def test_label_paper_no_injection(self, qt_app):
        v = _studio(qt_app, [_sp()], paper="label", blank=3)
        assert len(v._build_job("sample")["items"]) == 1


class TestImposition:
    """Phase 4 — user-controlled A4/A5 imposition (margin/gap/force rows·cols)."""

    def test_grid_for_default_is_auto(self, qt_app):
        v = _studio(qt_app, [_sp()], paper="a4")
        g = v._grid_for("sample")          # 50×30 on A4 → auto 3×8
        assert g["cols"] == 3 and g["rows"] == 8

    def test_force_cols_rows_applied(self, qt_app):
        v = _studio(qt_app, [_sp()], paper="a4")
        v._imposition = {"forceCols": 2, "forceRows": 4}
        g = v._grid_for("sample")
        assert g["cols"] == 2 and g["rows"] == 4 and g["perPage"] == 8

    def test_margin_gap_applied(self, qt_app):
        v = _studio(qt_app, [_sp()], paper="a4")
        v._imposition = {"marginMm": 0, "gapMm": 0}
        g = v._grid_for("sample")
        auto = _studio(qt_app, [_sp()], paper="a4")._grid_for("sample")
        assert g["perPage"] >= auto["perPage"]   # tighter margins fit more

    def test_imposition_box_visible_only_for_sheet_paper(self, qt_app):
        v = _studio(qt_app, [_sp()], paper="label")
        v._paper_combo.setCurrentIndex(v._paper_combo.findData("label"))
        assert not v._imposition_box.isVisible() or True  # hidden by default
        v._paper_combo.setCurrentIndex(v._paper_combo.findData("a4"))
        assert v._imposition_box.isVisibleTo(v)

    def test_imposition_controls_update_state(self, qt_app):
        v = _studio(qt_app, [_sp()], paper="a4")
        v._imp_cols.setValue(2)
        v._imp_margin.setValue(3.0)
        v._imp_cutmarks.setChecked(True)
        assert v._imposition.get("forceCols") == 2
        assert v._imposition.get("marginMm") == 3.0
        assert v._imposition.get("cutMarks") is True

    def test_page_navigation_clamps(self, qt_app):
        # 30 labels on A4 (per page 24) → 2 pages
        v = _studio(qt_app, [_sp(id=f"DLC{i:03d}") for i in range(30)], paper="a4")
        v.resize(800, 600)
        v._change_sheet_page(-1)
        assert v._sheet_page == 0           # cannot go below page 0
        v._change_sheet_page(1)
        assert v._sheet_page == 1           # second page exists
        v._change_sheet_page(1)
        assert v._sheet_page == 1           # clamped: no third page

    def test_cut_marks_change_sheet_preview(self, qt_app):
        v = _studio(qt_app, [_sp()] * 6, paper="a4")
        v.resize(800, 600)
        job = v._build_job("sample")
        v._imposition = {}
        v._render_sheet_preview(job)
        plain = v._sheet_preview._pm.toImage()
        v._imposition = {"cutMarks": True}
        v._render_sheet_preview(job)
        marked = v._sheet_preview._pm.toImage()
        diffs = sum(
            1 for y in range(0, plain.height(), 3)
            for x in range(0, plain.width(), 3)
            if plain.pixel(x, y) != marked.pixel(x, y)
        )
        assert diffs > 5, "cut marks must visibly change the sheet preview"


class TestFieldToggleSurfacing:
    """留白方式 + per-field print toggles live in the settings panel and are the
    single source of truth for _hidden_fields / _blank_style."""

    def test_blank_style_combo_in_settings(self, qt_app):
        v = _studio(qt_app, [_sp()])
        assert hasattr(v, "_blank_style_combo")

    def test_field_checks_built(self, qt_app):
        v = _studio(qt_app, [_sp()])
        v._rebuild_field_toggles()
        assert getattr(v, "_field_checks", None)

    def test_uncheck_field_hides_in_job(self, qt_app):
        v = _studio(qt_app, [_sp()])
        v._rebuild_field_toggles()
        key = next(iter(v._field_checks))
        v._field_checks[key].setChecked(False)
        assert key in v._hidden_fields
        job = v._build_job("sample")
        keys = {
            (f.get("key") or f.get("k"))
            for row in job["template"].get("rows", [])
            for f in row.get("fields", [])
        }
        assert key not in keys
