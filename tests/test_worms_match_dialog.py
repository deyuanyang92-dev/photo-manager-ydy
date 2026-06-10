"""test_worms_match_dialog.py — offscreen smoke for the Match-Taxa wizard.

Guards construction + the decoupled logic methods (load_file / name_list /
selected_append_cols / set_results / resolve_row / export) without GUI events.
"""
from __future__ import annotations

import os

import openpyxl
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app.widgets.worms_match_dialog import WormsMatchDialog  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


class _FakeService:
    """Stand-in WormsService — no network, deterministic results."""

    def match_names(self, names, *, marine_only=False, auto_accept_near=False, progress_cb=None):
        out = []
        for n in names:
            nn = (n or "").strip()
            if not nn:
                out.append({"input": n, "candidates": [], "resolution": "none", "best": None})
                continue
            best = {"AphiaID": 1, "scientificname": nn, "authority": "Auth, 1900",
                    "valid_name": nn, "match_type": "exact", "rank": "Species"}
            out.append({"input": n, "candidates": [best], "resolution": "matched", "best": best})
        if progress_cb:
            progress_cb(len(names), len(names))
        return out


def _write_csv(path, text):
    path.write_text(text, encoding="utf-8-sig")
    return str(path)


def test_construct_and_load(qt_app, tmp_path):
    dlg = WormsMatchDialog(_FakeService())
    csv = _write_csv(tmp_path / "names.csv", "编号,学名,备注\n1,Abra alba,x\n2,Cancer pagurus,y\n")
    dlg.load_file(csv)
    assert dlg.name_column() == "学名"          # auto-guessed
    assert dlg.name_list() == ["Abra alba", "Cancer pagurus"]
    # all WoRMS append columns checked by default
    assert "aphia_id" in dlg.selected_append_cols()


def test_results_and_export_roundtrip(qt_app, tmp_path):
    dlg = WormsMatchDialog(_FakeService())
    csv = _write_csv(tmp_path / "n.csv", "学名\nAbra alba\n")
    dlg.load_file(csv)
    results = _FakeService().match_names(dlg.name_list())
    dlg.set_results(results)

    out = dlg.export(str(tmp_path / "out.xlsx"))
    ws = openpyxl.load_workbook(out).active
    hdr = [c.value for c in ws[1]]
    assert hdr[0] == "学名"                      # original column preserved first
    assert "AphiaID" in hdr                       # WoRMS column appended
    assert ws.cell(row=2, column=1).value == "Abra alba"


def test_resolve_row_marks_none(qt_app, tmp_path):
    dlg = WormsMatchDialog(_FakeService())
    csv = _write_csv(tmp_path / "n.csv", "学名\nAbra alba\n")
    dlg.load_file(csv)
    dlg.set_results(_FakeService().match_names(["Abra alba"]))
    dlg.resolve_row(0, None, mark_none=True)
    assert dlg._results[0]["resolution"] == "none"
    assert dlg._results[0]["best"] is None
