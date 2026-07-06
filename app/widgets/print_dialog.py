"""print_dialog.py — Unified printer-selection dialog shared by the labels
print studio and the workbench quick-print path.

Replaces the broken ``QPrintDialog`` + ``PdfFormat`` hack in
``labels_view._run_print_dialog`` and adds a visible printer choice to the
workbench one-click path when ``quick_print_mode`` is set to ``"dialog"``.

Usage::

    from app.widgets.print_dialog import PrintJobDialog

    dlg = PrintJobDialog(jobs, self)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        printer_name = dlg.selected_printer()
        # … configure QPrinter, setPrinterName(printer_name), paint_jobs …
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtPrintSupport import QPrinterInfo
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from app.config import icons
from app.utils import ui


def _summarize_jobs(jobs: list[dict]) -> str:
    """Build one human-readable line per job bucket for the dialog summary.

    Example output::

        瓶签: 12 张 · 内置标准 · LABEL
        RNA签: 3 张 · 内置标准 · A4
    """
    lines: list[str] = []
    for job in jobs:
        bucket = "RNA签" if job.get("bucket") == "tissue" else "瓶签"
        items = job.get("items") or []
        tmpl = job.get("template") or {}
        tmpl_name = str(tmpl.get("name") or tmpl.get("code") or "未命名模板")
        paper = str(job.get("paperType") or "label").upper()
        lines.append(f"{bucket}: {len(items)} 张 · {tmpl_name} · {paper}")
    return "\n".join(lines) if lines else "（无标签数据）"


class PrintJobDialog(QDialog):
    """Modal dialog that lets the user pick a printer and review what will print.

    Does NOT configure ``QPrinter`` or render — the caller handles that with
    :func:`label_print.build_printer` and :func:`label_print.paint_jobs`.
    """

    def __init__(self, jobs: list[dict], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._jobs = jobs
        self._selected_printer: str = ""

        self.setWindowTitle("打印标签")
        self.setModal(True)
        self.resize(460, 340)
        self._build_ui()
        ui.center_on(self, parent)

    # ── accessors ──────────────────────────────────────────────────────────

    def selected_printer(self) -> str:
        """Printer name chosen by the user; empty string = system default."""
        return self._selected_printer

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        # title
        title = QLabel("打印标签")
        title.setObjectName("PaneTitle")
        lay.addWidget(title)

        # printer selector
        printer_label = QLabel("打印机")
        printer_label.setObjectName("Muted")
        self._printer_combo = QComboBox()
        self._printer_combo.setFixedHeight(32)
        self._refresh_printer_combo()
        self._printer_combo.currentIndexChanged.connect(self._sync_niimbot_hint)
        lay.addWidget(printer_label)
        lay.addWidget(self._printer_combo)

        self._niimbot_hint = QLabel("")
        self._niimbot_hint.setObjectName("MutedSmall")
        self._niimbot_hint.setWordWrap(True)
        self._niimbot_hint.setVisible(False)
        lay.addWidget(self._niimbot_hint)
        self._sync_niimbot_hint()

        # job summary
        summary_title = QLabel("打印内容")
        summary_title.setObjectName("Muted")
        lay.addWidget(summary_title)

        self._summary_label = QLabel(_summarize_jobs(self._jobs))
        self._summary_label.setObjectName("MutedSmall")
        self._summary_label.setWordWrap(True)
        lay.addWidget(self._summary_label)

        lay.addStretch()

        # buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._print_btn = QPushButton("打印")
        self._print_btn.setObjectName("Primary")
        self._print_btn.setFixedHeight(32)
        icons.set_button_icon(self._print_btn, "mdi6.printer", size=16)
        self._print_btn.clicked.connect(self._accept)

        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setObjectName("Outline")
        self._cancel_btn.setFixedHeight(32)
        self._cancel_btn.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self._cancel_btn)
        btn_layout.addWidget(self._print_btn)
        lay.addLayout(btn_layout)

    def _accept(self) -> None:
        self._selected_printer = str(self._printer_combo.currentData() or "")
        self.accept()

    def _refresh_printer_combo(self, selected: str = "") -> None:
        """Populate the printer combo from Qt/Windows printers plus virtual ones."""
        self._printer_combo.blockSignals(True)
        self._printer_combo.clear()
        self._printer_combo.addItem("系统默认打印机", "")
        try:
            from app.utils import windows_print
            if windows_print.is_available():
                names = windows_print.windows_printer_names()
            else:
                names = [p.printerName() for p in QPrinterInfo.availablePrinters()]
        except Exception:
            names = []
        for name in sorted({n for n in names if n}):
            self._printer_combo.addItem(name, name)
        try:
            from app.services import niimbot_print_service
            for printer in niimbot_print_service.available_printers():
                self._printer_combo.addItem(printer.name, printer.id)
        except Exception:
            pass
        if selected and self._printer_combo.findData(selected) < 0:
            self._printer_combo.addItem(f"{selected}（未检测到）", selected)
        idx = self._printer_combo.findData(selected or "")
        self._printer_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._printer_combo.blockSignals(False)

    def _sync_niimbot_hint(self, *_args) -> None:
        hint = getattr(self, "_niimbot_hint", None)
        if hint is None:
            return
        try:
            from app.services import niimbot_print_service
            printer_id = str(self._printer_combo.currentData() or "")
            if not niimbot_print_service.is_niimbot_printer_id(printer_id):
                hint.clear()
                hint.setVisible(False)
                return
            paper_types = [str(job.get("paperType") or "") for job in self._jobs]
            hint.setText(niimbot_print_service.compatibility_notice(paper_types))
            hint.setVisible(True)
        except Exception:
            hint.clear()
            hint.setVisible(False)
