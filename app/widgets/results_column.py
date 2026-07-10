"""② 成果内容 column widget compatibility facade.

Large preview and card implementations live in results_column_lightbox and
results_column_cards.  This module preserves the original import path and
monkeypatch points used by tests.
"""
from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.widgets.results_column_cards import (
    _ArchiveCard,
    _BASE_THUMB,
    _DEFAULT_THUMB,
    _MAX_THUMB,
    _MIN_PAIRED_COLUMNS_WIDTH,
    _MIN_THUMB,
    _ResultCardBase,
    _ResultPairIndicator,
    _ResultRow,
    _SpecimenResultHeader,
    _TiffCard,
    _compact_result_sequence_label,
    _decode_thumb,
    _fmt_size,
    _pair_results,
    _placeholder,
    _unique_id_from_result_name,
)
from app.widgets.results_column_lightbox import (
    _PanImageLabel,
    _TiffLightboxDialog,
    _decode_preview_pixmap,
)

class ResultsColumn(QWidget):
    """② 成果内容 column: collapsible, zoomable, paired TIFF↔ZIP rows."""

    restore_requested = pyqtSignal(str)  # ZIP 绝对路径 → 还原原片 JPG
    specimen_requested = pyqtSignal(str)  # all-results group header → select UID
    show_all_requested = pyqtSignal()
    current_requested = pyqtSignal()
    link_result_requested = pyqtSignal(str, str)  # tiff_path, zip_path
    # 纠错:TIF 关联到了错误编号 → 解绑(不挂任何编号)/ 改绑(挂到指定编号)
    unbind_result_requested = pyqtSignal(str, str)   # tiff_path, zip_path
    rebind_result_requested = pyqtSignal(str, str)   # tiff_path, zip_path
    tiff_naming_check_requested = pyqtSignal(str)  # tiff_path
    tiff_delete_requested = pyqtSignal(str)  # tiff_path

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._thumb_size = _DEFAULT_THUMB
        self._result_view_mode = "list"
        self._thumb_cache: dict = {}
        self._thumb_queue: deque[_ResultCardBase] = deque()
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(20)
        self._thumb_timer.timeout.connect(self._load_next_thumbnail_batch)
        self._layout_refresh_timer = QTimer(self)
        self._layout_refresh_timer.setSingleShot(True)
        self._layout_refresh_timer.setInterval(60)
        self._layout_refresh_timer.timeout.connect(self._refresh_layout_if_needed)
        self._cards: list = []
        self._collapsed = False
        self._sort_key = "seq"
        self._paired_columns_enabled = True
        self._paired_selection_enabled = False
        self._results_dir: str = ""
        self._current_tiffs: list[dict] = []
        self._current_zips: list[dict] = []
        self._current_groups: list[dict] = []
        self._selected_result_paths: set[str] = set()
        self._display_mode = "single"
        self._filename_mode = "full"
        self._rendered_paired_columns = True
        self._shortcuts: list[QShortcut] = []
        self._setup_ui()
        self._install_shortcuts()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("WorkbenchSection")
        outer.addWidget(card)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ── Header row: collapse + title + count + zoom ──
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 0)
        hdr.setSpacing(8)

        self._collapse_btn = QPushButton("▾")
        self._collapse_btn.setObjectName("Ghost")
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.setCheckable(True)
        self._collapse_btn.setToolTip("收起 / 展开成果区")
        self._collapse_btn.clicked.connect(
            lambda: self._set_collapsed(not self._collapsed)
        )
        hdr.addWidget(self._collapse_btn)

        self._title = QLabel("成果")
        self._title.setObjectName("WorkbenchTitle")
        hdr.addWidget(self._title)

        self._count = QLabel("0 项")
        self._count.setObjectName("WorkbenchCount")
        hdr.addWidget(self._count)

        self._current_mode_btn = QPushButton("当前")
        self._current_mode_btn.setObjectName("Ghost")
        self._current_mode_btn.setCheckable(True)
        self._current_mode_btn.setChecked(True)
        self._current_mode_btn.setFixedSize(44, 28)
        self._current_mode_btn.setToolTip("只显示当前选中编号的 TIFF 和 ZIP")
        self._current_mode_btn.clicked.connect(lambda: self.current_requested.emit())
        hdr.addWidget(self._current_mode_btn)

        self._all_mode_btn = QPushButton("全部")
        self._all_mode_btn.setObjectName("Ghost")
        self._all_mode_btn.setCheckable(True)
        self._all_mode_btn.setFixedSize(44, 28)
        self._all_mode_btn.setToolTip("按编号分组显示当前项目全部 TIFF 和 ZIP")
        self._all_mode_btn.clicked.connect(lambda: self.show_all_requested.emit())
        hdr.addWidget(self._all_mode_btn)
        hdr.addStretch()

        self._paired_selection_btn = QPushButton("联选")
        self._paired_selection_btn.setObjectName("Ghost")
        self._paired_selection_btn.setCheckable(True)
        self._paired_selection_btn.setChecked(False)
        self._paired_selection_btn.setFixedSize(56, 28)
        self._paired_selection_btn.clicked.connect(self._set_paired_selection_enabled)
        hdr.addWidget(self._paired_selection_btn)
        self._update_paired_selection_button_state()

        self._options_btn = QPushButton("")
        self._options_btn.setObjectName("Ghost")
        self._options_btn.setFixedSize(28, 28)
        self._options_btn.setToolTip("结果显示与排序选项")
        icons.set_button_icon(self._options_btn, "mdi6.dots-vertical",
                              color=icons.TONE_MUTED, size=14)
        self._options_btn.clicked.connect(self._show_results_options_menu)
        hdr.addWidget(self._options_btn)

        root.addLayout(hdr)
        self._update_options_button_tooltip()

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        root.addWidget(divider)

        # ── Body: scrollable paired rows ──
        self._body = QScrollArea()
        self._body.setObjectName("ResultsScroll")
        self._body.setWidgetResizable(True)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._body.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._body.customContextMenuRequested.connect(self._show_body_menu)
        self._body.viewport().installEventFilter(self)
        self._body.verticalScrollBar().setSingleStep(36)
        self._rows_container = QWidget()
        self._rows_lay = QVBoxLayout(self._rows_container)
        self._rows_lay.setContentsMargins(0, 1, 0, 1)
        self._rows_lay.setSpacing(4)
        self._rows_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._body.setWidget(self._rows_container)
        root.addWidget(self._body, stretch=1)

        self._show_empty()

    # ── Public API ────────────────────────────────────────────────────────────

    def load_uid(self, uid: str,
                 composed_tiffs: list,
                 archive_zips: list) -> None:
        """Populate the paired rows for the given specimen UID."""
        self._display_mode = "single"
        self._update_mode_button_state()
        self._current_groups = []
        self._clear_rows()
        self._title.setText("成果")

        self._current_tiffs = list(composed_tiffs or [])
        self._current_zips = list(archive_zips or [])
        self._prune_selected_result_paths()
        self._remember_results_dir(composed_tiffs, archive_zips)
        composed_tiffs = list(composed_tiffs or [])
        archive_zips = list(archive_zips or [])
        rows = self._sort_rows(_pair_results(composed_tiffs, archive_zips))
        show_paired_columns = self._should_show_paired_columns()
        self._rendered_paired_columns = show_paired_columns
        all_tiff_paths = [
            Path(tinfo["path"]) for _label, tinfo, _zinfo in rows
            if tinfo is not None and tinfo.get("path")
        ]
        self._lightbox_all_paths = list(all_tiff_paths)
        for seq_label, tinfo, zinfo in rows:
            tc = None
            zc = None
            if tinfo is not None:
                tc = _TiffCard(
                    self._display_info(tinfo),
                    open_fn=self._open_in_explorer,
                    lightbox_fn=lambda p, _paths=all_tiff_paths: self._open_tiff_lightbox(p, _paths),
                    link_fn=self._emit_link_result,
                    paired_zip=(zinfo or {}).get("path", "") if zinfo else "",
                    naming_check_fn=self._emit_tiff_naming_check,
                    delete_fn=self._emit_tiff_delete,
                    unbind_fn=self._emit_unbind_result,
                    rebind_fn=self._emit_rebind_result,
                    select_fn=self._toggle_result_selection,
                    selected=self._is_result_selected(tinfo.get("path", "")),
                    thumb_provider=self._thumb_provider,
                    thumb_size=self._thumb_size,
                    result_view_mode=self._result_view_mode,
                    # §7 旧: defer_thumbnail=False —— 卡片构造时同步解码 TIFF
                    # (PIL 全量栅格, 单张几十~几百 ms), 全压在点击处理器里 →
                    # 「点击反应迟钝」。异步分批队列(_queue_thumbnail + 20ms
                    # _thumb_timer)本就写好, 却因这里恒 False 成了死代码。
                    defer_thumbnail=True,
                )
                self._cards.append(tc)
                self._queue_thumbnail(tc)
            if zinfo is not None:
                zc = _ArchiveCard(
                    self._display_info(zinfo), open_fn=self._open_in_explorer,
                    restore_fn=lambda p: self.restore_requested.emit(p),
                    link_fn=self._emit_link_result,
                    paired_tiff=(tinfo or {}).get("path", "") if tinfo else "",
                    select_fn=self._toggle_result_selection,
                    selected=self._is_result_selected(zinfo.get("path", "")),
                    thumb_size=self._thumb_size,
                    result_view_mode=self._result_view_mode,
                )
                self._cards.append(zc)
            self._rows_lay.addWidget(
                _ResultRow(
                    seq_label, tc, zc,
                    show_paired_columns=show_paired_columns,
                )
            )

        n = len(rows)
        self._count.setText(f"{n} 项")
        self._update_options_button_tooltip()
        if not n:
            self._show_empty()

    def load_many(self, groups: list[dict], *, title: str | None = None) -> None:
        """Populate results for multiple specimen UIDs, grouped by UID.

        ``title`` 让「所选 N 个编号」复用同一套分组渲染(默认「全部成果」)。
        """
        self._display_mode = "many"
        self._update_mode_button_state()
        self._title.setText(title or "全部成果")
        self._current_groups = [
            {
                "uid": str(g.get("uid") or ""),
                "tiffs": list(g.get("tiffs") or []),
                "zips": list(g.get("zips") or []),
            }
            for g in list(groups or [])
        ]
        self._current_tiffs = [
            item for g in self._current_groups for item in g.get("tiffs", [])
        ]
        self._current_zips = [
            item for g in self._current_groups for item in g.get("zips", [])
        ]
        self._prune_selected_result_paths()
        self._clear_rows()
        self._remember_results_dir(self._current_tiffs, self._current_zips)

        total_rows = 0
        visible_groups = 0
        show_paired_columns = self._should_show_paired_columns()
        self._rendered_paired_columns = show_paired_columns

        # 「全部」模式的灯箱翻页范围 = 全部编号的成片, 按分组顺序串成一条链。
        # §7 旧: all_tiff_paths 在下面的分组循环内部按 group 重建 —— 打开大图后
        # 上/下一张被锁死在单个编号内, 用户「想预览全部编号照片」做不到。
        grouped_rows: list[tuple[dict, list]] = []
        for group in self._current_groups:
            rows = self._sort_rows(_pair_results(group["tiffs"], group["zips"]))
            if rows:
                grouped_rows.append((group, rows))
        all_tiff_paths = [
            Path(tinfo["path"])
            for _group, rows in grouped_rows
            for _label, tinfo, _zinfo in rows
            if tinfo is not None and tinfo.get("path")
        ]
        self._lightbox_all_paths = list(all_tiff_paths)

        for group, rows in grouped_rows:
            visible_groups += 1
            total_rows += len(rows)
            header = _SpecimenResultHeader(group["uid"], len(rows))
            header.clicked.connect(self.specimen_requested.emit)
            self._rows_lay.addWidget(header)
            # all_tiff_paths: 见上, 跨编号连续翻页
            for seq_label, tinfo, zinfo in rows:
                tc = None
                zc = None
                if tinfo is not None:
                    tc = _TiffCard(
                        self._display_info(tinfo),
                        open_fn=self._open_in_explorer,
                        lightbox_fn=lambda p, _paths=all_tiff_paths: self._open_tiff_lightbox(p, _paths),
                        link_fn=self._emit_link_result,
                        paired_zip=(zinfo or {}).get("path", "") if zinfo else "",
                        naming_check_fn=self._emit_tiff_naming_check,
                        delete_fn=self._emit_tiff_delete,
                        unbind_fn=self._emit_unbind_result,
                        rebind_fn=self._emit_rebind_result,
                        select_fn=self._toggle_result_selection,
                        selected=self._is_result_selected(tinfo.get("path", "")),
                        thumb_provider=self._thumb_provider,
                        thumb_size=self._thumb_size,
                        result_view_mode=self._result_view_mode,
                        # §7 旧: defer_thumbnail=False —— 「全部」模式一次同步解码
                        # 所有编号的成片, 是最慢的一条路径。改延迟 + 分批队列。
                        defer_thumbnail=True,
                    )
                    self._cards.append(tc)
                    self._queue_thumbnail(tc)
                if zinfo is not None:
                    zc = _ArchiveCard(
                        self._display_info(zinfo), open_fn=self._open_in_explorer,
                        restore_fn=lambda p: self.restore_requested.emit(p),
                        link_fn=self._emit_link_result,
                        paired_tiff=(tinfo or {}).get("path", "") if tinfo else "",
                        select_fn=self._toggle_result_selection,
                        selected=self._is_result_selected(zinfo.get("path", "")),
                        thumb_size=self._thumb_size,
                        result_view_mode=self._result_view_mode,
                    )
                    self._cards.append(zc)
                self._rows_lay.addWidget(
                    _ResultRow(
                        seq_label, tc, zc,
                        show_paired_columns=show_paired_columns,
                    )
                )

        self._count.setText(f"{visible_groups} 编号 / {total_rows} 项")
        self._update_options_button_tooltip()
        if not total_rows:
            self._show_empty("暂无成果：当前项目还没有已整理结果")

    def clear(self) -> None:
        """Reset to empty (暂无成果) state."""
        self._clear_rows()
        self._current_tiffs = []
        self._current_zips = []
        self._current_groups = []
        self._selected_result_paths.clear()
        self._display_mode = "single"
        self._update_mode_button_state()
        self._title.setText("成果")
        self._results_dir = ""
        self._show_empty()

    def _emit_link_result(self, tiff_path: str, zip_path: str) -> None:
        self.link_result_requested.emit(tiff_path or "", zip_path or "")

    def _emit_unbind_result(self, tiff_path: str, zip_path: str) -> None:
        self.unbind_result_requested.emit(tiff_path or "", zip_path or "")

    def _emit_rebind_result(self, tiff_path: str, zip_path: str) -> None:
        self.rebind_result_requested.emit(tiff_path or "", zip_path or "")

    def _emit_tiff_naming_check(self, tiff_path: str) -> None:
        self.tiff_naming_check_requested.emit(tiff_path or "")

    def _emit_tiff_delete(self, tiff_path: str) -> None:
        self.tiff_delete_requested.emit(tiff_path or "")

    def selected_result_paths(self) -> list[str]:
        return sorted(self._selected_result_paths)

    def _result_key(self, path: str) -> str:
        if not path:
            return ""
        try:
            from app.utils.path_utils import localize_path
            localized = localize_path(str(path))
            p = Path(localized)
            if p.exists():
                return str(p.resolve())
            return localized
        except OSError:
            return str(path)

    def _is_result_selected(self, path: str) -> bool:
        key = self._result_key(path)
        return bool(key and key in self._selected_result_paths)

    def _toggle_result_selection(self, path: str, card=None) -> None:
        key = self._result_key(path)
        if not key:
            return

        keys = {key}
        if self._paired_selection_enabled:
            tiff_key, zip_key = self._visible_result_pair_for(key)
            if tiff_key and zip_key:
                keys = {tiff_key, zip_key}

        selected = not all(k in self._selected_result_paths for k in keys)
        if selected:
            self._selected_result_paths.update(keys)
        else:
            self._selected_result_paths.difference_update(keys)
        for c in self._cards:
            c_key = self._result_key(c._info.get("path", ""))
            if c_key in keys and hasattr(c, "set_selected"):
                c.set_selected(c_key in self._selected_result_paths)
        self._update_visible_pair_indicator_state()

    def _prune_selected_result_paths(self) -> None:
        valid = self._visible_result_keys()
        self._selected_result_paths &= valid
        self._update_visible_pair_indicator_state()

    def _visible_result_keys(self) -> set[str]:
        return {
            self._result_key(info.get("path", ""))
            for info in self._current_tiffs + self._current_zips
            if info.get("path")
        }

    def _visible_result_pair_for(self, path: str) -> tuple[str, str]:
        target_key = self._result_key(path)
        if not target_key:
            return "", ""
        groups = (
            self._current_groups
            if self._current_groups
            else [{"tiffs": self._current_tiffs, "zips": self._current_zips}]
        )
        for group in groups:
            for _label, tinfo, zinfo in _pair_results(
                list(group.get("tiffs") or []),
                list(group.get("zips") or []),
            ):
                if not tinfo or not zinfo:
                    continue
                tiff_key = self._result_key(tinfo.get("path", ""))
                zip_key = self._result_key(zinfo.get("path", ""))
                if tiff_key and zip_key and target_key in {tiff_key, zip_key}:
                    return tiff_key, zip_key
        return "", ""

    def _update_visible_pair_indicator_state(self) -> None:
        for indicator in self._rows_container.findChildren(_ResultPairIndicator):
            keys = [self._result_key(p) for p in indicator.visible_pair_paths()]
            keys = [k for k in keys if k]
            indicator.set_selected(bool(keys) and all(
                k in self._selected_result_paths for k in keys
            ))

    def _set_paired_selection_enabled(self, checked: bool) -> None:
        self._paired_selection_enabled = bool(checked)
        self._update_paired_selection_button_state()
        self._update_options_button_tooltip()

    def _update_paired_selection_button_state(self) -> None:
        if not hasattr(self, "_paired_selection_btn"):
            return
        self._paired_selection_btn.blockSignals(True)
        self._paired_selection_btn.setChecked(self._paired_selection_enabled)
        self._paired_selection_btn.blockSignals(False)
        self._paired_selection_btn.setObjectName(
            "Outline" if self._paired_selection_enabled else "Ghost"
        )
        icon_name = (
            "mdi6.checkbox-marked-outline"
            if self._paired_selection_enabled
            else "mdi6.checkbox-blank-outline"
        )
        color = (
            icons.TONE_ACCENT
            if self._paired_selection_enabled
            else icons.TONE_MUTED
        )
        icons.set_button_icon(
            self._paired_selection_btn, icon_name, color=color, size=14
        )
        state = "开" if self._paired_selection_enabled else "关"
        self._paired_selection_btn.setToolTip(
            f"联选：{state}；开启后单击 TIF/ZIP 会同时选择当前可见配对"
        )
        self._paired_selection_btn.style().unpolish(self._paired_selection_btn)
        self._paired_selection_btn.style().polish(self._paired_selection_btn)

    # ── Internals ───────────────────────────────────────────────────────────────

    def _thumb_provider(self, path: str) -> Optional[QPixmap]:
        """Return a cached base pixmap for *path* (None if undecodable)."""
        if path in self._thumb_cache:
            return self._thumb_cache[path]
        pm = _decode_thumb(path, _BASE_THUMB)
        self._thumb_cache[path] = pm
        return pm

    def _queue_thumbnail(self, card: "_ResultCardBase") -> None:
        self._thumb_queue.append(card)
        if not self._thumb_timer.isActive():
            self._thumb_timer.start()

    def _load_next_thumbnail_batch(self) -> None:
        loaded = 0
        while self._thumb_queue and loaded < 2:
            card = self._thumb_queue.popleft()
            try:
                if card.parent() is not None:
                    card.load_thumbnail_now()
                    loaded += 1
            except RuntimeError:
                continue
        if not self._thumb_queue:
            self._thumb_timer.stop()

    def _clear_rows(self) -> None:
        self._thumb_queue.clear()
        self._thumb_timer.stop()
        while self._rows_lay.count():
            it = self._rows_lay.takeAt(0)
            if it and it.widget():
                it.widget().deleteLater()
        self._cards = []

    def _show_empty(self, text: str = "暂无成果") -> None:
        self._count.setText("0 项")
        empty = QLabel(text)
        empty.setObjectName("Muted")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        self._rows_lay.addWidget(empty)

    def _display_info(self, info: dict) -> dict:
        out = dict(info)
        raw_name = out.get("name") or Path(out.get("path", "")).name
        if self._filename_mode == "uid":
            out["display_name"] = _unique_id_from_result_name(
                str(raw_name), out.get("seq")
            )
        else:
            out.pop("display_name", None)
        return out

    def _refresh_current_results(self) -> None:
        if self._display_mode == "many":
            self.load_many(self._current_groups)
        else:
            self.load_uid("", self._current_tiffs, self._current_zips)

    def _should_show_paired_columns(self) -> bool:
        if not self._paired_columns_enabled:
            return False
        width = 0
        if hasattr(self, "_body"):
            width = self._body.viewport().width()
        if width <= 0:
            width = self.width()
        if width <= 0 or not self.isVisible():
            return True
        return width >= _MIN_PAIRED_COLUMNS_WIDTH

    def _schedule_layout_refresh(self) -> None:
        if hasattr(self, "_layout_refresh_timer"):
            self._layout_refresh_timer.start()

    def _refresh_layout_if_needed(self) -> None:
        if not (self._current_tiffs or self._current_zips or self._current_groups):
            return
        show_paired_columns = self._should_show_paired_columns()
        if show_paired_columns != self._rendered_paired_columns:
            self._refresh_current_results()

    def eventFilter(self, obj, event) -> bool:
        if hasattr(self, "_body") and obj is self._body.viewport():
            if event.type() == QEvent.Type.Resize:
                self._schedule_layout_refresh()
            if (
                event.type() == QEvent.Type.Wheel
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ):
                self._zoom_results_by_wheel_delta(event.angleDelta().y())
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_layout_refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._schedule_layout_refresh()

    def _show_results_options_menu(self) -> None:
        self._show_results_options_menu_at(
            self._options_btn.mapToGlobal(self._options_btn.rect().bottomLeft())
        )

    def _show_body_menu(self, pos) -> None:
        self._show_results_options_menu_at(
            self._body.viewport().mapToGlobal(pos),
            include_open=True,
        )

    def _show_results_options_menu_at(self, global_pos, *,
                                      include_open: bool = False) -> None:
        menu = QMenu(self)
        if include_open or self._results_dir:
            open_act = menu.addAction("打开 results 文件夹")
            open_act.setEnabled(bool(self._results_dir))
            open_act.triggered.connect(self._open_results_folder)
            menu.addSeparator()

        view_menu = menu.addMenu("显示方式")
        for key, label in (
            ("list", "列表"),
            ("large_thumbnail", "大缩略图"),
        ):
            act = view_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._result_view_mode == key)
            act.triggered.connect(
                lambda _=False, k=key: self._set_result_view_mode(k)
            )

        name_menu = menu.addMenu("显示名称")
        for key, label in (
            ("full", "完整文件名"),
            ("uid", "唯一编号"),
        ):
            act = name_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._filename_mode == key)
            act.triggered.connect(lambda _=False, k=key: self._set_filename_mode(k))

        sort_menu = menu.addMenu("排序")
        for key, label in (
            ("seq", "顺序"),
            ("name", "名称"),
            ("type", "类型"),
            ("size", "大小"),
            ("mtime", "修改时间"),
        ):
            act = sort_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._sort_key == key)
            act.triggered.connect(lambda _=False, k=key: self._set_sort_key(k))

        thumb_menu = menu.addMenu("缩略图大小")
        for size, label in (
            (48, "小"),
            (72, "中"),
            (112, "大"),
            (144, "最大"),
        ):
            act = thumb_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._thumb_size == size)
            act.triggered.connect(lambda _=False, s=size: self._set_zoom(s))

        menu.addSeparator()
        columns_act = menu.addAction("双栏对照")
        columns_act.setCheckable(True)
        columns_act.setChecked(self._paired_columns_enabled)
        columns_act.triggered.connect(self._set_paired_columns_enabled)

        paired_selection_act = menu.addAction("联选 TIF/ZIP")
        paired_selection_act.setCheckable(True)
        paired_selection_act.setChecked(self._paired_selection_enabled)
        paired_selection_act.triggered.connect(self._set_paired_selection_enabled)

        menu.exec(global_pos)

    def _set_filename_mode(self, mode: str) -> None:
        if mode not in {"full", "uid"} or mode == self._filename_mode:
            return
        self._filename_mode = mode
        self._update_options_button_tooltip()
        self._refresh_current_results()

    def _set_result_view_mode(self, mode: str) -> None:
        if mode not in {"list", "large_thumbnail"} or mode == self._result_view_mode:
            return
        self._result_view_mode = mode
        self._update_options_button_tooltip()
        self._refresh_current_results()

    def _set_zoom(self, size: int) -> None:
        """Resize every result thumbnail."""
        size = max(_MIN_THUMB, min(_MAX_THUMB, int(size)))
        if size == self._thumb_size:
            return
        self._thumb_size = size
        for c in self._cards:
            c.set_thumb_size(size)
        self._update_options_button_tooltip()

    def _install_shortcuts(self) -> None:
        def add(seq, callback) -> None:
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

        add("Ctrl++", lambda: self._set_zoom(self._thumb_size + 16))
        add("Ctrl+=", lambda: self._set_zoom(self._thumb_size + 16))
        add("Ctrl+-", lambda: self._set_zoom(self._thumb_size - 16))
        add("Ctrl+0", lambda: self._set_zoom(_DEFAULT_THUMB))

    def _zoom_results_by_wheel_delta(self, delta: int) -> None:
        if delta == 0:
            return
        steps = max(1, abs(delta) // 120)
        direction = 1 if delta > 0 else -1
        self._set_zoom(self._thumb_size + direction * steps * 16)

    def _set_collapsed(self, collapsed: bool) -> None:
        """Collapse / expand the whole results area (single toggle)."""
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        self._collapse_btn.setText("▸" if collapsed else "▾")
        self._collapse_btn.setChecked(collapsed)

    def _update_mode_button_state(self) -> None:
        is_many = self._display_mode == "many"
        for btn, checked in (
            (self._current_mode_btn, not is_many),
            (self._all_mode_btn, is_many),
        ):
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.setObjectName("Outline" if checked else "Ghost")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
            btn.blockSignals(False)

    def _set_sort_key(self, key: str) -> None:
        if key not in {"seq", "name", "type", "size", "mtime"}:
            return
        self._sort_key = key
        self._update_options_button_tooltip()
        if self._display_mode == "many":
            self.load_many(self._current_groups)
        else:
            self.load_uid("", self._current_tiffs, self._current_zips)

    def _set_paired_columns_enabled(self, checked: bool) -> None:
        self._paired_columns_enabled = bool(checked)
        self._update_options_button_tooltip()
        if self._display_mode == "many":
            self.load_many(self._current_groups)
        else:
            self.load_uid("", self._current_tiffs, self._current_zips)

    def _update_options_button_tooltip(self) -> None:
        if not hasattr(self, "_options_btn"):
            return
        view = "大缩略图" if self._result_view_mode == "large_thumbnail" else "列表"
        filename = "唯一编号" if self._filename_mode == "uid" else "完整文件名"
        sort_label = {
            "seq": "顺序",
            "name": "名称",
            "type": "类型",
            "size": "大小",
            "mtime": "修改时间",
        }.get(self._sort_key, "顺序")
        columns = (
            "双栏对照"
            if self._paired_columns_enabled and self._rendered_paired_columns
            else "单列"
        )
        if self._paired_columns_enabled and not self._rendered_paired_columns:
            columns = "单列(宽度不足)"
        paired = "联选开" if self._paired_selection_enabled else "联选关"
        self._options_btn.setToolTip(
            f"结果选项：{view} / {filename} / {sort_label} / {self._thumb_size}px / {columns} / {paired}"
        )

    def _sort_rows(self, rows: list) -> list:
        def stat_value(info: dict, attr: str, default=0):
            if not info:
                return default
            try:
                return getattr(Path(str(info.get("path") or "")).stat(), attr)
            except Exception:
                return default

        def file_name(info: dict) -> str:
            if not info:
                return ""
            path = Path(str(info.get("path") or info.get("name") or ""))
            return str(info.get("name") or path.name)

        def seq_value(info: dict):
            if not info:
                return (1, "")
            seq = info.get("seq")
            if seq is None:
                return (1, Path(info.get("path") or info.get("name") or "").stem.lower())
            try:
                return (0, int(seq))
            except Exception:
                return (0, str(seq).lower())

        def sort_key_for_result_pair(row):
            _label, tinfo, zinfo = row
            primary = tinfo or zinfo or {}
            name = file_name(primary)
            secondary = file_name(zinfo if primary is tinfo else tinfo)
            name_key = (name.lower(), secondary.lower())
            if self._sort_key == "seq":
                return (seq_value(primary), name_key)
            if self._sort_key == "type":
                return (0 if tinfo else 1, Path(name).suffix.lower(), name.lower())
            if self._sort_key == "size":
                size = int((tinfo or {}).get("size") or stat_value(tinfo, "st_size"))
                size += int((zinfo or {}).get("size") or stat_value(zinfo, "st_size"))
                return (size, name.lower())
            if self._sort_key == "mtime":
                return (max(stat_value(tinfo, "st_mtime"), stat_value(zinfo, "st_mtime")),
                        name.lower())
            return name_key

        return sorted(list(rows or []), key=sort_key_for_result_pair)

    def _remember_results_dir(self, composed_tiffs: list, archive_zips: list) -> None:
        for info in list(composed_tiffs or []) + list(archive_zips or []):
            path = str(info.get("path") or "")
            if path:
                self._results_dir = str(Path(path).parent)
                return

    def _open_results_folder(self) -> None:
        if self._results_dir:
            self._open_in_explorer(self._results_dir)

    def _lightbox_scope(self, clicked_path: Path) -> list:
        """大图翻页范围:勾选了 ≥2 张成片就只在勾选集合内翻,否则翻当前全部。

        这就是「选择多个编号预览」——勾选可以跨编号(全部模式下按编号分组显示),
        勾选集合即预览集合;没勾选时保持旧行为(翻当前可见的全部成片)。
        """
        all_paths = list(getattr(self, "_lightbox_all_paths", []) or [])
        selected = [p for p in all_paths if self._is_result_selected(str(p))]
        if len(selected) >= 2 and clicked_path in selected:
            return selected
        return all_paths

    def _open_tiff_lightbox(self, clicked_path: Path, all_paths: list | None = None) -> None:
        """Open the TIFF lightbox dialog starting at *clicked_path*."""
        if all_paths is not None:
            self._lightbox_all_paths = list(all_paths)
        # §7 旧: 直接用传入的 all_paths —— 现在先过勾选范围(多选预览)。
        paths = self._lightbox_scope(clicked_path) or list(all_paths or [])
        try:
            idx = paths.index(clicked_path)
        except ValueError:
            idx = 0
        dlg = _TiffLightboxDialog(paths, initial_index=idx, parent=self)
        dlg.exec()

    def _open_in_explorer(self, path: str) -> None:
        """Open the folder containing *path* in the system file explorer.

        Oracle: app.js openInExplorer() / electron shell.showItemInFolder().
        """
        import subprocess
        import sys
        try:
            is_dir = os.path.isdir(path)
            if sys.platform == "win32":
                if is_dir:
                    subprocess.Popen(["explorer", os.path.normpath(path)])
                else:
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                # WSL: open Windows Explorer via explorer.exe with wslpath
                win_path = path
                try:
                    from app.utils.path_utils import wsl_to_windows
                    win_path = wsl_to_windows(path) or path
                except Exception:
                    pass
                if is_dir:
                    subprocess.Popen(["explorer.exe", win_path])
                else:
                    subprocess.Popen(["explorer.exe", "/select,", win_path])
        except Exception:
            pass

