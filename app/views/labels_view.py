"""labels_view.py — 标签打印 4-step wizard view.

View ID : "labels"
Nav     : 🏷️  标签打印

4-step wizard
─────────────
Step 1 — 选标本 + 双桶预览
    Checkbox list of specimens (de-duplicated by uniqueId).
    Preview of sample-bucket count vs tissue-bucket count.

Step 2 — 选模板
    Left column: sample-bucket template selector.
    Right column: tissue-bucket template selector (filtered to flavor="tissue").
    Mini WYSIWYG preview of first specimen label.

Step 3 — WYSIWYG 编辑
    LabelEditorWidget (QGraphicsScene, QR draggable, 2mm safety margin, undo/redo).
    Bucket toggle: [样品瓶] / [组织管].

Step 4 — 打印
    Warnings summary + two independent print buttons:
      [打印样品瓶 (N)]  [打印 RNAlater 组织管标签 (M)]
    Each uses QPrinter → PDF preview dialog → QPrintDialog.
"""

from __future__ import annotations

import tempfile
from typing import Optional

from PyQt6.QtCore import Qt, QMarginsF, QSizeF
from PyQt6.QtGui import QPainter, QPageSize
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog, QAbstractPrintDialog
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.views.base_view import BaseView
from app.services.label_service import (
    BUILTIN_TEMPLATES,
    PAPER_SIZES,
    LabelService,
)
from app.utils.label_core import normalize_template, unique_id
from app.widgets.label_editor import LabelEditorWidget

if TYPE_CHECKING := False:
    from app.app_context import AppContext


# ── Helper: render warnings ───────────────────────────────────────────────────

def _warnings_html(warnings: list[dict]) -> str:
    if not warnings:
        return "<span style='color:#4caf50'>✓ 无警告</span>"
    lines: list[str] = []
    for w in warnings:
        color = "#f44336" if w.get("level") == "error" else "#ff9800"
        lines.append(f"<span style='color:{color}'>● {w.get('message','')}</span>")
    return "<br>".join(lines)


# ── Step widgets ──────────────────────────────────────────────────────────────

class _Step1Widget(QWidget):
    """Step 1: specimen selection + dual-bucket preview."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        title = QLabel("第 1 步：选择标本")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        # Select-all / clear buttons
        btn_row = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_none = QPushButton("取消全选")
        btn_row.addWidget(self._btn_all)
        btn_row.addWidget(self._btn_none)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Specimen list
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self._list)

        # Bucket summary
        self._summary_label = QLabel("样品桶：0 个    组织管桶：0 个")
        self._summary_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._summary_label)

        # Connections
        self._btn_all.clicked.connect(self._select_all)
        self._btn_none.clicked.connect(self._select_none)
        self._list.itemChanged.connect(self._update_summary)

    def populate(self, specimens: list[dict]) -> None:
        """Populate list from a list of camelCase specimen dicts."""
        self._list.clear()
        seen: set[str] = set()
        for idx, sp in enumerate(specimens):
            uid = unique_id(sp)
            if uid in seen:
                continue
            seen.add(uid)
            label = (
                f"{sp.get('id', '?')} — "
                f"{sp.get('species') or sp.get('scientificName') or '未命名'}"
            )
            storage = sp.get("storage") or ""
            is_rna = bool(storage and storage[0].upper() == "R")
            if is_rna:
                label += "  🧬"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        self._update_summary()

    def selected_indices(self) -> list[int]:
        result: list[int] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                idx = item.data(Qt.ItemDataRole.UserRole)
                if idx is not None:
                    result.append(idx)
        return result

    def _select_all(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item:
                item.setCheckState(Qt.CheckState.Checked)

    def _select_none(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)

    def _update_summary(self) -> None:
        # Count RNA vs total
        total = rna = 0
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                total += 1
                if "🧬" in item.text():
                    rna += 1
        self._summary_label.setText(
            f"样品桶：{total} 个    组织管桶（R 前缀）：{rna} 个"
        )


class _Step2Widget(QWidget):
    """Step 2: template selection for both buckets."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        title = QLabel("第 2 步：选择模板")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        split = QHBoxLayout()

        # Sample bucket column
        self._sample_group = QGroupBox("样品瓶模板")
        sg_layout = QVBoxLayout(self._sample_group)
        self._sample_btns: dict[str, QPushButton] = {}
        for key, tmpl in BUILTIN_TEMPLATES.items():
            if tmpl.get("flavor") == "tissue":
                continue
            btn = QPushButton(tmpl["name"])
            btn.setCheckable(True)
            btn.setProperty("template_key", key)
            btn.clicked.connect(lambda checked, k=key: self._select_template("sample", k))
            sg_layout.addWidget(btn)
            self._sample_btns[key] = btn
        sg_layout.addStretch()
        split.addWidget(self._sample_group)

        # Tissue bucket column
        self._tissue_group = QGroupBox("RNAlater 组织管模板")
        tg_layout = QVBoxLayout(self._tissue_group)
        self._tissue_btns: dict[str, QPushButton] = {}
        for key, tmpl in BUILTIN_TEMPLATES.items():
            if tmpl.get("flavor") != "tissue":
                continue
            btn = QPushButton(tmpl["name"])
            btn.setCheckable(True)
            btn.setProperty("template_key", key)
            btn.clicked.connect(lambda checked, k=key: self._select_template("tissue", k))
            tg_layout.addWidget(btn)
            self._tissue_btns[key] = btn
        tg_layout.addStretch()
        split.addWidget(self._tissue_group)

        layout.addLayout(split)

        # Default selections
        self._sample_key = "standard"
        self._tissue_key = "tissueCompact"
        self._apply_selection()

    def _select_template(self, bucket: str, key: str) -> None:
        if bucket == "sample":
            self._sample_key = key
        else:
            self._tissue_key = key
        self._apply_selection()

    def _apply_selection(self) -> None:
        for k, btn in self._sample_btns.items():
            btn.setChecked(k == self._sample_key)
        for k, btn in self._tissue_btns.items():
            btn.setChecked(k == self._tissue_key)

    def selected_sample_template(self) -> dict:
        return BUILTIN_TEMPLATES.get(self._sample_key, BUILTIN_TEMPLATES["standard"])

    def selected_tissue_template(self) -> dict:
        return BUILTIN_TEMPLATES.get(self._tissue_key, BUILTIN_TEMPLATES["tissueCompact"])


