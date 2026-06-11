"""helicon_params_panel.py — Helicon Focus rendering-params editor.

Qt editor for Helicon Focus' own "Rendering method" controls:

  - Rendering method radios, value 0/1/2 (app.js:7101-7104):
      0 = Method A (weighted average)
      1 = Method B (depth map)
      2 = Method C (pyramid)
  - Radius   slider+number, range 1–30 step 0.5 (FLOAT)
  - Smoothing slider+number, range 1–10 step 1   (INT)
  - Reset button -> Method B / Radius 8 / Smoothing 4

The installed Helicon Focus help documents Radius/Smoothing as the two main
stacking controls, shows practical Radius examples up to 22, and lists the CLI
flags as ``-mp``, ``-rp`` and ``-sp``. Radius is disabled for Method C because
Helicon uses it only for A/B.

Public API: ``get_params()`` / ``set_params({method, radius, smoothing})`` and
``params_changed``. ``get_params()['radius']`` is an int when whole (8 → ``8``)
and a float otherwise (4.5 → ``4.5``), so the CLI renders ``-rp:8`` / ``-rp:4.5``
cleanly.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Helicon Focus factory defaults.
HELICON_DEFAULT_METHOD = 1       # Method B (depth map)
HELICON_DEFAULT_RADIUS = 8.0
HELICON_DEFAULT_SMOOTHING = 4

# Helicon desktop-style slider ranges. The bundled/online help demonstrates
# Radius=22 for normal use, so the former web-prototype 0-8 cap was too small.
HELICON_RADIUS_MIN, HELICON_RADIUS_MAX, HELICON_RADIUS_STEP = 1.0, 30.0, 0.5
HELICON_SMOOTH_MIN, HELICON_SMOOTH_MAX = 1, 10

# Backward-compatible internal aliases used by tests and helper methods.
_DEFAULT_METHOD = HELICON_DEFAULT_METHOD
_DEFAULT_RADIUS = HELICON_DEFAULT_RADIUS
_DEFAULT_SMOOTHING = HELICON_DEFAULT_SMOOTHING
_RADIUS_MIN, _RADIUS_MAX, _RADIUS_STEP = (
    HELICON_RADIUS_MIN,
    HELICON_RADIUS_MAX,
    HELICON_RADIUS_STEP,
)
_SMOOTH_MIN, _SMOOTH_MAX = HELICON_SMOOTH_MIN, HELICON_SMOOTH_MAX

# Radius slider is integer-only in Qt -> scale by 1/_RADIUS_STEP (x2).
_R_SCALE = int(round(1.0 / _RADIUS_STEP))  # 2

# (label, tooltip) per method — labels mirror the oracle radios.
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
        Emitted on any user change (not on ``set_params``).
    """

    params_changed = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._method: int = _DEFAULT_METHOD
        self._radius: float = _DEFAULT_RADIUS
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

        # Radius: float slider (x2) + double spinbox, matching Helicon desktop use.
        self._radius_lbl = QLabel("Radius:")
        self._radius_lbl.setObjectName("MutedSmall")
        radius_tip = (
            "Helicon Radius controls the analysed area around each pixel. "
            "Small values keep fine intersecting detail; higher values reduce noise, halos, and edge artifacts."
        )
        self._radius_lbl.setToolTip(radius_tip)
        self._radius_slider = QSlider(Qt.Orientation.Horizontal)
        self._radius_slider.setRange(int(_RADIUS_MIN * _R_SCALE), int(_RADIUS_MAX * _R_SCALE))
        self._radius_slider.setValue(int(self._radius * _R_SCALE))
        self._radius_slider.setToolTip(radius_tip)
        self._radius_spin = QDoubleSpinBox()
        self._radius_spin.setRange(_RADIUS_MIN, _RADIUS_MAX)
        self._radius_spin.setSingleStep(_RADIUS_STEP)
        self._radius_spin.setDecimals(1)
        self._radius_spin.setValue(self._radius)
        self._radius_spin.setFixedWidth(74)
        self._radius_spin.setToolTip(radius_tip)
        self._radius_slider.valueChanged.connect(self._on_radius_slider)
        self._radius_spin.valueChanged.connect(self._on_radius_spin)
        grid.addWidget(self._radius_lbl, 0, 0)
        grid.addWidget(self._radius_slider, 0, 1)
        grid.addWidget(self._radius_spin, 0, 2)

        # Smoothing: int slider + spinbox.
        smooth_lbl = QLabel("Smoothing:")
        smooth_lbl.setObjectName("MutedSmall")
        smooth_tip = (
            "Helicon Smoothing controls transition smoothing. "
            "For Method B it smooths the depth map; for A/C it smooths combined sharp areas."
        )
        smooth_lbl.setToolTip(smooth_tip)
        self._smooth_slider = QSlider(Qt.Orientation.Horizontal)
        self._smooth_slider.setRange(_SMOOTH_MIN, _SMOOTH_MAX)
        self._smooth_slider.setValue(self._smoothing)
        self._smooth_slider.setToolTip(smooth_tip)
        self._smooth_spin = QSpinBox()
        self._smooth_spin.setRange(_SMOOTH_MIN, _SMOOTH_MAX)
        self._smooth_spin.setValue(self._smoothing)
        self._smooth_spin.setFixedWidth(64)
        self._smooth_spin.setToolTip(smooth_tip)
        self._smooth_slider.valueChanged.connect(self._on_smooth_changed)
        self._smooth_spin.valueChanged.connect(self._on_smooth_changed)
        grid.addWidget(smooth_lbl, 1, 0)
        grid.addWidget(self._smooth_slider, 1, 1)
        grid.addWidget(self._smooth_spin, 1, 2)

        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        # Reset button: Method B / Radius 8 / Smoothing 4.
        reset_row = QHBoxLayout()
        reset_row.setContentsMargins(0, 4, 0, 0)
        self._reset_btn = QPushButton("Reset")
        self._reset_btn.setObjectName("Outline")
        self._reset_btn.setToolTip("复位 渲染方法 / Radius / Smoothing 到默认 (B / 8 / 4)")
        self._reset_btn.clicked.connect(self.reset_to_defaults)
        reset_row.addWidget(self._reset_btn)
        reset_row.addStretch()
        root.addLayout(reset_row)

        root.addStretch()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_method_changed(self, method_id: int) -> None:
        self._method = method_id
        self._apply_method_enable()
        self.params_changed.emit()

    def _apply_method_enable(self) -> None:
        # Radius is used only by methods A/B; disabled for C (pyramid).
        radius_on = self._method != 2
        self._radius_lbl.setEnabled(radius_on)
        self._radius_slider.setEnabled(radius_on)
        self._radius_spin.setEnabled(radius_on)

    def _on_radius_slider(self, raw: int) -> None:
        value = raw / _R_SCALE
        if value == self._radius:
            return
        self._radius = value
        self._radius_spin.blockSignals(True)
        self._radius_spin.setValue(value)
        self._radius_spin.blockSignals(False)
        self.params_changed.emit()

    def _on_radius_spin(self, value: float) -> None:
        if value == self._radius:
            return
        self._radius = value
        raw = int(round(value * _R_SCALE))
        if self._radius_slider.value() != raw:
            self._radius_slider.blockSignals(True)
            self._radius_slider.setValue(raw)
            self._radius_slider.blockSignals(False)
        self.params_changed.emit()

    def _on_smooth_changed(self, value: int) -> None:
        if value == self._smoothing:
            return
        self._smoothing = value
        for w in (self._smooth_slider, self._smooth_spin):
            if w.value() != value:
                w.blockSignals(True)
                w.setValue(value)
                w.blockSignals(False)
        self.params_changed.emit()

    # ── Public API ────────────────────────────────────────────────────────────

    def reset_to_defaults(self) -> None:
        """Reset to Helicon defaults: Method B / Radius 8 / Smoothing 4.

        Emits ``params_changed`` so listeners (settings auto-save) persist the reset.
        """
        self.set_params({
            "method": _DEFAULT_METHOD,
            "radius": _DEFAULT_RADIUS,
            "smoothing": _DEFAULT_SMOOTHING,
        })
        self.params_changed.emit()

    def get_params(self) -> dict:
        """Return {method:int, radius:(int|float), smoothing:int}.

        Radius is int when whole (8 → 8) else float (4.5 → 4.5), so the CLI
        renders ``-rp:8`` / ``-rp:4.5`` exactly like the oracle (app.js:7288).
        """
        r = self._radius
        radius = int(r) if float(r).is_integer() else r
        return {"method": self._method, "radius": radius, "smoothing": self._smoothing}

    def set_params(self, params: dict) -> None:
        """Load params into the UI (does not emit ``params_changed``)."""
        if "method" in params:
            self._method = int(params["method"])
            rb = self._btn_group.button(self._method)
            if rb:
                rb.setChecked(True)
            self._apply_method_enable()
        if "radius" in params:
            self._radius_spin.blockSignals(True)
            self._radius_spin.setValue(float(params["radius"]))
            self._radius_spin.blockSignals(False)
            self._radius = self._radius_spin.value()
            self._radius_slider.blockSignals(True)
            self._radius_slider.setValue(int(round(self._radius * _R_SCALE)))
            self._radius_slider.blockSignals(False)
        if "smoothing" in params:
            self._smooth_spin.blockSignals(True)
            self._smooth_spin.setValue(int(round(float(params["smoothing"]))))
            self._smooth_spin.blockSignals(False)
            self._smoothing = self._smooth_spin.value()
            self._smooth_slider.blockSignals(True)
            self._smooth_slider.setValue(self._smoothing)
            self._smooth_slider.blockSignals(False)
