"""compose_workbench_dialog.py — 合成工作台（合成后预览/重合成对话框）。

从 app/views/workbench_view.py 原样拆出（行为逐字节一致）；
workbench_view 仍以原名 `_ComposeWorkbenchDialog` / `_ScaledImagePreview`
re-export。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QSize, Qt
from PyQt6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.config.theme import TOKENS
from app.utils.image_thumbnail import decode_image_thumbnail
from app.widgets.helicon_params_panel import HeliconParamsPanel


class _ScaledImagePreview(QLabel):
    """Scaled image label for the compose result preview."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._source_pixmap: Optional[QPixmap] = None
        self._scroll_area: Optional[QScrollArea] = None
        self._fit_to_window = True
        self._zoom_percent = 100
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def set_scroll_area(self, scroll_area: QScrollArea) -> None:
        self._scroll_area = scroll_area
        scroll_area.viewport().installEventFilter(self)

    def set_preview_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        if pixmap is None or pixmap.isNull():
            self._source_pixmap = None
            self.setPixmap(QPixmap())
            return
        self._source_pixmap = QPixmap(pixmap)
        self._fit_to_window = True
        self._refresh_scaled_pixmap()

    def set_placeholder(self, text: str) -> None:
        self._source_pixmap = None
        self.setPixmap(QPixmap())
        self.setText(text)

    def source_pixmap(self) -> Optional[QPixmap]:
        if self._source_pixmap is None:
            return None
        return QPixmap(self._source_pixmap)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_to_window:
            self._refresh_scaled_pixmap()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if (
            self._scroll_area is not None
            and obj is self._scroll_area.viewport()
            and event.type() == QEvent.Type.Resize
            and self._fit_to_window
        ):
            self._refresh_scaled_pixmap()
        if (
            event.type() == QEvent.Type.Wheel
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.zoom_by_wheel_delta(event.angleDelta().y())
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by_wheel_delta(event.angleDelta().y())
            event.accept()
            return
        super().wheelEvent(event)

    def zoom_by_wheel_delta(self, delta: int) -> None:
        if delta == 0:
            return
        steps = max(1, abs(delta) // 120)
        direction = 1 if delta > 0 else -1
        self.set_zoom_percent(self._zoom_percent + direction * steps * 10)

    def set_zoom_percent(self, percent: int) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        self._fit_to_window = False
        self._zoom_percent = max(25, min(400, int(percent)))
        self._refresh_scaled_pixmap()

    def fit_to_window(self) -> None:
        self._fit_to_window = True
        self._refresh_scaled_pixmap()

    def actual_size(self) -> None:
        self.set_zoom_percent(100)

    def _refresh_scaled_pixmap(self) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        source_w = max(1, self._source_pixmap.width())
        source_h = max(1, self._source_pixmap.height())
        if self._fit_to_window:
            if self._scroll_area is not None:
                area = self._scroll_area.viewport().size()
                target_w = max(1, area.width() - 24)
                target_h = max(1, area.height() - 24)
            else:
                target_w = max(1, self.width() - 24)
                target_h = max(1, self.height() - 24)
            scaled = self._source_pixmap.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            label_w = max(target_w, scaled.width())
            label_h = max(target_h, scaled.height())
        else:
            target_w = max(1, int(source_w * self._zoom_percent / 100))
            target_h = max(1, int(source_h * self._zoom_percent / 100))
            scaled = self._source_pixmap.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            label_w = scaled.width()
            label_h = scaled.height()
        self.setText("")
        self.setFixedSize(max(1, label_w), max(1, label_h))
        self.setPixmap(scaled)


class _ComposeWorkbenchDialog(QDialog):
    """Post-compose preview workspace.

    Mirrors the web compose page at a desktop scale: left source JPG checklist,
    center TIFF preview/status, right Helicon params, footer save/cancel/recompose.
    """

    ACTION_SAVE = "save"
    ACTION_CANCEL = "cancel"
    ACTION_RECOMPOSE = "recompose"

    def __init__(
        self,
        jpg_paths: list[str],
        tiff_path: str,
        params: dict,
        *,
        angle_label: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._jpg_paths = list(jpg_paths)
        self._tiff_path = tiff_path
        self._action = self.ACTION_CANCEL
        self._checks: list[tuple[QCheckBox, str]] = []
        self._params_panel = HeliconParamsPanel()
        self._params_panel.set_params(params)
        self._shortcuts: list[QShortcut] = []
        self.setWindowTitle("合成工作台")
        self.setMinimumSize(920, 560)
        self._build_ui(angle_label)
        self._install_shortcuts()

    def _build_ui(self, angle_label: str) -> None:
        t = TOKENS
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("合成工作台")
        title.setStyleSheet(f"font-size: 17px; font-weight: 700; color: {t['text']};")
        head.addWidget(title)
        if angle_label:
            badge = QLabel(angle_label)
            badge.setStyleSheet(
                f"color:{t['accent']}; border:1px solid {t['accent_glow']};"
                " border-radius:5px; padding:2px 8px; font-size:12px;"
            )
            head.addWidget(badge)
        fname = QLabel(Path(self._tiff_path).name)
        fname.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fname.setStyleSheet(f"color:{t['muted']}; font-size:12px;")
        head.addWidget(fname, 1)
        root.addLayout(head)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(12)

        sources = QFrame()
        sources.setObjectName("Panel")
        src_lay = QVBoxLayout(sources)
        src_lay.setContentsMargins(12, 12, 12, 12)
        src_lay.setSpacing(8)
        src_lay.addWidget(QLabel("源图"))
        src_list = QListWidget()
        src_list.setAlternatingRowColors(True)
        for path in self._jpg_paths:
            item = QListWidgetItem(src_list)
            cb = QCheckBox(Path(path).name)
            cb.setChecked(True)
            cb.setToolTip("")
            thumb = self._load_source_preview_pixmap(path)
            if thumb is not None and not thumb.isNull():
                cb.setIcon(QIcon(thumb))
                cb.setIconSize(QSize(46, 46))
            self._checks.append((cb, path))
            src_list.setItemWidget(item, cb)
            item.setSizeHint(cb.sizeHint())
        src_lay.addWidget(src_list, 1)
        body.addWidget(sources)

        preview = QFrame()
        preview.setObjectName("Panel")
        pv_lay = QVBoxLayout(preview)
        pv_lay.setContentsMargins(16, 16, 16, 16)
        pv_lay.setSpacing(10)
        pv_title = QLabel("TIFF 预览")
        pv_title.setStyleSheet(f"font-size: 13px; font-weight: 700; color:{t['text']};")
        pv_lay.addWidget(pv_title)
        self._tiff_preview = _ScaledImagePreview()
        self._tiff_preview.setObjectName("ComposeTiffPreview")
        self._tiff_preview.setStyleSheet(
            f"color:{t['muted']}; background:{t['panel_inset']};"
            f" border:1px dashed {t['border_medium']}; border-radius:8px;"
            " padding:8px; font-size:12px;"
        )
        self._tiff_preview.setToolTip("")
        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(False)
        self._preview_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_scroll.setMinimumSize(360, 300)
        self._preview_scroll.setStyleSheet(
            f"QScrollArea {{ background:{t['panel_inset']};"
            f" border:1px dashed {t['border_medium']}; border-radius:8px; }}"
        )
        self._tiff_preview.set_scroll_area(self._preview_scroll)
        self._preview_scroll.setWidget(self._tiff_preview)
        meta = QLabel()
        meta.setObjectName("Muted")
        if os.path.isfile(self._tiff_path):
            size_mb = os.path.getsize(self._tiff_path) / (1024 * 1024)
            pixmap = self._load_tiff_preview_pixmap()
            if pixmap is not None and not pixmap.isNull():
                self._tiff_preview.set_preview_pixmap(pixmap)
                meta.setText(
                    f"已生成 TIFF · {Path(self._tiff_path).name} · "
                    f"{size_mb:.1f} MB"
                )
            else:
                self._tiff_preview.set_placeholder(
                    f"无法预览 TIFF\n{self._tiff_path}\n\n大小：{size_mb:.1f} MB"
                )
                meta.setText("TIFF 已生成，但当前环境无法解码预览图。")
        else:
            self._tiff_preview.set_placeholder(f"未找到 TIFF\n{self._tiff_path}")
            meta.setText("输出文件不存在。")
        meta.setWordWrap(True)
        meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pv_lay.addWidget(self._preview_scroll, 1)
        pv_lay.addWidget(meta)
        hint = QLabel("调整右侧参数后可重合成预览；保存后写入当前分组结果。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{t['muted_dim']}; font-size:11px;")
        pv_lay.addWidget(hint)
        body.addWidget(preview)

        body.addWidget(self._params_panel)
        body.setSizes([240, 460, 220])
        root.addWidget(body, 1)

        foot = QHBoxLayout()
        foot.addStretch()
        cancel = QPushButton("取消（退回 TIFF）")
        cancel.setObjectName("Outline")
        cancel.clicked.connect(self._reject_compose_preview)
        foot.addWidget(cancel)
        recompose = QPushButton("重合成预览")
        recompose.setObjectName("Outline")
        recompose.clicked.connect(self._accept_recompose_preview)
        foot.addWidget(recompose)
        save = QPushButton("保存到结果")
        save.setObjectName("Primary")
        save.clicked.connect(self._accept_save_result)
        foot.addWidget(save)
        root.addLayout(foot)

    def _install_shortcuts(self) -> None:
        def add(seq, callback) -> None:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

        add("Ctrl++", lambda: self._tiff_preview.set_zoom_percent(self._tiff_preview._zoom_percent + 10))
        add("Ctrl+=", lambda: self._tiff_preview.set_zoom_percent(self._tiff_preview._zoom_percent + 10))
        add("Ctrl+-", lambda: self._tiff_preview.set_zoom_percent(self._tiff_preview._zoom_percent - 10))
        add("Ctrl+0", self._tiff_preview.fit_to_window)
        add("Ctrl+1", self._tiff_preview.actual_size)

    def _load_tiff_preview_pixmap(self) -> Optional[QPixmap]:
        try:
            return decode_image_thumbnail(self._tiff_path, max_size=2400, use_cache=False)
        except Exception:
            return None

    def _load_source_preview_pixmap(self, path: str) -> Optional[QPixmap]:
        try:
            return decode_image_thumbnail(path, max_size=96, use_cache=True)
        except Exception:
            return None

    def selected_jpgs(self) -> list[str]:
        return [path for cb, path in self._checks if cb.isChecked()]

    def params(self) -> dict:
        return self._params_panel.get_params()

    def action(self) -> str:
        return self._action

    def _accept_save_result(self) -> None:
        self._action = self.ACTION_SAVE
        self.accept()

    def _reject_compose_preview(self) -> None:
        self._action = self.ACTION_CANCEL
        self.reject()

    def _accept_recompose_preview(self) -> None:
        self._action = self.ACTION_RECOMPOSE
        self.accept()
