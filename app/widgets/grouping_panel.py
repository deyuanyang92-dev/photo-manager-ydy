"""grouping_panel.py — Specimen grouping editor.

Displays two sections:
  1. **Draft groups** (未合成): editable list — angle label + drag-and-drop
     reorderable JPG list per group.  Groups without a composed TIFF.
  2. **Composed rows** (已合成): read-only summary — composedTiffPath basename,
     📦 Organise button, ↩ Undo-compose button.

Data source: ``grouping_service.load_grouping(db, uid)``

Emits
-----
compose_requested(uid: str, group_index: int)
    User clicked "合成" for a draft group.
organise_requested(uid: str, group_index: int)
    User clicked "📦整理" on a composed row.
undo_compose_requested(uid: str, group_index: int)
    User clicked "↩撤销" on a composed row.
grouping_changed()
    Emitted after any in-memory edit (label rename, JPG removal) so the
    parent view knows a save is needed.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.services import grouping_service
from app.widgets.grouping_dialogs import (
    _MediaLocationPickerDialog,
    _RelatedFilesPickerDialog,
    _ResultPairPickerDialog,
    _TiffImportDialog,
    _pick_media_location_folder,
    _pick_media_paths_or_folder,
)
from app.widgets.grouping_media_helpers import (
    _JPG_EXTS,
    _TIFF_EXTS,
    _add_dir_shortcut,
    _archive_jpg_count,
    _clear_group_tiff_link,
    _clear_uid_mismatched_result_links,
    _deduplicate_tiff_links,
    _filename_tokens,
    _find_related_media_dirs,
    _folder_from_picker_selection,
    _grouping_attribution_ctx,
    _is_blank_draft_group,
    _is_broad_scan_root,
    _is_composed_group,
    _media_entries_in_dir,
    _media_thumbnail_pixmap,
    _mtime_text,
    _name_matches_any_term,
    _normal_dir,
    _paths_from_mime,
    _project_incoming_dir,
    _registered_result_paths,
    _related_media_candidates,
    _resolve_path_for_group,
    _resolved_archive_zip,
    _result_pair_candidates,
    _result_path_key,
    _scan_all_media_timeline_in_dir,
    _scan_jpgs_near_tiff_in_dir,
    _scan_related_files_in_dir,
    _show_path_in_folder,
    _split_media_paths,
    _uid_core_key,
    _uid_filename_mismatch,
    _uid_match_terms,
    _uid_matches_name,
    _uid_parts,
    _without_blank_draft_groups,
)
from app.widgets.grouping_rows import (
    _AutoGroupDropZone,
    _ComposedRow,
    _CrossGroupList,
    _DraftGroupRow,
    _SuppDropButton,
    _ThumbAreaResizeHandle,
)

if TYPE_CHECKING:
    from app.app_context import AppContext
    from app.services.grouping_service import Group, SpecimenGrouping


class GroupingPanel(QWidget):
    """Full grouping editor: draft groups above, composed rows below.

    Signals
    -------
    compose_requested(uid, group_index)
    organise_requested(uid, group_index)
    undo_compose_requested(uid, group_index)
    grouping_changed()
    """

    compose_requested = pyqtSignal(str, int)
    organise_requested = pyqtSignal(str, int)
    undo_compose_requested = pyqtSignal(str, int)
    grouping_changed = pyqtSignal()
    # Bulk-action signals (capture-main-actions row)
    compose_all_requested = pyqtSignal(str)    # uid — compose all pending groups
    compose_and_organise_all_requested = pyqtSignal(str)  # uid — 合成全部 + 逐组整理
    organise_all_requested = pyqtSignal(str)   # uid — organise all composed groups
    # Add-to-group / free-compose / retroactive signals
    add_selection_to_group_requested = pyqtSignal(int)  # group_index
    free_compose_requested = pyqtSignal()
    retroactive_requested = pyqtSignal()
    auto_group_organize_requested = pyqtSignal()
    tiff_naming_check_requested = pyqtSignal()
    tiff_naming_check_path_requested = pyqtSignal(str)
    helicon_params_requested = pyqtSignal()
    import_tiff_requested = pyqtSignal(str, int)  # uid, group_index  #cursor groupingImportTiff
    archive_zip_registered = pyqtSignal(str, int)  # uid, group_index
    # 补处理 (supplementary archival) — independent of the active-specimen gate.
    supp_process_requested = pyqtSignal()       # click → consume monitor selection
    supp_files_dropped = pyqtSignal(list)       # OS drop → list[str] of local paths

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self._uid: Optional[str] = None
        self._grouping: Optional["SpecimenGrouping"] = None
        self._render_signature: tuple | None = None
        self._selected_group_indexes: set[int] = set()
        self._auto_group_staged_paths: list[str] = []
        self._auto_group_preview_result: Optional[dict] = None
        self._setup_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        section = QFrame()
        section.setObjectName("WorkbenchSection")
        outer.addWidget(section)
        from app.config.effects import apply_card_shadow
        apply_card_shadow(section)

        root = QVBoxLayout(section)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(14)

        # ── capture-main-actions row (web parity: ⚡合成/🗜整理/合成+整理/⋯更多) ──
        # Hidden when no specimen active (mirrors app.js:7374-7378 early return)
        self._toolbar_widget = QWidget()
        main_actions = QHBoxLayout(self._toolbar_widget)
        main_actions.setContentsMargins(0, 0, 0, 0)
        main_actions.setSpacing(8)

        self._select_all_btn = QPushButton("全选组")
        self._select_all_btn.setObjectName("Ghost")
        self._select_all_btn.setFixedHeight(30)
        self._select_all_btn.setToolTip("勾选所有分组；顶部合成/整理只处理勾选组")
        self._select_all_btn.clicked.connect(self.select_all_groups)
        main_actions.addWidget(self._select_all_btn)

        self._clear_selection_btn = QPushButton("清除选择")
        self._clear_selection_btn.setObjectName("Ghost")
        self._clear_selection_btn.setFixedHeight(30)
        self._clear_selection_btn.setToolTip("清除分组勾选；未勾选任何组时顶部操作处理全部")
        self._clear_selection_btn.clicked.connect(self.clear_group_selection)
        main_actions.addWidget(self._clear_selection_btn)

        compose_btn = QPushButton("合成")
        compose_btn.setObjectName("Primary")
        compose_btn.setFixedHeight(30)
        icons.set_button_icon(compose_btn, "mdi6.layers-triple-outline",
                              color=icons.TONE_ON_ACCENT, size=14)
        compose_btn.setToolTip("对所有待合成组调用 Helicon Focus")
        compose_btn.clicked.connect(self._request_compose_all_groups)
        main_actions.addWidget(compose_btn)

        org_btn = QPushButton("整理")
        org_btn.setObjectName("Outline")
        org_btn.setFixedHeight(30)
        icons.set_button_icon(org_btn, "mdi6.folder-zip-outline",
                              color=icons.TONE_ACCENT, size=14)
        org_btn.setToolTip("整理所有已合成组（归档 JPG）")
        org_btn.clicked.connect(self._request_organise_all_groups)
        main_actions.addWidget(org_btn)

        compose_org_btn = QPushButton("合成+整理")
        compose_org_btn.setObjectName("Primary")
        compose_org_btn.setFixedHeight(30)
        icons.set_button_icon(compose_org_btn, "mdi6.archive-check-outline",
                              color=icons.TONE_ON_ACCENT, size=14)
        compose_org_btn.setToolTip("合成后立即整理归档（一条龙）")
        compose_org_btn.clicked.connect(self._request_compose_and_organise_all_groups)
        main_actions.addWidget(compose_org_btn)

        self._auto_group_btn = QPushButton("自动分组整理")
        self._auto_group_btn.setObjectName("Outline")
        self._auto_group_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._auto_group_btn,
            "mdi6.auto-fix",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._auto_group_btn.setToolTip(
            "第一步：扫描并预览分组（不打包）；"
            "核对无误后再点同一按钮执行整理归档"
        )
        self._auto_group_btn.clicked.connect(
            self.auto_group_organize_requested.emit
        )
        main_actions.addWidget(self._auto_group_btn)

        self._tiff_naming_check_btn = QPushButton("检查 TIF")
        self._tiff_naming_check_btn.setObjectName("Outline")
        self._tiff_naming_check_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._tiff_naming_check_btn,
            "mdi6.file-search-outline",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._tiff_naming_check_btn.setToolTip(
            "检查勾选/当前 TIF 命名是否符合项目规则；"
            "当前无 TIF 时可选择目录批量检查；可导出 CSV"
        )
        self._tiff_naming_check_btn.clicked.connect(
            self.tiff_naming_check_requested.emit
        )
        main_actions.addWidget(self._tiff_naming_check_btn)

        self._related_first_btn = QPushButton("相关优先")
        self._related_first_btn.setObjectName("Outline")
        self._related_first_btn.setCheckable(True)
        self._related_first_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._related_first_btn,
            "mdi6.sort-variant",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._related_first_btn.setToolTip(
            "用于每组「+」选图：开启后，当前编号匹配的 TIF 及其时间附近 JPG 优先显示；"
            "关闭时按普通修改时间排序"
        )
        self._related_first_btn.toggled.connect(self._on_related_first_toggled)
        main_actions.addWidget(self._related_first_btn)

        self._related_filter_btn = QPushButton("筛相关")
        self._related_filter_btn.setObjectName("Outline")
        self._related_filter_btn.setCheckable(True)
        self._related_filter_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._related_filter_btn,
            "mdi6.filter-outline",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._related_filter_btn.setToolTip(
            "用于每组「+」选图：先选目录，再打开相关文件列表；"
            "只列当前编号匹配 TIF 及其时间附近 JPG/TIF"
        )
        self._related_filter_btn.toggled.connect(self._on_related_filter_toggled)
        main_actions.addWidget(self._related_filter_btn)

        more_btn = QPushButton("⋯ 更多 ▾")
        more_btn.setObjectName("Ghost")
        more_btn.setFixedHeight(30)
        more_btn.setToolTip("更多操作")
        more_btn.setMenu(self._build_more_menu())
        main_actions.addWidget(more_btn)

        main_actions.addStretch()

        self._add_btn = QPushButton("新组")
        self._add_btn.setObjectName("Outline")
        self._add_btn.setFixedHeight(30)
        icons.set_button_icon(self._add_btn, "mdi6.plus", color=icons.TONE_ACCENT, size=14)
        self._add_btn.clicked.connect(self._add_group)
        self._add_btn.hide()
        main_actions.addWidget(self._add_btn)

        self._import_pending_btn = QPushButton("导入待整理")
        self._import_pending_btn.setObjectName("Outline")
        self._import_pending_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._import_pending_btn,
            "mdi6.image-plus-outline",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._import_pending_btn.setToolTip("选择 JPG，新建一个待整理分组")
        self._import_pending_btn.clicked.connect(self._on_import_pending_group)
        self._import_pending_btn.hide()
        main_actions.addWidget(self._import_pending_btn)

        self._link_result_pair_btn = QPushButton("关联成品")
        self._link_result_pair_btn.setObjectName("Outline")
        self._link_result_pair_btn.setFixedHeight(30)
        icons.set_button_icon(
            self._link_result_pair_btn,
            "mdi6.link-variant",
            color=icons.TONE_ACCENT,
            size=14,
        )
        self._link_result_pair_btn.setToolTip("选择已有 TIF + ZIP，登记到当前编号")
        self._link_result_pair_btn.clicked.connect(self._on_link_result_pair)
        self._link_result_pair_btn.hide()
        main_actions.addWidget(self._link_result_pair_btn)

        self._target_label = QLabel("—")
        self._target_label.setObjectName("Mono")
        self._target_label.setToolTip("当前目标标本编号")
        self._target_label.hide()
        root.addWidget(self._toolbar_widget)
        self._toolbar_widget.hide()

        # ── ▸ 分组工具 collapsible header (popup hides whole row — redundant) ──
        self._group_header_widget = QWidget()
        group_toggle_row = QHBoxLayout(self._group_header_widget)
        group_toggle_row.setContentsMargins(0, 0, 0, 0)
        group_toggle_row.setSpacing(6)
        self._group_toggle_btn = QPushButton("▸ 分组工具")
        self._group_toggle_btn.setObjectName("Ghost")
        self._group_toggle_btn.setFixedHeight(26)
        self._group_toggle_btn.setCheckable(True)
        self._group_toggle_btn.setChecked(True)
        self._group_toggle_btn.clicked.connect(self._set_group_editor_expanded)
        group_toggle_row.addWidget(self._group_toggle_btn)
        self._supp_btn = _SuppDropButton("拖入所选 JPG + TIFF 补处理")
        self._supp_btn.setObjectName("Ghost")
        self._supp_btn.setFixedHeight(26)
        self._supp_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._supp_btn.setToolTip(
            "在监控区勾选 JPG 原片 + TIFF 成片后点击，或直接把文件拖到此处。\n"
            "无需激活标本——标本身份从 TIFF 文件名识别。"
        )
        self._supp_btn.clicked.connect(self.supp_process_requested.emit)
        self._supp_btn.files_dropped.connect(self.supp_files_dropped.emit)
        self._supp_btn.hide()
        group_toggle_row.addStretch()

        self._uid_label = QLabel("未选择标本")
        self._uid_label.setObjectName("Mono")
        self._uid_label.hide()

        root.addWidget(self._group_header_widget)

        self._header_divider = QFrame()
        self._header_divider.setObjectName("Divider")
        self._header_divider.setFixedHeight(1)
        root.addWidget(self._header_divider)

        # Collapsible body
        self._group_body = QWidget()
        body_lay = QVBoxLayout(self._group_body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(8)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 4, 0, 4)
        self._content_lay.setSpacing(8)
        self._content_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)
        body_lay.addWidget(scroll, stretch=1)

        self._auto_group_drop = _AutoGroupDropZone(self)
        self._auto_group_drop.files_dropped.connect(self._on_auto_group_files_added)
        body_lay.addWidget(self._auto_group_drop)

        self._auto_group_preview_host = QFrame()
        self._auto_group_preview_host.setObjectName("Panel")
        preview_lay = QVBoxLayout(self._auto_group_preview_host)
        preview_lay.setContentsMargins(12, 10, 12, 10)
        preview_lay.setSpacing(6)
        self._auto_group_preview_title = QLabel("自动分组预览（尚未整理）")
        self._auto_group_preview_title.setObjectName("Section")
        preview_lay.addWidget(self._auto_group_preview_title)
        self._auto_group_preview_body = QLabel("")
        self._auto_group_preview_body.setObjectName("Muted")
        self._auto_group_preview_body.setWordWrap(True)
        preview_lay.addWidget(self._auto_group_preview_body)
        preview_btn_row = QHBoxLayout()
        preview_btn_row.addStretch()
        self._clear_preview_btn = QPushButton("清除预览")
        self._clear_preview_btn.setObjectName("Ghost")
        self._clear_preview_btn.setFixedHeight(26)
        self._clear_preview_btn.clicked.connect(self.clear_auto_group_preview)
        preview_btn_row.addWidget(self._clear_preview_btn)
        preview_btn_row.addStretch()
        preview_lay.addLayout(preview_btn_row)
        self._auto_group_preview_host.hide()
        body_lay.addWidget(self._auto_group_preview_host)

        root.addWidget(self._group_body, stretch=1)

    # ── Public API ────────────────────────────────────────────────────────────

    def load_grouping(self, uid: str, grouping: "SpecimenGrouping") -> None:
        """Display all groups for *uid*."""
        self._uid = uid
        self._grouping = grouping
        mismatches_removed = _clear_uid_mismatched_result_links(uid, self._grouping.groups)
        if mismatches_removed:
            self._grouping.groups = _without_blank_draft_groups(self._grouping.groups)
        duplicates_removed = _deduplicate_tiff_links(self._grouping.groups)
        self._renumber_default_angle_labels()
        if mismatches_removed or duplicates_removed:
            self._persist_grouping()
        valid = {g.group_index for g in grouping.groups}
        self._selected_group_indexes &= valid
        short = uid[:30] + ("…" if len(uid) > 30 else "")
        self._uid_label.setText(short)
        self._target_label.setText(short)
        self._toolbar_widget.show()
        self._add_btn.show()
        self._import_pending_btn.show()
        self._link_result_pair_btn.show()
        signature = self._grouping_render_signature(uid, grouping)
        if signature == self._render_signature:
            self._refresh_auto_group_drop_visibility()
            return
        self._render_signature = signature
        self._rebuild()

    def clear(self) -> None:
        self._uid = None
        self._grouping = None
        self._render_signature = None
        self._selected_group_indexes.clear()
        self._uid_label.setText("— 未选择标本 —")
        self._target_label.setText("—")
        self._toolbar_widget.hide()
        self._add_btn.hide()
        self._import_pending_btn.hide()
        self._link_result_pair_btn.hide()
        self._clear_content()
        self._refresh_auto_group_drop_visibility()

    def staged_auto_group_paths(self) -> list[str]:
        return list(self._auto_group_staged_paths)

    def has_auto_group_preview(self) -> bool:
        return self._auto_group_preview_result is not None

    def auto_group_preview_result(self) -> Optional[dict]:
        return self._auto_group_preview_result

    def _on_auto_group_files_added(self, paths: list[str]) -> None:
        self.clear_auto_group_preview()
        self.add_auto_group_staged(paths)

    def add_auto_group_staged(self, paths: list[str]) -> None:
        from app.utils.path_utils import normalize_path

        seen = set(self._auto_group_staged_paths)
        for raw in paths:
            ext = Path(raw).suffix.lower()
            if ext not in _JPG_EXTS | _TIFF_EXTS:
                continue
            try:
                resolved = normalize_path(raw)
            except OSError:
                resolved = str(raw)
            if resolved in seen or not Path(resolved).is_file():
                continue
            seen.add(resolved)
            self._auto_group_staged_paths.append(resolved)
        self._refresh_auto_group_drop_visibility()

    def show_auto_group_preview(self, result: dict) -> None:
        self._auto_group_preview_result = result
        lines: list[str] = []
        total_groups = 0
        for sp in result.get("specimens", []):
            uid = sp.get("uid", "")
            for g in sp.get("groups", []):
                total_groups += 1
                jpg_names = ", ".join(Path(p).name for p in g.get("jpgPaths", [])[:6])
                if len(g.get("jpgPaths", [])) > 6:
                    jpg_names += " …"
                lines.append(
                    f"• {uid} / 成果 #{g.get('seq')} / {g.get('tiffName', '')}\n"
                    f"  ← {g.get('jpgCount', 0)} 张：{jpg_names or '（无原片）'}"
                )
        unnamed = result.get("unnamedTiffs") or []
        if unnamed:
            lines.append(f"⚠ {len(unnamed)} 个 TIF 无法识别编号（预览中未纳入整理）")
        unassigned = result.get("unassignedJpgs") or []
        if unassigned:
            lines.append(f"⚠ {len(unassigned)} 张 JPG 未配到 TIF")
        if not lines:
            lines.append("（没有识别到可整理的分组）")
        self._auto_group_preview_body.setText("\n\n".join(lines))
        self._auto_group_preview_title.setText(
            f"自动分组预览（尚未整理）— 共 {total_groups} 组"
        )
        self._auto_group_preview_host.show()
        self._sync_auto_group_action_button()

    def clear_auto_group_preview(self) -> None:
        if self._auto_group_preview_result is None:
            return
        self._auto_group_preview_result = None
        self._auto_group_preview_body.clear()
        self._auto_group_preview_host.hide()
        self._sync_auto_group_action_button()

    def _sync_auto_group_action_button(self) -> None:
        if self._auto_group_preview_result is not None:
            self._auto_group_btn.setText("执行整理归档")
        else:
            self._auto_group_btn.setText("自动分组整理")

    def clear_auto_group_staging(self) -> None:
        if not self._auto_group_staged_paths:
            return
        self._auto_group_staged_paths.clear()
        self.clear_auto_group_preview()
        self._refresh_auto_group_drop_visibility()

    def _refresh_auto_group_drop_visibility(self) -> None:
        has_groups = bool(self._grouping and self._grouping.groups)
        count = len(self._auto_group_staged_paths)
        has_preview = self._auto_group_preview_result is not None
        show = (not has_groups) or count > 0 or has_preview
        self._auto_group_drop.setVisible(show)
        self._auto_group_drop.set_staged_count(count, self._auto_group_staged_paths)
        if has_preview:
            self._auto_group_preview_host.show()

    def selected_group_indexes(self) -> list[int]:
        """Return checked group indexes. Empty means bulk actions should use all."""
        return sorted(self._selected_group_indexes)

    def select_all_groups(self) -> None:
        """Check every current group."""
        if not self._grouping:
            return
        self._selected_group_indexes = {g.group_index for g in self._grouping.groups}
        self._rebuild()

    def clear_group_selection(self) -> None:
        """Clear all group checks; bulk actions fall back to all groups."""
        if not self._selected_group_indexes:
            return
        self._selected_group_indexes.clear()
        self._rebuild()

    def add_jpgs_to_group(self, group_index: int, jpg_paths: list[str]) -> None:
        """Add *jpg_paths* to the group at *group_index* (mutual exclusion).

        Removes paths from all other groups first, then appends (no duplicates).
        Mirrors web groupingAddSelectedToGroup() app.js:5258–5271.
        """
        if not self._grouping:
            return
        # P1: remove paths from all other groups (mutual exclusion)
        for g in self._grouping.groups:
            if g.group_index != group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p not in jpg_paths]
        # P2: add to target group (no duplicates)
        target = next((g for g in self._grouping.groups if g.group_index == group_index), None)
        if target is None:
            return
        for p in jpg_paths:
            if p not in target.jpg_paths:
                target.jpg_paths.append(p)
        self._rebuild()
        self.grouping_changed.emit()

    def drop_external_files(
        self,
        group_index: int,
        jpg_paths: list[str],
        tiff_path: Optional[str] = None,
    ) -> None:
        """监控区/文件夹拖入：JPG 进组；单个 TIF → 关联成片，output_name = TIF 基础名。"""
        if not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index),
            None,
        )
        if target is None:
            return

        if jpg_paths:
            incoming_dir = _project_incoming_dir(self.ctx)
            if incoming_dir is not None:
                from app.services.photo_import_service import import_jpgs_to_incoming
                result = import_jpgs_to_incoming(list(jpg_paths), incoming_dir)
                if result.errors:
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(
                        self, "拖入文件部分失败", "\n".join(result.errors[:5])
                    )
                jpg_paths = result.imported_paths
            jpg_paths = [r for p in jpg_paths if (r := _resolve_path_for_group(p))]
        if tiff_path:
            tiff_resolved = _resolve_path_for_group(tiff_path)
            if not tiff_resolved:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "拖入文件", f"找不到 TIFF：\n{tiff_path}")
                return
            tiff_path = tiff_resolved

        tiff_ok = True
        if tiff_path:
            tiff_ok = self._associate_tiff_with_group(target, tiff_path)

        if jpg_paths:
            self.add_jpgs_to_group(group_index, jpg_paths)
        elif tiff_ok and tiff_path:
            self._rebuild()
            self._persist_grouping_after_editor_change()

        if tiff_ok and tiff_path and self._uid:
            self.import_tiff_requested.emit(self._uid, group_index)

    def _result_file_matches_current_uid(self, path: str, title: str) -> bool:
        if not self._uid or not _uid_filename_mismatch(self._uid, path):
            return True
        QMessageBox.warning(
            self,
            title,
            "文件名编号和当前标本不一致，已拒绝关联。\n\n"
            f"当前编号：{_uid_core_key(self._uid)}\n"
            f"选择文件：{Path(path).name}",
        )
        return False

    def _associate_tiff_with_group(self, target: "Group", tiff_path: str) -> bool:
        """Bind TIFF to *target*; output_name = TIF stem (ZIP 整理同名)."""
        if not self._result_file_matches_current_uid(tiff_path, "关联 TIFF"):
            return False
        new_key = _result_path_key(tiff_path)
        if self._grouping and new_key:
            owner = next(
                (
                    g for g in self._grouping.groups
                    if g is not target
                    and _result_path_key(getattr(g, "composed_tiff_path", None)) == new_key
                ),
                None,
            )
            if owner is not None:
                QMessageBox.warning(
                    self,
                    "TIFF 已关联",
                    f"这个 TIFF 已经在 {owner.angle_label or f'组{owner.group_index + 1}'} 中。\n"
                    "同一个 TIFF 不能重复关联到多个角度。",
                )
                return False
        if target.composed_tiff_path and target.composed_tiff_path != tiff_path:
            reply = QMessageBox.question(
                self,
                "替换 TIFF？",
                f"本组已有 TIFF：{Path(target.composed_tiff_path).name}\n\n"
                f"是否替换为：{Path(tiff_path).name}？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return False

        from datetime import datetime, timezone
        target.composed_tiff_path = tiff_path
        target.output_name = Path(tiff_path).stem
        target.status = "composed"
        target.source = target.source or "external-tif"
        target.updated_at = datetime.now(tz=timezone.utc).isoformat()
        return True

    def remove_jpg_from_group(self, group_index: int, jpg_path: str) -> None:
        """Remove *jpg_path* from the specified group.

        Mirrors web groupingRemoveFile() app.js:5274–5280.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p != jpg_path]
                break
        self._rebuild()
        self.grouping_changed.emit()

    def clear_group(self, group_index: int) -> None:
        """Clear all JPGs from *group_index* (does not delete files).

        Mirrors web groupingClearGroup() app.js:5291–5297.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.jpg_paths = []
                break
        self._rebuild()
        self.grouping_changed.emit()

    def delete_group(self, group_index: int) -> None:
        """Delete the group entirely (in-memory only, no file deletion).

        Mirrors web groupingDeleteGroup() app.js:5283–5289.
        If a TIFF is associated, only the grouping record is removed; the
        image file remains on disk. Use undo-compose for deliberate TIFF
        deletion.
        """
        if not self._grouping:
            return
        target = next((g for g in self._grouping.groups if g.group_index == group_index), None)
        if target is None:
            return
        self._grouping.groups = [g for g in self._grouping.groups if g.group_index != group_index]
        self._renumber_default_angle_labels()
        self._selected_group_indexes.discard(group_index)
        self._rebuild()
        self.grouping_changed.emit()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _move_jpg_between_groups(
        self, src_group_index: int, dst_group_index: int, jpg_path: str
    ) -> None:
        """Move *jpg_path* from *src_group_index* to *dst_group_index* in the
        in-memory model, then persist and emit grouping_changed.

        Called by _CrossGroupList.dropEvent on a cross-group drop.
        """
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == src_group_index:
                g.jpg_paths = [p for p in g.jpg_paths if p != jpg_path]
            elif g.group_index == dst_group_index:
                if jpg_path not in g.jpg_paths:
                    g.jpg_paths.append(jpg_path)
        self._persist_grouping_after_editor_change()

    def _persist_grouping_after_editor_change(self) -> None:
        """Persist the current in-memory grouping to DB and emit grouping_changed."""
        if not self._grouping or not self._uid:
            return
        if _clear_uid_mismatched_result_links(self._uid, self._grouping.groups):
            self._grouping.groups = _without_blank_draft_groups(self._grouping.groups)
        _deduplicate_tiff_links(self._grouping.groups)
        self._persist_grouping()
        self.grouping_changed.emit()

    def _persist_grouping(self) -> None:
        if not self._grouping or not self._uid:
            return
        db = self.ctx.get_db()
        if db is not None:
            grouping_service.save_grouping(
                db, self._uid, self._grouping.groups, clean_phantoms=False
            )

    def _build_more_menu(self):
        """Build the ⋯ 更多 dropdown menu."""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        free_act = menu.addAction("无号合成（选中 JPG → incoming-jpg/）")
        free_act.triggered.connect(self.free_compose_requested.emit)
        retro_act = menu.addAction("存量整理…")
        retro_act.triggered.connect(self.retroactive_requested.emit)
        menu.addSeparator()
        helicon_act = menu.addAction("Helicon 合成参数")
        helicon_act.triggered.connect(self.helicon_params_requested.emit)
        return menu

    def _default_group_label_prefix(self) -> str:
        """Default group label reflects whether this is a specimen or ad-hoc job."""
        from app.services.grouping_service import ADHOC_GROUPING_UID

        return "结果" if self._uid == ADHOC_GROUPING_UID else "角度"

    def _renumber_default_angle_labels(self) -> None:
        """只重排系统默认标签；用户自定义角度名保持不变。"""
        if not self._grouping:
            return
        prefix = self._default_group_label_prefix()
        for display_number, group in enumerate(self._grouping.groups, start=1):
            label = (group.angle_label or "").strip()
            if re.fullmatch(r"(角度|结果)\d+", label):
                group.angle_label = f"{prefix}{display_number}"

    def _grouping_render_signature(self, uid: str, grouping: "SpecimenGrouping") -> tuple:
        """Small immutable snapshot of fields that affect rendered group cards."""
        return (
            uid,
            tuple(
                (
                    g.group_index,
                    g.angle_label or "",
                    tuple(g.jpg_paths),
                    g.composed_tiff_path or "",
                    getattr(g, "archive_zip", None) or "",
                    getattr(g, "status", None) or "",
                    getattr(g, "output_name", None) or "",
                )
                for g in grouping.groups
            ),
            tuple(sorted(self._selected_group_indexes)),
        )

    def _rebuild(self) -> None:
        self._clear_content()
        if not self._grouping or not self._uid:
            self._refresh_auto_group_drop_visibility()
            return

        groups = self._grouping.groups
        draft = [
            g for g in groups
            if not _is_composed_group(g)
        ]
        composed = [
            g for g in groups
            if _is_composed_group(g)
        ]
        display_numbers = {id(g): n for n, g in enumerate(groups, start=1)}

        if draft:
            # 横向胶片条：草稿卡片并排，横向滚动；多角度组也不挤。
            hscroll = QScrollArea()
            hscroll.setObjectName("GroupStrip")
            hscroll.setWidgetResizable(True)
            hscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            hscroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            hscroll.setFixedHeight(286)  # 一张卡片(≈266)+横向滚动条，下方按钮不被裁
            strip = QWidget()
            strip_lay = QHBoxLayout(strip)
            strip_lay.setContentsMargins(0, 0, 0, 0)
            strip_lay.setSpacing(10)
            strip_lay.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            for g in draft:
                row = _DraftGroupRow(
                    g,
                    strip,
                    panel=self,
                    selected=g.group_index in self._selected_group_indexes,
                    display_number=display_numbers[id(g)],
                )
                row.compose_clicked.connect(self._request_compose_group)
                row.organise_clicked.connect(self._request_organise_group)
                row.label_changed.connect(self._rename_group_label)
                row.add_selected_to_group.connect(self._request_add_current_selection_to_group)
                row.jpg_remove_requested.connect(self._remove_jpg_from_draft_group)
                row.clear_group_requested.connect(self._clear_draft_group)      # #cursor
                row.delete_group_requested.connect(self._delete_draft_group)    # #cursor
                row.import_tiff_requested.connect(self._import_existing_tiff_into_group)      # #cursor
                row.add_photos_requested.connect(self._on_add_photos_from_picker)
                row.output_name_changed.connect(self._rename_group_output_stem)
                row.selected_changed.connect(self._track_group_selection_state)
                row.tiff_naming_check_requested.connect(
                    self.tiff_naming_check_path_requested.emit
                )
                row.tiff_delete_requested.connect(self._request_delete_group_tiff)
                strip_lay.addWidget(row)
            hscroll.setWidget(strip)
            self._content_lay.addWidget(hscroll)

        if composed:
            sep = QFrame()
            sep.setObjectName("Divider")
            sep.setFixedHeight(1)
            self._content_lay.addWidget(sep)
            sec_lbl2 = QLabel("已合成")
            sec_lbl2.setObjectName("Section")
            self._content_lay.addWidget(sec_lbl2)
            for g in composed:
                row2 = _ComposedRow(
                    g,
                    self,
                    selected=g.group_index in self._selected_group_indexes,
                    display_number=display_numbers[id(g)],
                )
                row2.organise_clicked.connect(self._request_organise_group)
                row2.link_jpg_clicked.connect(self._link_original_jpgs_to_composed_group)
                row2.undo_clicked.connect(self._request_undo_group_compose)
                row2.selected_changed.connect(self._track_group_selection_state)
                row2.register_zip_clicked.connect(self._register_existing_archive_zip)
                row2.tiff_naming_check_requested.connect(
                    self.tiff_naming_check_path_requested.emit
                )
                row2.tiff_delete_requested.connect(self._request_delete_group_tiff)
                self._content_lay.addWidget(row2)

        if not groups:
            no_lbl = QLabel("此标本暂无分组 — 点「+ 新组」创建")
            no_lbl.setObjectName("Muted")
            self._content_lay.addWidget(no_lbl)

        self._refresh_auto_group_drop_visibility()

    def _clear_content(self) -> None:
        while self._content_lay.count():
            item = self._content_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _add_group(self) -> None:
        """新增一个空草稿组；编号标本用角度N，无编号任务用结果N。"""
        if self._append_group() is None:
            return
        self._render_signature = None
        self._rebuild()
        self.grouping_changed.emit()

    def _append_group(self, *, jpg_paths: Optional[list[str]] = None) -> Optional[int]:
        """Append a draft group and return its group index."""
        if not self._grouping or not self._uid:
            return None
        from app.services.grouping_service import Group

        new_index = max((g.group_index for g in self._grouping.groups), default=-1) + 1
        display_number = len(self._grouping.groups) + 1
        prefix = self._default_group_label_prefix()
        self._grouping.groups.append(
            Group(
                group_index=new_index,
                angle_label=f"{prefix}{display_number}",
                jpg_paths=list(jpg_paths or []),
            )
        )
        return new_index

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _set_group_editor_expanded(self, checked: bool) -> None:
        """Show/hide the group editor body; update toggle button label."""
        self._group_body.setVisible(checked)
        self._group_toggle_btn.setText("▾ 分组工具" if checked else "▸ 分组工具")

    def _request_compose_all_groups(self) -> None:
        """[⚡合成] 批量:发单信号,由 workbench 驱动顺序队列(异步合成需串行,
        不能在面板里紧循环 emit——会同时启动多个 HeliconWorker 互相覆盖)。"""
        if not self._uid:
            return
        self.compose_all_requested.emit(self._uid)

    def _request_organise_all_groups(self) -> None:
        """[🗜整理] 批量:发单信号,workbench 逐组同步整理已合成组。"""
        if not self._uid:
            return
        self.organise_all_requested.emit(self._uid)

    def _request_compose_and_organise_all_groups(self) -> None:
        """[合成+整理] 批量:发单信号,workbench 顺序队列——每组合成完成(异步回调)
        后再同步整理该组,然后下一组。旧紧循环 emit 在合成完成前就读 composed →
        刚合成的组整理空跑,故移除。"""
        if not self._uid:
            return
        self.compose_and_organise_all_requested.emit(self._uid)

    def _request_compose_group(self, group_index: int) -> None:
        if self._uid:
            self.compose_requested.emit(self._uid, group_index)

    def _request_organise_group(self, group_index: int) -> None:
        if self._uid:
            self.organise_requested.emit(self._uid, group_index)

    def _request_undo_group_compose(self, group_index: int) -> None:
        if self._uid:
            self.undo_compose_requested.emit(self._uid, group_index)

    def _request_delete_group_tiff(self, group_index: int) -> None:
        self._request_undo_group_compose(group_index)

    def _rename_group_label(self, group_index: int, new_label: str) -> None:
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.angle_label = new_label
                break
        self.grouping_changed.emit()

    def _rename_group_output_stem(self, group_index: int, name: str) -> None:
        """用户编辑某组「输出 TIF」命名 → 写入 group.output_name（空=回到自动派生）。"""
        if not self._grouping:
            return
        for g in self._grouping.groups:
            if g.group_index == group_index:
                g.output_name = name.strip() or None
                break
        self.grouping_changed.emit()

    def _track_group_selection_state(self, group_index: int, checked: bool) -> None:
        if checked:
            self._selected_group_indexes.add(group_index)
        else:
            self._selected_group_indexes.discard(group_index)

    def _request_add_current_selection_to_group(self, group_index: int) -> None:
        """Request workbench view to resolve monitor selection and add to group."""
        self.add_selection_to_group_requested.emit(group_index)

    def _remove_jpg_from_draft_group(self, group_index: int, jpg_path: str) -> None:
        """Handle right-click remove from _DraftGroupRow."""
        self.remove_jpg_from_group(group_index, jpg_path)

    def _link_original_jpgs_to_composed_group(self, group_index: int) -> None:
        """Link original JPGs to an existing TIFF-only composed row."""
        if not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index),
            None,
        )
        if target is None or not target.composed_tiff_path:
            return
        start = ""
        try:
            start = str(Path(target.composed_tiff_path).parent)
        except Exception:
            start = ""
        jpgs = self._pick_jpgs_for_existing_tiff(target, start=start)
        if not jpgs:
            return
        self.add_jpgs_to_group(group_index, jpgs)

    def _clear_draft_group(self, group_index: int) -> None:  # #cursor
        """Handle clear-group button from _DraftGroupRow."""
        self.clear_group(group_index)

    def _delete_draft_group(self, group_index: int) -> None:  # #cursor
        """Handle delete-group button from _DraftGroupRow."""
        self.delete_group(group_index)

    def _sync_toggle_button_state(self, button: QPushButton, base_text: str) -> None:
        checked = button.isChecked()
        button.setText(f"{base_text}:开" if checked else base_text)
        button.setObjectName("Primary" if checked else "Outline")
        button.style().unpolish(button)
        button.style().polish(button)

    def _on_related_first_toggled(self, checked: bool) -> None:
        self._sync_toggle_button_state(self._related_first_btn, "相关优先")

    def _on_related_filter_toggled(self, checked: bool) -> None:
        if checked and not self._related_first_btn.isChecked():
            self._related_first_btn.setChecked(True)
        self._sync_toggle_button_state(self._related_filter_btn, "筛相关")

    def _group_by_index(self, group_index: int) -> Optional["Group"]:
        if not self._grouping:
            return None
        return next(
            (g for g in self._grouping.groups if g.group_index == group_index),
            None,
        )

    def _start_dir_for_group_picker(self, group_index: int, fallback: str = "") -> str:
        group = self._group_by_index(group_index)
        if group is None:
            return fallback
        media_paths = list(getattr(group, "jpg_paths", None) or [])
        tiff_path = getattr(group, "composed_tiff_path", None)
        if tiff_path:
            media_paths.insert(0, tiff_path)
        for path in media_paths:
            try:
                parent = Path(path).parent
            except Exception:
                continue
            if parent.is_dir():
                return str(parent)
        return fallback

    def _media_location_shortcuts(
        self,
        *,
        start: str = "",
        uid: str = "",
    ) -> list[tuple[str, str]]:
        shortcuts: list[tuple[str, str]] = []
        seen: set[str] = set()
        project_dir = _normal_dir(getattr(self.ctx, "current_project_dir", None))
        settings = getattr(self.ctx, "settings", None)
        incoming_subdir = getattr(settings, "incoming_subdir", None) if settings else None
        results_subdir = getattr(settings, "results_subdir", None) if settings else None
        incoming_subdir = incoming_subdir if isinstance(incoming_subdir, str) and incoming_subdir else "incoming-jpg"
        results_subdir = results_subdir if isinstance(results_subdir, str) and results_subdir else "results"

        start_dir = _normal_dir(start)
        sibling_dsc = project_dir.parent / "dsc" if project_dir is not None else None
        _add_dir_shortcut(shortcuts, seen, "当前起点", start_dir)
        _add_dir_shortcut(shortcuts, seen, "相机原图", sibling_dsc)
        if project_dir is not None:
            _add_dir_shortcut(shortcuts, seen, "incoming-jpg", project_dir / incoming_subdir)
            _add_dir_shortcut(shortcuts, seen, "results", project_dir / results_subdir)
            _add_dir_shortcut(shortcuts, seen, "工作目录", project_dir)
        return shortcuts

    def _preferred_media_location_start(self, start: str, shortcuts: list[tuple[str, str]]) -> str:
        start_dir = _normal_dir(start)
        if start_dir is not None and not _is_broad_scan_root(start_dir):
            return str(start_dir)
        for _label, path in shortcuts:
            p = _normal_dir(path)
            if p is not None:
                return str(p)
        return start

    def _on_add_photos_from_picker(self, group_index: int) -> None:
        """「+」从文件夹多选 JPG/TIF 加入指定组。"""
        if not self._grouping:
            return

        start = ""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            pd = Path(project_dir)
            candidates: list[Path] = []
            try:
                from app.services.project_service import resolve_incoming_jpg_dir
                candidates.append(Path(resolve_incoming_jpg_dir(str(pd))))
            except Exception:
                pass
            s = getattr(self.ctx, "settings", None)
            inc = getattr(s, "incoming_subdir", None) if s else None
            if isinstance(inc, str) and inc:
                candidates.append(pd / inc)
            candidates.append(pd / "incoming-jpg")
            candidates.append(pd / "新拍JPG")
            candidates.append(pd)
            for candidate in candidates:
                if candidate.is_dir():
                    start = str(candidate)
                    break

        from app.utils.ui import get_open_file_names
        related_first = bool(getattr(self, "_related_first_btn", None)
                             and self._related_first_btn.isChecked())
        filter_related = bool(
            self._uid
            and getattr(self, "_related_filter_btn", None)
            and self._related_filter_btn.isChecked()
        )
        if filter_related:
            related_uid = str(self._uid or "").strip()
            paths = self._pick_related_files_from_dir(
                uid=related_uid,
                start=self._start_dir_for_group_picker(group_index, start),
            )
            if paths is None:
                return
            if paths:
                self._add_selected_media_paths_to_group(group_index, paths)
                return
            filter_related = False

        priority_paths = []
        priority_terms = []
        filter_terms = []
        if related_first or filter_related:
            priority_terms = [self._uid] if self._uid else []
        if filter_related:
            filter_terms = [self._uid] if self._uid else []
        caption = "选择 JPG / TIFF 加入分组"
        if filter_related:
            caption = f"{caption}（筛相关：{self._uid or '当前编号'}）"
        elif related_first:
            caption = f"{caption}（相关优先：{self._uid or '当前编号'}）"
        paths = get_open_file_names(
            self,
            caption,
            start=start,
            filter=(
                "JPG 与 TIFF (*.jpg *.jpeg *.JPG *.JPEG *.tif *.tiff *.TIF *.TIFF);;"
                "JPG (*.jpg *.jpeg *.JPG *.JPEG);;"
                "TIFF (*.tif *.tiff *.TIF *.TIFF)"
            ),
            # The proxy-backed mtime sorter is useful for related-file review,
            # but expensive on mounted/network folders. Keep the common "+"
            # path lightweight; users can enable 相关优先 when they need it.
            sort_by_mtime=bool(related_first or filter_related),
            priority_paths=priority_paths,
            priority_terms=priority_terms,
            filter_terms=filter_terms,
        )
        if not paths:
            return

        self._add_selected_media_paths_to_group(group_index, paths)

    def _add_selected_media_paths_to_group(self, group_index: int, paths: list[str]) -> None:
        """Add selected JPG/TIFF paths from any picker to a group."""
        if not paths:
            return

        jpgs, tiffs = _split_media_paths(paths)
        jpgs = [r for p in jpgs if (r := _resolve_path_for_group(p))]
        tiff_path = None
        if tiffs:
            tiff_resolved = _resolve_path_for_group(tiffs[0])
            if not tiff_resolved:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "添加照片", f"找不到 TIFF：\n{tiffs[0]}")
                return
            tiff_path = tiff_resolved
        if not jpgs and not tiff_path:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "添加照片", "未识别到 JPG 或 TIFF 文件，请检查扩展名。"
            )
            return
        if len(tiffs) > 1:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "添加照片", "一次只能关联 1 个 TIFF。")
            return

        self.drop_external_files(group_index, jpgs, tiff_path)

        if (
            jpgs
            and self._uid
            and self._uid != grouping_service.ADHOC_GROUPING_UID
        ):
            project_dir = getattr(self.ctx, "current_project_dir", None)
            if project_dir:
                try:
                    from app.services.activation_service import manual_assign
                    manual_assign(project_dir, self._uid, jpgs)
                except Exception:
                    pass

    def _pick_related_files_from_dir(
        self,
        *,
        uid: str | None = None,
        start: str = "",
    ) -> list[str] | None:
        """Pick current-UID related files after selecting a visible JPG/TIF."""
        target_uid = str(uid or self._uid or "").strip()
        display_key = _uid_core_key(target_uid)
        if not target_uid:
            QMessageBox.warning(self, "筛相关", "当前没有激活编号，无法筛选相关文件。")
            return None

        shortcuts = self._media_location_shortcuts(start=start, uid=target_uid)
        picker_start = self._preferred_media_location_start(start, shortcuts)
        selected_paths, folder = _pick_media_paths_or_folder(
            self,
            f"选择 {display_key} 相关 JPG/TIF 所在位置",
            start=picker_start,
            priority_terms=[],
            file_exts=_JPG_EXTS | _TIFF_EXTS,
            shortcuts=shortcuts,
        )
        if selected_paths:
            return selected_paths
        if not folder:
            return None
        if not Path(folder).is_dir():
            QMessageBox.warning(self, "筛相关", f"无法读取所选位置：\n{folder}")
            return []
        return self._select_related_files_from_folder(
            folder,
            target_uid,
            display_key,
            show_empty_message=True,
        )

    def _select_related_files_from_folder(
        self,
        folder: str,
        target_uid: str,
        display_key: str,
        *,
        show_empty_message: bool,
    ) -> list[str] | None:
        candidates = _scan_related_files_in_dir(folder, target_uid)
        if not candidates:
            if show_empty_message:
                QMessageBox.information(
                    self,
                    "筛相关",
                    f"此目录没有找到 {display_key} 匹配的 TIF，或没有时间附近的 JPG。",
                )
            return []
        dlg = _RelatedFilesPickerDialog(
            display_key,
            folder,
            candidates,
            all_candidates_loader=lambda: _scan_all_media_timeline_in_dir(folder, target_uid),
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.selected_paths()

    def _pick_jpgs_for_existing_tiff(self, group: "Group", *, start: str = "") -> list[str] | None:
        """Pick JPG originals near an already-linked TIFF's timestamp."""
        tiff_path = getattr(group, "composed_tiff_path", None) or ""
        if not tiff_path:
            return None

        display_key = _uid_core_key(self._uid or Path(tiff_path).stem)
        shortcuts = self._media_location_shortcuts(start=start, uid=display_key)
        picker_start = self._preferred_media_location_start(start, shortcuts)
        selected_paths, folder = _pick_media_paths_or_folder(
            self,
            f"选择 {Path(tiff_path).name} 对应 JPG 所在位置",
            start=picker_start,
            priority_terms=[],
            file_exts=_JPG_EXTS,
            shortcuts=shortcuts,
        )
        if selected_paths:
            jpgs, _tiffs = _split_media_paths(selected_paths)
            return jpgs
        if not folder:
            return None
        if not Path(folder).is_dir():
            QMessageBox.warning(self, "关联JPG", f"无法读取所选位置：\n{folder}")
            return []
        candidates = _scan_jpgs_near_tiff_in_dir(folder, tiff_path)
        if not candidates:
            QMessageBox.information(
                self,
                "关联JPG",
                "此目录没有找到与该 TIF 修改时间接近的 JPG。",
            )
            return []
        dlg = _RelatedFilesPickerDialog(
            self._uid or "",
            folder,
            candidates,
            title=f"{Path(tiff_path).name} 附近 JPG",
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        jpgs, _tiffs = _split_media_paths(dlg.selected_paths())
        return jpgs

    def _on_import_pending_group(self) -> None:
        """Toolbar action: select JPGs and create one pending draft group."""
        if not self._grouping:
            return

        start = ""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            pd = Path(project_dir)
            s = getattr(self.ctx, "settings", None)
            inc = getattr(s, "incoming_subdir", None) if s else None
            inc = inc if isinstance(inc, str) and inc else "incoming-jpg"
            candidate = pd / inc
            if candidate.is_dir():
                start = str(candidate)
            elif pd.is_dir():
                start = str(pd)

        from app.utils.ui import get_open_file_names
        paths = get_open_file_names(
            self,
            "导入 JPG 到待整理分组",
            start=start,
            filter="JPG (*.jpg *.jpeg *.JPG *.JPEG)",
            sort_by_mtime=True,
        )
        if not paths:
            return

        jpgs, _tiffs = _split_media_paths(paths)
        if not jpgs:
            QMessageBox.warning(self, "导入待整理", "请选择 JPG 文件。")
            return

        incoming_dir = _project_incoming_dir(self.ctx)
        if incoming_dir is not None:
            from app.services.photo_import_service import import_jpgs_to_incoming
            result = import_jpgs_to_incoming(list(jpgs), incoming_dir)
            if result.errors:
                QMessageBox.warning(
                    self, "导入待整理部分失败", "\n".join(result.errors[:5])
                )
            jpgs = [
                r for p in result.imported_paths
                if (r := _resolve_path_for_group(p))
            ]
        else:
            jpgs = [r for p in jpgs if (r := _resolve_path_for_group(p))]
        if not jpgs:
            QMessageBox.warning(self, "导入待整理", "没有成功导入 JPG，未创建空分组。")
            return

        group_index = self._append_group(jpg_paths=jpgs)
        if group_index is None:
            return
        self._render_signature = None
        self._rebuild()
        self.grouping_changed.emit()

    def _on_link_result_pair(self) -> None:
        """Register an existing TIFF+ZIP pair as a finished result for this UID."""
        if not self._uid or not self._grouping:
            return

        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            QMessageBox.warning(self, "关联成品", "当前没有打开项目。")
            return

        s = getattr(self.ctx, "settings", None)
        res = getattr(s, "results_subdir", None) if s else None
        res = res if isinstance(res, str) and res else "results"
        results_dir = Path(project_dir) / res
        db = None
        try:
            db = self.ctx.get_db(project_dir)
        except Exception:
            db = None
        used = _registered_result_paths(
            db,
            current_uid=self._uid,
            current_groups=list(self._grouping.groups),
        )
        candidates = [
            c for c in _result_pair_candidates(results_dir, used)
            if not _uid_filename_mismatch(self._uid, str(c.get("tiff") or ""))
            and not _uid_filename_mismatch(self._uid, str(c.get("zip") or ""))
        ]
        dlg = _ResultPairPickerDialog(candidates, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_pair()
        if not selected:
            return
        tiff_path = _resolve_path_for_group(selected["tiff"])
        zip_path = str(Path(selected["zip"]))
        if not tiff_path or not Path(zip_path).is_file():
            QMessageBox.warning(self, "关联成品", "TIF 或 ZIP 文件不存在。")
            return
        if (
            not self._result_file_matches_current_uid(tiff_path, "关联成品")
            or not self._result_file_matches_current_uid(zip_path, "关联成品")
        ):
            return
        owner = used.get(_result_path_key(tiff_path)) or used.get(_result_path_key(zip_path))
        if owner:
            QMessageBox.warning(
                self,
                "关联成品",
                f"这组成品已经关联到编号：{owner}\n如需改绑，请先在成果区使用“关联到右侧编号”。",
            )
            return

        group_index = self._append_group()
        if group_index is None:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index),
            None,
        )
        if target is None:
            return

        from datetime import datetime, timezone
        target.composed_tiff_path = tiff_path
        target.output_name = Path(tiff_path).stem
        target.archive_zip = zip_path
        target.status = "organized"
        target.source = "existing-result-pair"
        target.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._render_signature = None
        self._rebuild()
        self.grouping_changed.emit()
        self.archive_zip_registered.emit(self._uid, group_index)

    def _import_existing_tiff_into_group(self, group_index: int) -> None:  # #cursor groupingImportTiff
        """Open TIFF-import dialog and update the group composedTiffPath."""
        if not self._uid or not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index), None
        )
        if target is None:
            return

        # Collect TIFF candidates from the project's results/ and incoming-jpg/
        tiff_candidates: list[str] = []
        try:
            import os
            project_dir = getattr(self.ctx, "current_project_dir", None)
            if project_dir:
                # 用项目配置的 incoming/results 子目录（含遗留 新拍JPG），不写死。
                s = getattr(self.ctx, "settings", None)
                inc = getattr(s, "incoming_subdir", None)
                res = getattr(s, "results_subdir", None)
                inc = inc if isinstance(inc, str) and inc else "incoming-jpg"
                res = res if isinstance(res, str) and res else "results"
                subs = [res, inc]
                if not os.path.isdir(os.path.join(project_dir, inc)) and \
                   os.path.isdir(os.path.join(project_dir, "新拍JPG")):
                    subs.append("新拍JPG")
                for sub in subs:
                    d = os.path.join(project_dir, sub)
                    if os.path.isdir(d):
                        for f in sorted(os.listdir(d)):
                            if f.lower().endswith((".tif", ".tiff")):
                                tiff_candidates.append(os.path.join(d, f))
        except Exception:
            pass

        # Show picker dialog
        dlg = _TiffImportDialog(
            group_index=group_index,
            tiff_candidates=tiff_candidates,
            existing_tiff=target.composed_tiff_path or "",
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        tiff_path = dlg.selected_path()
        if not tiff_path:
            return

        if not self._associate_tiff_with_group(target, tiff_path):
            return

        self._rebuild()
        self.grouping_changed.emit()
        # Propagate to workbench view as well
        if self._uid:
            self.import_tiff_requested.emit(self._uid, group_index)

    def _register_existing_archive_zip(self, group_index: int) -> None:
        """Associate an existing ZIP archive with a composed group."""
        if not self._uid or not self._grouping:
            return
        target = next(
            (g for g in self._grouping.groups if g.group_index == group_index), None
        )
        if target is None:
            return

        from app.utils.ui import get_open_file_name
        zip_path = get_open_file_name(
            self,
            "选择 ZIP 归档",
            filter="ZIP 归档 (*.zip *.ZIP)",
        )
        if not zip_path:
            return
        if not zip_path.lower().endswith(".zip"):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "注册 ZIP", "请选择 .zip 文件。")
            return
        if not self._result_file_matches_current_uid(zip_path, "注册 ZIP"):
            return

        if target.archive_zip and target.archive_zip != zip_path:
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "替换 ZIP？",
                f"本组已有 ZIP：{Path(target.archive_zip).name}\n\n"
                f"是否替换为：{Path(zip_path).name}？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        from datetime import datetime, timezone
        target.archive_zip = zip_path
        target.status = "organized"
        target.updated_at = datetime.now(tz=timezone.utc).isoformat()
        self._rebuild()
        self.grouping_changed.emit()
        self.archive_zip_registered.emit(self._uid, group_index)
