"""Contextual property editor for the label designer."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.widgets import label_designer_support as _ld

# ── Property panel ──────────────────────────────────────────────────────────────

class _PropertyPanel(QWidget):
    """Contextual property editor; emits a semantic ``edit(dict)`` per change."""

    edit = pyqtSignal(dict)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("background:#08161b; color:#eef3ef;")
        self.setMinimumWidth(240)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 12, 12, 12)
        self._root.setSpacing(8)
        self._tmpl: dict = {}
        self.show_for("none", -1, -1, {})

    def _clear_property_controls(self) -> None:
        while self._root.count():
            item = self._root.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

    def _title(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setStyleSheet("color:#eef3ef; font-size:13px; font-weight:bold;")
        self._root.addWidget(lbl)

    def _add_property_row(self, *widgets) -> None:
        row_layout = QHBoxLayout()
        row_layout.setSpacing(6)
        for widget in widgets:
            if isinstance(widget, str):
                lbl = QLabel(widget)
                lbl.setStyleSheet("color:#87a2a1; font-size:12px;")
                row_layout.addWidget(lbl)
            else:
                row_layout.addWidget(widget)
        row_layout.addStretch()
        wrap = QWidget()
        wrap.setLayout(row_layout)
        self._root.addWidget(wrap)

    def show_for(self, kind: str, row: int, field: int, tmpl: dict) -> None:
        self._tmpl = tmpl
        self._clear_property_controls()
        if kind == "field":
            self._build_field(row, field)
        elif kind == "qr":
            self._build_qr()
        elif kind == "element":
            self._build_element(field)
        else:
            self._build_label()
        self._root.addStretch(1)

    # ----- free-form element -----
    def _build_element(self, index: int) -> None:
        els = self._tmpl.get("elements") or []
        if not (0 <= index < len(els)):
            return self._build_label()
        el = els[index]
        et = el.get("type")
        names = {"text": "文字", "field": "绑定字段", "line": "直线", "rect": "矩形",
                 "ellipse": "椭圆", "shape": "多边形", "image": "图片",
                 "barcode": "条码"}
        self._title(f"元素 · {names.get(et, et)}")

        def make_element_property_spinbox(
            val, lo=-500.0, hi=500.0, step=0.5, suffix=" mm"
        ):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setSingleStep(step)
            s.setSuffix(suffix)
            s.setValue(float(val or 0))
            return s

        if et == "line":
            x1 = make_element_property_spinbox(el.get("x1"))
            y1 = make_element_property_spinbox(el.get("y1"))
            x2 = make_element_property_spinbox(el.get("x2"))
            y2 = make_element_property_spinbox(el.get("y2"))
            for s, k in ((x1, "x1"), (y1, "y1"), (x2, "x2"), (y2, "y2")):
                s.valueChanged.connect(lambda v, kk=k: self.edit.emit(
                    {"op": "element_line", "index": index, kk: v}))
            self._add_property_row("起点", x1, y1)
            self._add_property_row("终点", x2, y2)
            wd = make_element_property_spinbox(el.get("width"), 0.0, 20.0, 0.1)
            wd.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_line", "index": index, "width": v}))
            self._add_property_row("粗细", wd)
        else:
            xs = make_element_property_spinbox(el.get("x"))
            ys = make_element_property_spinbox(el.get("y"))
            ws = make_element_property_spinbox(el.get("w"), 0.5, 500.0)
            hs = make_element_property_spinbox(el.get("h"), 0.5, 500.0)
            xs.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_move", "index": index, "x": v, "y": el.get("y", 0)}))
            ys.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_move", "index": index, "x": el.get("x", 0), "y": v}))
            ws.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_resize", "index": index, "x": el.get("x", 0),
                 "y": el.get("y", 0), "w": v, "h": el.get("h", 0)}))
            hs.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_resize", "index": index, "x": el.get("x", 0),
                 "y": el.get("y", 0), "w": el.get("w", 0), "h": v}))
            self._add_property_row("位置", xs, ys)
            self._add_property_row("大小", ws, hs)

        # rotation (not meaningful for line)
        if et != "line":
            rot = QSpinBox(); rot.setRange(-180, 180); rot.setSuffix("°")
            rot.setValue(int(float(el.get("rotation") or 0)))
            rot.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_rotation", "index": index, "value": v}))
            self._add_property_row("旋转", rot)

        if et == "text":
            txt = QLineEdit(el.get("text") or "")
            txt.textChanged.connect(lambda t: self.edit.emit(
                {"op": "element_text", "index": index, "value": t}))
            self._add_property_row("内容", txt)
            self._text_style_rows(index, el)
        elif et == "field":
            combo = QComboBox()
            for key, name in _ld.FIELD_LABELS.items():
                combo.addItem(name, key)
            ci = combo.findData(el.get("key"))
            combo.setCurrentIndex(ci if ci >= 0 else 0)
            combo.currentIndexChanged.connect(lambda _i: self.edit.emit(
                {"op": "element_key", "index": index, "value": combo.currentData()}))
            self._add_property_row("字段", combo)
            self._text_style_rows(index, el)
        elif et in ("rect", "ellipse", "shape"):
            stroke = QPushButton(); stroke.setFixedSize(60, 22)
            self._update_color_btn(stroke, el.get("stroke") or "#000000")
            stroke.clicked.connect(lambda _=False, b=stroke: self._pick_element_color(
                index, "element_stroke", b))
            self._add_property_row("描边", stroke)
            sw = make_element_property_spinbox(el.get("strokeWidth"), 0.0, 20.0, 0.1)
            sw.valueChanged.connect(lambda v: self.edit.emit(
                {"op": "element_strokeWidth", "index": index, "value": v}))
            self._add_property_row("线宽", sw)
            fill = QPushButton(); fill.setFixedSize(60, 22)
            self._update_color_btn(fill, el.get("fill") or "#ffffff")
            fill.clicked.connect(lambda _=False, b=fill: self._pick_element_color(
                index, "element_fill", b))
            nofill = _ld._make_designer_button("无填充")
            nofill.clicked.connect(lambda: self.edit.emit(
                {"op": "element_fill", "index": index, "value": None}))
            self._add_property_row("填充", fill, nofill)
            if et == "rect":
                cr = make_element_property_spinbox(el.get("cornerRadius"), 0.0, 30.0, 0.5)
                cr.valueChanged.connect(lambda v: self.edit.emit(
                    {"op": "element_cornerRadius", "index": index, "value": v}))
                self._add_property_row("圆角", cr)
        elif et == "image":
            pick = _ld._make_designer_button("选择图片…")
            pick.clicked.connect(lambda: self._pick_element_image(index))
            self._add_property_row("图片", pick)
            ka = _ld._make_designer_button("保持比例", True)
            ka.setChecked(el.get("keepAspect") is not False)
            ka.toggled.connect(lambda on: self.edit.emit(
                {"op": "element_keepAspect", "index": index, "value": on}))
            self._add_property_row(ka)
        elif et == "barcode":
            content = QLineEdit(el.get("content") or "")
            content.setPlaceholderText("字段key或字面值，如 uniqueId")
            content.textChanged.connect(lambda t: self.edit.emit(
                {"op": "element_content", "index": index, "value": t}))
            self._add_property_row("内容", content)
            st = _ld._make_designer_button("显示文本", True); st.setChecked(el.get("showText") is not False)
            st.toggled.connect(lambda on: self.edit.emit(
                {"op": "element_showText", "index": index, "value": on}))
            self._add_property_row(st)

        # universal appearance (opacity / dash / font / wrap / arrows)
        self._appearance_rows(index, el)

        # z-order + delete/duplicate
        zup, zdn = _ld._make_designer_button("上移一层"), _ld._make_designer_button("下移一层")
        zup.clicked.connect(lambda: self.edit.emit({"op": "element_z", "index": index, "value": 1}))
        zdn.clicked.connect(lambda: self.edit.emit({"op": "element_z", "index": index, "value": -1}))
        self._add_property_row(zup, zdn)
        hid = _ld._make_designer_button("隐藏", True); hid.setChecked(bool(el.get("hidden")))
        hid.toggled.connect(lambda on: self.edit.emit(
            {"op": "element_hidden", "index": index, "value": on}))
        lck = _ld._make_designer_button("锁定", True); lck.setChecked(bool(el.get("locked")))
        lck.toggled.connect(lambda on: self.edit.emit(
            {"op": "element_locked", "index": index, "value": on}))
        self._add_property_row(hid, lck)
        dup, dele = _ld._make_designer_button("复制元素"), _ld._make_designer_button("删除元素")
        dup.clicked.connect(lambda: self.edit.emit({"op": "element_dup", "index": index}))
        dele.clicked.connect(lambda: self.edit.emit({"op": "element_del", "index": index}))
        self._add_property_row(dup, dele)

    _DASH_OPTIONS = (("实线", "solid"), ("虚线", "dash"), ("点线", "dot"),
                     ("点划线", "dashdot"))

    def _appearance_rows(self, index: int, el: dict) -> None:
        et = el.get("type")
        # opacity (all types)
        op = QSlider(Qt.Orientation.Horizontal)
        op.setRange(10, 100)
        op.setValue(int(round(float(el.get("opacity", 1.0) or 1.0) * 100)))
        op.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "element_opacity", "index": index, "value": v / 100.0}))
        self._add_property_row("不透明", op)
        # dash (stroked shapes)
        if et in ("line", "rect", "ellipse", "shape"):
            dash = QComboBox()
            for label, val in self._DASH_OPTIONS:
                dash.addItem(label, val)
            di = dash.findData(el.get("dash") or "solid")
            dash.setCurrentIndex(di if di >= 0 else 0)
            dash.currentIndexChanged.connect(lambda _i: self.edit.emit(
                {"op": "element_dash", "index": index, "value": dash.currentData()}))
            self._add_property_row("线型", dash)
        # arrowheads (line)
        if et == "line":
            a0 = _ld._make_designer_button("起点箭头", True); a0.setChecked(bool(el.get("arrowStart")))
            a1 = _ld._make_designer_button("终点箭头", True); a1.setChecked(bool(el.get("arrowEnd")))
            a0.toggled.connect(lambda on: self.edit.emit(
                {"op": "element_arrowStart", "index": index, "value": on}))
            a1.toggled.connect(lambda on: self.edit.emit(
                {"op": "element_arrowEnd", "index": index, "value": on}))
            self._add_property_row(a0, a1)
        # font family + wrap (text/field)
        if et in ("text", "field"):
            font = QComboBox()
            font.addItem("默认字体", "")
            try:
                from PyQt6.QtGui import QFontDatabase
                for fam in QFontDatabase.families():
                    font.addItem(fam, fam)
            except Exception:
                pass
            fi = font.findData(el.get("font") or "")
            font.setCurrentIndex(fi if fi >= 0 else 0)
            font.currentIndexChanged.connect(lambda _i: self.edit.emit(
                {"op": "element_font", "index": index, "value": font.currentData()}))
            self._add_property_row("字体", font)
            wrap = _ld._make_designer_button("自动换行", True); wrap.setChecked(bool(el.get("wrap")))
            wrap.toggled.connect(lambda on: self.edit.emit(
                {"op": "element_wrap", "index": index, "value": on}))
            self._add_property_row(wrap)
        # gradient + shadow (filled shapes)
        if et in ("rect", "ellipse", "shape"):
            grad_on = _ld._make_designer_button("渐变填充", True); grad_on.setChecked(bool(el.get("gradient")))
            grad_on.toggled.connect(
                lambda on, i=index: self._toggle_gradient(i, on))
            sh_on = _ld._make_designer_button("投影", True); sh_on.setChecked(bool(el.get("shadow")))
            sh_on.toggled.connect(lambda on, i=index: self._toggle_shadow(i, on))
            self._add_property_row(grad_on, sh_on)

    def _toggle_gradient(self, index: int, on: bool) -> None:
        if on:
            self.edit.emit({"op": "element_gradient", "index": index, "value": {
                "type": "linear", "angle": 0,
                "stops": [["#ffffff", 0.0], ["#000000", 1.0]]}})
        else:
            self.edit.emit({"op": "element_gradient", "index": index, "value": None})

    def _toggle_shadow(self, index: int, on: bool) -> None:
        if on:
            self.edit.emit({"op": "element_shadow", "index": index, "value": {
                "dx": 0.6, "dy": 0.6, "blur": 0, "color": "#888888"}})
        else:
            self.edit.emit({"op": "element_shadow", "index": index, "value": None})

    def _text_style_rows(self, index: int, el: dict) -> None:
        size = QSpinBox(); size.setRange(4, 60)
        size.setValue(int(el.get("size") or 9))
        size.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "element_size", "index": index, "value": v}))
        b = _ld._make_designer_button("B", True); b.setChecked("bold" in (el.get("style") or ""))
        i = _ld._make_designer_button("I", True); i.setChecked("italic" in (el.get("style") or ""))
        b.toggled.connect(lambda on: self.edit.emit({"op": "element_bold", "index": index, "value": on}))
        i.toggled.connect(lambda on: self.edit.emit({"op": "element_italic", "index": index, "value": on}))
        self._add_property_row("字号", size, b, i)
        al, ac, ar = _ld._make_designer_button("左", True), _ld._make_designer_button("中", True), _ld._make_designer_button("右", True)
        {"left": al, "center": ac, "right": ar}.get(el.get("align") or "left", al).setChecked(True)
        al.clicked.connect(lambda: self.edit.emit({"op": "element_align", "index": index, "value": "left"}))
        ac.clicked.connect(lambda: self.edit.emit({"op": "element_align", "index": index, "value": "center"}))
        ar.clicked.connect(lambda: self.edit.emit({"op": "element_align", "index": index, "value": "right"}))
        self._add_property_row("对齐", al, ac, ar)
        color_btn = QPushButton(); color_btn.setFixedSize(60, 22)
        self._update_color_btn(color_btn, el.get("color") or "#000000")
        color_btn.clicked.connect(lambda _=False, b=color_btn: self._pick_element_color(
            index, "element_color", b))
        self._add_property_row("颜色", color_btn)

    def _pick_element_color(self, index: int, op: str, btn: QPushButton) -> None:
        c = QColorDialog.getColor(QColor(btn.text() or "#000000"), btn.window())
        if c.isValid():
            self._update_color_btn(btn, c.name())
            self.edit.emit({"op": op, "index": index, "value": c.name()})

    def _pick_element_image(self, index: int) -> None:
        import base64
        from app.utils.ui import get_open_file_name
        path = get_open_file_name(self.window(), "选择图片",
                                  filter="图片 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            b64 = base64.b64encode(raw).decode("ascii")
        except Exception:
            return
        self.edit.emit({"op": "element_image", "index": index, "data": b64})

    # ----- field / row -----
    def _build_field(self, row_idx: int, field_idx: int) -> None:
        rows = self._tmpl.get("rows") or []
        if not (0 <= row_idx < len(rows)):
            return self._build_label()
        row = rows[row_idx]
        fields = row.get("fields") or []
        fld = fields[field_idx] if 0 <= field_idx < len(fields) else {"key": "", "size": None, "style": ""}

        self._title(f"行 {row_idx + 1} · 字段 {field_idx + 1}")

        combo = QComboBox()
        for key, name in _ld.FIELD_LABELS.items():
            combo.addItem(name, key)
        ci = combo.findData(fld.get("key"))
        combo.setCurrentIndex(ci if ci >= 0 else 0)
        combo.currentIndexChanged.connect(
            lambda _i: self.edit.emit({"op": "field_key", "row": row_idx, "field": field_idx,
                                       "value": combo.currentData()}))
        self._add_property_row("字段", combo)

        size = QSpinBox()
        size.setRange(4, 40)
        size.setValue(int(fld.get("size") or row.get("size") or 9))
        size.valueChanged.connect(
            lambda v: self.edit.emit({"op": "field_size", "row": row_idx, "field": field_idx, "value": v}))
        b = _ld._make_designer_button("B", True); b.setChecked("bold" in (fld.get("style") or ""))
        i = _ld._make_designer_button("I", True); i.setChecked("italic" in (fld.get("style") or ""))
        b.toggled.connect(lambda on: self.edit.emit({"op": "field_bold", "row": row_idx, "field": field_idx, "value": on}))
        i.toggled.connect(lambda on: self.edit.emit({"op": "field_italic", "row": row_idx, "field": field_idx, "value": on}))
        self._add_property_row("字号", size, b, i)

        al, ac, ar = _ld._make_designer_button("左", True), _ld._make_designer_button("中", True), _ld._make_designer_button("右", True)
        cur = row.get("align") or "left"
        {"left": al, "center": ac, "right": ar}[cur].setChecked(True)
        al.clicked.connect(lambda: self.edit.emit({"op": "row_align", "row": row_idx, "value": "left"}))
        ac.clicked.connect(lambda: self.edit.emit({"op": "row_align", "row": row_idx, "value": "center"}))
        ar.clicked.connect(lambda: self.edit.emit({"op": "row_align", "row": row_idx, "value": "right"}))
        self._add_property_row("对齐", al, ac, ar)

        wrap = _ld._make_designer_button("换行", True); wrap.setChecked(row.get("wrap") is not False)
        wrap.toggled.connect(lambda on: self.edit.emit({"op": "row_wrap", "row": row_idx, "value": on}))
        self._add_property_row(wrap)

        prefix = QLineEdit(row.get("prefix") or "")
        prefix.setPlaceholderText("前缀")
        prefix.textChanged.connect(lambda t: self.edit.emit({"op": "row_prefix", "row": row_idx, "value": t}))
        sep = QLineEdit(row.get("sep") if row.get("sep") is not None else " ")
        sep.setPlaceholderText("分隔")
        sep.textChanged.connect(lambda t: self.edit.emit({"op": "row_sep", "row": row_idx, "value": t}))
        self._add_property_row("前缀", prefix)
        self._add_property_row("分隔", sep)

        # per-row line-height override (0 / 继承 = inherit template + global)
        lh = QDoubleSpinBox(); lh.setRange(0.0, 3.0); lh.setSingleStep(0.1)
        lh.setSpecialValueText("继承")   # 0.0 shows as 继承
        lh.setValue(float(row.get("lineHeight") or 0.0))
        lh.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "row_lineHeight", "row": row_idx,
             "value": None if v <= 0.0 else v}))
        self._add_property_row("行高", lh)

        # nudge
        left, up, down, right = _ld._make_designer_button("←"), _ld._make_designer_button("↑"), _ld._make_designer_button("↓"), _ld._make_designer_button("→")
        reset = _ld._make_designer_button("归零")
        S = 0.5
        left.clicked.connect(lambda: self.edit.emit({"op": "field_nudge", "row": row_idx, "field": field_idx, "dx": -S, "dy": 0}))
        right.clicked.connect(lambda: self.edit.emit({"op": "field_nudge", "row": row_idx, "field": field_idx, "dx": S, "dy": 0}))
        up.clicked.connect(lambda: self.edit.emit({"op": "field_nudge", "row": row_idx, "field": field_idx, "dx": 0, "dy": -S}))
        down.clicked.connect(lambda: self.edit.emit({"op": "field_nudge", "row": row_idx, "field": field_idx, "dx": 0, "dy": S}))
        reset.clicked.connect(lambda: self.edit.emit({"op": "field_reset", "row": row_idx, "field": field_idx}))
        self._add_property_row("微移", left, up, down, right, reset)

        color_btn = QPushButton()
        color_btn.setFixedSize(60, 22)
        color_btn.setToolTip("字段文字颜色")
        self._update_color_btn(color_btn, fld.get("color") or "#000000")
        color_btn.clicked.connect(
            lambda _=False, btn=color_btn: self._pick_field_color(row_idx, field_idx, btn))
        self._add_property_row("字色", color_btn)

        addf = _ld._make_designer_button("+加字段")
        delf = _ld._make_designer_button("×删字段")
        addf.clicked.connect(lambda: self.edit.emit({"op": "field_add", "row": row_idx}))
        delf.clicked.connect(lambda: self.edit.emit({"op": "field_del", "row": row_idx, "field": field_idx}))
        self._add_property_row(addf, delf)

        dup, dele = _ld._make_designer_button("复制本行"), _ld._make_designer_button("删除本行")
        mvu, mvd = _ld._make_designer_button("上移↑"), _ld._make_designer_button("下移↓")
        dup.clicked.connect(lambda: self.edit.emit({"op": "row_dup", "row": row_idx}))
        dele.clicked.connect(lambda: self.edit.emit({"op": "row_del", "row": row_idx}))
        mvu.clicked.connect(lambda: self.edit.emit({"op": "row_move", "row": row_idx, "value": -1}))
        mvd.clicked.connect(lambda: self.edit.emit({"op": "row_move", "row": row_idx, "value": 1}))
        self._add_property_row(dup, dele)
        self._add_property_row(mvu, mvd)

    # ----- QR -----
    def _build_qr(self) -> None:
        qr = self._tmpl.get("qr") or {}
        self._title("二维码 QR")
        positions = [("left", "左"), ("right", "右"), ("top", "上"),
                     ("bottom", "下"), ("free", "自由"), ("none", "无")]
        cur = qr.get("position") or "right"
        h = QHBoxLayout(); h.setSpacing(4)
        for key, name in positions:
            b = _ld._make_designer_button(name, True); b.setChecked(key == cur)
            b.clicked.connect(lambda _=False, k=key: self.edit.emit({"op": "qr_position", "value": k}))
            h.addWidget(b)
        h.addStretch()
        ww = QWidget(); ww.setLayout(h); self._root.addWidget(ww)

        size = QSlider(Qt.Orientation.Horizontal)
        size.setRange(20, 70)
        size.setValue(int(round(float(qr.get("sizePct") or 0.4) * 100)))
        size.valueChanged.connect(lambda v: self.edit.emit({"op": "qr_size", "value": v / 100.0}))
        self._add_property_row("大小", size)

        content = QComboBox()
        for key, name in _ld.FIELD_LABELS.items():
            content.addItem(name, key)
        ci = content.findData(qr.get("content") or "uniqueId")
        content.setCurrentIndex(ci if ci >= 0 else 0)
        content.currentIndexChanged.connect(
            lambda _i: self.edit.emit({"op": "qr_content", "value": content.currentData()}))
        self._add_property_row("内容", content)

        cure = qr.get("ecc") or "Q"
        h2 = QHBoxLayout(); h2.setSpacing(4)
        lbl = QLabel("容错"); lbl.setStyleSheet("color:#87a2a1; font-size:12px;"); h2.addWidget(lbl)
        for lv in ("L", "M", "Q", "H"):
            b = _ld._make_designer_button(lv, True); b.setChecked(lv == cure)
            b.clicked.connect(lambda _=False, v=lv: self.edit.emit({"op": "qr_ecc", "value": v}))
            h2.addWidget(b)
        h2.addStretch()
        ww2 = QWidget(); ww2.setLayout(h2); self._root.addWidget(ww2)
        hint = QLabel("提示：选「自由」后可在画布上拖动 QR 到任意位置。")
        hint.setStyleSheet("color:#5f7d7a; font-size:11px;")
        hint.setWordWrap(True)
        self._root.addWidget(hint)

    # ----- label level -----
    def _build_label(self) -> None:
        self._title("标签")
        lh = QSlider(Qt.Orientation.Horizontal)
        lh.setRange(80, 250)
        lh.setValue(int(round(float(self._tmpl.get("lineHeight") or 1.3) * 100)))
        lh.valueChanged.connect(lambda v: self.edit.emit({"op": "line_height", "value": v / 100.0}))
        self._add_property_row("全局行高", lh)

        # Shape selector
        _SHAPE_KEYS = ["rect", "circle", "roundrect"]
        _SHAPE_NAMES = ["矩形", "圆形", "圆角矩形"]
        shape_combo = QComboBox()
        for n in _SHAPE_NAMES:
            shape_combo.addItem(n)
        cur_shape = (self._tmpl.get("shape") or "rect").lower()
        shape_combo.setCurrentIndex(_SHAPE_KEYS.index(cur_shape) if cur_shape in _SHAPE_KEYS else 0)
        shape_combo.currentIndexChanged.connect(
            lambda i: self.edit.emit({"op": "tmpl_shape", "value": _SHAPE_KEYS[i]}))
        self._add_property_row("形状", shape_combo)

        # Background color
        bg_btn = QPushButton()
        bg_btn.setFixedSize(60, 22)
        bg_btn.setToolTip("标签背景色")
        self._update_color_btn(bg_btn, self._tmpl.get("bgColor") or "#ffffff")
        bg_btn.clicked.connect(
            lambda _=False, btn=bg_btn: self._pick_tmpl_color("bgColor", btn))
        self._add_property_row("背景色", bg_btn)

        # Corner radius (visible for rect/roundrect)
        corner_spin = QDoubleSpinBox()
        corner_spin.setRange(0.0, 10.0)
        corner_spin.setSingleStep(0.5)
        corner_spin.setSuffix(" mm")
        corner_spin.setValue(float(self._tmpl.get("cornerRadius") or 0.0))
        corner_spin.valueChanged.connect(
            lambda v: self.edit.emit({"op": "tmpl_cornerRadius", "value": round(v, 2)}))
        self._add_property_row("圆角", corner_spin)

        # Label dimensions (mm) — designer-editable; persisted as a custom size
        dims = getattr(self, "_dims", None) or {"w": 60, "h": 40}
        w_spin = QDoubleSpinBox(); w_spin.setRange(5.0, 300.0)
        w_spin.setSingleStep(1.0); w_spin.setSuffix(" mm")
        w_spin.setValue(float(dims.get("w", 60)))
        h_spin = QDoubleSpinBox(); h_spin.setRange(5.0, 300.0)
        h_spin.setSingleStep(1.0); h_spin.setSuffix(" mm")
        h_spin.setValue(float(dims.get("h", 40)))
        w_spin.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "dims", "w": v, "h": h_spin.value()}))
        h_spin.valueChanged.connect(lambda v: self.edit.emit(
            {"op": "dims", "w": w_spin.value(), "h": v}))
        self._add_property_row("标签宽", w_spin)
        self._add_property_row("标签高", h_spin)

        # monochrome collapse (B&W laser): gradients→first stop, no shadow/opacity
        mono = _ld._make_designer_button("单色折叠(黑白打印)", True)
        mono.setChecked(bool(self._tmpl.get("monochrome")))
        mono.toggled.connect(lambda on: self.edit.emit(
            {"op": "tmpl_monochrome", "value": on}))
        self._add_property_row(mono)

        dims_lbl = QLabel("点击画布上的文字/图形/QR 进行编辑；顶部「+元素」可加文字/图形/图片/条码。"
                          "选中元素后拖角缩放、拖动自动吸附对齐。")
        dims_lbl.setStyleSheet("color:#5f7d7a; font-size:12px;")
        dims_lbl.setWordWrap(True)
        self._root.addWidget(dims_lbl)

    # ----- color helpers -----
    def _update_color_btn(self, btn: QPushButton, color_str: str) -> None:
        c = QColor(color_str or "#ffffff")
        fg = "#000000" if c.lightness() > 128 else "#ffffff"
        btn.setStyleSheet(
            f"background:{c.name()};color:{fg};border:1px solid #888;border-radius:3px;")
        btn.setText(c.name())

    def _pick_tmpl_color(self, field: str, btn: QPushButton) -> None:
        c = QColorDialog.getColor(QColor(self._tmpl.get(field) or "#ffffff"), btn.window())
        if c.isValid():
            self._tmpl[field] = c.name()
            self._update_color_btn(btn, c.name())
            self.edit.emit({"op": f"tmpl_{field}", "value": c.name()})

    def _pick_field_color(self, row_idx: int, field_idx: int, btn: QPushButton) -> None:
        rows = self._tmpl.get("rows") or []
        if 0 <= row_idx < len(rows):
            flds = rows[row_idx].get("fields") or []
            cur = (flds[field_idx].get("color") if 0 <= field_idx < len(flds)
                   and isinstance(flds[field_idx], dict) else None) or "#000000"
        else:
            cur = "#000000"
        c = QColorDialog.getColor(QColor(cur), btn.window())
        if c.isValid():
            self._update_color_btn(btn, c.name())
            self.edit.emit({"op": "field_color", "row": row_idx, "field": field_idx,
                            "value": c.name()})

