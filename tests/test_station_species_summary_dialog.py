from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.db.db_manager import open_project_db

_APP = QApplication.instance() or QApplication([])


def _make_workspace(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    conn = open_project_db(str(root), create=True)
    return conn


def test_dialog_filter_searches_taxa_and_sample_overview(qtbot, tmp_path):
    from app.widgets.station_species_summary_dialog import StationSpeciesSummaryDialog

    ws = tmp_path / "survey" / "transect-a"
    conn = _make_workspace(ws)
    conn.execute(
        """
        INSERT INTO specimens (
          uid, id, province, site, station, collection_date, scientific_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "FJ-S1-A-DLC001-T95E-20260601",
            "DLC001",
            "FJ",
            "S1",
            "A",
            "20260601",
            "Taxon alpha",
        ),
    )
    conn.execute(
        """
        INSERT INTO grouping (uid, group_index, status)
        VALUES (?, ?, ?)
        """,
        ("FJ-S1-A-MIX01-T95E-20260601", 0, "pending"),
    )
    conn.commit()

    dlg = StationSpeciesSummaryDialog(initial_root=str(tmp_path / "survey"))
    qtbot.addWidget(dlg)

    assert dlg._taxon_table.rowCount() == 1
    assert dlg._sample_table.rowCount() == 2

    dlg._filter_edit.setText("Taxon alpha")
    assert dlg._taxon_table.rowCount() == 1
    assert dlg._sample_table.rowCount() == 0

    dlg._filter_edit.setText("MIX01")
    assert dlg._taxon_table.rowCount() == 0
    assert dlg._sample_table.rowCount() == 1
