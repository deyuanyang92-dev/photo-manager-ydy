"""labels_view.py — 标签打印工作台.

The page keeps the web oracle's label-printing behavior and built-in templates,
but presents them as a three-pane print station:

    left   specimen search and selection
    center active-label preview plus paper layout preview
    right  template, size, paper, copy count, and print actions

Hidden Step widgets remain alive as state adapters for the existing selection,
template-library, paper, and output contracts.
"""
from __future__ import annotations

import tempfile
from typing import TYPE_CHECKING
from pathlib import Path

from typing import Optional

from PyQt6.QtCore import Qt, QPoint, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog, QAbstractPrintDialog
from PyQt6.QtWidgets import (
    QButtonGroup,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.config.theme import local_font_css
from app.views.base_view import BaseView
from app.services.label_service import (
    BUILTIN_TEMPLATES,
    DEFAULT_SIZE_KEY,
    DEFAULT_TEMPLATE_KEY,
    LABEL_SIZE_KEYS,
    PAPER_SIZES,
    LabelService,
    LabelTemplateLibrary,
    key_from_id,
    load_specimen_dicts,
    persist_imposition,
    persisted_imposition,
    resolve_template,
    sanitize_imposition,
)
from app.utils.label_core import (
    apply_field_visibility,
    calculate_grid,
    effective_page_mm,
    has_rna_tissue,
    unique_id,
)
from app.utils.label_print import build_printer, paint_jobs
from app.utils.label_sheet import (
    compute_sheet_geometry,
    draw_crop_marks,
    paint_sheet_page,
)
from app.utils.label_render import (
    render_label_onto,
    render_label_pixmap as _render_label_pixmap,   # noqa: F401  (back-compat re-export)
    render_label_preview,
    render_label_preview as _render_label_preview,  # noqa: F401  (back-compat re-export)
)
from app.widgets._collapse import set_layout_children_visible
from app.widgets.label_step1_select import LabelStep1Select
from app.widgets.label_step2_templates import LabelStep2Templates, _DEMO_SPECIMEN
from app.utils.label_core import specimen_to_label_data
from app.widgets.label_step3_paper import LabelStep3Paper
from app.widgets.label_step4_output import LabelStep4Output

if TYPE_CHECKING:
    from app.app_context import AppContext


def _template_display_name(key: str, tmpl: dict) -> str:
    labels = {
        "standard": "样品瓶 · 标准信息（50×30）",
        "compact": "小标签 · 编号 + QR（25×10）",
        "detailed": "样品瓶 · 详细采集信息（60×40）",
        "tissueCompact": "RNAlater · 组织管（30×15）",
        "tissueMini": "RNAlater · 极小管（25×10）",
    }
    return labels.get(key, tmpl.get("name", key))


class _ClickablePreview(QLabel):
    """Big label preview that doubles as a discoverable designer entry.

    A plain QLabel showing the rendered label; double-clicking it opens the
    full template designer (the rich LabelDesignerDialog), so the canvas-based
    designer is reachable straight from the preview the user is looking at.
    """

    doubleClicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("双击打开标签设计器（自由排版 · 对齐 · 二维码 · 多选）")

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(e)


class _PannablePreview(QWidget):
    """Sheet-layout preview that supports drag-to-pan + double-click to enlarge."""

    doubleClicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._pm: Optional[QPixmap] = None
        self._pan_x = 0
        self._pan_y = 0
        self._press_pt: Optional[QPoint] = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("双击放大预览整页 A4 排版")

    def mouseDoubleClickEvent(self, e) -> None:
        self.doubleClicked.emit()

    def setPixmap(self, pm: QPixmap) -> None:
        self._pm = pm
        self._pan_x = 0
        self._pan_y = 0
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#ffffff"))
        if self._pm is not None:
            # 用设备无关尺寸居中：HiDPI 下 pixmap 物理像素 = 逻辑×dpr，裸 width() 会偏。
            sz = self._pm.deviceIndependentSize()
            x = int((self.width() - sz.width()) // 2 + self._pan_x)
            y = int((self.height() - sz.height()) // 2 + self._pan_y)
            p.drawPixmap(x, y, self._pm)

    def mousePressEvent(self, e) -> None:
        self._press_pt = e.pos()
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e) -> None:
        if self._press_pt is not None:
            self._pan_x += e.pos().x() - self._press_pt.x()
            self._pan_y += e.pos().y() - self._press_pt.y()
            self._press_pt = e.pos()
            self.update()

    def mouseReleaseEvent(self, e) -> None:
        self._press_pt = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class LabelsView(BaseView):
    """标签打印页面 — specimen selection, label template, preview, and printing."""

    view_id = "labels"
    nav_title = "标签打印"
    nav_icon = "🏷️"

    def __init__(self, ctx: "AppContext") -> None:
        self._specimens: list[dict] = []
        self._libs = {
            "sample": LabelTemplateLibrary("sample"),
            "tissue": LabelTemplateLibrary("tissue"),
        }
        self._projects: list[dict] = []
        self._active_bucket = "sample"
        self._syncing_specimen_list = False
        self._variant = "studio"
        self._label_edits: dict = {}  # {specimen_idx: {field_key: str}}
        # batch-wide field-level print on/off + blank style (整批统一，不按标本存)
        self._hidden_fields: set = set()
        self._blank_style: str = "placeholder"  # collapse | blank | placeholder
        # sheet-level 空白手写标签：A4/A5 排版时在末尾追加 N 张空白标签供手写。
        self._blank_cells: int = 0
        # A4/A5 拼版参数（空 = 自动：边距 8mm / 间距 2mm / 自动行列）。
        # 键：marginMm, gapMm, forceCols, forceRows, cutMarks(bool) + 排版设计
        # 扩展键（marginTop/Bottom/Left/RightMm, gapX/YMm, shrinkToFit,
        # orientation, startSlot）。按 bucket 各存一份并跨会话持久化。
        self._impositions: dict = {
            b: persisted_imposition(b) for b in ("sample", "tissue")
        }
        self._imposition: dict = self._impositions["sample"]
        self._sheet_page: int = 0      # 当前预览页（多页拼版翻页）
        self._sheet_info: dict = {}    # 最近一次整页绘制的元数据（total_pages 等）
        self._field_checks: dict = {}  # {field_key: QCheckBox} in settings panel
        super().__init__(ctx)
        # 拖动预览分隔线后防抖重绘（预览图按 widget 当前尺寸渲染，须重绘避免发虚/裁切）。
        self._preview_resize_timer = QTimer(self)
        self._preview_resize_timer.setSingleShot(True)
        self._preview_resize_timer.setInterval(80)
        self._preview_resize_timer.timeout.connect(self._refresh_print_studio)

    # ── UI ─────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self._apply_variant("studio")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        header = QFrame()
        header.setObjectName("LabelsHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        title = QLabel("打印标签")
        title.setObjectName("PageTitle")
        title.setStyleSheet("font-size:18px; font-weight:700;")
        h.addWidget(title)
        h.addSpacing(8)
        h.addWidget(QLabel("项目"))
        self._project_combo = QComboBox()
        self._project_combo.setMinimumWidth(260)
        self._project_combo.currentIndexChanged.connect(self._on_project_combo_changed)
        h.addWidget(self._project_combo)
        self._project_hint = QLabel("")
        self._project_hint.setObjectName("Muted")
        h.addWidget(self._project_hint)
        h.addStretch()
        outer.addWidget(header)

        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setChildrenCollapsible(True)
        self._main_splitter.setHandleWidth(6)
        outer.addWidget(self._main_splitter, stretch=1)
        splitter = self._main_splitter

        # Legacy functional widgets stay alive for tests and shared behavior.
        self._step1 = LabelStep1Select()
        self._step2 = LabelStep2Templates(self._libs)
        self._step3 = LabelStep3Paper(self._libs)
        self._step4 = LabelStep4Output()
        for legacy in (self._step1, self._step2, self._step3, self._step4):
            legacy.hide()

        splitter.addWidget(self._build_specimen_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.addWidget(self._build_settings_panel())
        splitter.setSizes([330, 620, 320])

        # Wiring (each change re-pushes data + refreshes counts → mirrors render())
        self._step1.selection_changed.connect(self._on_selection_changed)
        self._step2.config_changed.connect(self._on_config_changed)
        self._step3.config_changed.connect(self._on_config_changed)
        self._step4.print_requested.connect(self._print)

        self._apply_variant_layout("studio")
        self._refresh_print_studio()

    def _build_specimen_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Pane")
        root = QVBoxLayout(panel)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("编号")
        title.setObjectName("PaneTitle")
        head.addWidget(title)
        head.addStretch()
        self._selected_badge = QLabel("0 / 0")
        self._selected_badge.setObjectName("CountBadge")
        head.addWidget(self._selected_badge)
        root.addLayout(head)

        self._spec_search = QLineEdit()
        self._spec_search.setPlaceholderText("搜索编号、物种、地点")
        self._spec_search.textChanged.connect(self._rebuild_specimen_list)
        root.addWidget(self._spec_search)

        quick = QHBoxLayout()
        quick.setSpacing(6)
        for text, fn in (
            ("全选", self._step1.select_all),
            ("RNA", self._step1.select_rna_only),
            ("样品", self._step1.select_sample_only),
            ("清空", self._step1.clear_selection),
        ):
            btn = QPushButton(text)
            btn.setObjectName("GhostBtn")
            btn.clicked.connect(fn)
            quick.addWidget(btn)
        root.addLayout(quick)

        # ── 列表 / 平铺 视图切换（移植自第二设计 LabelListPanel）─────────────────
        view_row = QHBoxLayout()
        view_row.setSpacing(6)
        self._spec_view_mode = "list"
        self._btn_view_list = QPushButton("列表")
        self._btn_view_grid = QPushButton("平铺")
        self._spec_view_group = QButtonGroup(self)
        self._spec_view_group.setExclusive(True)
        for mode, b in (("list", self._btn_view_list), ("grid", self._btn_view_grid)):
            b.setObjectName("GhostBtn")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, m=mode: self._set_spec_view(m))
            self._spec_view_group.addButton(b)
            view_row.addWidget(b)
        view_row.addStretch()
        self._btn_view_list.setChecked(True)
        root.addLayout(view_row)

        self._spec_stack = QStackedWidget()
        # page 0 — list
        self._spec_list = QListWidget()
        self._spec_list.itemChanged.connect(self._on_spec_item_changed)
        self._spec_stack.addWidget(self._spec_list)
        # page 1 — grid (thumbnail tiles of selected labels)
        self._spec_grid_scroll = QScrollArea()
        self._spec_grid_scroll.setWidgetResizable(True)
        self._spec_grid_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._spec_grid_inner = QWidget()
        self._spec_grid_layout = QGridLayout(self._spec_grid_inner)
        self._spec_grid_layout.setContentsMargins(2, 2, 2, 2)
        self._spec_grid_layout.setSpacing(8)
        self._spec_grid_scroll.setWidget(self._spec_grid_inner)
        self._spec_stack.addWidget(self._spec_grid_scroll)
        root.addWidget(self._spec_stack, stretch=1)
        self._specimen_panel = panel
        return panel

    # ── 列表 / 平铺 视图 ──────────────────────────────────────────────────────
    def _set_spec_view(self, mode: str) -> None:
        self._spec_view_mode = "grid" if mode == "grid" else "list"
        self._btn_view_list.setChecked(self._spec_view_mode == "list")
        self._btn_view_grid.setChecked(self._spec_view_mode == "grid")
        self._spec_stack.setCurrentIndex(1 if self._spec_view_mode == "grid" else 0)
        if self._spec_view_mode == "grid":
            self._rebuild_specimen_grid()

    def _spec_thumb(self, idx: int, bucket: str) -> QPixmap:
        sp = self._specimens[idx]
        tmpl = resolve_template(self._libs[bucket])
        dims = self._step3.dims(bucket)
        return render_label_preview(
            tmpl, dims, specimen_to_label_data(sp), 138, 96, dpr=2.0
        )

    def _rebuild_specimen_grid(self) -> None:
        if not hasattr(self, "_spec_grid_layout"):
            return
        while self._spec_grid_layout.count():
            item = self._spec_grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        q = (self._spec_search.text() if hasattr(self, "_spec_search") else "").strip().lower()
        selected = set(self._step1.selected_indices())
        cols = 2
        slot = 0
        for idx in sorted(selected):
            if not (0 <= idx < len(self._specimens)):
                continue
            sp = self._specimens[idx]
            uid = unique_id(sp) or str(sp.get("id") or f"#{idx + 1}")
            species = sp.get("species") or sp.get("latin") or ""
            haystack = f"{uid} {species} {sp.get('collector') or ''}".lower()
            if q and not all(part in haystack for part in q.split()):
                continue
            buckets = ["sample"] + (["tissue"] if has_rna_tissue(sp) else [])
            for bucket in buckets:
                tile = self._make_spec_tile(idx, bucket, sp, uid)
                self._spec_grid_layout.addWidget(tile, slot // cols, slot % cols)
                slot += 1
        if slot == 0:
            empty = QLabel("（没有勾选标本，去“列表”勾选）")
            empty.setObjectName("Muted")
            self._spec_grid_layout.addWidget(empty, 0, 0, 1, cols)

    def _make_spec_tile(self, idx: int, bucket: str, sp: dict, uid: str) -> QFrame:
        tile = QFrame()
        tile.setObjectName("LabelTile")
        tile.setFixedSize(160, 150)
        tile.setCursor(Qt.CursorShape.PointingHandCursor)
        tile.setStyleSheet(
            "QFrame#LabelTile{background:palette(base);border:1px solid #d9e0e6;"
            "border-radius:8px;}"
        )
        v = QVBoxLayout(tile)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)
        img = QLabel()
        img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        pm = self._spec_thumb(idx, bucket)
        if pm is not None and not pm.isNull():
            img.setPixmap(pm)
        else:
            img.setText("—")
        v.addWidget(img, stretch=1)
        cap = QLabel(uid + ("  · RNA" if bucket == "tissue" else ""))
        cap.setObjectName("Muted")
        cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cap.setWordWrap(True)
        v.addWidget(cap)
        # 点击磁贴 = 取消勾选该标本（与列表复选框同一数据源 _step1._checked）
        tile.mousePressEvent = lambda _e, i=idx: self._toggle_specimen(i)  # type: ignore[assignment]
        return tile

    def _toggle_specimen(self, idx: int) -> None:
        if idx in self._step1._checked:
            self._step1._checked.discard(idx)
        else:
            self._step1._checked.add(idx)
        self._step1._sync_checks()
        self._step1.selection_changed.emit()

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("PreviewPane")
        root = QVBoxLayout(panel)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        top = QHBoxLayout()
        self._btn_sample_bucket = QPushButton("样品瓶")
        self._btn_tissue_bucket = QPushButton("RNAlater")
        self._bucket_group = QButtonGroup(self)
        self._bucket_group.setExclusive(True)
        for bucket, btn in (("sample", self._btn_sample_bucket), ("tissue", self._btn_tissue_bucket)):
            btn.setObjectName("BucketBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, b=bucket: self._set_bucket(b))
            self._bucket_group.addButton(btn)
            top.addWidget(btn)
        top.addStretch()
        self._preview_summary = QLabel("")
        self._preview_summary.setObjectName("Muted")
        top.addWidget(self._preview_summary)
        self._btn_open_sheet = QPushButton("放大整页")
        self._btn_open_sheet.setObjectName("GhostBtn")
        self._btn_open_sheet.clicked.connect(self._open_sheet_preview)
        top.addWidget(self._btn_open_sheet)
        root.addLayout(top)

        self._label_preview = _ClickablePreview()
        self._label_preview.setObjectName("LabelCanvas")
        self._label_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label_preview.setMinimumHeight(280)
        self._label_preview.doubleClicked.connect(lambda: self._edit_current_template())

        self._sheet_preview = _PannablePreview()
        self._sheet_preview.setObjectName("SheetCanvas")
        self._sheet_preview.setMinimumHeight(170)
        self._sheet_preview.doubleClicked.connect(self._open_sheet_preview)

        # 两预览框之间用竖向 splitter，让用户上下拖拽改变高度比（默认仍是 2:1 观感）。
        self._preview_split = QSplitter(Qt.Orientation.Vertical)
        self._preview_split.setObjectName("PreviewSplit")
        self._preview_split.setChildrenCollapsible(False)
        self._preview_split.setHandleWidth(6)
        self._preview_split.addWidget(self._label_preview)
        self._preview_split.addWidget(self._sheet_preview)
        self._preview_split.setSizes([400, 200])
        self._preview_split.splitterMoved.connect(self._on_preview_split_moved)
        root.addWidget(self._preview_split, stretch=1)

        # ── 编辑标签内容 (label field overrides, mirrors web oracle §15068-15090) ──
        self._edit_toggle = QPushButton("▶ 编辑标签内容")
        self._edit_toggle.setObjectName("GhostBtn")
        self._edit_toggle.setCheckable(True)
        self._edit_toggle.setFixedHeight(28)
        self._edit_toggle.clicked.connect(self._on_edit_toggle)
        root.addWidget(self._edit_toggle)

        self._edit_scroll = QScrollArea()
        self._edit_scroll.setWidgetResizable(True)
        self._edit_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._edit_scroll.setMaximumHeight(200)
        self._edit_inner = QWidget()
        self._edit_grid = QGridLayout(self._edit_inner)
        self._edit_grid.setContentsMargins(4, 4, 4, 4)
        self._edit_grid.setSpacing(4)
        self._edit_scroll.setWidget(self._edit_inner)
        self._edit_scroll.hide()
        root.addWidget(self._edit_scroll)

        return panel

    def _on_preview_split_moved(self, *_) -> None:
        self._preview_resize_timer.start()

    # ── 编辑标签内容 helpers ──────────────────────────────────────────────────

    # Field names map mirrors web oracle fieldNames (app.js:15074).
    _FIELD_NAMES = {
        "uniqueId": "唯一编号", "headerId": "编号头", "storage": "保存方式",
        "shortDate": "日期段", "fullDate": "完整日期段", "speciesName": "物种名称",
        "latin": "拉丁名", "family": "科", "region": "地点",
        "collectorLabel": "采集人", "photographer": "拍摄者",
        "lon": "经度", "lat": "纬度", "geoArea": "采集地理区",
    }

    def _on_edit_toggle(self, checked: bool) -> None:
        if checked:
            self._edit_toggle.setText("▼ 编辑标签内容")
            self._edit_scroll.show()
            self._rebuild_edit_form()
        else:
            self._edit_toggle.setText("▶ 编辑标签内容")
            self._edit_scroll.hide()

    def _rebuild_edit_form(self) -> None:
        # Clear existing widgets.
        while self._edit_grid.count():
            item = self._edit_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        indices = self._step1.selected_indices()
        if not indices:
            lbl = QLabel("（请先选择标本）")
            lbl.setObjectName("Muted")
            self._edit_grid.addWidget(lbl, 0, 0, 1, 2)
            return

        first_idx = indices[0]
        if first_idx >= len(self._specimens):
            return
        sp = self._specimens[first_idx]
        sp_id = sp.get("id") or ""
        self._edit_toggle.setText(f"▼ 编辑标签内容（{sp_id}）")

        field_keys = self._current_field_keys()
        data = specimen_to_label_data(sp)
        overrides = self._label_edits.get(first_idx, {})

        # Per-specimen value overrides only. The 字段打印开关 + 留白方式 now live in
        # the settings panel (single source of truth — see _rebuild_field_toggles).
        for row_n, key in enumerate(field_keys):
            display = self._FIELD_NAMES.get(key, key)
            lbl = QLabel(display + ":")
            lbl.setObjectName("FormLabel")
            inp = QLineEdit()
            inp.setPlaceholderText(str(data.get(key) or ""))
            inp.setText(str(overrides.get(key, data.get(key) or "")))

            def _handler(text: str, idx: int = first_idx, k: str = key) -> None:
                edits = self._label_edits.setdefault(idx, {})
                edits[k] = text
                self._refresh_print_studio()

            inp.textChanged.connect(_handler)
            self._edit_grid.addWidget(lbl, row_n, 0)
            self._edit_grid.addWidget(inp, row_n, 1)

    def _current_field_keys(self) -> list:
        """Distinct field keys of the active bucket's current template, in order."""
        tmpl = resolve_template(self._libs[self._active_bucket])
        seen: set = set()
        keys: list = []
        for row in tmpl.get("rows", []):
            for f in row.get("fields", []):
                key = f.get("key") or f.get("k") or ""
                if key and key not in seen:
                    seen.add(key)
                    keys.append(key)
        return keys

    def _on_blank_style_changed(self, index: int) -> None:
        combo = self.sender()
        key = combo.itemData(index) if combo is not None else None
        if key:
            self._blank_style = key
            self._refresh_print_studio()

    # ── 字段 / 留白 group (settings panel) ───────────────────────────────────
    def _on_fields_toggle(self, checked: bool) -> None:
        self._fields_toggle.setText("▼ 字段 / 留白" if checked else "▶ 字段 / 留白")
        self._fields_box.setVisible(checked)
        if checked:
            self._rebuild_field_toggles()

    def _rebuild_field_toggles(self) -> None:
        """Build one print-on/off checkbox per template field. Bound to
        _hidden_fields (the single source of truth shared with _build_job)."""
        grid = self._field_checks_grid
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._field_checks = {}
        for i, key in enumerate(self._current_field_keys()):
            chk = QCheckBox(self._FIELD_NAMES.get(key, key))
            chk.setChecked(key not in self._hidden_fields)
            chk.setToolTip("勾选=打印此字段；取消=不打印，按留白方式留白")

            def _toggle(checked: bool, k: str = key) -> None:
                if checked:
                    self._hidden_fields.discard(k)
                else:
                    self._hidden_fields.add(k)
                self._refresh_print_studio()

            chk.toggled.connect(_toggle)
            self._field_checks[key] = chk
            grid.addWidget(chk, i // 2, i % 2)

    def _on_blank_toggle(self, checked: bool) -> None:
        self._blank_count_spin.setEnabled(checked)
        self._blank_cells = self._blank_count_spin.value() if checked else 0
        self._refresh_print_studio()

    def _on_blank_count_changed(self, value: int) -> None:
        if self._blank_check.isChecked():
            self._blank_cells = value
            self._refresh_print_studio()

    def _build_settings_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Pane")
        root = QVBoxLayout(panel)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        title = QLabel("设置")
        title.setObjectName("PaneTitle")
        root.addWidget(title)

        root.addWidget(QLabel("模板"))
        self._template_combo = QComboBox()
        self._template_combo.currentIndexChanged.connect(self._on_template_changed)
        root.addWidget(self._template_combo)

        self._tmpl_action_bar = QWidget()
        tmpl_actions = QHBoxLayout(self._tmpl_action_bar)
        tmpl_actions.setContentsMargins(0, 0, 0, 0)
        self._btn_edit_template = QPushButton("✎ 编辑模板")
        self._btn_edit_template.setObjectName("PrimaryBtn")
        self._btn_edit_template.clicked.connect(self._edit_current_template)
        self._btn_manage_templates = QPushButton("模板库")
        self._btn_manage_templates.setObjectName("GhostBtn")
        self._btn_manage_templates.clicked.connect(self._manage_current_templates)
        tmpl_actions.addWidget(self._btn_edit_template)
        tmpl_actions.addWidget(self._btn_manage_templates)
        root.addWidget(self._tmpl_action_bar)

        # ── 字段 / 留白（整批统一：哪些字段打印 + 留白方式）──────────────────
        # 从折叠的「编辑标签内容」前置到此处，模板旁更显眼。_hidden_fields /
        # _blank_style 单一数据源，「编辑标签内容」仅保留逐标本值覆写。
        self._fields_toggle = QPushButton("▶ 字段 / 留白")
        self._fields_toggle.setObjectName("GhostBtn")
        self._fields_toggle.setCheckable(True)
        self._fields_toggle.setFixedHeight(26)
        self._fields_toggle.clicked.connect(self._on_fields_toggle)
        root.addWidget(self._fields_toggle)

        self._fields_box = QWidget()
        fbox = QVBoxLayout(self._fields_box)
        fbox.setContentsMargins(2, 2, 2, 2)
        fbox.setSpacing(6)
        style_row = QHBoxLayout()
        style_row.setContentsMargins(0, 0, 0, 0)
        style_lbl = QLabel("留白方式：")
        style_lbl.setObjectName("FormLabel")
        self._blank_style_combo = QComboBox()
        for name, data_key in (("收紧", "collapse"), ("留空行", "blank"),
                               ("字段名占位", "placeholder")):
            self._blank_style_combo.addItem(name, data_key)
        cur = self._blank_style_combo.findData(self._blank_style)
        self._blank_style_combo.setCurrentIndex(cur if cur >= 0 else 2)
        self._blank_style_combo.currentIndexChanged.connect(self._on_blank_style_changed)
        style_row.addWidget(style_lbl)
        style_row.addWidget(self._blank_style_combo, stretch=1)
        fbox.addLayout(style_row)
        self._field_checks_wrap = QWidget()
        self._field_checks_grid = QGridLayout(self._field_checks_wrap)
        self._field_checks_grid.setContentsMargins(0, 0, 0, 0)
        self._field_checks_grid.setSpacing(2)
        fbox.addWidget(self._field_checks_wrap)
        root.addWidget(self._fields_box)
        self._fields_box.hide()

        root.addWidget(QLabel("标签尺寸"))
        # 尺寸下拉（预设 + 自定义）
        self._size_combo = QComboBox()
        for key in list(LABEL_SIZE_KEYS) + ["custom"]:
            name = PAPER_SIZES[key]["name"] if key in PAPER_SIZES else "自定义"
            self._size_combo.addItem(name, key)
        self._size_combo.currentIndexChanged.connect(self._on_size_combo)
        root.addWidget(self._size_combo)

        # 自定义 W×H（仅 size==custom 时显示）
        self._size_custom_row = QWidget()
        cw = QHBoxLayout(self._size_custom_row)
        cw.setContentsMargins(0, 0, 0, 0)
        cw.setSpacing(6)
        self._w_spin = QSpinBox()
        self._w_spin.setRange(5, 300)
        self._w_spin.setSuffix(" mm")
        self._h_spin = QSpinBox()
        self._h_spin.setRange(3, 300)
        self._h_spin.setSuffix(" mm")
        self._w_spin.valueChanged.connect(lambda v: self._on_custom_dim("w", v))
        self._h_spin.valueChanged.connect(lambda v: self._on_custom_dim("h", v))
        cw.addWidget(QLabel("宽"))
        cw.addWidget(self._w_spin)
        cw.addWidget(QLabel("高"))
        cw.addWidget(self._h_spin)
        root.addWidget(self._size_custom_row)
        self._size_custom_row.hide()

        root.addWidget(QLabel("纸张"))
        self._paper_combo = QComboBox()
        for key, name in (("label", "小标签纸（一张一页）"), ("a4", "A4 排版"), ("a5", "A5 排版")):
            self._paper_combo.addItem(name, key)
        self._paper_combo.currentIndexChanged.connect(self._on_paper_changed)
        root.addWidget(self._paper_combo)

        # ── 空白手写标签（仅 A4/A5 排版）：末尾补 N 张空白标签供手写 ──────────
        self._blank_row = QWidget()
        blank_l = QHBoxLayout(self._blank_row)
        blank_l.setContentsMargins(0, 0, 0, 0)
        blank_l.setSpacing(6)
        self._blank_check = QCheckBox("空白标签（手写）")
        self._blank_check.toggled.connect(self._on_blank_toggle)
        self._blank_count_spin = QSpinBox()
        self._blank_count_spin.setRange(1, 48)
        self._blank_count_spin.setValue(1)
        self._blank_count_spin.setSuffix(" 张")
        self._blank_count_spin.setEnabled(False)
        self._blank_count_spin.valueChanged.connect(self._on_blank_count_changed)
        blank_l.addWidget(self._blank_check)
        blank_l.addWidget(self._blank_count_spin)
        blank_l.addStretch()
        root.addWidget(self._blank_row)
        self._blank_row.setVisible(False)  # shown only for a4/a5

        # ── 拼版（仅 A4/A5）：边距 / 间距 / 强制行列 / 裁切标记 ──────────────────
        self._imposition_box = self._build_imposition_box()
        root.addWidget(self._imposition_box)
        self._imposition_box.setVisible(False)  # shown only for a4/a5

        root.addWidget(QLabel("每种份数"))
        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 10)
        self._copies_spin.setValue(1)
        self._copies_spin.valueChanged.connect(self._on_copies_changed)
        root.addWidget(self._copies_spin)

        self._job_summary = QLabel("")
        self._job_summary.setObjectName("JobSummary")
        self._job_summary.setWordWrap(True)
        root.addWidget(self._job_summary)

        self._btn_print_sample = QPushButton("打印样品瓶")
        self._btn_print_sample.setObjectName("PrintBtn")
        self._btn_print_sample.clicked.connect(lambda: self._print("sample"))
        self._btn_print_tissue = QPushButton("打印 RNAlater")
        self._btn_print_tissue.setObjectName("PrintBtn")
        self._btn_print_tissue.clicked.connect(lambda: self._print("tissue"))
        # 一键同时打印「样品瓶 + RNAlater 组织管」(单个打印对话框)。
        # 仅当有 R 前缀标本(组织管桶非空)时才有意义。
        self._btn_print_both = QPushButton("打印（样品瓶＋组织管）")
        self._btn_print_both.setObjectName("PrintBtn")
        self._btn_print_both.clicked.connect(self._print_both)
        root.addWidget(self._btn_print_sample)
        root.addWidget(self._btn_print_tissue)
        root.addWidget(self._btn_print_both)
        root.addStretch()
        return panel

    def _apply_variant(self, variant: str) -> None:
        palettes = {
            "studio": {
                "bg": "#f4f6f8", "pane": "#ffffff", "text": "#172026",
                "muted": "#687782", "line": "#d9e0e6", "accent": "#0f766e",
                "accent2": "#2563eb", "canvas": "#eef2f5",
            },
            "sheet": {
                "bg": "#ece7dc", "pane": "#fffaf0", "text": "#1f2933",
                "muted": "#766f62", "line": "#d7cbb8", "accent": "#9a3412",
                "accent2": "#334155", "canvas": "#e3d8c6",
            },
            "plain": {
                "bg": "#fbfbfb", "pane": "#ffffff", "text": "#111111",
                "muted": "#666666", "line": "#d4d4d4", "accent": "#111111",
                "accent2": "#444444", "canvas": "#f2f2f2",
            },
        }
        p = palettes.get(variant, palettes["studio"])
        _ff = local_font_css()
        self.setStyleSheet(f"""
QWidget {{ {_ff}background: {p['bg']}; color: {p['text']}; font-size: 12px; }}
QFrame#Pane, QFrame#PreviewPane {{
    background: {p['pane']}; border: 1px solid {p['line']}; border-radius: 8px;
}}
QLabel#PageTitle {{ color: {p['text']}; font-size: 19px; font-weight: 800; }}
QLabel#PaneTitle {{ color: {p['text']}; font-size: 15px; font-weight: 800; }}
QLabel#Muted {{ color: {p['muted']}; }}
QLabel#CountBadge {{
    color: {p['accent']}; background: {p['canvas']}; border-radius: 4px;
    padding: 3px 7px; font-weight: 700;
}}
QLabel#JobSummary {{
    color: {p['text']}; background: {p['canvas']}; border: 1px solid {p['line']};
    border-radius: 6px; padding: 10px;
}}
QLabel#LabelCanvas, QLabel#SheetCanvas {{
    background: {p['canvas']}; border: 1px solid {p['line']}; border-radius: 8px;
}}
QLineEdit, QComboBox, QSpinBox {{
    background: {p['pane']}; border: 1px solid {p['line']}; border-radius: 5px;
    padding: 6px 8px; color: {p['text']};
}}
QListWidget {{
    background: {p['pane']}; border: 1px solid {p['line']}; border-radius: 6px;
    outline: 0;
}}
QListWidget::item {{ padding: 7px 4px; border-bottom: 1px solid {p['line']}; }}
QListWidget::item:selected {{ background: {p['canvas']}; color: {p['text']}; }}
QPushButton#ModeBtn, QPushButton#GhostBtn, QPushButton#BucketBtn, QPushButton#SizeChip {{
    background: transparent; border: 1px solid {p['line']}; border-radius: 5px;
    padding: 6px 10px; color: {p['text']};
}}
QPushButton#SizeChip {{ padding: 5px 6px; font-size: 11px; }}
QPushButton#ModeBtn:checked, QPushButton#BucketBtn:checked, QPushButton#SizeChip:checked {{
    background: {p['accent']}; color: white; border-color: {p['accent']};
}}
QPushButton#PrimaryBtn, QPushButton#PrintBtn {{
    background: {p['accent']}; color: white; border: none; border-radius: 6px;
    padding: 8px 12px; font-weight: 700;
}}
QPushButton#PrintBtn:disabled {{
    background: {p['line']}; color: {p['muted']};
}}
""")

    def _apply_variant_layout(self, variant: str) -> None:
        # 左侧标本栏：打印台显示，排版/极简隐藏
        if hasattr(self, "_specimen_panel"):
            self._specimen_panel.setVisible(variant == "studio")
        # 中间预览比例：排版模式放大下方纸张排版预览
        if hasattr(self, "_preview_split"):
            if variant == "sheet":
                self._preview_split.setSizes([250, 450])
            else:
                self._preview_split.setSizes([400, 200])
        # 模板操作栏：极简模式隐藏（只保留预览 + 纸张/份数 + 打印）
        if hasattr(self, "_tmpl_action_bar"):
            self._tmpl_action_bar.setVisible(variant != "plain")

    def _set_variant(self, variant: str) -> None:
        self._variant = variant
        self._apply_variant(variant)
        self._apply_variant_layout(variant)
        for key, btn in getattr(self, "_variant_buttons", {}).items():
            btn.setChecked(key == variant)
        self._refresh_print_studio()

    def _set_bucket(self, bucket: str) -> None:
        # 拼版参数按 bucket 各存一份（样品瓶/组织管标签纸通常不同）
        self._impositions[self._active_bucket] = self._imposition
        self._active_bucket = bucket
        self._imposition = self._impositions.setdefault(bucket, {})
        if hasattr(self, "_imp_margin"):
            self._sync_imposition_panel()
        self._btn_sample_bucket.setChecked(bucket == "sample")
        self._btn_tissue_bucket.setChecked(bucket == "tissue")
        self._refresh_print_studio()

    def _rebuild_specimen_list(self) -> None:
        if not hasattr(self, "_spec_list"):
            return
        q = (self._spec_search.text() if hasattr(self, "_spec_search") else "").strip().lower()
        selected = set(self._step1.selected_indices()) if hasattr(self, "_step1") else set()
        self._syncing_specimen_list = True
        self._spec_list.clear()
        for idx, sp in enumerate(self._specimens):
            uid = unique_id(sp) or str(sp.get("id") or f"#{idx + 1}")
            species = sp.get("species") or sp.get("latin") or ""
            site = " ".join(str(sp.get(k) or "") for k in ("province", "site", "station"))
            haystack = f"{uid} {species} {site} {sp.get('collector') or ''}".lower()
            if q and not all(part in haystack for part in q.split()):
                continue
            suffix = "  RNA" if has_rna_tissue(sp) else ""
            item = QListWidgetItem(f"{uid}\n{species}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, idx)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if idx in selected else Qt.CheckState.Unchecked)
            self._spec_list.addItem(item)
        self._syncing_specimen_list = False
        if getattr(self, "_spec_view_mode", "list") == "grid":
            self._rebuild_specimen_grid()

    def _on_spec_item_changed(self, item: QListWidgetItem) -> None:
        if self._syncing_specimen_list:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        checked = item.checkState() == Qt.CheckState.Checked
        if checked:
            self._step1._checked.add(int(idx))
        else:
            self._step1._checked.discard(int(idx))
        self._step1._sync_checks()
        self._step1.selection_changed.emit()

    def _refresh_control_values(self) -> None:
        if not hasattr(self, "_template_combo"):
            return
        bucket = self._active_bucket
        lib = self._libs[bucket]

        blocked = self._template_combo.blockSignals(True)
        self._template_combo.clear()
        is_tissue = bucket == "tissue"
        for key, tmpl in BUILTIN_TEMPLATES.items():
            if (tmpl.get("flavor") == "tissue") != is_tissue or key == "tissueCustom":
                continue
            self._template_combo.addItem(_template_display_name(key, tmpl), key)
        for rec in lib.records():
            self._template_combo.addItem(f"自定义 · {rec.get('name', '未命名')}", key_from_id(rec["id"]))
        cur_key = lib.selected_key() or DEFAULT_TEMPLATE_KEY[bucket]
        idx = self._template_combo.findData(cur_key)
        self._template_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._template_combo.blockSignals(blocked)

        size_key = lib.selected_size_key() or DEFAULT_SIZE_KEY[bucket]
        sb = self._size_combo.blockSignals(True)
        si = self._size_combo.findData(size_key)
        self._size_combo.setCurrentIndex(si if si >= 0 else 0)
        self._size_combo.blockSignals(sb)
        is_custom = self._size_combo.currentData() == "custom"
        self._size_custom_row.setVisible(is_custom)
        if is_custom:
            d = self._step3.dims(bucket)
            for spin, axis in ((self._w_spin, "w"), (self._h_spin, "h")):
                b = spin.blockSignals(True)
                spin.setValue(int(d.get(axis, 0) or 0))
                spin.blockSignals(b)

        paper_blocked = self._paper_combo.blockSignals(True)
        paper_idx = self._paper_combo.findData(self._step3.paper_type(bucket))
        self._paper_combo.setCurrentIndex(paper_idx if paper_idx >= 0 else 0)
        self._paper_combo.blockSignals(paper_blocked)

        copies_blocked = self._copies_spin.blockSignals(True)
        self._copies_spin.setValue(self._step3.copies())
        self._copies_spin.blockSignals(copies_blocked)

    def _refresh_print_studio(self) -> None:
        if not hasattr(self, "_label_preview"):
            return
        self._refresh_control_values()
        self._rebuild_specimen_list()

        sample_job = self._build_job("sample")
        tissue_job = self._build_job("tissue")
        sample_n = len(sample_job.get("items") or [])
        tissue_n = len(tissue_job.get("items") or [])
        total = (sample_n + tissue_n) * self._step3.copies()
        selected_n = len(self._step1.selected_indices())
        self._selected_badge.setText(f"{selected_n} / {len(self._specimens)}")
        # 末尾计入的空白手写标签（仅当前桶为 A4/A5 时）。
        blank_note = (
            f"（含 {self._blank_cells} 张空白手写）"
            if self._blank_cells > 0
            and self._step3.paper_type(self._active_bucket) in ("a4", "a5")
            else ""
        )
        self._preview_summary.setText(
            f"样品瓶 {sample_n} · RNAlater {tissue_n} · 共 {total} 张{blank_note}")
        self._job_summary.setText(
            f"当前会打印：样品瓶 {sample_n} 张，RNAlater {tissue_n} 张。\n"
            f"当前桶：{'样品瓶' if self._active_bucket == 'sample' else 'RNAlater'} · "
            f"{self._step3.dims(self._active_bucket).get('w')}×{self._step3.dims(self._active_bucket).get('h')} mm"
        )
        self._btn_tissue_bucket.setEnabled(tissue_n > 0)
        if self._active_bucket == "tissue" and tissue_n <= 0:
            # 自动回落到样品瓶桶时拼版参数也要跟着换（按 bucket 各存一份）
            self._impositions["tissue"] = self._imposition
            self._active_bucket = "sample"
            self._imposition = self._impositions.setdefault("sample", {})
            self._sync_imposition_panel()
        self._btn_sample_bucket.setChecked(self._active_bucket == "sample")
        self._btn_tissue_bucket.setChecked(self._active_bucket == "tissue")
        self._btn_print_sample.setEnabled(sample_n > 0)
        self._btn_print_tissue.setEnabled(tissue_n > 0)
        # 一键打两张只在两桶都有内容时才有意义(否则等同于单独打样品瓶)。
        self._btn_print_both.setEnabled(sample_n > 0 and tissue_n > 0)

        job = sample_job if self._active_bucket == "sample" else tissue_job
        items = job.get("items") or []
        has_real = bool(self._step1.selected_indices())
        if items and has_real:
            data = items[0].get("data") if isinstance(items[0], dict) else items[0]
        else:
            # No real specimen selected — show demo data in the BIG preview so the
            # template stays visible. (Printing still uses the blank items above,
            # so output is blank — the demo is preview-only visualization.)
            data = specimen_to_label_data(_DEMO_SPECIMEN)
        tmpl = job.get("template") or resolve_template(self._libs[self._active_bucket])
        dims = job.get("dims") or self._step3.dims(self._active_bucket)
        pm = render_label_preview(
            tmpl, dims, data or {},
            max(260, self._label_preview.width() - 32),
            max(180, self._label_preview.height() - 32),
            dpr=2.0,
        )
        self._label_preview.setPixmap(pm)
        self._label_preview.setText("")
        self._render_sheet_preview(job)

    @staticmethod
    def _draw_crop_marks(painter: QPainter, x: int, y: int, w: int, h: int,
                         arm: int = 4, gap: int = 2) -> None:
        """Crop/cut marks — delegates to the shared label_sheet painter
        (kept as a method because the print path passes it as a callback)."""
        draw_crop_marks(painter, x, y, w, h, arm=arm, gap=gap)

    def _open_sheet_preview(self) -> None:
        """双击底部缩略图 → 弹出大窗，高清预览整页排版。

        24-up 整页铺一屏每格很小，文字看着像横杠。这里放进可滚动画布 + 缩放
        （➕/➖/适应窗口 或 Ctrl+滚轮），放大后每格的真实标签内容清晰可读。
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("排版预览 — 整页（Ctrl+滚轮 或 ➕/➖ 缩放，拖动滚动条查看）")
        dlg.setModal(True)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setStyleSheet("background:#e9edf0;")
        canvas = QLabel()
        canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        canvas.setStyleSheet("background:#ffffff; border:1px solid #c8d0d8;")
        scroll.setWidget(canvas)
        v.addWidget(scroll, stretch=1)

        bar = QHBoxLayout()
        page_lbl = QLabel("")
        page_lbl.setStyleSheet("color:#4b5563;")
        btn_out = QPushButton("➖")
        btn_in = QPushButton("➕")
        btn_fit = QPushButton("适应窗口")
        btn_prev = QPushButton("◀ 上一页")
        btn_next = QPushButton("下一页 ▶")
        btn_close = QPushButton("关闭")
        bar.addWidget(page_lbl)
        bar.addStretch()
        for b in (btn_out, btn_in, btn_fit, btn_prev, btn_next, btn_close):
            bar.addWidget(b)
        v.addLayout(bar)

        scr = self.screen() or QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        fit_h = int((avail.height() * 0.80) if avail else 820)
        fit_h = max(540, min(fit_h, 1080))
        dpr = float(canvas.devicePixelRatioF() or 1.0)
        state = {"zoom": 1.0}   # 1.0 = 适应窗口；最高 5×

        def _redraw() -> None:
            job_now = self._build_job(self._active_bucket)
            ph = int(fit_h * state["zoom"])
            paper = job_now.get("paperType") or "label"
            if paper in ("a4", "a5"):
                # 纸张可能横放（排版设计），画布纵横比跟随有效页面
                pw_mm, ph_mm = effective_page_mm(
                    PAPER_SIZES[paper], paper, self._grid_opts())
                aspect = pw_mm / max(1.0, ph_mm)
            else:
                aspect = 0.74               # 小标签纸沿用旧画布比例
            pw = int(ph * aspect) + 60
            canvas.setPixmap(self._paint_sheet(job_now, pw, ph, dpr))
            canvas.setFixedSize(pw, ph)
            if paper in ("a4", "a5"):
                # 总页数读共享画家结果（已含起始格偏移）
                total = (self._sheet_info or {}).get("total_pages", 1)
            else:
                total = 1
            page_lbl.setText(
                f"第 {self._sheet_page + 1} / {total} 页 · {int(state['zoom'] * 100)}%")
            btn_prev.setEnabled(self._sheet_page > 0)
            btn_next.setEnabled(self._sheet_page < total - 1)

        def _set_zoom(z: float) -> None:
            state["zoom"] = max(1.0, min(5.0, z))
            _redraw()

        def _step(delta: int) -> None:
            self._sheet_page = max(0, self._sheet_page + delta)
            self._refresh_print_studio()   # keep inline thumbnail in sync + clamp page
            _redraw()

        def _wheel(e) -> None:
            if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
                _set_zoom(state["zoom"] * (1.25 if e.angleDelta().y() > 0 else 0.8))
                e.accept()
            else:
                QScrollArea.wheelEvent(scroll, e)

        scroll.wheelEvent = _wheel
        btn_out.clicked.connect(lambda: _set_zoom(state["zoom"] * 0.8))
        btn_in.clicked.connect(lambda: _set_zoom(state["zoom"] * 1.25))
        btn_fit.clicked.connect(lambda: _set_zoom(1.0))
        btn_prev.clicked.connect(lambda: _step(-1))
        btn_next.clicked.connect(lambda: _step(1))
        btn_close.clicked.connect(dlg.accept)

        win_w = min(int(fit_h * 0.74) + 110, (avail.width() - 40) if avail else 900)
        dlg.resize(win_w, fit_h + 96)
        _redraw()
        dlg.exec()

    def _render_sheet_preview(self, job: dict) -> None:
        w = max(280, self._sheet_preview.width() or 420)
        h = max(150, self._sheet_preview.height() or 190)
        # HiDPI：按屏幕 devicePixelRatio 渲染，避免合成器放大导致发虚（大标签预览用
        # dpr=2.0；排版预览此前用裸逻辑像素，在 Windows 缩放下模糊）。painter 仍以
        # 逻辑像素作图，下方所有坐标无需改动。
        dpr = self._sheet_preview.devicePixelRatioF() or 1.0
        self._sheet_preview.setPixmap(self._paint_sheet(job, w, h, dpr))

    def _paint_sheet(self, job: dict, w: int, h: int, dpr: float = 1.0) -> QPixmap:
        """Render the A4/A5/小标签 排版 sheet into a pixmap (w×h logical px).

        Shared by the inline thumbnail and the large 双击 preview dialog so both
        show byte-identical layout — only the size differs.
        """
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor("#ffffff"))
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(0, 0, w, h, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#c8d0d8"), 1))
        painter.drawRect(8, 8, w - 16, h - 16)

        items = job.get("items") or []
        dims = job.get("dims") or {"w": 50, "h": 30}
        paper_type = job.get("paperType") or "label"
        tmpl = job.get("template") or {}
        is_circle = (tmpl.get("shape") or "rect").lower() == "circle"
        painter.setPen(QPen(QColor("#7b8794"), 1))
        if paper_type == "label":
            label_w = min(w - 80, 300)
            label_h = max(34, int(label_w * float(dims.get("h", 30)) / max(1.0, float(dims.get("w", 50)))))
            label_h = min(label_h, h - 70)
            x = int((w - label_w) / 2)
            y = int((h - label_h) / 2) - 6
            painter.fillRect(x, y, int(label_w), int(label_h), QColor("#f9fafb"))
            if is_circle:
                painter.drawEllipse(x, y, int(label_w), int(label_h))
            else:
                painter.drawRect(x, y, int(label_w), int(label_h))
            painter.setPen(QColor("#4b5563"))
            painter.drawText(18, h - 12, f"小标签纸 · 每页 1 张 · {dims.get('w')}×{dims.get('h')} mm")
        else:
            paper = PAPER_SIZES.get(paper_type, PAPER_SIZES["a4"])
            grid_opts = self._grid_opts()
            # 共享画家：真实 mm 几何（边距/间距/缩放按比例呈现），与打印同源。
            geom = compute_sheet_geometry(dims, paper_type, paper, grid_opts, w, h)
            info = paint_sheet_page(
                painter, job, grid_opts, self._sheet_page, geom,
                cut_marks=bool(self._imposition.get("cutMarks")),
                demo_data=specimen_to_label_data(_DEMO_SPECIMEN),
            )
            cols, rows = geom["grid"]["cols"], geom["grid"]["rows"]
            per_page, total_pages = info["per_page"], info["total_pages"]
            page_count = info["page_count"]
            self._sheet_page = max(0, min(self._sheet_page, total_pages - 1))
            self._sheet_info = info     # 排版预览弹窗读取 total_pages（含起始格）
            note_suffix = "" if info["wysiwyg"] else " · 格子过多，省略内容预览"
            painter.setPen(QColor("#4b5563"))
            page_note = f" · 第 {self._sheet_page + 1}/{total_pages} 页" if total_pages > 1 else ""
            ghost_note = (
                f" · 重复内容为排版示意（实印 {page_count} 张）"
                if info["wysiwyg"] and 0 < page_count < per_page else "")
            painter.drawText(
                18, h - 12,
                f"{paper['name']} · {cols}列 × {rows}行 · 本页最多 {per_page} 张"
                f"{note_suffix}{page_note}{ghost_note}")
        painter.end()
        return pm

    def _on_template_changed(self, index: int) -> None:
        key = self._template_combo.itemData(index)
        if not key:
            return
        self._libs[self._active_bucket].set_selected_key(key)
        self._on_config_changed()

    def _on_size_combo(self, _idx: int) -> None:
        key = self._size_combo.currentData()
        if not key:
            return
        self._step3._on_size(self._active_bucket, key)
        self._size_custom_row.setVisible(key == "custom")
        self._refresh_print_studio()

    def _on_custom_dim(self, axis: str, value: float) -> None:
        self._step3._on_custom(self._active_bucket, axis, value)
        self._refresh_print_studio()

    def _build_imposition_box(self) -> QWidget:
        """A4/A5 拼版控制：边距/间距/强制行列/裁切标记 → self._imposition。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        v.addWidget(QLabel("拼版"))

        def _spin(lo, hi, val, suffix=" mm", step=0.5):
            s = QDoubleSpinBox(); s.setRange(lo, hi); s.setSingleStep(step)
            s.setSuffix(suffix); s.setValue(val)
            return s

        r1 = QHBoxLayout(); r1.setSpacing(6)
        self._imp_margin = _spin(0.0, 30.0, 8.0)
        self._imp_gap = _spin(0.0, 30.0, 2.0)
        self._imp_margin.valueChanged.connect(
            lambda val: self._on_imposition_changed("marginMm", val))
        self._imp_gap.valueChanged.connect(
            lambda val: self._on_imposition_changed("gapMm", val))
        r1.addWidget(QLabel("边距")); r1.addWidget(self._imp_margin)
        r1.addWidget(QLabel("间距")); r1.addWidget(self._imp_gap)
        v.addLayout(r1)

        r2 = QHBoxLayout(); r2.setSpacing(6)
        # 0 = 自动
        self._imp_cols = QSpinBox(); self._imp_cols.setRange(0, 50)
        self._imp_cols.setSpecialValueText("自动")
        self._imp_rows = QSpinBox(); self._imp_rows.setRange(0, 50)
        self._imp_rows.setSpecialValueText("自动")
        self._imp_cols.valueChanged.connect(
            lambda val: self._on_imposition_changed("forceCols", val or None))
        self._imp_rows.valueChanged.connect(
            lambda val: self._on_imposition_changed("forceRows", val or None))
        r2.addWidget(QLabel("列")); r2.addWidget(self._imp_cols)
        r2.addWidget(QLabel("行")); r2.addWidget(self._imp_rows)
        v.addLayout(r2)

        r3 = QHBoxLayout(); r3.setSpacing(6)
        self._imp_cutmarks = QCheckBox("裁切标记")
        self._imp_cutmarks.toggled.connect(
            lambda on: self._on_imposition_changed("cutMarks", on or None))
        r3.addWidget(self._imp_cutmarks)
        self._btn_imposition_design = QPushButton("排版设计…")
        self._btn_imposition_design.clicked.connect(self._open_imposition_designer)
        r3.addWidget(self._btn_imposition_design)
        self._btn_prev_page = QPushButton("◀ 上一页")
        self._btn_next_page = QPushButton("下一页 ▶")
        self._btn_prev_page.clicked.connect(lambda: self._change_sheet_page(-1))
        self._btn_next_page.clicked.connect(lambda: self._change_sheet_page(1))
        r3.addStretch()
        r3.addWidget(self._btn_prev_page)
        r3.addWidget(self._btn_next_page)
        v.addLayout(r3)
        self._sync_imposition_panel()      # 恢复持久化的拼版参数到面板
        return box

    def _on_imposition_changed(self, key: str, value) -> None:
        if getattr(self, "_imposition_syncing", False):
            return
        if value is None:
            self._imposition.pop(key, None)
        else:
            self._imposition[key] = value
        # 面板上的单值控件仍是权威：写统一边距/间距时清掉排版设计的分边/分轴键
        if key == "marginMm":
            for k in ("marginTopMm", "marginBottomMm",
                      "marginLeftMm", "marginRightMm"):
                self._imposition.pop(k, None)
        elif key == "gapMm":
            self._imposition.pop("gapXMm", None)
            self._imposition.pop("gapYMm", None)
        persist_imposition(self._active_bucket, self._imposition)
        self._sheet_page = 0
        self._refresh_print_studio()

    def _sync_imposition_panel(self) -> None:
        """Reflect self._imposition into the 拼版 panel controls (no signals)."""
        self._imposition_syncing = True
        try:
            imp = self._imposition
            self._imp_margin.setValue(float(imp.get("marginMm", 8.0)))
            self._imp_gap.setValue(float(imp.get("gapMm", 2.0)))
            self._imp_cols.setValue(int(imp.get("forceCols", 0)))
            self._imp_rows.setValue(int(imp.get("forceRows", 0)))
            self._imp_cutmarks.setChecked(bool(imp.get("cutMarks")))
        finally:
            self._imposition_syncing = False

    def _on_imposition_replaced(self, imposition: dict) -> None:
        """Adopt a whole new imposition dict (排版设计 dialog live/accept)."""
        self._imposition = sanitize_imposition(imposition or {})
        self._impositions[self._active_bucket] = self._imposition
        self._sheet_page = 0
        self._sync_imposition_panel()
        self._refresh_print_studio()

    def _open_imposition_designer(self) -> None:
        """打开排版设计对话框：实时预览＋拖拽参考线；取消则回滚。"""
        from app.widgets.label_imposition_dialog import LabelImpositionDialog

        job = self._build_job(self._active_bucket)
        snapshot = dict(self._imposition)
        dlg = LabelImpositionDialog(
            job, dict(self._imposition), self,
            demo_data=specimen_to_label_data(_DEMO_SPECIMEN))
        dlg.imposition_changed.connect(self._on_imposition_replaced)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._on_imposition_replaced(dlg.imposition())
            persist_imposition(self._active_bucket, self._imposition)
        else:
            self._on_imposition_replaced(snapshot)

    def _change_sheet_page(self, delta: int) -> None:
        self._sheet_page = max(0, self._sheet_page + delta)
        self._refresh_print_studio()

    def _on_paper_changed(self, index: int) -> None:
        key = self._paper_combo.itemData(index)
        if not key:
            return
        self._step3._on_paper(self._active_bucket, key)
        # 空白手写标签 / 拼版控制仅对 A4/A5 排版有意义（小标签纸一张一页）。
        self._blank_row.setVisible(key in ("a4", "a5"))
        self._imposition_box.setVisible(key in ("a4", "a5"))

    def _on_copies_changed(self, value: int) -> None:
        self._step3._copies.setValue(value)

    def _edit_current_template(self) -> None:
        key = self._libs[self._active_bucket].selected_key() or DEFAULT_TEMPLATE_KEY[self._active_bucket]
        if key.startswith("custom:"):
            self._step2._manage_custom(self._active_bucket, key.split(":", 1)[1])
        else:
            self._step2._edit_builtin(self._active_bucket, key)

    def _manage_current_templates(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("标签模板库")
        dlg.resize(860, 680)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        scroll = QScrollArea(dlg)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        library = LabelStep2Templates(self._libs, scroll)
        library.set_data(self._specimens, self._step1.selected_indices())
        library.config_changed.connect(self._on_config_changed)
        scroll.setWidget(library)
        layout.addWidget(scroll, stretch=1)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("GhostBtn")
        close_btn.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)

        dlg.exec()
        self._on_config_changed()

    # ── Specimen loading ───────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._refresh_project_combo()
        self._load_specimens()
        pending_uid = getattr(self.ctx, "pending_label_uid", None)
        if isinstance(pending_uid, str) and pending_uid:
            self.select_uid(pending_uid)
            try:
                self.ctx.pending_label_uid = None
            except Exception:
                pass

    def _load_specimens(self) -> None:
        specimens = load_specimen_dicts(self.ctx.get_db())
        self._specimens = specimens
        self._label_edits = {}  # reset per-specimen overrides on project reload
        self._hidden_fields = set()  # reset field-visibility toggles on reload
        proj = getattr(self.ctx, "current_project_dir", None)
        self._step1.set_project_name(Path(str(proj)).name if proj else "—")
        self._project_hint.setText(f"{len(specimens)} 个标本" if proj else "未选择项目")
        self._step1.set_specimens(specimens)  # emits selection_changed → pushes data

    def _refresh_project_combo(self) -> None:
        try:
            from app.views.overview_view import _load_projects
            self._projects = _load_projects()
        except Exception:
            self._projects = []
        cur_dir = getattr(self.ctx, "current_project_dir", None) or ""
        blocked = self._project_combo.blockSignals(True)
        self._project_combo.clear()
        self._project_combo.addItem("选择项目…", "")
        current_idx = 0
        for proj in self._projects:
            directory = proj.get("directory") or proj.get("dir") or ""
            name = proj.get("name") or Path(directory).name or "未命名项目"
            code = proj.get("projectCode") or ""
            label = f"{code} · {name}" if code else name
            self._project_combo.addItem(label, directory)
            if directory and directory == cur_dir:
                current_idx = self._project_combo.count() - 1
        if current_idx == 0 and cur_dir:
            self._project_combo.addItem(Path(cur_dir).name, cur_dir)
            current_idx = self._project_combo.count() - 1
        self._project_combo.setCurrentIndex(current_idx)
        self._project_combo.blockSignals(blocked)

    def _on_project_combo_changed(self, index: int) -> None:
        directory = self._project_combo.itemData(index)
        if not directory:
            return
        try:
            self.ctx.current_project_dir = str(directory)
        except Exception:
            setattr(self.ctx, "current_project_dir", str(directory))
        main_win = self.window()
        if hasattr(main_win, "refresh_context_bar"):
            main_win.refresh_context_bar()
        self._load_specimens()

    def select_uid(self, uid: str) -> bool:
        """Select exactly one specimen by uniqueId (workspace jump)."""
        return self._step1.select_only_uid(uid)

    # ── Signal handlers (mirror web render() re-render) ─────────────────────

    def _on_selection_changed(self) -> None:
        indices = self._step1.selected_indices()
        self._step2.set_data(self._specimens, indices)
        self._step3.set_data(self._specimens, indices)
        self._refresh_output()
        self._refresh_print_studio()
        if hasattr(self, "_edit_toggle") and self._edit_toggle.isChecked():
            self._rebuild_edit_form()

    def _on_config_changed(self) -> None:
        # Template (Step2) or paper/size (Step3) changed: re-render Step2 card
        # previews at the current dims and refresh the output counts.
        indices = self._step1.selected_indices()
        self._step2.set_data(self._specimens, indices)
        # Template may have changed → field set differs; rebuild the toggles.
        if getattr(self, "_fields_box", None) is not None and self._fields_box.isVisible():
            self._rebuild_field_toggles()
        self._refresh_output()
        self._refresh_print_studio()

    # ── Output counts ────────────────────────────────────────────────────────

    def _refresh_output(self) -> None:
        sample_job = self._build_job("sample")
        tissue_job = self._build_job("tissue")
        sample_n = len(sample_job.get("items") or [])
        tissue_n = len(tissue_job.get("items") or [])
        self._step4.set_counts(sample_n, tissue_n, self._step3.copies())
        warnings = [
            f"{name}: {w.get('message', '')}"
            for name, job in (("样品瓶", sample_job), ("RNAlater 组织管", tissue_job))
            for w in (job.get("warnings") or [])
            if w.get("code") != "empty"
        ]
        self._step4.set_warnings(warnings)

    # ── Print-job construction (behavior unchanged from web oracle) ──────────

    # cutMarks 是打印旗标不参与网格数学，其余键全部透传给 calculate_grid
    _GRID_OPT_KEYS = (
        "marginMm", "gapMm", "forceCols", "forceRows",
        "marginTopMm", "marginBottomMm", "marginLeftMm", "marginRightMm",
        "gapXMm", "gapYMm", "shrinkToFit", "orientation", "startSlot",
    )

    def _grid_opts(self, bucket: Optional[str] = None) -> dict:
        """calculate_grid opts from the imposition settings (auto = {}).

        Default = active bucket; pass *bucket* for 一键双打 so each job carries
        its own 排版设计 parameters.
        """
        if bucket is None or bucket == self._active_bucket:
            imp = self._imposition
        else:
            imp = self._impositions.get(bucket) or {}
        out = {}
        for k in self._GRID_OPT_KEYS:
            v = imp.get(k)
            if v is not None:
                out[k] = v
        return out

    def _grid_for(self, bucket: str) -> dict:
        """Imposition grid (cols/rows/perPage) for *bucket* on its A4/A5 paper."""
        dims = self._step3.dims(bucket)
        paper_type = self._step3.paper_type(bucket)
        opts = self._grid_opts(bucket)
        page_w, page_h = effective_page_mm(
            PAPER_SIZES.get(paper_type, PAPER_SIZES["a4"]),
            paper_type if paper_type in ("a4", "a5") else "a4", opts)
        return calculate_grid(float(dims["w"]), float(dims["h"]),
                              page_w, page_h, opts=opts)

    def _build_job(self, bucket: str) -> dict:
        indices = self._step1.selected_indices()
        tmpl = resolve_template(self._libs[bucket])
        # batch-wide field-level print on/off (整批统一) — applies to preview & print
        tmpl = apply_field_visibility(
            tmpl, self._hidden_fields, self._blank_style, self._FIELD_NAMES)
        dims = self._step3.dims(bucket)
        paper_type = self._step3.paper_type(bucket)
        paper = PAPER_SIZES.get(paper_type) if paper_type in ("a4", "a5") else None
        job = LabelService.build_print_job(
            self._specimens, tmpl, bucket,
            selected_indices=indices, dims=dims, copies=self._step3.copies(),
            paper_type=paper_type, paper=paper,
            edits=self._label_edits or None,
            # no specimen selected → print N blank labels of the bare template
            # ("编号" is supported, not required)
            fill_blank=True,
        )
        items = job.get("items") or []
        if (
            bucket == "sample"
            and paper_type in ("a4", "a5")
            and not indices
            and len(items) == 1
            and not (items[0].get("data") if isinstance(items[0], dict) else items[0])
        ):
            # Preview-only: show demo content in the sheet layout when the job is
            # a standalone blank label. The printer still receives data == {}.
            job["_previewDemoWhenBlank"] = True
        # sheet-level 空白手写标签：A4/A5 排版时在末尾追加 N 张空白标签（手写）。
        # 预览与打印同读 job["items"]，故只需注入一次。
        if paper_type in ("a4", "a5") and self._blank_cells > 0:
            blanks = [{"idx": -1, "data": {}} for _ in range(self._blank_cells)]
            job["items"] = list(job.get("items") or []) + blanks
            job["labels"] = list(job.get("labels") or []) + [{} for _ in range(self._blank_cells)]
        # 排版设计参数随 job 走（一键双打时各 bucket 各自的拼版、纸向生效）
        if paper_type in ("a4", "a5"):
            job["gridOpts"] = self._grid_opts(bucket)
        return job

    # ── Printing (delegates to the shared label_print adapter) ──────────────

    def _print(self, bucket: str) -> None:
        """Print a single bucket via QPrintDialog → shared paint adapter."""
        job = self._build_job(bucket)
        if not (job.get("items") or []):
            QMessageBox.information(
                self, "打印",
                "本桶没有可打印标签。\n"
                + ("（RNAlater 组织管标签仅对 R 前缀标本生成）" if bucket == "tissue" else "")
            )
            return
        self._run_print_dialog([job])

    def _print_both(self) -> None:
        """一键同时打印「样品瓶 + RNAlater 组织管」于单个打印对话框。"""
        jobs = [j for j in (self._build_job("sample"), self._build_job("tissue"))
                if (j.get("items") or [])]
        if not jobs:
            QMessageBox.information(self, "打印", "没有可打印标签。")
            return
        self._run_print_dialog(jobs)

    def _run_print_dialog(self, jobs: list[dict]) -> None:
        """Open ONE QPrintDialog and paint all *jobs* onto the chosen printer.

        Paper is configured from the first job; subsequent jobs are appended
        after a page break (see :func:`label_print.paint_jobs`).
        """
        printer = build_printer(
            jobs[0], jobs[0].get("gridOpts") or self._grid_opts())
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name
        printer.setOutputFileName(tmp_path)

        dialog = QPrintDialog(printer, self)
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintToFile, True)
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return

        paint_jobs(
            printer, jobs,
            grid_opts=self._grid_opts(),
            cut_marks=bool(self._imposition.get("cutMarks")),
            draw_crop_marks=self._draw_crop_marks,
        )
