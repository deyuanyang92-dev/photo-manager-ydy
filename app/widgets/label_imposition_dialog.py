"""label_imposition_dialog.py — 排版设计 dialog for the label print page.

A live full-page canvas (left) + parameter panel (right) over the imposition
dict the print page already feeds into ``calculate_grid``/``plan_label_pages``
— same math as the printer, so the preview is geometry-exact.

Direct manipulation on the canvas:
  - drag the 4 dashed margin guides (switches margins to per-side mode),
  - drag the first column/row gap guide to change 横/纵间距,
  - click any cell to set 起始格 (残张续打; clicking the first cell clears it).

Every change emits ``imposition_changed(dict)`` live so the host view can
keep its inline thumbnail and 拼版 panel in sync; Cancel is handled by the
host restoring its pre-dialog snapshot.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.label_service import sanitize_imposition
from app.utils.label_core import slot_origin_mm
from app.utils.label_sheet import compute_sheet_geometry, paint_sheet_page

_MARGIN_KEYS = ("marginTopMm", "marginBottomMm", "marginLeftMm", "marginRightMm")
_MARGIN_LABELS = {"marginTopMm": "上", "marginBottomMm": "下",
                  "marginLeftMm": "左", "marginRightMm": "右"}
_HIT_PX = 5.0          # guide grab tolerance in px
_MARGIN_DEFAULT = 8.0
_GAP_DEFAULT = 2.0


def _snap(mm: float) -> float:
    """Snap to 0.5 mm steps."""
    return round(mm * 2) / 2


class _SheetDesignCanvas(QWidget):
    """Interactive sheet preview: shared painter + draggable guides."""

    guide_dragged = pyqtSignal(str, float)   # imposition key, new mm (snapped)
    cell_clicked = pyqtSignal(int)           # slot index (起始格)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._job: dict = {}
        self._opts: dict = {}
        self._page = 0
        self._demo: Optional[dict] = None
        self._drag_key: Optional[str] = None
        self._geom: Optional[dict] = None
        self.setMouseTracking(True)
        self.setMinimumSize(420, 520)

    # ── state ────────────────────────────────────────────────────────────────
    def set_state(self, job: dict, imposition: dict, page: int,
                  demo_data: Optional[dict] = None) -> None:
        self._job = job or {}
        self._opts = dict(imposition or {})
        self._page = int(page)
        if demo_data is not None:
            self._demo = demo_data
        self._geom = None
        self.update()

    def geometry_info(self) -> dict:
        """Sheet geometry for the current size/options (cached per state)."""
        if self._geom is None:
            dims = self._job.get("dims") or {"w": 50, "h": 30}
            self._geom = compute_sheet_geometry(
                dims, self._job.get("paperType") or "a4",
                self._job.get("paper"), self._opts,
                max(60, self.width()), max(60, self.height()))
        return self._geom

    def resizeEvent(self, e) -> None:  # noqa: N802 (Qt override)
        self._geom = None
        super().resizeEvent(e)

    # ── painting ─────────────────────────────────────────────────────────────
    def paintEvent(self, e) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#eef2f5"))
        geom = self.geometry_info()
        painter.fillRect(int(geom["page_x"]), int(geom["page_y"]),
                         int(geom["page_w_px"]), int(geom["page_h_px"]),
                         QColor("#ffffff"))
        paint_sheet_page(
            painter, self._job, self._opts, self._page, geom,
            cut_marks=bool(self._opts.get("cutMarks")),
            demo_data=self._demo,
        )
        self._draw_guides(painter, geom)
        painter.end()

    def _guide_positions(self, geom: dict) -> dict:
        """Pixel position of each draggable guide line (key → px)."""
        g = geom["grid"]
        ppm = geom["px_per_mm"]
        px, py = geom["page_x"], geom["page_y"]
        pw, ph = geom["page_w_px"], geom["page_h_px"]
        pos = {
            "marginLeftMm": px + g["marginLeft"] * ppm,
            "marginRightMm": px + pw - g["marginRight"] * ppm,
            "marginTopMm": py + g["marginTop"] * ppm,
            "marginBottomMm": py + ph - g["marginBottom"] * ppm,
        }
        # gap guide = left/top edge of the SECOND column/row
        if g["cols"] >= 2:
            pos["gapXMm"] = px + slot_origin_mm(g, 1)[0] * ppm
        if g["rows"] >= 2:
            pos["gapYMm"] = py + slot_origin_mm(g, g["cols"])[1] * ppm
        return pos

    def _draw_guides(self, painter: QPainter, geom: dict) -> None:
        pos = self._guide_positions(geom)
        px, py = geom["page_x"], geom["page_y"]
        pw, ph = geom["page_w_px"], geom["page_h_px"]
        y0, y1 = int(py - 6), int(py + ph + 6)
        x0, x1 = int(px - 6), int(px + pw + 6)
        margin_pen = QPen(QColor("#0284c7"), 1, Qt.PenStyle.DashLine)
        gap_pen = QPen(QColor("#d97706"), 1, Qt.PenStyle.DashLine)
        painter.setPen(margin_pen)
        for key in ("marginLeftMm", "marginRightMm"):
            x = int(pos[key])
            painter.drawLine(x, y0, x, y1)
        for key in ("marginTopMm", "marginBottomMm"):
            y = int(pos[key])
            painter.drawLine(x0, y, x1, y)
        painter.setPen(gap_pen)
        if "gapXMm" in pos:
            painter.drawLine(int(pos["gapXMm"]), y0, int(pos["gapXMm"]), y1)
        if "gapYMm" in pos:
            painter.drawLine(x0, int(pos["gapYMm"]), x1, int(pos["gapYMm"]))

    # ── hit-testing / px→mm ──────────────────────────────────────────────────
    def _hit_test(self, x: float, y: float) -> Optional[str]:
        """Which guide (if any) is under (x, y)? Vertical guides need y within
        the page extent (± slack), horizontal guides need x within it."""
        geom = self.geometry_info()
        pos = self._guide_positions(geom)
        px, py = geom["page_x"], geom["page_y"]
        pw, ph = geom["page_w_px"], geom["page_h_px"]
        in_v = (py - 12) <= y <= (py + ph + 12)
        in_h = (px - 12) <= x <= (px + pw + 12)
        for key in ("marginLeftMm", "marginRightMm", "gapXMm"):
            if key in pos and in_v and abs(x - pos[key]) <= _HIT_PX:
                return key
        for key in ("marginTopMm", "marginBottomMm", "gapYMm"):
            if key in pos and in_h and abs(y - pos[key]) <= _HIT_PX:
                return key
        return None

    def _mm_for(self, key: str, x: float, y: float) -> float:
        """Convert a drag position to the guide's mm value (snapped+clamped)."""
        geom = self.geometry_info()
        g = geom["grid"]
        ppm = geom["px_per_mm"]
        px, py = geom["page_x"], geom["page_y"]
        pw, ph = geom["page_w_px"], geom["page_h_px"]
        sc = g["scale"]
        if key == "marginLeftMm":
            mm = (x - px) / ppm
        elif key == "marginRightMm":
            mm = (px + pw - x) / ppm
        elif key == "marginTopMm":
            mm = (y - py) / ppm
        elif key == "marginBottomMm":
            mm = (py + ph - y) / ppm
        elif key == "gapXMm":
            # dragged line = left edge of column 1
            mm = ((x - px) / ppm - g["marginLeft"]) / max(sc, 1e-6) - g["labelW"]
        elif key == "gapYMm":
            mm = ((y - py) / ppm - g["marginTop"]) / max(sc, 1e-6) - g["labelH"]
        else:
            return 0.0
        hi = 30.0 if key.startswith("gap") else 50.0
        return max(0.0, min(hi, _snap(mm)))

    def _slot_at(self, x: float, y: float) -> Optional[int]:
        geom = self.geometry_info()
        g = geom["grid"]
        ppm = geom["px_per_mm"]
        cell_w = g["labelW"] * g["scale"] * ppm
        cell_h = g["labelH"] * g["scale"] * ppm
        for slot in range(g["perPage"]):
            sx, sy = slot_origin_mm(g, slot)
            cx = geom["page_x"] + sx * ppm
            cy = geom["page_y"] + sy * ppm
            if cx <= x <= cx + cell_w and cy <= y <= cy + cell_h:
                return slot
        return None

    # ── mouse ────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e) -> None:  # noqa: N802 (Qt override)
        p = e.position()
        key = self._hit_test(p.x(), p.y())
        if key:
            self._drag_key = key
            e.accept()
            return
        slot = self._slot_at(p.x(), p.y())
        if slot is not None and self._page == 0:
            self.cell_clicked.emit(slot)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802 (Qt override)
        p = e.position()
        if self._drag_key:
            self.guide_dragged.emit(
                self._drag_key, self._mm_for(self._drag_key, p.x(), p.y()))
            e.accept()
            return
        key = self._hit_test(p.x(), p.y())
        if key in ("marginLeftMm", "marginRightMm", "gapXMm"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif key in ("marginTopMm", "marginBottomMm", "gapYMm"):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif self._slot_at(p.x(), p.y()) is not None and self._page == 0:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802 (Qt override)
        self._drag_key = None
        super().mouseReleaseEvent(e)


class LabelImpositionDialog(QDialog):
    """排版设计 — full-freedom imposition editor with live preview."""

    imposition_changed = pyqtSignal(dict)

    def __init__(self, job: dict, imposition: Optional[dict] = None,
                 parent: Optional[QWidget] = None,
                 demo_data: Optional[dict] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("排版设计")
        self.setModal(True)
        self._job = job or {}
        self._imp = sanitize_imposition(imposition or {})
        self._page = 0
        self._syncing = False
        self._demo = demo_data
        self._build_ui()
        self._sync_controls()
        self._recompute()
        self.resize(1040, 780)

    # ── public ───────────────────────────────────────────────────────────────
    def imposition(self) -> dict:
        return dict(self._imp)

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        self._canvas = _SheetDesignCanvas(self)
        self._canvas.guide_dragged.connect(self._on_guide_dragged)
        self._canvas.cell_clicked.connect(self._on_cell_clicked)
        root.addWidget(self._canvas, stretch=1)

        panel = QVBoxLayout()
        panel.setSpacing(8)

        def _spin(lo: float, hi: float, step: float = 0.5) -> QDoubleSpinBox:
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setSuffix(" mm")
            s.setDecimals(1)
            return s

        # 纸张方向
        panel.addWidget(QLabel("纸张方向"))
        rb_row = QHBoxLayout()
        self._rb_portrait = QRadioButton("纵向")
        self._rb_landscape = QRadioButton("横向")
        self._rb_portrait.setChecked(True)
        rb_row.addWidget(self._rb_portrait)
        rb_row.addWidget(self._rb_landscape)
        rb_row.addStretch()
        panel.addLayout(rb_row)
        self._rb_portrait.toggled.connect(self._on_control_changed)

        # 预设
        panel.addWidget(QLabel("预设"))
        preset_row = QHBoxLayout()
        for name, m, g in (("紧凑", 4.0, 0.0), ("标准", 8.0, 2.0),
                           ("宽松", 12.0, 4.0)):
            btn = QPushButton(name)
            btn.clicked.connect(
                lambda _=False, mm=m, gg=g: self._apply_preset(mm, gg))
            preset_row.addWidget(btn)
        panel.addLayout(preset_row)

        # 边距
        panel.addWidget(QLabel("页边距"))
        self._chk_unify = QCheckBox("四边统一")
        self._chk_unify.setChecked(True)
        panel.addWidget(self._chk_unify)
        self._chk_unify.toggled.connect(self._on_control_changed)
        m_grid = QGridLayout()
        m_grid.setSpacing(6)
        self._margin_spins: dict[str, QDoubleSpinBox] = {}
        for i, key in enumerate(_MARGIN_KEYS):
            spin = _spin(0.0, 50.0)
            spin.setValue(_MARGIN_DEFAULT)
            spin.valueChanged.connect(
                lambda _v, k=key: self._on_margin_spin(k))
            self._margin_spins[key] = spin
            m_grid.addWidget(QLabel(_MARGIN_LABELS[key]), i // 2, (i % 2) * 2)
            m_grid.addWidget(spin, i // 2, (i % 2) * 2 + 1)
        panel.addLayout(m_grid)

        # 间距
        panel.addWidget(QLabel("标签间距"))
        g_row = QGridLayout()
        g_row.setSpacing(6)
        self._gap_x = _spin(0.0, 30.0)
        self._gap_x.setValue(_GAP_DEFAULT)
        self._gap_y = _spin(0.0, 30.0)
        self._gap_y.setValue(_GAP_DEFAULT)
        self._gap_x.valueChanged.connect(self._on_control_changed)
        self._gap_y.valueChanged.connect(self._on_control_changed)
        g_row.addWidget(QLabel("横"), 0, 0)
        g_row.addWidget(self._gap_x, 0, 1)
        g_row.addWidget(QLabel("纵"), 0, 2)
        g_row.addWidget(self._gap_y, 0, 3)
        panel.addLayout(g_row)

        # 行列 + 缩小
        panel.addWidget(QLabel("行列（0 = 自动）"))
        rc_row = QGridLayout()
        rc_row.setSpacing(6)
        self._cols = QSpinBox()
        self._cols.setRange(0, 50)
        self._cols.setSpecialValueText("自动")
        self._rows = QSpinBox()
        self._rows.setRange(0, 50)
        self._rows.setSpecialValueText("自动")
        self._cols.valueChanged.connect(self._on_control_changed)
        self._rows.valueChanged.connect(self._on_control_changed)
        rc_row.addWidget(QLabel("列"), 0, 0)
        rc_row.addWidget(self._cols, 0, 1)
        rc_row.addWidget(QLabel("行"), 0, 2)
        rc_row.addWidget(self._rows, 0, 3)
        panel.addLayout(rc_row)
        self._chk_shrink = QCheckBox("放不下时缩小标签（更紧凑）")
        self._chk_shrink.toggled.connect(self._on_control_changed)
        panel.addWidget(self._chk_shrink)

        # 起始格
        panel.addWidget(QLabel("起始格（残张续打）"))
        self._start = QSpinBox()
        self._start.setRange(0, 9999)
        self._start.valueChanged.connect(self._on_control_changed)
        panel.addWidget(self._start)
        hint = QLabel("提示：也可直接在左侧预览中点击起始格")
        hint.setStyleSheet("color:#6b7280; font-size:11px;")
        panel.addWidget(hint)

        # 裁切标记
        self._chk_cuts = QCheckBox("裁切标记")
        self._chk_cuts.toggled.connect(self._on_control_changed)
        panel.addWidget(self._chk_cuts)

        # 摘要 + 翻页
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("color:#374151;")
        panel.addWidget(self._summary)
        nav = QHBoxLayout()
        self._btn_prev = QPushButton("◀ 上一页")
        self._btn_next = QPushButton("下一页 ▶")
        self._btn_prev.clicked.connect(lambda: self._change_page(-1))
        self._btn_next.clicked.connect(lambda: self._change_page(1))
        nav.addWidget(self._btn_prev)
        nav.addWidget(self._btn_next)
        panel.addLayout(nav)

        panel.addStretch()

        # 恢复默认 + 确定/取消
        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self._restore_defaults)
        panel.addWidget(btn_reset)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        panel.addWidget(box)

        side = QWidget()
        side.setLayout(panel)
        side.setFixedWidth(300)
        root.addWidget(side)

    # ── controls → dict ──────────────────────────────────────────────────────
    def _read_controls(self) -> dict:
        d: dict = {}
        if self._rb_landscape.isChecked():
            d["orientation"] = "landscape"
        if self._chk_unify.isChecked():
            m = self._margin_spins["marginTopMm"].value()
            if m != _MARGIN_DEFAULT:
                d["marginMm"] = m
        else:
            for key, spin in self._margin_spins.items():
                d[key] = spin.value()
        gx, gy = self._gap_x.value(), self._gap_y.value()
        if gx == gy:
            if gx != _GAP_DEFAULT:
                d["gapMm"] = gx
        else:
            d["gapXMm"] = gx
            d["gapYMm"] = gy
        if self._cols.value() > 0:
            d["forceCols"] = self._cols.value()
        if self._rows.value() > 0:
            d["forceRows"] = self._rows.value()
        if self._chk_shrink.isChecked():
            d["shrinkToFit"] = True
        if self._start.value() > 0:
            d["startSlot"] = self._start.value()
        if self._chk_cuts.isChecked():
            d["cutMarks"] = True
        return d

    def _on_control_changed(self, *_a) -> None:
        if self._syncing:
            return
        self._imp = sanitize_imposition(self._read_controls())
        self._recompute()
        self.imposition_changed.emit(dict(self._imp))

    def _on_margin_spin(self, key: str) -> None:
        if self._syncing:
            return
        if self._chk_unify.isChecked():
            # unified mode: mirror all four spins
            val = self._margin_spins[key].value()
            self._syncing = True
            for k, spin in self._margin_spins.items():
                if k != key:
                    spin.setValue(val)
            self._syncing = False
        self._on_control_changed()

    # ── dict → controls ──────────────────────────────────────────────────────
    def _sync_controls(self) -> None:
        self._syncing = True
        imp = self._imp
        self._rb_landscape.setChecked(imp.get("orientation") == "landscape")
        self._rb_portrait.setChecked(imp.get("orientation") != "landscape")
        uniform = imp.get("marginMm", _MARGIN_DEFAULT)
        per_side = any(k in imp for k in _MARGIN_KEYS)
        self._chk_unify.setChecked(not per_side)
        for key, spin in self._margin_spins.items():
            spin.setValue(float(imp.get(key, uniform)))
        gap = imp.get("gapMm", _GAP_DEFAULT)
        self._gap_x.setValue(float(imp.get("gapXMm", gap)))
        self._gap_y.setValue(float(imp.get("gapYMm", gap)))
        self._cols.setValue(int(imp.get("forceCols", 0)))
        self._rows.setValue(int(imp.get("forceRows", 0)))
        self._chk_shrink.setChecked(bool(imp.get("shrinkToFit")))
        self._start.setValue(int(imp.get("startSlot", 0)))
        self._chk_cuts.setChecked(bool(imp.get("cutMarks")))
        self._syncing = False

    # ── canvas interactions ──────────────────────────────────────────────────
    def _on_guide_dragged(self, key: str, mm: float) -> None:
        if key in _MARGIN_KEYS:
            if self._chk_unify.isChecked():
                self._syncing = True
                self._chk_unify.setChecked(False)
                self._syncing = False
            self._margin_spins[key].setValue(mm)
        elif key == "gapXMm":
            self._gap_x.setValue(mm)
        elif key == "gapYMm":
            self._gap_y.setValue(mm)

    def _on_cell_clicked(self, slot: int) -> None:
        self._start.setValue(int(slot))

    def _apply_preset(self, margin: float, gap: float) -> None:
        self._syncing = True
        self._chk_unify.setChecked(True)
        for spin in self._margin_spins.values():
            spin.setValue(margin)
        self._gap_x.setValue(gap)
        self._gap_y.setValue(gap)
        self._syncing = False
        self._on_control_changed()

    def _restore_defaults(self) -> None:
        self._imp = {}
        self._sync_controls()
        self._recompute()
        self.imposition_changed.emit({})

    def _change_page(self, delta: int) -> None:
        self._page = max(0, self._page + delta)
        self._recompute()

    # ── recompute / summary ──────────────────────────────────────────────────
    def _recompute(self) -> None:
        self._canvas.set_state(self._job, self._imp, self._page,
                               demo_data=self._demo)
        geom = self._canvas.geometry_info()
        g = geom["grid"]
        per_page = g["perPage"]
        # 起始格上限跟随每页格数
        self._start.blockSignals(True)
        self._start.setMaximum(max(0, per_page - 1))
        self._start.blockSignals(False)
        items = len(self._job.get("items") or [])
        start = int(self._imp.get("startSlot", 0)) % max(1, per_page)
        total = max(1, -(-(items + start) // per_page)) if items else 1
        self._page = max(0, min(self._page, total - 1))
        scale_note = (f" · 缩放 {g['scale']:.0%}" if g["scale"] < 1.0 else "")
        self._summary.setText(
            f"{g['cols']} 列 × {g['rows']} 行 · 每页 {per_page} 张"
            f"{scale_note} · 第 {self._page + 1}/{total} 页")
        self._btn_prev.setEnabled(self._page > 0)
        self._btn_next.setEnabled(self._page < total - 1)
