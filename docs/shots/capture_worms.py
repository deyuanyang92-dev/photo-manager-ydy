"""capture_worms.py — Render the WoRMS page to a PNG for design review.

Runs fully headless (QT_QPA_PLATFORM=offscreen).
Seeds the detail panel with a synthetic Acanthurus olivaceus record so
every zone (header, search bar, result list, classification chain,
overview tab) has content.

Usage:
    QT_QPA_PLATFORM=offscreen python3 docs/shots/capture_worms.py
Output:
    docs/shots/page_worms.png
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402

from app.views.worms_view import WormsView  # noqa: E402


# ── Synthetic data (mirrors real WoRMS API shapes) ────────────────────────────

_SEARCH_RESULTS = [
    {
        "AphiaID": 219014,
        "valid_AphiaID": 219014,
        "scientificname": "Acanthurus olivaceus",
        "valid_name": "Acanthurus olivaceus",
        "authority": "Bloch & Schneider, 1801",
        "rank": "Species",
        "status": "accepted",
        "kingdom": "Animalia",
        "phylum": "Chordata",
        "class": "Actinopterygii",
        "order": "Acanthuriformes",
        "family": "Acanthuridae",
        "genus": "Acanthurus",
        "isMarine": 1,
        "url": "https://www.marinespecies.org/aphia.php?p=taxdetails&id=219014",
        "lsid": "urn:lsid:marinespecies.org:taxname:219014",
    },
    {
        "AphiaID": 100001,
        "valid_AphiaID": 219014,
        "scientificname": "Hepatus olivaceus",
        "valid_name": "Acanthurus olivaceus",
        "authority": "Bloch & Schneider, 1801",
        "rank": "Species",
        "status": "unaccepted",
        "class": "Actinopterygii",
        "order": "Acanthuriformes",
        "family": "Acanthuridae",
        "genus": "Hepatus",
        "isMarine": 1,
    },
]

_CHAIN = [
    {"rank": "Kingdom",   "scientificname": "Animalia",       "AphiaID": 2},
    {"rank": "Phylum",    "scientificname": "Chordata",        "AphiaID": 11},
    {"rank": "Class",     "scientificname": "Actinopterygii",  "AphiaID": 216},
    {"rank": "Order",     "scientificname": "Acanthuriformes", "AphiaID": 1036},
    {"rank": "Family",    "scientificname": "Acanthuridae",    "AphiaID": 4040},
    {"rank": "Genus",     "scientificname": "Acanthurus",      "AphiaID": 204},
    {"rank": "Species",   "scientificname": "Acanthurus olivaceus", "AphiaID": 219014},
]

_SYNONYMS = [
    {"AphiaID": 100001, "scientificname": "Hepatus olivaceus",
     "authority": "Bloch & Schneider, 1801", "status": "unaccepted"},
    {"AphiaID": 100002, "scientificname": "Teuthis olivaceus",
     "authority": "Forster, 1801", "status": "unaccepted"},
]

_CHILDREN: list[dict] = []


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # Apply theme if available
    theme_path = _ROOT / "resources" / "theme.qss"
    if theme_path.exists():
        app.setStyleSheet(theme_path.read_text(encoding="utf-8"))
    else:
        app.setStyleSheet(
            "QWidget { background-color: #08161b; color: #eef3ef;"
            "  font-family: 'Segoe UI', sans-serif; font-size: 13px; }"
        )

    tmp = Path(tempfile.mkdtemp(prefix="worms-shot-"))
    ctx = MagicMock()
    ctx.current_project_dir = str(tmp)

    view = WormsView(ctx)
    view.resize(1920, 1080)

    # Inject synthetic search results into the result area
    view._search_input.setText("Acanthurus olivaceus")
    view._like_cb.setChecked(True)
    view._on_search_done(_SEARCH_RESULTS)

    # Inject detail for the first (accepted) result
    selected = _SEARCH_RESULTS[0]
    view._selected = selected
    view._detail_panel.show_detail(selected, _CHAIN, _SYNONYMS, _CHILDREN)

    view.show()
    for _ in range(10):
        app.processEvents()

    out = Path(__file__).resolve().parent / "page_worms.png"
    pix = view.grab()
    pix.save(str(out))
    size = out.stat().st_size if out.exists() else 0
    print(f"saved: {out}  ({pix.width()}x{pix.height()}, {size} bytes)")
    return 0 if size > 5000 else 1


if __name__ == "__main__":
    sys.exit(main())
