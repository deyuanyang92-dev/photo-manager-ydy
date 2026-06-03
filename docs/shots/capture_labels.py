"""capture_labels.py — Offscreen screenshot of LabelsView across all 4 steps.

Usage (WSL):
    QT_QPA_PLATFORM=offscreen python docs/shots/capture_labels.py

Saves: docs/shots/page_labels.png  (vertical strip, all 4 steps)
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtCore import Qt, QTimer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.app_context import AppContext
from app.views.labels_view import LabelsView

OUT = ROOT / "docs" / "shots" / "page_labels.png"


def _grab(view: LabelsView, step: int, w: int = 900, h: int = 640) -> QPixmap:
    view.resize(w, h)
    view._go_to_step(step)
    view.repaint()
    QApplication.processEvents()
    return view.grab()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv[:1])

    ctx = AppContext()
    view = LabelsView(ctx)
    view.show()
    QApplication.processEvents()

    # Inject mock specimens so the grids are non-empty
    mock_specimens = [
        {
            "province": "FJ", "site": "XM", "station": "B2",
            "id": "DLC004", "storage": "T95E",
            "collectionDate": "20260602", "photoDate": "20260602",
            "species": "多毛类 sp.04", "latin": "Polychaeta sp.",
            "collector": "杨德援", "photographer": "钟珅",
            "family": "Polynoidae", "region": "福建·厦门",
            "lon": "118.18", "lat": "24.48", "geoArea": "东海",
            "photoNotes": "",
        },
        {
            "province": "FJ", "site": "XM", "station": "B2",
            "id": "BLC001", "storage": "RD75E",
            "collectionDate": "20260602", "photoDate": "20260602",
            "species": "端足目 sp.01", "latin": "Amphipoda sp.",
            "collector": "杨德援", "photographer": "钟珅",
            "family": "Gammaridae", "region": "福建·厦门",
            "lon": "118.18", "lat": "24.48", "geoArea": "东海",
            "photoNotes": "",
        },
    ]
    view._specimens = mock_specimens
    view._step1.set_specimens(mock_specimens)
    QApplication.processEvents()

    W, H = 900, 640
    shots: list[QPixmap] = []
    for step in range(4):
        pix = _grab(view, step, W, H)
        shots.append(pix)

    # Stitch vertically
    total_h = sum(p.height() for p in shots)
    combined = QPixmap(W, total_h)
    combined.fill(Qt.GlobalColor.black)
    painter = QPainter(combined)
    y = 0
    for pix in shots:
        painter.drawPixmap(0, y, pix)
        y += pix.height()
    painter.end()

    combined.save(str(OUT))
    print(f"Saved: {OUT}")


if __name__ == "__main__":
    main()
