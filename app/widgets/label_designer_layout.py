"""Layout construction for the label designer dialog."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.widgets import label_designer_support as _ld
from app.widgets.label_designer_canvas import _DesignCanvas
from app.widgets.label_designer_panels import _FloatingToolbar, LayersPanel
from app.widgets.label_designer_properties import _PropertyPanel


class LabelDesignerLayoutMixin:
    # ── Format painter (Phase 6) ──────────────────────────────────────────────
    def _on_format_painter_toggled(self, on: bool) -> None:
        if on:
            kind, _r, field = self._sel
            self._fmt_pending = self._capture_style(field) \
                if kind == "element" and field >= 0 else {}
        else:
            self._fmt_pending = None

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Toolbar
        bar = QHBoxLayout(); bar.setSpacing(6)
        add_field = _ld._make_designer_button("+加字段 ▾")
        fmenu = QMenu(add_field)
        for key, name in _ld.FIELD_LABELS.items():
            fmenu.addAction(name, lambda _=False, k=key: self._add_row_with_field(k))
        add_field.setMenu(fmenu)
        add_row = _ld._make_designer_button("+加行")
        add_row.clicked.connect(lambda: self._add_row_with_field("headerId"))
        qr_btn = _ld._make_designer_button("QR 设置")
        qr_btn.clicked.connect(lambda: self._select("qr", -1, -1))

        add_el = _ld._make_designer_button("+元素 ▾")
        emenu = QMenu(add_el)
        for etype, name in _ld.ELEMENT_TYPE_LABELS.items():
            if etype == "shape":
                smenu = emenu.addMenu(name)
                for pname, pts in _ld.SHAPE_PRESETS.items():
                    smenu.addAction(
                        pname,
                        lambda _=False, p=pts: self._add_element("shape", points=p))
            else:
                emenu.addAction(
                    name, lambda _=False, t=etype: self._add_element(t))
        add_el.setMenu(emenu)

        presets_btn = _ld._make_designer_button("模板库 ▾")
        pmenu = QMenu(presets_btn)
        from app.services.label_presets import STARTER_PRESETS
        for pid, preset in STARTER_PRESETS.items():
            pmenu.addAction(preset.get("name") or pid,
                            lambda _=False, p=preset: self._apply_preset(p))
        presets_btn.setMenu(pmenu)

        # Less-used canvas controls live in one menu so the primary toolbar
        # remains usable on 1366px-wide screens.
        view_btn = _ld._make_designer_button("视图 ▾")
        view_menu = QMenu(view_btn)
        self._guide_action = view_menu.addAction("显示安全区与辅助线")
        self._guide_action.setCheckable(True)
        self._guide_action.toggled.connect(self._toggle_guide_overlay)
        view_menu.addAction("清除参考线", lambda: self._canvas.clear_user_guides())
        view_menu.addSeparator()
        view_menu.addAction("放大", lambda: self._canvas.zoom_by(1.2))
        view_menu.addAction("缩小", lambda: self._canvas.zoom_by(1 / 1.2))
        view_menu.addAction("适应窗口", lambda: self._canvas.reset_zoom())
        view_btn.setMenu(view_menu)

        self._fmt_btn = _ld._make_designer_button("格式刷", True)
        self._fmt_btn.setToolTip("先选好样板元素点此，再点目标元素套用其样式")
        self._fmt_btn.toggled.connect(self._on_format_painter_toggled)

        self._undo_btn = _ld._make_designer_button("↶ 撤销")
        self._redo_btn = _ld._make_designer_button("↷ 重做")
        self._undo_btn.clicked.connect(self._undo_template_edit)
        self._redo_btn.clicked.connect(self._redo_template_edit)
        saveas = _ld._make_designer_button("另存为 ▾")
        smenu = QMenu(saveas)
        smenu.addAction("另存为新模板…", self._save_as_new)
        if self._lib is not None:
            smenu.addAction("重命名当前自定义…", self._rename_current)
            smenu.addAction("复制当前自定义", self._duplicate_current)
            smenu.addAction("删除当前自定义", self._delete_current)
        saveas.setMenu(smenu)
        for w in (add_field, add_row, add_el, qr_btn, presets_btn, view_btn):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self._undo_btn)
        bar.addWidget(self._redo_btn)
        bar.addWidget(saveas)
        root.addLayout(bar)

        # Align / distribute toolbar (operates on the group selection, else the
        # single element relative to the whole label). Mirrors pro design tools.
        abar = QHBoxLayout(); abar.setSpacing(4)
        abar.addWidget(QLabel("对齐"))
        for mode, txt, tip in (
            ("left", "⇤", "左对齐"), ("hcenter", "⇆", "水平居中"), ("right", "⇥", "右对齐"),
            ("top", "⤒", "顶对齐"), ("vcenter", "⇕", "垂直居中"), ("bottom", "⤓", "底对齐"),
        ):
            b = _ld._make_designer_button(txt); b.setToolTip(tip); b.setFixedWidth(34)
            b.clicked.connect(lambda _=False, m=mode: self._align_elements(m))
            abar.addWidget(b)
        abar.addSpacing(10)
        abar.addWidget(QLabel("分布"))
        for axis, txt, tip in (("h", "↔", "水平等距分布"), ("v", "↕", "垂直等距分布")):
            b = _ld._make_designer_button(txt); b.setToolTip(tip); b.setFixedWidth(34)
            b.clicked.connect(lambda _=False, a=axis: self._distribute_elements(a))
            abar.addWidget(b)
        abar.addSpacing(10)
        copy_b = _ld._make_designer_button("复制"); copy_b.setToolTip("复制所选元素 (Ctrl+C)")
        copy_b.clicked.connect(self._copy_selection)
        paste_b = _ld._make_designer_button("粘贴"); paste_b.setToolTip("粘贴元素 (Ctrl+V)")
        paste_b.clicked.connect(self._paste_clipboard)
        self._delete_btn = _ld._make_designer_button("删除所选")
        self._delete_btn.setToolTip("删除当前选中的字段或元素 (Del)")
        self._delete_btn.clicked.connect(self._delete_selection)
        for w in (self._fmt_btn, copy_b, paste_b, self._delete_btn):
            abar.addWidget(w)
        abar.addSpacing(10)
        grp_b = _ld._make_designer_button("组合"); grp_b.setToolTip("把多选元素组合 (Ctrl+G)")
        grp_b.clicked.connect(self._group_selection)
        ungrp_b = _ld._make_designer_button("取消组合"); ungrp_b.setToolTip("解散所选组 (Ctrl+Shift+G)")
        ungrp_b.clicked.connect(self._ungroup_selection)
        abar.addWidget(grp_b); abar.addWidget(ungrp_b)
        abar.addStretch()
        root.addLayout(abar)

        from PyQt6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence.StandardKey.Copy, self, activated=self._copy_selection)
        QShortcut(QKeySequence.StandardKey.Paste, self, activated=self._paste_clipboard)
        QShortcut(QKeySequence("Ctrl+D"), self,
                  activated=lambda: (self._copy_selection(), self._paste_clipboard()))
        QShortcut(QKeySequence("Ctrl+G"), self, activated=self._group_selection)
        QShortcut(QKeySequence("Ctrl+Shift+G"), self,
                  activated=self._ungroup_selection)

        # Canvas | property panel
        self._canvas = _DesignCanvas()
        self._panel = _PropertyPanel()
        self._canvas.selected.connect(self._select)
        self._canvas.drag_started.connect(self._on_drag_start)
        self._canvas.dragged.connect(self._on_dragged)
        self._canvas.nudged.connect(self._on_nudged)
        self._canvas.element_resized.connect(self._on_element_resized)
        self._canvas.element_rotated.connect(self._on_element_rotated)
        self._canvas.interaction_finished.connect(self._finish_interaction)
        self._canvas.multi_toggle.connect(self._toggle_multi)
        self._canvas.marquee.connect(self._marquee_select)
        self._canvas.delete_pressed.connect(self._delete_selection)
        self._canvas.edit_requested.connect(self._begin_inline_edit)
        self._panel.edit.connect(self._apply_edit)

        # Floating quick-format toolbar (over the canvas, for text/field elements)
        self._float_bar = _FloatingToolbar(self._canvas)
        self._float_bar.size_delta.connect(self._float_size_delta)
        self._float_bar.bold_toggled.connect(lambda: self._float_style("bold"))
        self._float_bar.italic_toggled.connect(lambda: self._float_style("italic"))
        self._float_bar.align_set.connect(self._float_align)
        self._float_bar.color_pick.connect(self._float_color)
        self._float_bar.z_delta.connect(self._float_z)

        # right column = property panel (top) + layers panel (bottom)
        self._layers = LayersPanel()
        self._layers.layer_selected.connect(
            lambda i: self._select("element", -1, i))
        self._layers.layer_action.connect(self._apply_edit)
        self._layers.layer_reordered.connect(self._reorder_element)
        self._layers.itemDoubleClicked.connect(
            lambda _it: self._layers.toggle_hidden_at_row(self._layers.currentRow()))

        right = QSplitter(Qt.Orientation.Vertical)
        right.setMinimumWidth(270)
        property_scroll = QScrollArea()
        property_scroll.setWidgetResizable(True)
        property_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        property_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        property_scroll.setWidget(self._panel)
        right.addWidget(property_scroll)
        layers_box = QWidget()
        lb = QVBoxLayout(layers_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(3)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("图层"))
        lrow.addStretch()
        eye = QPushButton("👁 隐/显"); eye.setObjectName("Outline")
        eye.clicked.connect(
            lambda: self._layers.toggle_hidden_at_row(self._layers.currentRow()))
        lock = QPushButton("🔒 锁/解"); lock.setObjectName("Outline")
        lock.clicked.connect(
            lambda: self._layers.toggle_locked_at_row(self._layers.currentRow()))
        lrow.addWidget(eye); lrow.addWidget(lock)
        lb.addLayout(lrow)
        lb.addWidget(self._layers)
        right.addWidget(layers_box)
        right.setStretchFactor(0, 7)
        right.setStretchFactor(1, 3)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self._canvas)
        split.addWidget(right)
        split.setStretchFactor(0, 7)
        split.setStretchFactor(1, 3)
        split.setSizes([650, 290])
        self._main_splitter = split
        self._property_scroll = property_scroll
        root.addWidget(split, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._sel = ("none", -1, -1)
