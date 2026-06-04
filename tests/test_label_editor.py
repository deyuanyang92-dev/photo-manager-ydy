"""tests/test_label_editor.py — TDD tests for tasks 2-H and 2-I.

2-H: QR numeric position input spinboxes (mm units) in LabelEditorWidget.
2-I: ConstrainedFieldItem — row-aware y-clamping for field text items.
"""

from __future__ import annotations

import os
import sys

import pytest
from PyQt6.QtCore import QPointF


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    existing = QApplication.instance()
    if existing is not None:
        yield existing
    else:
        app = QApplication(sys.argv[:1])
        yield app


def _make_template():
    from app.utils.label_core import normalize_template
    return normalize_template({
        "name": "测试模板",
        "rows": [
            {"fields": ["uniqueId"], "size": 9, "style": ""},
            {"fields": ["speciesName"], "size": 8, "style": "bold"},
        ],
        "qr": {"position": "right", "sizePct": 0.4, "ecc": "Q"},
    })


def _make_data():
    return {
        "uniqueId": "FJ-YGLZ-B2-001",
        "speciesName": "背鳞虫 sp.01",
        "region": "福建·厦门",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2-I: ConstrainedFieldItem — row-aware y-clamping
# ══════════════════════════════════════════════════════════════════════════════

class TestConstrainedFieldItem:
    def _make_item(self, qt_app, text="测试文字", row_top=10.0, row_bottom=50.0):
        from app.widgets.label_editor import ConstrainedFieldItem
        item = ConstrainedFieldItem(text, row_top, row_bottom)
        return item

    def test_instantiates_without_crash(self, qt_app):
        item = self._make_item(qt_app)
        assert item is not None

    def test_constrained_field_clamps_y_above_top(self, qt_app):
        """Moving item above row_top must clamp y to row_top."""
        from app.widgets.label_editor import ConstrainedFieldItem
        from PyQt6.QtWidgets import QGraphicsScene
        scene = QGraphicsScene()
        item = ConstrainedFieldItem("text", row_top=20.0, row_bottom=80.0)
        scene.addItem(item)
        item.setPos(5.0, 5.0)
        actual_y = item.pos().y()
        assert actual_y >= 20.0, f"y={actual_y} should be clamped to row_top=20.0"

    def test_constrained_field_clamps_y_below_bottom(self, qt_app):
        """Moving item so its bottom exceeds row_bottom must clamp."""
        from app.widgets.label_editor import ConstrainedFieldItem
        from PyQt6.QtWidgets import QGraphicsScene
        scene = QGraphicsScene()
        item = ConstrainedFieldItem("text", row_top=10.0, row_bottom=200.0)
        scene.addItem(item)
        h = item.boundingRect().height()
        item.setPos(0.0, 200.0)
        actual_y = item.pos().y()
        assert actual_y <= 200.0 - h, (
            f"y={actual_y} should be clamped so bottom <= row_bottom=200.0"
        )

    def test_constrained_field_allows_x_movement(self, qt_app):
        """x is not constrained — any x value is accepted."""
        from app.widgets.label_editor import ConstrainedFieldItem
        from PyQt6.QtWidgets import QGraphicsScene
        scene = QGraphicsScene()
        item = ConstrainedFieldItem("text", row_top=10.0, row_bottom=80.0)
        scene.addItem(item)
        item.setPos(100.0, 15.0)
        assert item.pos().x() == pytest.approx(100.0, abs=1e-3)

    def test_constrained_field_y_within_range_unchanged(self, qt_app):
        """When y is within valid range, it must not be altered."""
        from app.widgets.label_editor import ConstrainedFieldItem
        from PyQt6.QtWidgets import QGraphicsScene
        scene = QGraphicsScene()
        item = ConstrainedFieldItem("text", row_top=10.0, row_bottom=80.0)
        scene.addItem(item)
        item.setPos(0.0, 15.0)
        assert item.pos().y() == pytest.approx(15.0, abs=1e-3)

    def test_has_sends_geometry_changes_flag(self, qt_app):
        """Item must have ItemSendsGeometryChanges flag set."""
        from app.widgets.label_editor import ConstrainedFieldItem
        from PyQt6.QtWidgets import QGraphicsItem
        item = ConstrainedFieldItem("text", 10.0, 80.0)
        flag = QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        assert bool(item.flags() & flag)


# ══════════════════════════════════════════════════════════════════════════════
# 2-H: QR numeric position spinboxes
# ══════════════════════════════════════════════════════════════════════════════

class TestQrSpinboxes:
    def _make_editor(self, qt_app):
        from app.widgets.label_editor import LabelEditorWidget
        tmpl = _make_template()
        data = _make_data()
        return LabelEditorWidget(tmpl, {"w": 60, "h": 40}, data)

    def test_qr_x_spin_exists(self, qt_app):
        editor = self._make_editor(qt_app)
        assert hasattr(editor, "_qr_x_spin")

    def test_qr_y_spin_exists(self, qt_app):
        editor = self._make_editor(qt_app)
        assert hasattr(editor, "_qr_y_spin")

    def test_qr_x_spin_range(self, qt_app):
        from PyQt6.QtWidgets import QDoubleSpinBox
        editor = self._make_editor(qt_app)
        spin = editor._qr_x_spin
        assert isinstance(spin, QDoubleSpinBox)
        assert spin.minimum() == pytest.approx(0.0)
        assert spin.maximum() == pytest.approx(200.0)

    def test_qr_y_spin_range(self, qt_app):
        from PyQt6.QtWidgets import QDoubleSpinBox
        editor = self._make_editor(qt_app)
        spin = editor._qr_y_spin
        assert isinstance(spin, QDoubleSpinBox)
        assert spin.minimum() == pytest.approx(0.0)
        assert spin.maximum() == pytest.approx(200.0)

    def test_qr_x_spin_suffix(self, qt_app):
        editor = self._make_editor(qt_app)
        assert editor._qr_x_spin.suffix() == " mm"

    def test_qr_y_spin_suffix(self, qt_app):
        editor = self._make_editor(qt_app)
        assert editor._qr_y_spin.suffix() == " mm"

    def test_qr_x_spin_decimals(self, qt_app):
        editor = self._make_editor(qt_app)
        assert editor._qr_x_spin.decimals() == 1

    def test_qr_y_spin_decimals(self, qt_app):
        editor = self._make_editor(qt_app)
        assert editor._qr_y_spin.decimals() == 1

    def test_qr_spin_updates_on_drag(self, qt_app):
        """When scene emits qr_moved, spins update to corresponding mm values."""
        editor = self._make_editor(qt_app)
        from app.widgets.label_editor import _mm_to_px, _px_to_mm
        x_px, y_px = 50.0, 30.0
        editor._scene.qr_moved.emit(x_px, y_px)
        expected_x = _px_to_mm(x_px)
        expected_y = _px_to_mm(y_px)
        assert editor._qr_x_spin.value() == pytest.approx(expected_x, abs=0.1)
        assert editor._qr_y_spin.value() == pytest.approx(expected_y, abs=0.1)

    def test_qr_spin_changed_moves_qr(self, qt_app):
        """Setting spin value must move the QR item in the scene."""
        editor = self._make_editor(qt_app)
        from app.widgets.label_editor import _mm_to_px
        if editor._scene.qr_item is None:
            pytest.skip("No QR item in scene (qrcode library not installed)")
        editor._qr_x_spin.setValue(5.0)
        editor._qr_y_spin.setValue(3.0)
        qr_pos = editor._scene.qr_item.pos()
        assert qr_pos.x() == pytest.approx(_mm_to_px(5.0), abs=1.0)
        assert qr_pos.y() == pytest.approx(_mm_to_px(3.0), abs=1.0)

    def test_no_feedback_loop_on_spin_change(self, qt_app):
        """Spin → scene → spin must not recurse or freeze."""
        editor = self._make_editor(qt_app)
        if editor._scene.qr_item is None:
            pytest.skip("No QR item in scene")
        # Set spin value — if feedback loop existed this would recurse
        editor._qr_x_spin.setValue(10.0)
        editor._qr_y_spin.setValue(8.0)
        assert editor._qr_x_spin.value() == pytest.approx(10.0, abs=0.1)

    def test_qr_scene_has_qr_moved_signal(self, qt_app):
        """LabelScene must expose a qr_moved(float, float) signal."""
        editor = self._make_editor(qt_app)
        assert hasattr(editor._scene, "qr_moved")
