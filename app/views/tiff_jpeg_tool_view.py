"""tiff_jpeg_tool_view.py — 工具箱：TIFF 批量转 JPG."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config.i18n import tr
from app.services.tiff_jpeg_export_service import (
    EXPORT_PRESETS,
    OverwritePolicy,
    TiffJpegExportSettings,
    analyze_tiff,
    collect_tiff_paths,
    recommend_export_settings,
    resolve_jpeg_output_path,
    format_batch_export_report,
)
from app.utils import ui
from app.views.base_view import BaseView
from app.workers.tiff_jpeg_export_worker import TiffJpegExportWorker


class TiffJpegToolView(BaseView):
    view_id = "tiff_jpeg_tool"
    nav_title = "TIFF 转 JPG"
    nav_icon = "mdi6.image-filter-hdr"

    def __init__(self, ctx) -> None:
        self._sources: list[str] = []
        self._source_root: str | None = None
        self._output_dir: str | None = None
        self._worker: Optional[TiffJpegExportWorker] = None
        self._last_result = None
        super().__init__(ctx)

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        title = QLabel(tr("TIFF 批量转 JPG"))
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        hint = QLabel(
            tr(
                "将 TIFF 原图批量导出为 JPG。源 TIFF 只读、不会被修改或删除。"
                "可用品质拉杆微调，或选「智能推荐」自动匹配尺寸与文件大小。"
            )
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#667085;")
        outer.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        v = QVBoxLayout(body)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        v.addWidget(self._build_source_group())
        v.addWidget(self._build_output_group())
        v.addWidget(self._build_quality_group())
        v.addWidget(self._build_advanced_group())
        v.addWidget(self._build_file_table(), 1)
        v.addWidget(self._build_action_bar())
        outer.addWidget(scroll, 1)

    def _build_source_group(self) -> QGroupBox:
        box = QGroupBox(tr("来源"))
        lay = QVBoxLayout(box)

        row = QHBoxLayout()
        self._source_lbl = QLabel(tr("未选择文件或文件夹"))
        self._source_lbl.setWordWrap(True)
        btn_files = QPushButton(tr("选择 TIFF 文件…"))
        btn_files.clicked.connect(self._pick_files)
        btn_dir = QPushButton(tr("选择文件夹…"))
        btn_dir.clicked.connect(self._pick_folder)
        btn_clear = QPushButton(tr("清空"))
        btn_clear.clicked.connect(self._clear_sources)
        row.addWidget(btn_files)
        row.addWidget(btn_dir)
        row.addWidget(btn_clear)
        row.addWidget(self._source_lbl, 1)
        lay.addLayout(row)

        self._recursive = QCheckBox(tr("包含子文件夹"))
        self._recursive.setChecked(True)
        self._recursive.stateChanged.connect(lambda *_: self._refresh_file_table())
        lay.addWidget(self._recursive)
        return box

    def _build_output_group(self) -> QGroupBox:
        box = QGroupBox(tr("输出"))
        lay = QVBoxLayout(box)

        row = QHBoxLayout()
        self._output_lbl = QLabel(tr("与 TIFF 同目录（同名 .jpg）"))
        self._output_lbl.setWordWrap(True)
        btn_out = QPushButton(tr("指定输出文件夹…"))
        btn_out.clicked.connect(self._pick_output_dir)
        btn_same = QPushButton(tr("恢复默认同目录"))
        btn_same.clicked.connect(self._reset_output_dir)
        row.addWidget(btn_out)
        row.addWidget(btn_same)
        row.addWidget(self._output_lbl, 1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("已存在 JPG")))
        self._overwrite = QComboBox()
        self._overwrite.addItem(tr("覆盖"), OverwritePolicy.OVERWRITE.value)
        self._overwrite.addItem(tr("跳过"), OverwritePolicy.SKIP.value)
        self._overwrite.addItem(tr("自动重命名"), OverwritePolicy.RENAME.value)
        row2.addWidget(self._overwrite)
        row2.addStretch(1)
        lay.addLayout(row2)
        return box

    def _build_quality_group(self) -> QGroupBox:
        box = QGroupBox(tr("品质与尺寸"))
        lay = QVBoxLayout(box)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(tr("预设")))
        self._preset = QComboBox()
        for preset in EXPORT_PRESETS:
            self._preset.addItem(tr(preset.label), preset.preset_id)
        self._preset.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self._preset, 1)
        lay.addLayout(preset_row)

        self._smart_hint = QLabel("")
        self._smart_hint.setWordWrap(True)
        self._smart_hint.setStyleSheet("color:#667085;")
        lay.addWidget(self._smart_hint)

        q_row = QHBoxLayout()
        q_row.addWidget(QLabel(tr("JPEG 品质")))
        self._quality_slider = QSlider(Qt.Orientation.Horizontal)
        self._quality_slider.setRange(1, 100)
        self._quality_slider.setValue(90)
        self._quality_slider.valueChanged.connect(self._on_manual_tweak)
        self._quality_lbl = QLabel("90")
        self._quality_lbl.setMinimumWidth(28)
        q_row.addWidget(self._quality_slider, 1)
        q_row.addWidget(self._quality_lbl)
        lay.addLayout(q_row)

        edge_row = QHBoxLayout()
        edge_row.addWidget(QLabel(tr("最长边")))
        self._edge_slider = QSlider(Qt.Orientation.Horizontal)
        self._edge_slider.setRange(0, 8192)
        self._edge_slider.setValue(4096)
        self._edge_slider.valueChanged.connect(self._on_manual_tweak)
        self._edge_lbl = QLabel("4096 px")
        self._edge_lbl.setMinimumWidth(72)
        self._edge_unlimited = QCheckBox(tr("不限制"))
        self._edge_unlimited.stateChanged.connect(self._on_edge_unlimited)
        edge_row.addWidget(self._edge_slider, 1)
        edge_row.addWidget(self._edge_lbl)
        edge_row.addWidget(self._edge_unlimited)
        lay.addLayout(edge_row)

        sub_row = QHBoxLayout()
        sub_row.addWidget(QLabel(tr("色度抽样")))
        self._subsampling = QComboBox()
        self._subsampling.addItem("4:4:4（最高细节）", 0)
        self._subsampling.addItem("4:2:2（平衡）", 1)
        self._subsampling.addItem("4:2:0（最小体积）", 2)
        self._subsampling.setCurrentIndex(1)
        self._subsampling.currentIndexChanged.connect(self._on_manual_tweak)
        sub_row.addWidget(self._subsampling)
        self._progressive = QCheckBox(tr("渐进式 JPEG"))
        self._progressive.stateChanged.connect(self._on_manual_tweak)
        sub_row.addWidget(self._progressive)
        sub_row.addStretch(1)
        lay.addLayout(sub_row)
        return box

    def _build_advanced_group(self) -> QGroupBox:
        box = QGroupBox(tr("高级保留信息"))
        lay = QVBoxLayout(box)

        self._preserve_exif = QCheckBox(tr("保留 EXIF（相机、镜头、拍摄参数等，若 TIFF 内存在）"))
        self._preserve_icc = QCheckBox(tr("保留 ICC 色彩配置（若 TIFF 内存在）"))
        self._preserve_dpi = QCheckBox(tr("保留 DPI/分辨率信息（若 TIFF 内存在）"))
        lay.addWidget(self._preserve_exif)
        lay.addWidget(self._preserve_icc)
        lay.addWidget(self._preserve_dpi)

        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel(tr("透明区域背景")))
        self._alpha_bg = QComboBox()
        self._alpha_bg.addItem(tr("白色"), (255, 255, 255))
        self._alpha_bg.addItem(tr("黑色"), (0, 0, 0))
        self._alpha_bg.addItem(tr("浅灰"), (240, 240, 240))
        bg_row.addWidget(self._alpha_bg)
        bg_row.addStretch(1)
        lay.addLayout(bg_row)

        hint = QLabel(tr("默认关闭保留选项，适合快速预览；需要传递影像信息时再勾选。"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#667085;")
        lay.addWidget(hint)
        return box

    def _build_file_table(self) -> QGroupBox:
        box = QGroupBox(tr("待转换列表"))
        lay = QVBoxLayout(box)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(
            [tr("文件名"), tr("尺寸"), tr("输出 JPG"), tr("智能建议")]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        lay.addWidget(self._table)
        self._count_lbl = QLabel(tr("共 0 个 TIFF"))
        lay.addWidget(self._count_lbl)
        return box

    def _build_action_bar(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._btn_start = QPushButton(tr("开始转换"))
        self._btn_start.clicked.connect(self._start_export)
        self._btn_cancel = QPushButton(tr("取消"))
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.clicked.connect(self._cancel_export)
        self._btn_report = QPushButton(tr("保存报告"))
        self._btn_report.setEnabled(False)
        self._btn_report.clicked.connect(self._save_report)
        lay.addWidget(self._progress, 1)
        lay.addWidget(self._status)
        lay.addWidget(self._btn_start)
        lay.addWidget(self._btn_cancel)
        lay.addWidget(self._btn_report)
        return row

    def on_activate(self) -> None:
        paths = getattr(self.ctx, "pending_tiff_jpeg_sources", None)
        preset_id = getattr(self.ctx, "pending_tiff_jpeg_preset_id", None) or "archive"
        if paths:
            self.load_tiff_sources(list(paths), preset_id=preset_id)
            self.ctx.pending_tiff_jpeg_sources = None
            self.ctx.pending_tiff_jpeg_preset_id = None
        else:
            self._refresh_file_table()

    def load_tiff_sources(
        self,
        paths: list[str],
        *,
        preset_id: str | None = "archive",
    ) -> int:
        """载入一批母版 TIFF 绝对路径（数据汇总等页面跳转时预填）。"""
        clean: list[str] = []
        seen: set[str] = set()
        for raw in paths or []:
            text = str(raw or "").strip()
            if not text:
                continue
            try:
                resolved = str(Path(text).resolve())
            except OSError:
                continue
            if Path(resolved).suffix.lower() not in {".tif", ".tiff"}:
                continue
            if not Path(resolved).is_file():
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            clean.append(resolved)

        self._sources = list(clean)
        self._source_root = str(Path(clean[0]).parent.resolve()) if clean else None
        self._recursive.setChecked(False)
        if preset_id:
            self._select_preset(preset_id)
        self._refresh_file_table()
        return len(clean)

    def _select_preset(self, preset_id: str) -> None:
        idx = self._preset.findData(preset_id)
        if idx >= 0 and self._preset.currentIndex() != idx:
            self._preset.setCurrentIndex(idx)
        elif idx < 0:
            self._on_preset_changed()
        else:
            self._on_preset_changed()

    def stop_background_work(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    def _pick_files(self) -> None:
        paths = ui.get_open_file_names(
            self,
            tr("选择 TIFF 文件"),
            "",
            tr("TIFF 文件 (*.tif *.tiff);;所有文件 (*)"),
        )
        if not paths:
            return
        self._sources.extend(paths)
        self._source_root = str(Path(paths[0]).parent.resolve())
        self._refresh_file_table()

    def _pick_folder(self) -> None:
        path = ui.get_existing_directory(self, tr("选择包含 TIFF 的文件夹"))
        if not path:
            return
        self._sources.append(path)
        self._source_root = path
        self._refresh_file_table()

    def _clear_sources(self) -> None:
        self._sources.clear()
        self._source_root = None
        self._refresh_file_table()

    def _pick_output_dir(self) -> None:
        path = ui.get_existing_directory(self, tr("选择 JPG 输出文件夹"))
        if not path:
            return
        self._output_dir = path
        self._output_lbl.setText(path)

    def _reset_output_dir(self) -> None:
        self._output_dir = None
        self._output_lbl.setText(tr("与 TIFF 同目录（同名 .jpg）"))

    def _current_preset_id(self) -> str:
        return str(self._preset.currentData() or "general")

    def _on_preset_changed(self) -> None:
        preset_id = self._current_preset_id()
        preset = next(p for p in EXPORT_PRESETS if p.preset_id == preset_id)
        smart = preset.smart
        self._quality_slider.blockSignals(True)
        self._edge_slider.blockSignals(True)
        self._subsampling.blockSignals(True)
        self._progressive.blockSignals(True)
        self._edge_unlimited.blockSignals(True)

        if smart:
            self._apply_smart_preview()
            self._smart_hint.setText(
                tr("智能模式：按每个文件单独计算品质与最长边。")
            )
        else:
            cfg = preset.settings.clamped()
            self._quality_slider.setValue(cfg.quality)
            if cfg.max_long_edge is None:
                self._edge_unlimited.setChecked(True)
                self._edge_slider.setEnabled(False)
            else:
                self._edge_unlimited.setChecked(False)
                self._edge_slider.setEnabled(True)
                self._edge_slider.setValue(cfg.max_long_edge)
            self._subsampling.setCurrentIndex(cfg.subsampling)
            self._progressive.setChecked(cfg.progressive)
            self._smart_hint.setText("")

        self._quality_slider.blockSignals(False)
        self._edge_slider.blockSignals(False)
        self._subsampling.blockSignals(False)
        self._progressive.blockSignals(False)
        self._edge_unlimited.blockSignals(False)
        self._sync_quality_labels()
        self._refresh_file_table()

    def _on_manual_tweak(self) -> None:
        if self._current_preset_id() != "smart":
            manual_idx = self._preset.findData("custom")
            if manual_idx < 0:
                self._preset.addItem(tr("自定义"), "custom")
                manual_idx = self._preset.findData("custom")
            if self._preset.currentData() != "custom":
                self._preset.blockSignals(True)
                self._preset.setCurrentIndex(manual_idx)
                self._preset.blockSignals(False)
        self._sync_quality_labels()
        self._refresh_smart_column_only()

    def _on_edge_unlimited(self) -> None:
        unlimited = self._edge_unlimited.isChecked()
        self._edge_slider.setEnabled(not unlimited)
        self._on_manual_tweak()

    def _sync_quality_labels(self) -> None:
        self._quality_lbl.setText(str(self._quality_slider.value()))
        if self._edge_unlimited.isChecked():
            self._edge_lbl.setText(tr("不限制"))
        else:
            self._edge_lbl.setText(f"{self._edge_slider.value()} px")

    def _build_settings(self) -> TiffJpegExportSettings:
        max_edge = None if self._edge_unlimited.isChecked() else self._edge_slider.value()
        return TiffJpegExportSettings(
            quality=self._quality_slider.value(),
            max_long_edge=max_edge,
            subsampling=int(self._subsampling.currentData()),
            progressive=self._progressive.isChecked(),
            optimize=True,
            preserve_exif=self._preserve_exif.isChecked(),
            preserve_icc_profile=self._preserve_icc.isChecked(),
            preserve_dpi=self._preserve_dpi.isChecked(),
            alpha_background=tuple(self._alpha_bg.currentData() or (255, 255, 255)),
        ).clamped()

    def _apply_smart_preview(self) -> None:
        paths = collect_tiff_paths(self._sources, recursive=self._recursive.isChecked())
        if not paths:
            self._smart_hint.setText(tr("添加 TIFF 后，智能模式会显示推荐参数。"))
            return
        analysis = analyze_tiff(paths[0])
        if analysis is None:
            self._smart_hint.setText(tr("无法读取首个 TIFF 的元数据。"))
            return
        cfg = recommend_export_settings(analysis)
        self._quality_slider.setValue(cfg.quality)
        if cfg.max_long_edge is None:
            self._edge_unlimited.setChecked(True)
            self._edge_slider.setEnabled(False)
        else:
            self._edge_unlimited.setChecked(False)
            self._edge_slider.setEnabled(True)
            self._edge_slider.setValue(cfg.max_long_edge)
        self._subsampling.setCurrentIndex(cfg.subsampling)
        self._progressive.setChecked(cfg.progressive)
        self._smart_hint.setText(
            tr("示例（首个文件）：") + analysis.summary
            + tr(" → 品质 {q}，最长边 {e}").format(
                q=cfg.quality,
                e=tr("不限制") if cfg.max_long_edge is None else f"{cfg.max_long_edge}px",
            )
        )

    def _refresh_file_table(self) -> None:
        paths = collect_tiff_paths(self._sources, recursive=self._recursive.isChecked())
        self._table.setRowCount(len(paths))
        smart = self._current_preset_id() == "smart"
        for row, path in enumerate(paths):
            p = Path(path)
            analysis = analyze_tiff(path)
            out = resolve_jpeg_output_path(
                path,
                output_dir=self._output_dir,
                source_root=self._source_root,
            )
            self._table.setItem(row, 0, QTableWidgetItem(p.name))
            size_txt = analysis.summary if analysis else "—"
            self._table.setItem(row, 1, QTableWidgetItem(size_txt))
            self._table.setItem(row, 2, QTableWidgetItem(str(out)))
            if smart and analysis:
                cfg = recommend_export_settings(analysis)
                tip = f"Q{cfg.quality} / {cfg.max_long_edge or '∞'}px"
            else:
                cfg = self._build_settings()
                tip = f"Q{cfg.quality} / {cfg.max_long_edge or '∞'}px"
            self._table.setItem(row, 3, QTableWidgetItem(tip))
        self._count_lbl.setText(tr("共 {n} 个 TIFF").format(n=len(paths)))
        if self._sources:
            self._source_lbl.setText("\n".join(self._sources[:3]) + (
                f"\n… (+{len(self._sources) - 3})" if len(self._sources) > 3 else ""
            ))
        if smart:
            self._apply_smart_preview()

    def _refresh_smart_column_only(self) -> None:
        if self._current_preset_id() != "smart":
            return
        paths = collect_tiff_paths(self._sources, recursive=self._recursive.isChecked())
        for row, path in enumerate(paths):
            analysis = analyze_tiff(path)
            if analysis:
                cfg = recommend_export_settings(analysis)
                tip = f"Q{cfg.quality} / {cfg.max_long_edge or '∞'}px"
            else:
                tip = "—"
            item = self._table.item(row, 3)
            if item is None:
                self._table.setItem(row, 3, QTableWidgetItem(tip))
            else:
                item.setText(tip)

    def _overwrite_policy(self) -> OverwritePolicy:
        value = self._overwrite.currentData()
        return OverwritePolicy(str(value))

    def _start_export(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        paths = collect_tiff_paths(self._sources, recursive=self._recursive.isChecked())
        if not paths:
            ui.warn(self, tr("TIFF 转 JPG"), tr("请先选择 TIFF 文件或文件夹。"))
            return
        smart = self._current_preset_id() == "smart"
        settings = self._build_settings()

        self._btn_start.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._btn_report.setEnabled(False)
        self._progress.setValue(0)
        self._status.setText(tr("准备转换…"))

        self._worker = TiffJpegExportWorker(
            self._sources,
            settings,
            output_dir=self._output_dir,
            source_root=self._source_root,
            recursive=self._recursive.isChecked(),
            overwrite=self._overwrite_policy(),
            smart=smart,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _cancel_export(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._status.setText(tr("正在取消…"))

    def _on_progress(self, current: int, total: int, name: str) -> None:
        pct = int((current / max(total, 1)) * 100)
        self._progress.setValue(pct)
        self._status.setText(tr("正在处理 {name} ({current}/{total})").format(
            name=name, current=current, total=total,
        ))

    def _on_finished(self, result) -> None:
        self._last_result = result
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._btn_report.setEnabled(True)
        self._progress.setValue(100)
        self._status.setText(
            tr("完成：成功 {ok}，跳过 {skip}，失败 {fail}").format(
                ok=result.ok_count,
                skip=len(result.skipped),
                fail=len(result.failed),
            )
        )
        if result.failed:
            sample = result.failed[0]
            ui.warn(
                self,
                tr("部分失败"),
                tr("{file}\n{err}").format(file=Path(sample[0]).name, err=sample[1]),
            )
        elif result.ok_count:
            ui.info(self, tr("TIFF 转 JPG"), tr("批量转换已完成。"))

    def _on_failed(self, message: str) -> None:
        self._btn_start.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._status.setText(message)
        ui.critical(self, tr("转换失败"), message)

    def _save_report(self) -> None:
        if self._last_result is None:
            return
        start = "tiff-jpeg-report.txt"
        if self._output_dir:
            start = str(Path(self._output_dir) / start)
        path = ui.get_save_file_name(
            self,
            tr("保存转换报告"),
            start,
            tr("文本文件 (*.txt);;所有文件 (*)"),
        )
        if not path:
            return
        try:
            Path(path).write_text(format_batch_export_report(self._last_result), encoding="utf-8")
        except Exception as exc:
            ui.warn(self, tr("保存报告失败"), str(exc))
