"""effects.py — small Qt visual-effect helpers (soft card elevation).

QSS cannot express ``box-shadow``; Qt expresses it through
``QGraphicsDropShadowEffect``.  ``apply_card_shadow`` gives a panel the soft,
diffuse drop that makes it read as *floating* above the canvas — the second
half (with the 1px inner top-highlight from the QSS) of the premium card look.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget


def apply_card_shadow(
    widget: QWidget,
    *,
    blur: int = 26,
    y: int = 6,
    alpha: int = 90,
) -> QGraphicsDropShadowEffect:
    """Attach a soft, downward drop shadow to *widget* and return it.

    Tuned for dark-canvas elevation: a wide, low-alpha blur that reads as
    ambient depth rather than a hard outline.  Each widget needs its own
    effect instance (Qt forbids sharing), so callers create one per card.
    """
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setXOffset(0)
    eff.setYOffset(y)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)
    return eff
