"""summary_column_filter_dialog.py — 编号表列筛选（Excel 式勾选值）。"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class SummaryColumnFilterDialog(QDialog):
    def __init__(
        self,
        column_label: str,
        values: list[str],
        selected: set[str] | None = None,
        *,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"筛选 · {column_label}")
        self.resize(360, 420)
        self._all_values = list(values or [])
        self._checks: list[QCheckBox] = []

        root = QVBoxLayout(self)
        hint = QLabel("勾选要保留的值（类似 Excel 列筛选）。留空表示不过滤此列。")
        hint.setWordWrap(True)
        hint.setObjectName("MutedSmall")
        root.addWidget(hint)

        tool = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索…")
        self._search.textChanged.connect(self._apply_search)
        tool.addWidget(self._search, 1)
        btn_all = QLabel('<a href="all">全选</a> · <a href="none">全不选</a>')
        btn_all.setTextFormat(Qt.TextFormat.RichText)
        btn_all.linkActivated.connect(self._on_link)
        tool.addWidget(btn_all)
        root.addLayout(tool)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        self._list_lay = QVBoxLayout(host)
        self._list_lay.setSpacing(4)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        initial = set(selected) if selected else set(self._all_values)
        for val in self._all_values:
            label = val if val else "（空）"
            cb = QCheckBox(label)
            cb.setProperty("raw_value", val)
            cb.setChecked(val in initial)
            self._checks.append(cb)
            self._list_lay.addWidget(cb)
        self._list_lay.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_link(self, href: str) -> None:
        checked = href != "none"
        for cb in self._checks:
            if cb.isVisible():
                cb.setChecked(checked)

    def _apply_search(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        for cb in self._checks:
            raw = str(cb.property("raw_value") or "")
            label = raw if raw else "（空）"
            cb.setVisible(not needle or needle in label.casefold())

    def selected_values(self) -> Optional[set[str]]:
        """None = 用户取消；空 set = 清除此列筛选。"""
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        picked = {
            str(cb.property("raw_value") or "")
            for cb in self._checks
            if cb.isChecked()
        }
        if len(picked) == len(self._all_values):
            return set()
        return picked
