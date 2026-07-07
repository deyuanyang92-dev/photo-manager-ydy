"""Printing and label-template workflows for ProjectSettingsDrawer."""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)


class ProjectSettingsPrintMixin:
    @staticmethod
    def _populate_quick_paper_combo(combo: QComboBox) -> None:
        combo.addItem("标签纸（每张标签单独打印）", "label")
        combo.addItem("A4 纸（多张标签合版）", "a4")
        combo.addItem("A5 纸（多张标签合版）", "a5")

    def _refresh_printer_combo(self, combo: QComboBox, selected: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("系统默认打印机", "")
        try:
            from app.utils import windows_print
            if windows_print.is_available():
                names = windows_print.windows_printer_names()
            else:
                from PyQt6.QtPrintSupport import QPrinterInfo
                names = [p.printerName() for p in QPrinterInfo.availablePrinters()]
        except Exception:
            names = []
        for name in sorted({n for n in names if n}):
            combo.addItem(name, name)
        try:
            from app.services import niimbot_print_service
            for printer in niimbot_print_service.available_printers():
                combo.addItem(printer.name, printer.id)
        except Exception:
            pass
        if selected and combo.findData(selected) < 0:
            combo.addItem(f"{selected}（未检测到）", selected)
        idx = combo.findData(selected or "")
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _sync_niimbot_print_hint(self, *_args) -> None:
        hint = getattr(self, "_niimbot_print_hint", None)
        if hint is None:
            return
        sample_combo = getattr(self, "_sample_printer_combo", None)
        tissue_combo = getattr(self, "_tissue_printer_combo", None)
        if sample_combo is None or tissue_combo is None:
            return
        sample_printer = str(sample_combo.currentData() or "")
        tissue_printer = str(tissue_combo.currentData() or "")
        try:
            from app.services import niimbot_print_service
            sample_b203 = niimbot_print_service.is_niimbot_printer_id(sample_printer)
            tissue_b203 = niimbot_print_service.is_niimbot_printer_id(tissue_printer)
            if not (sample_b203 or tissue_b203):
                hint.clear()
                hint.setVisible(False)
                return
            paper_types = []
            if sample_b203:
                paper_types.append(str(self._sample_paper_combo.currentData() or ""))
            if tissue_b203:
                paper_types.append(str(self._tissue_paper_combo.currentData() or ""))
            buckets = []
            if sample_b203:
                buckets.append("瓶签")
            if tissue_b203:
                buckets.append("RNA 签")
            hint.setText(
                f"NIIMBOT B203 已用于{'、'.join(buckets)}。"
                f"{niimbot_print_service.compatibility_notice(paper_types)}"
            )
            hint.setVisible(True)
        except Exception:
            hint.clear()
            hint.setVisible(False)

    def _refresh_template_combo(self, combo: QComboBox, bucket: str, selected: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        try:
            from app.services import label_service
            lib = label_service.LabelTemplateLibrary(bucket)
            selected = (
                selected
                or lib.selected_key()
                or label_service.DEFAULT_TEMPLATE_KEY[bucket]
            )
            for key, tmpl in label_service.BUILTIN_TEMPLATES.items():
                if bucket == "tissue" and tmpl.get("flavor") != "tissue":
                    continue
                if bucket == "sample" and tmpl.get("flavor") == "tissue":
                    continue
                size = tmpl.get("minSize") or {}
                size_text = f" · {size.get('w')}×{size.get('h')} mm" if size else ""
                combo.addItem(
                    f"内置：{tmpl.get('code', '?')} · {tmpl.get('name') or key}{size_text}", key
                )
            for rec in lib.records():
                tid = rec.get("id")
                if tid:
                    size = (rec.get("template") or {}).get("minSize") or {}
                    size_text = f" · {size.get('w')}×{size.get('h')} mm" if size else ""
                    combo.addItem(
                        f"自定义：{rec.get('name') or tid}{size_text}",
                        label_service.key_from_id(tid),
                    )
        except Exception:
            pass
        if selected and combo.findData(selected) < 0:
            combo.addItem(f"{selected}（未找到）", selected)
        idx = combo.findData(selected or "")
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

    def _open_template_manager(self, bucket: str) -> None:
        """Edit/create templates, then bind the chosen template to this project."""
        from app.services import label_service
        from app.widgets.label_step2_templates import LabelStep2Templates

        libs = {
            "sample": label_service.LabelTemplateLibrary("sample"),
            "tissue": label_service.LabelTemplateLibrary("tissue"),
        }
        dlg = QDialog(self)
        bucket_title = "瓶签模板中心" if bucket == "sample" else "RNA签模板中心"
        dlg.setWindowTitle(bucket_title)
        dlg.resize(1040, 760)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        scroll = QScrollArea(dlg)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        manager = LabelStep2Templates(
            libs,
            scroll,
            visible_buckets=[bucket],
            title=bucket_title,
            subtitle="当前选择会成为项目设置里的默认打印模板。",
            manager_mode=True,
        )
        try:
            specimens = label_service.load_specimen_dicts(self.ctx.get_db())
        except Exception:
            specimens = []
        preview_indices = list(range(len(specimens)))
        if bucket == "tissue":
            preview_indices = [
                i for i, item in enumerate(specimens)
                if str(item.get("storage") or "").upper().startswith("R")
            ]
        if not preview_indices:
            specimens = [self._demo_specimen_for_bucket(bucket)]
            preview_indices = [0]
        manager.set_data(specimens, preview_indices)
        scroll.setWidget(manager)
        layout.addWidget(scroll, stretch=1)
        close_btn = QPushButton("完成")
        close_btn.setObjectName("Primary")
        close_btn.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)
        dlg.exec()

        combo = self._sample_template_combo if bucket == "sample" else self._tissue_template_combo
        chosen = libs[bucket].selected_key() or label_service.DEFAULT_TEMPLATE_KEY[bucket]
        self._refresh_template_combo(combo, bucket, chosen)
        self._save_print_settings()

    def _paper_type_for_imposition(self, bucket: str) -> str:
        return self._effective_sheet_paper_for_bucket(bucket)

    def _effective_sheet_paper_for_bucket(self, bucket: str) -> str:
        combo = self._tissue_paper_combo if bucket == "tissue" else self._sample_paper_combo
        paper_type = str(combo.currentData() or "")
        if not paper_type:
            try:
                from app.services import label_service
                paper_type = label_service.persisted_paper_type(bucket)
            except Exception:
                paper_type = ""
        if paper_type not in {"a4", "a5"}:
            return ""
        return paper_type

    def _sync_imposition_buttons(self) -> None:
        for bucket, btn in (
            ("sample", self._sample_imposition_btn),
            ("tissue", self._tissue_imposition_btn),
        ):
            combo = self._tissue_paper_combo if bucket == "tissue" else self._sample_paper_combo
            paper_type = self._effective_sheet_paper_for_bucket(bucket)
            enabled = bool(paper_type) and combo.isEnabled()
            btn.setEnabled(enabled)
            if paper_type:
                btn.setText(f"设置 {paper_type.upper()} 多标签排版…")
                btn.setToolTip(f"编辑 {paper_type.upper()} 合版的边距、间距、行列、方向和起始格。")
            else:
                btn.setText("无需排版（每张标签单独打印）")
                btn.setToolTip("标签卷纸/单张标签是一张一张直接打印，不使用整页合版排版。")

    def _template_key_for_bucket(self, bucket: str) -> str:
        combo = self._tissue_template_combo if bucket == "tissue" else self._sample_template_combo
        return str(combo.currentData() or "")

    def _demo_specimen_for_bucket(self, bucket: str) -> dict:
        storage = "RD95E" if bucket == "tissue" else "D95E"
        return {
            "uid": f"FJ-XM-B2-DLC001-{storage}-20260602",
            "id": "DLC001",
            "province": "FJ",
            "site": "XM",
            "station": "B2",
            "storage": storage,
            "collectionDate": "20260602",
            "photoDate": "20260602",
            "collector": "采集人",
            "species": "样品标签",
            "latin": "Marphysa sp.",
            "family": "Eunicidae",
        }

    def _imposition_job(self, bucket: str) -> dict:
        from app.services import label_service
        lib = label_service.LabelTemplateLibrary(bucket)
        tmpl = label_service.resolve_template_key(lib, self._template_key_for_bucket(bucket))
        size = tmpl.get("minSize") or {}
        dims = (
            {"w": float(size["w"]), "h": float(size["h"])}
            if size.get("w") and size.get("h")
            else label_service.resolve_dims(lib, lib.selected_custom_dims())
        )
        paper_type = self._paper_type_for_imposition(bucket)
        return label_service.LabelService.build_print_job(
            [self._demo_specimen_for_bucket(bucket)],
            tmpl,
            bucket,
            selected_indices=[0],
            dims=dims,
            copies=12,
            paper_type=paper_type,
            paper=label_service.PAPER_SIZES.get(paper_type),
        )

    def _open_imposition_designer(self, bucket: str) -> None:
        from app.services import label_service
        from app.widgets.label_imposition_dialog import LabelImpositionDialog

        if not self._paper_type_for_imposition(bucket):
            QMessageBox.information(
                self,
                "排版设计",
                "当前纸张是标签卷纸/单张标签，不需要 A4/A5 合版排版。请先把纸张切换为 A4 合版或 A5 合版。",
            )
            return
        job = self._imposition_job(bucket)
        snapshot = label_service.persisted_imposition(bucket)
        dlg = LabelImpositionDialog(
            job,
            snapshot,
            self,
            demo_data=self._demo_specimen_for_bucket(bucket),
        )
        dlg.setWindowTitle(
            "瓶签排版设计" if bucket == "sample" else "RNA签排版设计"
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            label_service.persist_imposition(bucket, dlg.imposition())
