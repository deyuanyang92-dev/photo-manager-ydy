"""Modal host for cross-workspace data filtering (opened from project tree)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QWidget

from app.views.data_filter_view import DataFilterView

if TYPE_CHECKING:
    from app.app_context import AppContext


class DataFilterDialog(QDialog):
    """Cross-workspace specimen filter — lives under 项目树, not top nav."""

    def __init__(
        self,
        ctx: "AppContext",
        *,
        preselect_dirs: Optional[list[str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("数据筛选")
        self.setMinimumSize(820, 620)
        self.resize(960, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(0)

        self._panel = DataFilterView(ctx)
        root.addWidget(self._panel, 1)

        self._panel._refresh_workspaces()
        if preselect_dirs:
            self._panel.preselect_workspaces(preselect_dirs)


def open_data_filter_dialog(
    ctx: "AppContext",
    *,
    preselect_dirs: Optional[list[str]] = None,
    parent: Optional[QWidget] = None,
) -> None:
    dlg = DataFilterDialog(ctx, preselect_dirs=preselect_dirs, parent=parent)
    dlg.exec()
