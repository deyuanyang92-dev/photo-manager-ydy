"""helicon_params_panel.py — Helicon Focus parameter editor widget.

Mirrors the Helicon params side-panel in the web compose preview page
(app.js renderComposePage params section: method A/B/C + radius + smoothing).

Oracle: app.js:6884–6914 (compose page params panel).
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


class HeliconParamsPanel(QWidget):
    """Helicon Focus method/radius/smoothing editor.

    Signals
    -------
    params_changed()
        Emitted whenever any parameter changes.
    """

    params_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._method: int = 0
        self._radius: float = 4.0
        self._smoothing: int = 4
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        sec = QFrame()
        sec.setObjectName("Panel")
        sec_lay = QVBoxLayout(sec)
        sec_lay.setContentsMargins(12, 8, 12, 10)
        sec_lay.setSpacing(8)

        title = QLabel("Helicon 参数")
        title.setObjectName("Section")
        sec_lay.addWidget(title)

        # Method A/B/C toggle buttons
        meth_row = QHBoxLayout()
        meth_row.setContentsMargins(0, 0, 0, 0)
        meth_row.setSpacing(4)
        meth_lbl = QLabel("方法")
        meth_lbl.setObjectName("MutedSmall")
        meth_row.addWidget(meth_lbl)
        self._method_btns: list[QPushButton] = []
        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        for i, label in enumerate(["A", "B", "C"]):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(i == self._method)
            btn.setFixedSize(32, 26)
            btn.setObjectName("Primary" if i == self._method else "Outline")
            self._btn_group.addButton(btn, i)
            self._method_btns.append(btn)
            meth_row.addWidget(btn)
        meth_row.addStretch()
        self._btn_group.idClicked.connect(self._on_method_changed)
        sec_lay.addLayout(meth_row)

        # Radius slider (stored as x10 integer for int slider, 1.0–30.0 range)
        self._radius_slider, self._radius_lbl = self._make_slider(
            "半径", 10, 300, int(self._radius * 10), sec_lay, is_radius=True
        )
        self._radius_slider.valueChanged.connect(self._on_radius_changed)

        # Smoothing slider (1–10)
        self._smooth_slider, self._smooth_lbl = self._make_slider(
            "平滑", 1, 10, self._smoothing, sec_lay, is_radius=False
        )
        self._smooth_slider.valueChanged.connect(self._on_smooth_changed)

        root.addWidget(sec)
        root.addStretch()

    def _make_slider(
        self, label: str, min_val: int, max_val: int, init: int,
        parent_lay, *, is_radius: bool
    ) -> tuple[QSlider, QLabel]:
        row = QHBoxLayout()
        lbl_text = QLabel(label)
        lbl_text.setObjectName("MutedSmall")
        lbl_text.setFixedWidth(36)
        display = f"{init / 10:.1f}" if is_radius else str(init)
        val_lbl = QLabel(display)
        val_lbl.setObjectName("Mono")
        val_lbl.setFixedWidth(32)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(init)
        row.addWidget(lbl_text)
        row.addWidget(slider, stretch=1)
        row.addWidget(val_lbl)
        parent_lay.addLayout(row)
        return slider, val_lbl

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_method_changed(self, method_id: int) -> None:
        self._method = method_id
        for i, btn in enumerate(self._method_btns):
            btn.setObjectName("Primary" if i == method_id else "Outline")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.params_changed.emit()

    def _on_radius_changed(self, value: int) -> None:
        self._radius = value / 10.0
        self._radius_lbl.setText(f"{self._radius:.1f}")
        self.params_changed.emit()

    def _on_smooth_changed(self, value: int) -> None:
        self._smoothing = value
        self._smooth_lbl.setText(str(value))
        self.params_changed.emit()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_params(self) -> dict:
        """Return current params dict: {method: int, radius: float, smoothing: int}."""
        return {
            "method": self._method,
            "radius": self._radius,
            "smoothing": self._smoothing,
        }

    def set_params(self, params: dict) -> None:
        """Load params dict into the UI (does not emit params_changed)."""
        if "method" in params:
            self._method = int(params["method"])
            btn = self._btn_group.button(self._method)
            if btn:
                btn.setChecked(True)
            self._on_method_changed(self._method)
        if "radius" in params:
            self._radius = float(params["radius"])
            self._radius_slider.blockSignals(True)
            self._radius_slider.setValue(int(self._radius * 10))
            self._radius_lbl.setText(f"{self._radius:.1f}")
            self._radius_slider.blockSignals(False)
        if "smoothing" in params:
            self._smoothing = int(params["smoothing"])
            self._smooth_slider.blockSignals(True)
            self._smooth_slider.setValue(self._smoothing)
            self._smooth_lbl.setText(str(self._smoothing))
            self._smooth_slider.blockSignals(False)
