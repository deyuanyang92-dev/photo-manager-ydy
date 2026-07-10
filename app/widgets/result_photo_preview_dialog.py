"""result_photo_preview_dialog.py — 成片大图预览（项目树 / 网格右键）."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config.i18n import tr
from app.config.project_tree_layout import PREVIEW_DECODE_MAX
from app.utils import ui
from app.utils.image_thumbnail import decode_image_data, make_pixmap


class ResultPhotoPreviewDialog(QDialog):
    """Show a bounded large preview for one result photo."""

    def __init__(
        self,
        path: str,
        *,
        title: str | None = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self.setWindowTitle(title or tr("大图预览"))
        self.resize(920, 720)
        self._build_ui()
        ui.center_on(self, parent)
        self._load_image()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        self._meta = QLabel("")
        self._meta.setWordWrap(True)
        self._meta.setStyleSheet("color:#667085;")
        root.addWidget(self._meta)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image = QLabel(tr("加载中…"))
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setWidget(self._image)
        root.addWidget(self._scroll, 1)

        bar = QHBoxLayout()
        btn_folder = QPushButton(tr("在文件夹中显示"))
        btn_folder.clicked.connect(self._reveal)
        btn_close = QPushButton(tr("关闭"))
        btn_close.clicked.connect(self.accept)
        bar.addStretch(1)
        bar.addWidget(btn_folder)
        bar.addWidget(btn_close)
        root.addLayout(bar)

    def _load_image(self) -> None:
        p = Path(self._path)
        self._meta.setText(str(p))
        img = decode_image_data(self._path, PREVIEW_DECODE_MAX, use_cache=True)
        pm = make_pixmap(img)
        if pm is None or pm.isNull():
            self._image.setText(tr("无法加载此图片"))
            return
        self._image.setPixmap(pm)
        self._image.setMinimumSize(pm.size())
        self._meta.setText(
            f"{p.name}  ·  {pm.width()}×{pm.height()} px\n{p}"
        )

    def _reveal(self) -> None:
        from app.utils.file_manager import reveal_in_directory

        if not reveal_in_directory(self._path):
            ui.warn(self, tr("打开失败"), tr("无法在文件夹中显示此文件。"))
