"""Result card, pairing, and formatting widgets for ResultsColumn."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.config import icons

_DEFAULT_THUMB = 42
_MIN_THUMB = 32
_MAX_THUMB = 160
_LARGE_THUMB_MIN_SIZE = 128
_BASE_THUMB = 280  # base decode size; zoom scales DOWN from this cached pixmap
_MIN_PAIRED_COLUMNS_WIDTH = 640
_LARGE_THUMB_CARD_EXTRA_HEIGHT = 52


def _decode_thumb(path: str, max_size: int = _BASE_THUMB) -> Optional[QPixmap]:
    """Decode *path* to a QPixmap downscaled to ``max_size`` (KeepAspectRatio).

    Returns None for empty / missing / undecodable paths.  Callers fall back to
    an icon placeholder; TIFF decoding uses the shared multi-backend helper.
    """
    from app.utils.image_thumbnail import decode_image_thumbnail

    return decode_image_thumbnail(path, max_size)

# ── Individual result cards ────────────────────────────────────────────────────

class _ResultCardBase(QFrame):
    """Shared base: one compact file-list row — icon LEFT, name + meta RIGHT
    (Windows Explorer list style).  Subclasses supply the icon source, the meta
    text and the context-menu actions."""

    _FALLBACK_ICON = "mdi6.file-image-outline"
    _ICON_TINT = "#3a6b75"

    def __init__(self, info: dict, thumb_provider=None,
                 select_fn=None, selected: bool = False,
                 thumb_size: int = _DEFAULT_THUMB,
                 result_view_mode: str = "list",
                 defer_thumbnail: bool = False,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultFile")
        self.setMinimumWidth(0)
        self._result_view_mode = (
            "large_thumbnail"
            if result_view_mode == "large_thumbnail"
            else "list"
        )
        self.setProperty("resultViewMode", self._result_view_mode)
        self._info = info
        self._thumb_size = thumb_size
        self._thumb_provider = thumb_provider
        self._defer_thumbnail = defer_thumbnail
        self._thumbnail_loaded = False
        self._select_fn = select_fn
        self._selected = selected
        self._base_pm: Optional[QPixmap] = None
        self._setup_ui()
        self.set_selected(selected)

    def _setup_ui(self) -> None:
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if self._result_view_mode == "large_thumbnail":
            self._setup_large_thumbnail_ui()
        else:
            self._setup_list_ui()

        path = self._info.get("path", "")
        if self._thumb_provider and path and not self._defer_thumbnail:
            self.load_thumbnail_now()
        self._apply_icon()

    def _create_select_badge(self) -> QLabel:
        badge = QLabel("")
        badge.setObjectName("ResultSelectBadge")
        badge.setFixedSize(18, 18)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setToolTip("单击选择/取消选择")
        return badge

    def _create_icon_label(self) -> QLabel:
        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(self._display_thumb_size(), self._display_thumb_size())
        return icon_label

    def _setup_list_ui(self) -> None:
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(6)

        self._select_badge = self._create_select_badge()
        lay.addWidget(self._select_badge)

        self._icon = self._create_icon_label()
        lay.addWidget(self._icon)

        body = self._build_body()
        body.setMinimumWidth(0)
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(body, stretch=1)

    def _setup_large_thumbnail_ui(self) -> None:
        self.setMinimumHeight(self._large_thumbnail_min_height())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(5)

        media_row = QHBoxLayout()
        media_row.setContentsMargins(0, 0, 0, 0)
        media_row.setSpacing(8)
        self._select_badge = self._create_select_badge()
        media_row.addWidget(
            self._select_badge,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        media_row.addStretch()
        self._icon = self._create_icon_label()
        media_row.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignCenter)
        media_row.addStretch()
        media_row.addSpacing(self._select_badge.width())
        lay.addLayout(media_row)

        body = self._build_body()
        body.setMinimumWidth(0)
        body.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(body)

    def _display_thumb_size(self) -> int:
        if self._result_view_mode == "large_thumbnail":
            return max(_LARGE_THUMB_MIN_SIZE, self._thumb_size)
        return self._thumb_size

    def _large_thumbnail_min_height(self) -> int:
        return self._display_thumb_size() + _LARGE_THUMB_CARD_EXTRA_HEIGHT

    def load_thumbnail_now(self) -> None:
        """同步解码(§7 保留): 只剩「无 QApplication 的纯逻辑测试」等降级路径会走到。

        正常路径改由 ResultsColumn 把解码投给 GridThumbnailWorker 线程, 回包后调
        set_thumbnail_pixmap —— TIFF 母图冷解码最坏十几秒, 绝不能在 GUI 线程做。
        """
        if self._thumbnail_loaded:
            return
        self._thumbnail_loaded = True
        path = self._info.get("path", "")
        if self._thumb_provider and path:
            self._base_pm = self._thumb_provider(path)
        self._apply_icon()

    def set_thumbnail_pixmap(self, pm: Optional[QPixmap]) -> None:
        """异步解码回包(主线程)贴图。pm 为 None ⇒ 落到占位图标。"""
        self._thumbnail_loaded = True
        self._base_pm = pm
        self._apply_icon()

    def _build_body(self) -> QWidget:  # override
        return QWidget()

    def _registry_line(self, kind: str, counterpart_path: str = "") -> str:
        uid = str(self._info.get("owner_uid") or self._info.get("uid") or "")
        group_index = self._info.get("group_index")
        registered = bool(self._info.get("registered", bool(uid)))
        if uid:
            where = f"已入库: {uid}"
            if group_index is not None:
                where += f" / 组 {int(group_index) + 1}"
        elif registered:
            where = "已入库"
        else:
            where = "未入库"
        pair_label = "ZIP" if kind == "tiff" else "TIF"
        pair = f"已配 {pair_label}" if counterpart_path else f"缺 {pair_label}"
        return f"{where} · {pair}"

    def _show_registry_info(self, kind: str, counterpart_path: str = "") -> None:
        from PyQt6.QtWidgets import QMessageBox

        uid = str(self._info.get("owner_uid") or self._info.get("uid") or "")
        group_index = self._info.get("group_index")
        lines = [
            f"类型: {'TIFF 成果' if kind == 'tiff' else 'ZIP 归档'}",
            f"文件: {self._info.get('path') or ''}",
            f"入库: {'是' if self._info.get('registered', bool(uid)) else '否'}",
            f"关联编号: {uid or '未关联'}",
        ]
        if group_index is not None:
            lines.append(f"分组: {int(group_index) + 1}")
        if counterpart_path:
            lines.append(f"配对文件: {counterpart_path}")
        else:
            lines.append("配对文件: 未找到")
        QMessageBox.information(self, "入库/关联信息", "\n".join(lines))

    def _icon_pixmap(self) -> Optional[QPixmap]:
        """Override to force a glyph (e.g. ZIP).  Default = decoded thumbnail."""
        return self._base_pm

    def _apply_icon(self) -> None:
        s = self._display_thumb_size()
        self._icon.setFixedSize(s, s)
        self._icon.setStyleSheet("border:none; background:transparent;")
        pm = self._icon_pixmap()
        if pm is not None and not pm.isNull():
            self._icon.setProperty("hasThumbnail", True)
            self._icon.setPixmap(pm.scaled(
                s, s,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            self._icon.setProperty("hasThumbnail", False)
            glyph_cap = 76 if self._result_view_mode == "large_thumbnail" else 26
            g = min(glyph_cap, max(12, s - 6))
            self._icon.setPixmap(
                icons.icon(self._FALLBACK_ICON, color=self._ICON_TINT).pixmap(g, g)
            )
        self._icon.style().unpolish(self._icon)
        self._icon.style().polish(self._icon)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            path = self._info.get("path", "")
            if self._select_fn and path:
                self._select_fn(path, self)
                event.accept()
                return
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.setProperty("resultSelected", "true" if self._selected else "false")
        self._select_badge.setText("✓" if self._selected else "")
        self._select_badge.setProperty("selected", "true" if self._selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self._select_badge.style().unpolish(self._select_badge)
        self._select_badge.style().polish(self._select_badge)

    def set_thumb_size(self, size: int) -> None:
        self._thumb_size = size
        if self._result_view_mode == "large_thumbnail":
            self.setMinimumHeight(self._large_thumbnail_min_height())
        self._apply_icon()


class _TiffCard(_ResultCardBase):
    """One composed-TIFF file row: thumbnail icon + filename + 已合成."""

    _FALLBACK_ICON = "mdi6.file-image-outline"

    def __init__(self, info: dict, open_fn=None, lightbox_fn=None,
                 link_fn=None, paired_zip: str = "",
                 naming_check_fn=None, delete_fn=None,
                 select_fn=None, selected: bool = False,
                 thumb_provider=None, thumb_size: int = _DEFAULT_THUMB,
                 result_view_mode: str = "list",
                 defer_thumbnail: bool = False,
                 unbind_fn=None, rebind_fn=None,
                 parent: Optional[QWidget] = None) -> None:
        self._open_fn = open_fn
        self._lightbox_fn = lightbox_fn
        self._link_fn = link_fn
        self._paired_zip = paired_zip
        self._naming_check_fn = naming_check_fn
        self._delete_fn = delete_fn
        # 纠错入口(用户 2026-07-10):TIF 关联错编号时,解绑 / 改绑到指定编号。
        # 两者都只改数据库归属,不动磁盘上的 TIF 字节。
        self._unbind_fn = unbind_fn
        self._rebind_fn = rebind_fn
        super().__init__(info, thumb_provider=thumb_provider,
                         select_fn=select_fn, selected=selected,
                         thumb_size=thumb_size,
                         result_view_mode=result_view_mode,
                         defer_thumbnail=defer_thumbnail,
                         parent=parent)

    def _build_body(self) -> QWidget:
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(2)

        name = (
            self._info.get("display_name")
            or self._info.get("name")
            or Path(self._info.get("path", "")).name
        )
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setMinimumWidth(0)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if self._result_view_mode == "large_thumbnail":
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setWordWrap(True)
            name_lbl.setMaximumHeight(42)
        full_name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl.setToolTip("")
        body_lay.addWidget(name_lbl)

        registry_lbl = QLabel(f"TIF · {self._registry_line('tiff', self._paired_zip)}")
        registry_lbl.setObjectName("ResultMeta")
        registry_lbl.setMinimumWidth(0)
        registry_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if self._result_view_mode == "large_thumbnail":
            registry_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        registry_lbl.setToolTip("")
        body_lay.addWidget(registry_lbl)
        return body

    def mouseDoubleClickEvent(self, event) -> None:
        if self._lightbox_fn:
            path = self._info.get("path", "")
            if path:
                self._lightbox_fn(Path(path))
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        self._show_menu(event.globalPos())

    def _show_menu(self, global_pos) -> None:
        path = self._info.get("path", "")
        menu = QMenu(self)
        preview_action = menu.addAction("打开预览")
        preview_action.setEnabled(bool(self._lightbox_fn and path))
        preview_action.triggered.connect(lambda: self._lightbox_fn(Path(path)))
        check_action = menu.addAction("检查 TIF 命名格式")
        check_action.setEnabled(bool(self._naming_check_fn and path))
        check_action.triggered.connect(lambda: self._naming_check_fn(path))
        open_action = menu.addAction("在文件夹中显示")
        open_action.setEnabled(bool(self._open_fn and path))
        open_action.triggered.connect(lambda: self._open_fn(path))
        copy_action = menu.addAction("复制路径")
        copy_action.setEnabled(bool(path))
        copy_action.triggered.connect(lambda: QApplication.clipboard().setText(path))
        info_action = menu.addAction("查看入库/关联信息")
        info_action.triggered.connect(
            lambda: self._show_registry_info("tiff", self._paired_zip)
        )
        menu.addSeparator()
        link_action = menu.addAction("关联到右侧编号")
        link_action.setEnabled(bool(self._link_fn and path))
        link_action.triggered.connect(lambda: self._link_fn(path, self._paired_zip))
        # 关联错了的两条纠错路径:知道正确编号 → 改绑; 还要再确认 → 先解绑。
        rebind_action = menu.addAction("改绑到其他编号…")
        rebind_action.setEnabled(bool(self._rebind_fn and path))
        rebind_action.triggered.connect(lambda: self._rebind_fn(path, self._paired_zip))
        unbind_action = menu.addAction("解绑此成果")
        unbind_action.setEnabled(bool(self._unbind_fn and path))
        unbind_action.triggered.connect(lambda: self._unbind_fn(path, self._paired_zip))
        menu.addSeparator()
        delete_action = menu.addAction("删除 TIF")
        delete_action.setEnabled(bool(self._delete_fn and path))
        delete_action.triggered.connect(lambda: self._delete_fn(path))
        menu.exec(global_pos)


class _ArchiveCard(_ResultCardBase):
    """One ZIP-archive file row: Windows-style zip-folder icon + filename + size."""

    _FALLBACK_ICON = "mdi6.folder-zip-outline"
    _ICON_TINT = "#c9981f"  # Windows zip-folder amber

    def __init__(self, info: dict, open_fn=None, restore_fn=None,
                 link_fn=None, delete_fn=None, paired_tiff: str = "",
                 select_fn=None, selected: bool = False,
                 thumb_size: int = _DEFAULT_THUMB,
                 result_view_mode: str = "list",
                 parent: Optional[QWidget] = None) -> None:
        self._open_fn = open_fn
        self._restore_fn = restore_fn
        self._link_fn = link_fn
        # Claude Code 修改 2026-07-15 — 手动删除 ZIP 入口新增(用户 2026-07-12 裁定)
        self._delete_fn = delete_fn
        self._paired_tiff = paired_tiff
        super().__init__(info, thumb_provider=None,
                         select_fn=select_fn, selected=selected,
                         thumb_size=thumb_size,
                         result_view_mode=result_view_mode,
                         parent=parent)

    def _icon_pixmap(self) -> Optional[QPixmap]:
        # A ZIP has no preview — always the zip-folder glyph (like Windows).
        return None

    def _build_body(self) -> QWidget:
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(2)

        name = (
            self._info.get("display_name")
            or self._info.get("name")
            or Path(self._info.get("path", "")).name
        )
        name_lbl = QLabel(name)
        name_lbl.setObjectName("Mono")
        name_lbl.setMinimumWidth(0)
        name_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if self._result_view_mode == "large_thumbnail":
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl.setWordWrap(True)
            name_lbl.setMaximumHeight(42)
        full_name = self._info.get("name") or Path(self._info.get("path", "")).name
        name_lbl.setToolTip("")
        body_lay.addWidget(name_lbl)

        size_bytes = self._info.get("size", 0)
        size_str = _fmt_size(size_bytes) if size_bytes else "已归档"
        registry_lbl = QLabel(
            f"ZIP · {size_str} · {self._registry_line('zip', self._paired_tiff)}"
        )
        registry_lbl.setObjectName("ResultMeta")
        registry_lbl.setMinimumWidth(0)
        registry_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        if self._result_view_mode == "large_thumbnail":
            registry_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        registry_lbl.setToolTip("")
        body_lay.addWidget(registry_lbl)
        return body

    def contextMenuEvent(self, event) -> None:
        self._show_menu(event.globalPos())

    def _show_menu(self, global_pos) -> None:
        path = self._info.get("path", "")
        menu = QMenu(self)
        restore_action = menu.addAction("还原原片")
        restore_action.setEnabled(bool(self._restore_fn and path))
        restore_action.triggered.connect(lambda: self._restore_fn(path))
        open_action = menu.addAction("在文件夹中显示")
        open_action.setEnabled(bool(self._open_fn and path))
        open_action.triggered.connect(lambda: self._open_fn(path))
        info_action = menu.addAction("查看入库/关联信息")
        info_action.triggered.connect(
            lambda: self._show_registry_info("zip", self._paired_tiff)
        )
        menu.addSeparator()
        link_action = menu.addAction("关联到右侧编号")
        link_action.setEnabled(bool(self._link_fn and path))
        link_action.triggered.connect(lambda: self._link_fn(self._paired_tiff, path))
        menu.addSeparator()
        # Claude Code 修改 2026-07-15 — 手动删除 ZIP 入口新增(用户 2026-07-12 裁定:
        # 断电残留的孤儿 ZIP 之前删不掉, 菜单没有这一项)。仍挂在组上的 ZIP 会在
        # _on_delete_result_zip_path 里拒绝裸删并引导走"还原原片", 这里只管入口。
        delete_action = menu.addAction("删除 ZIP")
        delete_action.setEnabled(bool(self._delete_fn and path))
        delete_action.triggered.connect(lambda: self._delete_fn(path))
        menu.exec(global_pos)


def _placeholder(text: str) -> QWidget:
    """Muted box shown when a sequence has no files."""
    f = QFrame()
    f.setObjectName("ResultPlaceholder")
    f.setMinimumWidth(0)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(8, 6, 8, 6)
    lbl = QLabel(text)
    lbl.setObjectName("Muted")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    return f


class _ResultPairIndicator(QFrame):
    """Visual indicator for a visible TIFF/ZIP pair in two-column mode."""

    def __init__(
        self,
        tiff_path: str = "",
        zip_path: str = "",
        *,
        has_pair: bool = False,
        selected: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ResultPairIndicator")
        self.setFixedWidth(24)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._pair_paths = (
            [str(tiff_path or ""), str(zip_path or "")]
            if has_pair else []
        )
        self._has_pair = bool(has_pair)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._left_line = QFrame()
        self._left_line.setObjectName("ResultPairLine")
        self._left_line.setFixedHeight(1)
        lay.addWidget(self._left_line, stretch=1)

        self._arrow = QLabel("→" if has_pair else "")
        self._arrow.setObjectName("ResultPairArrow")
        self._arrow.setFixedSize(16, 18)
        self._arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._arrow.setToolTip("同一成果：左侧 TIF 对应右侧 ZIP" if has_pair else "")
        lay.addWidget(self._arrow)

        self._right_line = QFrame()
        self._right_line.setObjectName("ResultPairLine")
        self._right_line.setFixedHeight(1)
        lay.addWidget(self._right_line, stretch=1)

        self._apply_state(selected)

    def visible_pair_paths(self) -> list[str]:
        return list(self._pair_paths)

    def set_selected(self, selected: bool) -> None:
        self._apply_state(selected)

    def _apply_state(self, selected: bool) -> None:
        for widget in (self, self._arrow, self._left_line, self._right_line):
            widget.setProperty("hasPair", "true" if self._has_pair else "false")
            widget.setProperty("selected", "true" if selected else "false")
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class _ResultRow(QFrame):
    """One result sequence with optional aligned TIFF / ZIP columns."""

    def __init__(self, seq_label: str, tiff_row=None, zip_row=None,
                 *, show_paired_columns: bool = True,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultRow")
        self.setProperty("pairedColumns", "true" if show_paired_columns else "false")
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._rows = [r for r in (tiff_row, zip_row) if r is not None]
        self._pair_indicator = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        seq_badge = QLabel(_compact_result_sequence_label(seq_label))
        seq_badge.setObjectName("ResultSeqBadge")
        seq_badge.setFixedWidth(34)
        seq_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seq_badge.setToolTip(seq_label)
        row.addWidget(seq_badge)

        if not self._rows:
            row.addWidget(_placeholder("无成果"), stretch=1)
        elif show_paired_columns:
            row.addWidget(
                tiff_row if tiff_row is not None else _placeholder("无 TIFF"),
                stretch=1,
            )
            has_pair = tiff_row is not None and zip_row is not None
            tiff_path = tiff_row._info.get("path", "") if has_pair else ""
            zip_path = zip_row._info.get("path", "") if has_pair else ""
            selected = bool(
                has_pair
                and getattr(tiff_row, "_selected", False)
                and getattr(zip_row, "_selected", False)
            )
            self._pair_indicator = _ResultPairIndicator(
                tiff_path, zip_path, has_pair=has_pair, selected=selected
            )
            row.addWidget(self._pair_indicator)
            row.addWidget(
                zip_row if zip_row is not None else _placeholder("无 ZIP"),
                stretch=1,
            )
        else:
            stack = QVBoxLayout()
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(4)
            for r in self._rows:
                stack.addWidget(r)
            row.addLayout(stack, stretch=1)

    def set_thumb_size(self, size: int) -> None:
        for r in self._rows:
            if hasattr(r, "set_thumb_size"):
                r.set_thumb_size(size)


def _compact_result_sequence_label(seq_label: str) -> str:
    text = str(seq_label or "").strip()
    prefix = "成果 "
    if text.startswith(prefix) and text[len(prefix):].strip():
        return text[len(prefix):].strip()
    return text or "·"


class _SpecimenResultHeader(QFrame):
    """Divider header for all-specimens result view."""

    clicked = pyqtSignal(str)

    def __init__(self, uid: str, row_count: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._uid = uid
        self.setObjectName("ResultGroupHeader")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 6, 2, 2)
        lay.setSpacing(8)
        uid_lbl = QLabel(uid)
        uid_lbl.setObjectName("Mono")
        uid_lbl.setToolTip("")
        lay.addWidget(uid_lbl, stretch=1)
        count_lbl = QLabel(f"{row_count} 项")
        count_lbl.setObjectName("MutedSmall")
        lay.addWidget(count_lbl)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._uid:
            self.clicked.emit(self._uid)
            event.accept()
            return
        super().mousePressEvent(event)


def _pair_results(composed_tiffs: list, archive_zips: list) -> list:
    """Pair TIFF/ZIP infos by ``seq`` (fallback: filename stem), preserving the
    TIFF input order.  Returns a list of ``(seq_label, tiff_or_None, zip_or_None)``.
    """
    def keyfor(info: dict, fallback):
        s = info.get("seq")
        if s is not None:
            return ("seq", s)
        stem = Path(info.get("path") or info.get("name") or "").stem
        if stem:
            return ("stem", stem)
        return fallback

    key_order: list = []
    tiff_by: dict = {}
    zip_by: dict = {}

    for i, t in enumerate(composed_tiffs):
        k = keyfor(t, ("t", i))
        if k not in tiff_by:
            tiff_by[k] = t
            if k not in key_order:
                key_order.append(k)
    for i, z in enumerate(archive_zips):
        k = keyfor(z, ("z", i))
        if k not in zip_by:
            zip_by[k] = z
        if k not in key_order:
            key_order.append(k)

    out = []
    for k in key_order:
        label = f"成果 {k[1]}" if k[0] == "seq" else "成果"
        out.append((label, tiff_by.get(k), zip_by.get(k)))
    return out


def _unique_id_from_result_name(name: str, seq=None) -> str:
    """Return the specimen UID portion of a result filename when it matches."""
    stem = Path(name).stem
    parts = stem.split("-")
    if len(parts) < 7:
        return stem
    if seq is not None:
        seq_text = str(seq)
        for idx in range(3, len(parts)):
            if parts[idx] == seq_text:
                return "-".join(parts[:idx] + parts[idx + 1:])
    try:
        int(parts[4])
    except ValueError:
        return stem
    return "-".join(parts[:4] + parts[5:])



# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_size(n: int) -> str:
    """Format byte count as human-readable string."""
    if n >= 1024 ** 3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"
