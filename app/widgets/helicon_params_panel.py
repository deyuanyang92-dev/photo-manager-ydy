"""helicon_params_panel.py — Helicon Focus rendering-params editor.

Strict replication of the Helicon Focus desktop "Rendering" tab parameters:
a vertical list of rendering-method radios (Method A weighted average / Method B
depth map / Method C pyramid), a Radius slider+spinbox, and a Smoothing
slider+spinbox. Radius is disabled for Method C because Helicon only uses the
radius in methods A/B (heliconsoft.com/helicon-focus-main-parameters/ — "it is
only available in A and B methods"). Factory defaults: method B, radius 8,
smoothing 4.

Ranges follow the Helicon 8 GUI (radius 1–40, smoothing 0–20). The public docs
give the defaults and the A/B-only radius rule but not the exact slider maxima,
so those bounds are best-known values, not doc-confirmed.

Public API preserved for callers (config dialog + workbench):
``get_params()`` / ``set_params({method, radius, smoothing})`` and the
``params_changed`` signal.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QLabel,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Helicon factory defaults.
_DEFAULT_METHOD = 1       # Method B (depth map)
_DEFAULT_RADIUS = 8
_DEFAULT_SMOOTHING = 4

# Helicon 8 GUI slider bounds (best-known; not doc-confirmed maxima).
_RADIUS_MIN, _RADIUS_MAX = 1, 40
_SMOOTH_MIN, _SMOOTH_MAX = 0, 20

# (label, tooltip) per method — labels mirror the Helicon desktop radios.
_METHODS = [
    ("Method A (weighted average)",
     "Computes a per-pixel contrast weight, then averages all source pixels."),
    ("Method B (depth map)",
     "Selects the source image with the sharpest pixel and builds a depth map."),
    ("Method C (pyramid)",
     "Pyramid (high/low frequency) approach; best for >100 frames. "
     "Radius is not used by this method."),
]


class HeliconParamsPanel(QWidget):
    """Helicon Focus rendering-method / radius / smoothing editor.

    Signals
    -------
    params_changed()
        Emitted whenever any parameter changes (not on ``set_params``).
    """

    params_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._method: int = _DEFAULT_METHOD
        self._radius: int = _DEFAULT_RADIUS
        self._smoothing: int = _DEFAULT_SMOOTHING
        self._setup_ui()
        self._apply_method_enable()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        meth_lbl = QLabel("Rendering method:")
        meth_lbl.setObjectName("Section")
        root.addWidget(meth_lbl)

        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        self._method_radios: list[QRadioButton] = []
        for i, (text, tip) in enumerate(_METHODS):
            rb = QRadioButton(text)
            rb.setToolTip(tip)
            rb.setChecked(i == self._method)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn_group.addButton(rb, i)
            self._method_radios.append(rb)
            root.addWidget(rb)
        self._btn_group.idClicked.connect(self._on_method_changed)

        grid = QGridLayout()
        grid.setContentsMargins(0, 6, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

        self._radius_lbl = QLabel("Radius:")
        self._radius_lbl.setObjectName("MutedSmall")
        self._radius_slider, self._radius_spin = self._make_slider_spin(
            _RADIUS_MIN, _RADIUS_MAX, self._radius)
        self._radius_slider.valueChanged.connect(self._on_radius_changed)
        self._radius_spin.valueChanged.connect(self._on_radius_changed)
        grid.addWidget(self._radius_lbl, 0, 0)
        grid.addWidget(self._radius_slider, 0, 1)
        grid.addWidget(self._radius_spin, 0, 2)

        smooth_lbl = QLabel("Smoothing:")
        smooth_lbl.setObjectName("MutedSmall")
        self._smooth_slider, self._smooth_spin = self._make_slider_spin(
            _SMOOTH_MIN, _SMOOTH_MAX, self._smoothing)
        self._smooth_slider.valueChanged.connect(self._on_smooth_changed)
        self._smooth_spin.valueChanged.connect(self._on_smooth_changed)
        grid.addWidget(smooth_lbl, 1, 0)
        grid.addWidget(self._smooth_slider, 1, 1)
        grid.addWidget(self._smooth_spin, 1, 2)

        grid.setColumnStretch(1, 1)
        root.addLayout(grid)
        root.addStretch()

    def _make_slider_spin(
        self, lo: int, hi: int, val: int
    ) -> tuple[QSlider, QSpinBox]:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(val)
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(val)
        spin.setFixedWidth(58)
        return slider, spin

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_method_changed(self, method_id: int) -> None:
        self._method = method_id
        self._apply_method_enable()
        self.params_changed.emit()

    def _apply_method_enable(self) -> None:
        # Radius is used only by methods A/B; Helicon greys it out for C.
        radius_on = self._method != 2
        self._radius_lbl.setEnabled(radius_on)
        self._radius_slider.setEnabled(radius_on)
        self._radius_spin.setEnabled(radius_on)

    def _on_radius_changed(self, value: int) -> None:
        if value == self._radius:
            return
        self._radius = value
        self._sync(self._radius_slider, self._radius_spin, value)
        self.params_changed.emit()

    def _on_smooth_changed(self, value: int) -> None:
        if value == self._smoothing:
            return
        self._smoothing = value
        self._sync(self._smooth_slider, self._smooth_spin, value)
        self.params_changed.emit()

    @staticmethod
    def _sync(slider: QSlider, spin: QSpinBox, value: int) -> None:
        """Mirror ``value`` onto the sibling widget without re-firing signals."""
        for w in (slider, spin):
            if w.value() != value:
                w.blockSignals(True)
                w.setValue(value)
                w.blockSignals(False)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_params(self) -> dict:
        """Return current params: {method: int, radius: int, smoothing: int}."""
        return {
            "method": self._method,
            "radius": self._radius,
            "smoothing": self._smoothing,
        }

    def set_params(self, params: dict) -> None:
        """Load params into the UI (does not emit ``params_changed``)."""
        if "method" in params:
            self._method = int(params["method"])
            rb = self._btn_group.button(self._method)
            if rb:
                rb.setChecked(True)
            self._apply_method_enable()
        if "radius" in params:
            self._set_silent(
                self._radius_slider, self._radius_spin,
                int(round(float(params["radius"]))))
            self._radius = self._radius_spin.value()
        if "smoothing" in params:
            self._set_silent(
                self._smooth_slider, self._smooth_spin,
                int(round(float(params["smoothing"]))))
            self._smoothing = self._smooth_spin.value()

    @staticmethod
    def _set_silent(slider: QSlider, spin: QSpinBox, value: int) -> None:
        for w in (slider, spin):
            w.blockSignals(True)
            w.setValue(value)
            w.blockSignals(False)
