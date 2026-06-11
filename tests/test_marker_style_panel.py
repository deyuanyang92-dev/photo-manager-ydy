"""test_marker_style_panel.py — 站位标识样式面板.

面板发 style_changed(dict)，style()/set_style() 往返。纯 UI，持久化由视图负责。

Run:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_marker_style_panel.py -v
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])


def _panel(initial=None):
    from app.widgets.marker_style_panel import MarkerStylePanel
    return MarkerStylePanel(initial=initial)


class TestStylePanel:
    def test_instantiates_with_defaults(self):
        p = _panel()
        st = p.style()
        for k in ("shape", "size", "fill", "show_label", "label_source"):
            assert k in st

    def test_set_style_round_trip(self):
        p = _panel()
        p.set_style({"size": 200, "fill": "#ff0000", "show_label": True,
                     "label_source": "count"})
        st = p.style()
        assert st["size"] == 200
        assert st["fill"].lower() == "#ff0000"
        assert st["show_label"] is True
        assert st["label_source"] == "count"

    def test_emits_style_changed_on_size(self):
        p = _panel()
        got = []
        p.style_changed.connect(got.append)
        p._size.setValue(p._size.value() + 10)
        assert got
        assert got[-1]["size"] == p._size.value()

    def test_emits_on_label_toggle(self):
        p = _panel()
        got = []
        p.style_changed.connect(got.append)
        p._show_label.setChecked(not p._show_label.isChecked())
        assert got and "show_label" in got[-1]

    def test_color_can_be_typed_directly(self):
        p = _panel()
        got = []
        p.style_changed.connect(got.append)
        p._fill_edit.setText("#123456")
        p._on_color_edited("fill")
        assert got
        assert got[-1]["fill"] == "#123456"
        assert p.style()["fill"] == "#123456"

    def test_invalid_typed_color_does_not_replace_current_style(self):
        p = _panel({"fill": "#123456"})
        got = []
        p.style_changed.connect(got.append)
        p._fill_edit.setText("not-a-color")
        p._on_color_edited("fill")
        assert got == []
        assert p.style()["fill"] == "#123456"

    def test_reset_restores_defaults_and_emits(self):
        p = _panel({"size": 200, "fill": "#ff0000", "show_label": True})
        got = []
        p.style_changed.connect(got.append)
        p.reset_style()
        assert got
        assert p.style()["size"] == 80
        assert p.style()["fill"] == "#29b9ab"
        assert p.style()["show_label"] is False