class _Step3Widget(QWidget):
    """Step 3: WYSIWYG label editor with bucket toggle."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        title = QLabel("第 3 步：编辑预览")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        # Bucket toggle
        toggle_row = QHBoxLayout()
        self._sample_toggle = QPushButton("样品瓶")
        self._sample_toggle.setCheckable(True)
        self._sample_toggle.setChecked(True)
        self._tissue_toggle = QPushButton("RNAlater 组织管")
        self._tissue_toggle.setCheckable(True)
        self._sample_toggle.clicked.connect(lambda: self._switch_bucket("sample"))
        self._tissue_toggle.clicked.connect(lambda: self._switch_bucket("tissue"))
        toggle_row.addWidget(self._sample_toggle)
        toggle_row.addWidget(self._tissue_toggle)
        toggle_row.addStretch()
        layout.addLayout(toggle_row)

        # Editor placeholder — will be replaced in refresh()
        self._editor_container = QVBoxLayout()
        self._editor: Optional[LabelEditorWidget] = None
        self._editor_widget = QWidget()
        self._editor_widget.setLayout(self._editor_container)
        layout.addWidget(self._editor_widget)

        self._current_bucket = "sample"
        self._sample_template: dict = normalize_template(None)
        self._tissue_template: dict = normalize_template(None)
        self._sample_dims: dict = {"w": 60, "h": 40}
        self._tissue_dims: dict = {"w": 30, "h": 15}
        self._label_data: dict = {}

    def refresh(
        self,
        sample_template: dict,
        tissue_template: dict,
        label_data: dict,
        sample_dims: Optional[dict] = None,
        tissue_dims: Optional[dict] = None,
    ) -> None:
        self._sample_template = sample_template
        self._tissue_template = tissue_template
        self._label_data = label_data
        if sample_dims:
            self._sample_dims = sample_dims
        if tissue_dims:
            self._tissue_dims = tissue_dims
        self._rebuild_editor()

    def _switch_bucket(self, bucket: str) -> None:
        self._current_bucket = bucket
        self._sample_toggle.setChecked(bucket == "sample")
        self._tissue_toggle.setChecked(bucket == "tissue")
        self._rebuild_editor()

    def _rebuild_editor(self) -> None:
        # Remove existing editor
        while self._editor_container.count():
            item = self._editor_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._editor = None

        if self._current_bucket == "sample":
            tmpl = self._sample_template
            dims = self._sample_dims
        else:
            tmpl = self._tissue_template
            dims = self._tissue_dims

        editor = LabelEditorWidget(tmpl, dims, self._label_data)
        self._editor_container.addWidget(editor)
        self._editor = editor

    @property
    def editor(self) -> Optional[LabelEditorWidget]:
        return self._editor


class _Step4Widget(QWidget):
    """Step 4: warnings summary + two print buttons."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        title = QLabel("第 4 步：打印")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        self._warnings_label = QLabel("加载中…")
        self._warnings_label.setWordWrap(True)
        self._warnings_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._warnings_label)

        layout.addStretch()

        btn_row = QHBoxLayout()
        self._btn_sample = QPushButton("打印样品瓶 (0)")
        self._btn_sample.setMinimumHeight(36)
        self._btn_tissue = QPushButton("打印 RNAlater 组织管标签 (0)")
        self._btn_tissue.setMinimumHeight(36)
        btn_row.addWidget(self._btn_sample)
        btn_row.addWidget(self._btn_tissue)
        layout.addLayout(btn_row)

        self._note = QLabel(
            "提示：点击按钮后弹出系统打印对话框，可选打印机或「另存 PDF」。"
        )
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._note)

    def update_counts(
        self,
        sample_count: int,
        tissue_count: int,
        sample_warnings: list[dict],
        tissue_warnings: list[dict],
    ) -> None:
        self._btn_sample.setText(f"打印样品瓶 ({sample_count})")
        self._btn_tissue.setText(f"打印 RNAlater 组织管标签 ({tissue_count})")
        html = "<b>样品桶</b><br>" + _warnings_html(sample_warnings)
        if tissue_count > 0:
            html += "<br><b>组织管桶</b><br>" + _warnings_html(tissue_warnings)
        self._warnings_label.setText(html)

    @property
    def sample_button(self) -> QPushButton:
        return self._btn_sample

    @property
    def tissue_button(self) -> QPushButton:
        return self._btn_tissue


