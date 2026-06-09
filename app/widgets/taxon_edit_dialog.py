"""taxon_edit_dialog.py — 「一次编辑五级拉丁名和中名」modal.

Faithful to the web oracle's taxon edit modal (app.js openTaxonEditModal /
分类编辑 modal). A compact form editing all 5 taxonomy levels (Latin + 中名)
in one dialog; the workbench writes the result back into the TaxonCardPanel
and persists. Inline card editing remains available — this is the bulk-edit
convenience web also offers.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

# (label, latin_db, cn_db)
_ROWS: list[tuple[str, str, str]] = [
    ("类群", "taxon_group",     "taxon_group_cn"),
    ("目",   "order_name",      "order_cn"),
    ("科",   "family",          "family_cn"),
    ("属",   "genus",           "genus_cn"),
    ("物种", "scientific_name", "scientific_name_cn"),
]


class TaxonEditDialog(QDialog):
    """Edit 5-level taxonomy (latin + cn) in one modal."""

    def __init__(self, values: dict[str, str], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑 分类标签")
        self.setModal(True)
        self._latin: dict[str, QLineEdit] = {}
        self._cn: dict[str, QLineEdit] = {}

        root = QVBoxLayout(self)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.addWidget(QLabel("级别"), 0, 0)
        grid.addWidget(QLabel("拉丁名"), 0, 1)
        grid.addWidget(QLabel("中名"), 0, 2)
        for i, (label, latin_db, cn_db) in enumerate(_ROWS, start=1):
            grid.addWidget(QLabel(label), i, 0)
            le = QLineEdit(values.get(latin_db, "") or "")
            le.setMinimumWidth(180)
            cn = QLineEdit(values.get(cn_db, "") or "")
            cn.setMinimumWidth(140)
            grid.addWidget(le, i, 1)
            grid.addWidget(cn, i, 2)
            self._latin[latin_db] = le
            self._cn[cn_db] = cn
        root.addLayout(grid)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)
        self.resize(480, 280)

    def result_values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for db, le in self._latin.items():
            out[db] = le.text().strip()
        for db, le in self._cn.items():
            out[db] = le.text().strip()
        return out