# ── Main view ─────────────────────────────────────────────────────────────────

class LabelsView(BaseView):
    """标签打印页面 — 4-step wizard."""

    view_id = "labels"
    nav_title = "标签打印"
    nav_icon = "🏷️"

    def __init__(self, ctx: "AppContext") -> None:
        self._specimens: list[dict] = []
        super().__init__(ctx)

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Step navigation bar ─────────────────────────────────────────
        nav_bar = QHBoxLayout()
        nav_bar.setContentsMargins(8, 8, 8, 4)
        nav_bar.setSpacing(4)
        self._step_btns: list[QPushButton] = []
        step_labels = ["1 选标本", "2 选模板", "3 编辑", "4 打印"]
        for i, lbl in enumerate(step_labels):
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda checked, idx=i: self._go_to_step(idx))
            nav_bar.addWidget(btn)
            self._step_btns.append(btn)
        nav_bar.addStretch()
        root.addLayout(nav_bar)

        # ── Stacked pages ───────────────────────────────────────────────
        self._stack = QStackedWidget()

        self._step1 = _Step1Widget()
        self._step2 = _Step2Widget()
        self._step3 = _Step3Widget()
        self._step4 = _Step4Widget()

        self._stack.addWidget(self._step1)
        self._stack.addWidget(self._step2)
        self._stack.addWidget(self._step3)
        self._stack.addWidget(self._step4)
        root.addWidget(self._stack)

        # ── Bottom nav buttons ──────────────────────────────────────────
        bottom = QHBoxLayout()
        bottom.setContentsMargins(8, 4, 8, 8)
        self._btn_prev = QPushButton("← 上一步")
        self._btn_next = QPushButton("下一步 →")
        self._btn_prev.clicked.connect(self._prev_step)
        self._btn_next.clicked.connect(self._next_step)
        bottom.addWidget(self._btn_prev)
        bottom.addStretch()
        bottom.addWidget(self._btn_next)
        root.addLayout(bottom)

        # Wire print buttons
        self._step4.sample_button.clicked.connect(lambda: self._print("sample"))
        self._step4.tissue_button.clicked.connect(lambda: self._print("tissue"))

        self._current_step = 0
        self._go_to_step(0)

    # ── on_activate ─────────────────────────────────────────────────────────

    def on_activate(self) -> None:
        """Called when user navigates to this page."""
        self._load_specimens()

    # ── Navigation ──────────────────────────────────────────────────────────

    def _go_to_step(self, idx: int) -> None:
        self._current_step = idx
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._step_btns):
            btn.setChecked(i == idx)
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setEnabled(idx < 3)
        # Populate step content when entering
        if idx == 3:
            self._refresh_step4()

    def _prev_step(self) -> None:
        if self._current_step > 0:
            self._go_to_step(self._current_step - 1)

    def _next_step(self) -> None:
        if self._current_step < 3:
            next_idx = self._current_step + 1
            if next_idx == 2:
                self._refresh_step3()
            self._go_to_step(next_idx)

    # ── Data loading ─────────────────────────────────────────────────────────

    def _load_specimens(self) -> None:
        """Load specimens from DB or project settings via AppContext."""
        specimens: list[dict] = []
        db = self.ctx.get_db()
        if db is not None:
            try:
                rows = db.execute(
                    "SELECT * FROM specimens ORDER BY id"
                ).fetchall()
                for row in rows:
                    d = dict(row)
                    # Map snake_case → camelCase for label_core
                    specimens.append({
                        "province": d.get("province"),
                        "site": d.get("site"),
                        "station": d.get("station"),
                        "id": d.get("id"),
                        "storage": d.get("storage"),
                        "collectionDate": d.get("collection_date"),
                        "photoDate": d.get("photo_date"),
                        "species": d.get("scientific_name_cn") or d.get("scientific_name"),
                        "latin": d.get("scientific_name") or "",
                        "collector": d.get("collector"),
                        "photographer": d.get("photographer"),
                        "family": d.get("family"),
                        "region": d.get("geo_area") or "",
                        "lon": str(d.get("lon") or ""),
                        "lat": str(d.get("lat") or ""),
                        "geoArea": d.get("geo_area") or "",
                        "photoNotes": d.get("photo_notes") or "",
                    })
            except Exception:
                pass
        self._specimens = specimens
        self._step1.populate(specimens)

    # ── Step 3 refresh ────────────────────────────────────────────────────────

    def _refresh_step3(self) -> None:
        indices = self._step1.selected_indices()
        first_data: dict = {}
        if indices and self._specimens:
            from app.utils.label_core import specimen_to_label_data
            sp = self._specimens[indices[0]] if indices[0] < len(self._specimens) else {}
            first_data = specimen_to_label_data(sp) if sp else {}

        sample_tmpl = self._step2.selected_sample_template()
        tissue_tmpl = self._step2.selected_tissue_template()

        # Derive paper dims from BUILTIN_TEMPLATES minSize
        sample_dims = (sample_tmpl.get("minSize") or {"w": 60, "h": 40})
        tissue_dims = (tissue_tmpl.get("minSize") or {"w": 30, "h": 15})

        self._step3.refresh(sample_tmpl, tissue_tmpl, first_data, sample_dims, tissue_dims)

    # ── Step 4 refresh ────────────────────────────────────────────────────────

    def _refresh_step4(self) -> None:
        indices = self._step1.selected_indices()
        sample_tmpl = self._step2.selected_sample_template()
        tissue_tmpl = self._step2.selected_tissue_template()
        sample_dims = sample_tmpl.get("minSize") or {"w": 60, "h": 40}
        tissue_dims = tissue_tmpl.get("minSize") or {"w": 30, "h": 15}

        sample_job = LabelService.build_print_job(
            self._specimens, sample_tmpl, "sample",
            selected_indices=indices, dims=sample_dims,
        )
        tissue_job = LabelService.build_print_job(
            self._specimens, tissue_tmpl, "tissue",
            selected_indices=indices, dims=tissue_dims,
        )

        self._step4.update_counts(
            len(sample_job["items"]),
            len(tissue_job["items"]),
            sample_job.get("warnings") or [],
            tissue_job.get("warnings") or [],
        )
        self._sample_job = sample_job
        self._tissue_job = tissue_job

    # ── Printing ──────────────────────────────────────────────────────────────

    def _print(self, bucket: str) -> None:
        """Print the given bucket using QPrinter → QPrintDialog."""
        job = getattr(self, f"_{bucket}_job", None)
        if job is None:
            QMessageBox.warning(self, "打印", "请先完成前三步再打印。")
            return

        items = job.get("items") or []
        if not items:
            QMessageBox.information(self, "打印", "本桶没有可打印标签。")
            return

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dims = job.get("dims") or {}
        w_mm = dims.get("w", 60.0)
        h_mm = dims.get("h", 40.0)

        # Set page size in mm
        page_size = QPageSize(
            QSizeF(w_mm, h_mm),
            QPageSize.Unit.Millimeter,
            "Custom",
        )
        printer.setPageSize(page_size)
        printer.setPageMargins(QMarginsF(2, 2, 2, 2), QPageSize.Unit.Millimeter)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)

        # PDF preview: save to temp file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            tmp_path = f.name
        printer.setOutputFileName(tmp_path)

        dialog = QPrintDialog(printer, self)
        dialog.setOption(
            QAbstractPrintDialog.PrintDialogOption.PrintToFile, True
        )
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return

        # Paint labels onto pages
        self._paint_labels(printer, job)

    def _paint_labels(self, printer: "QPrinter", job: dict) -> None:
        """Paint all label items onto QPrinter pages."""
        from app.utils.label_core import qr_metrics
        from app.widgets.label_editor import _generate_qr_pixmap, _mm_to_px

        items = job.get("items") or []
        dims = job.get("dims") or {}
        tmpl = job.get("template") or {}
        w_mm = float(dims.get("w", 60))
        h_mm = float(dims.get("h", 40))
        qr_cfg = (tmpl.get("qr") or {})
        ecc = qr_cfg.get("ecc") or "Q"

        painter = QPainter()
        if not painter.begin(printer):
            return

        dpi = printer.resolution()
        mm_to_dot = dpi / 25.4

        for page_idx, item in enumerate(items):
            if page_idx > 0:
                printer.newPage()

            data = item.get("data") if isinstance(item, dict) else item
            if not data:
                continue

            w_dot = w_mm * mm_to_dot
            h_dot = h_mm * mm_to_dot

            # Background
            from PyQt6.QtGui import QColor
            painter.fillRect(0, 0, int(w_dot), int(h_dot), QColor("white"))

            # QR code
            metrics = qr_metrics(tmpl, dims)
            if metrics is not None:
                qr_content_key = qr_cfg.get("content") or "uniqueId"
                qr_text = str(data.get(qr_content_key) or "")
                size_dot = int(metrics["sizeMm"] * mm_to_dot)
                pixmap = _generate_qr_pixmap(qr_text, size_dot, ecc)
                if pixmap:
                    x_dot = int(metrics["x"] * mm_to_dot)
                    y_dot = int(metrics["y"] * mm_to_dot)
                    painter.drawPixmap(x_dot, y_dot, pixmap)

            # Text rows
            from PyQt6.QtGui import QFont
            from PyQt6.QtCore import QRectF as _QRectF
            qr_w_dot = (metrics["sizeMm"] * mm_to_dot) if (metrics and qr_cfg.get("position") == "right") else 0.0
            text_w_dot = max(1.0, w_dot - qr_w_dot - 2 * mm_to_dot)
            y_dot_cursor = 2 * mm_to_dot

            for row in (tmpl.get("rows") or []):
                fields = row.get("fields") or []
                parts: list[str] = []
                for f in fields:
                    key = f.get("key") if isinstance(f, dict) else str(f)
                    val = data.get(key)
                    if val is not None:
                        parts.append(str(val))
                text = (row.get("sep") or " ").join(parts)
                if row.get("prefix"):
                    text = row["prefix"] + text
                if not text:
                    continue

                size_pt = row.get("size") or 9
                font = QFont()
                font.setPointSizeF(float(size_pt) * dpi / 72.0)
                style = row.get("style") or ""
                if "bold" in style:
                    font.setBold(True)
                if "italic" in style:
                    font.setItalic(True)
                painter.setFont(font)

                fm = painter.fontMetrics()
                line_h = fm.height()
                rect = _QRectF(2 * mm_to_dot, y_dot_cursor, text_w_dot, line_h * 1.5)
                painter.drawText(rect, Qt.TextFlag.TextWordWrap, text)
                y_dot_cursor += line_h * float(row.get("lineHeight") or 1.3)

        painter.end()
