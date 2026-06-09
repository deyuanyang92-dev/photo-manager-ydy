"""helicon_config_dialog.py — standalone Helicon Focus 配置 dialog.

Opened from the top-bar "Helicon" button. Mirrors the web oracle's
``renderHeliconConfigModal()`` (app.js:7029-7368): consolidates the Helicon
executable-path config, synthesis parameters (method/radius/smoothing), advanced
output options, and a live CLI preview into one non-modal dialog.

All persistence reuses the same QSettings keys as ``settings_view`` (the Helicon
tab there stays as-is — this dialog is an additional, faster entry point). All
detection + command building reuses ``app.services.helicon_service`` so the
HARD red line holds: the Helicon CLI is only ever built via ``build_helicon_args``
and never carries cjxl flags.
"""
from __future__ import annotations

import os
import shlex
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import icons
from app.utils import ui
from app.widgets.helicon_params_panel import HeliconParamsPanel
from app.views.settings_view import (
    _K_HELICON_CONCURRENCY,
    _K_HELICON_EXE,
    _K_HELICON_METHOD,
    _K_HELICON_OUTPUT_FORMAT,
    _K_HELICON_QUALITY,
    _K_HELICON_RADIUS,
    _K_HELICON_RUN_MODE,
    _K_HELICON_SAVE_DEPTH_MAP,
    _K_HELICON_SMOOTHING,
    _K_HELICON_TIFF_COMPRESSION,
)

# Oracle factory defaults (app.js reset): method B, radius 8, smoothing 4.
_DEFAULT_METHOD = 1
_DEFAULT_RADIUS = 8.0
_DEFAULT_SMOOTHING = 4

_TIFF_VALS = ["u", "lzw", "zip"]
_RUN_VALS = ["silent", "progress", "gui"]


def _as_bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


class HeliconConfigDialog(QDialog):
    """Standalone Helicon Focus configuration dialog."""

    def __init__(self, ctx, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("HeliconConfigDialog")
        self.setWindowTitle("Helicon Focus 配置")
        self.setModal(False)
        self.resize(640, 720)
        self._build_ui()
        self._load_from_settings()
        self._detect_and_refresh()

    # ── settings access ────────────────────────────────────────────────────────

    @property
    def _qs(self):
        return self.ctx.settings._qs

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 16, 16, 8)
        root.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        root.addWidget(self._build_path_section())
        root.addWidget(self._params_section())
        root.addWidget(self._build_advanced_section())
        root.addWidget(self._build_cli_section())
        root.addStretch()

        outer.addWidget(self._build_button_bar())

    def _section_frame(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Panel")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lbl = QLabel(title)
        lbl.setObjectName("Section")
        lay.addWidget(lbl)
        return frame, lay

    def _build_path_section(self) -> QFrame:
        frame, lay = self._section_frame("Helicon Focus 路径")

        self._status_label = QLabel("—")
        self._status_label.setObjectName("Mono")
        lay.addWidget(self._status_label)

        self._effective_label = QLabel("—")
        self._effective_label.setObjectName("MutedSmall")
        self._effective_label.setWordWrap(True)
        lay.addWidget(self._effective_label)

        self._path_edit = QLineEdit()
        self._path_edit.setObjectName("ConfigPathInput")
        self._path_edit.setPlaceholderText("自定义 HeliconFocus.exe 路径或安装目录…")
        lay.addWidget(self._path_edit)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)

        browse = QPushButton("浏览…")
        browse.setObjectName("Outline")
        browse.clicked.connect(self._on_browse)
        btn_row.addWidget(browse)

        save_path = QPushButton("保存路径")
        save_path.setObjectName("Outline")
        save_path.clicked.connect(self._on_save_path)
        btn_row.addWidget(save_path)

        clear = QPushButton("清除自定义")
        clear.setObjectName("Ghost")
        clear.clicked.connect(self._on_clear_path)
        btn_row.addWidget(clear)

        redetect = QPushButton("重新探测")
        redetect.setObjectName("Ghost")
        redetect.clicked.connect(self._detect_and_refresh)
        btn_row.addWidget(redetect)

        btn_row.addStretch()
        lay.addLayout(btn_row)

        hint = QLabel("提示：探测优先级 自定义路径 > HELICON_FOCUS_PATH > 已知安装目录。")
        hint.setObjectName("MutedSmall")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return frame

    def _params_section(self) -> QFrame:
        frame, lay = self._section_frame("合成参数")
        self._params = HeliconParamsPanel()
        self._params.params_changed.connect(self._refresh_cli_preview)
        lay.addWidget(self._params)
        return frame

    def _build_advanced_section(self) -> QFrame:
        frame, lay = self._section_frame("高级输出选项")

        # collapsible body toggled by a header button
        self._adv_toggle = QPushButton("▶ 展开")
        self._adv_toggle.setObjectName("Ghost")
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.setFixedHeight(24)
        self._adv_toggle.toggled.connect(self._on_adv_toggled)
        lay.addWidget(self._adv_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        self._adv_body = QWidget()
        body = QVBoxLayout(self._adv_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        self._output_format_combo = QComboBox()
        self._output_format_combo.addItems(["TIF", "JPG"])
        self._output_format_combo.currentIndexChanged.connect(self._refresh_cli_preview)
        body.addLayout(self._row("输出格式", self._output_format_combo))

        self._tiff_compression_combo = QComboBox()
        self._tiff_compression_combo.addItems(["无 (u)", "LZW", "ZIP"])
        self._tiff_compression_combo.currentIndexChanged.connect(self._refresh_cli_preview)
        body.addLayout(self._row("TIFF 压缩", self._tiff_compression_combo))

        self._run_mode_combo = QComboBox()
        self._run_mode_combo.addItems(["静默 (silent)", "进度条 (progress)", "GUI 窗口 (gui)"])
        self._run_mode_combo.currentIndexChanged.connect(self._refresh_cli_preview)
        body.addLayout(self._row("运行模式", self._run_mode_combo))

        self._concurrency_spin = QSpinBox()
        self._concurrency_spin.setRange(1, 8)
        body.addLayout(self._row("批量并发", self._concurrency_spin))

        self._save_depth_map_chk = QCheckBox("保存深度图 (-dmap)")
        self._save_depth_map_chk.stateChanged.connect(self._refresh_cli_preview)
        body.addWidget(self._save_depth_map_chk)

        self._adv_body.setVisible(False)
        lay.addWidget(self._adv_body)
        return frame

    def _build_cli_section(self) -> QFrame:
        frame, lay = self._section_frame("CLI 命令预览")
        self._cli_preview = QPlainTextEdit()
        self._cli_preview.setObjectName("Mono")
        self._cli_preview.setReadOnly(True)
        self._cli_preview.setFixedHeight(96)
        lay.addWidget(self._cli_preview)
        return frame

    def _build_button_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("DialogFooter")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 8, 16, 12)
        lay.setSpacing(8)

        self._reset_btn = QPushButton("重置默认")
        self._reset_btn.setObjectName("Ghost")
        self._reset_btn.clicked.connect(self._on_reset)
        lay.addWidget(self._reset_btn)

        lay.addStretch()

        close = QPushButton("关闭")
        close.setObjectName("Outline")
        close.clicked.connect(self.close)
        lay.addWidget(close)

        self._save_btn = QPushButton("保存为默认")
        self._save_btn.setObjectName("Primary")
        icons.set_button_icon(self._save_btn, "mdi6.content-save",
                              color=icons.TONE_ON_ACCENT, size=14)
        self._save_btn.clicked.connect(self._on_save_defaults)
        lay.addWidget(self._save_btn)
        return bar

    def _row(self, label: str, widget: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        lbl = QLabel(label)
        lbl.setObjectName("MutedSmall")
        lbl.setFixedWidth(80)
        row.addWidget(lbl)
        row.addWidget(widget, stretch=1)
        return row

    # ── load / persist ──────────────────────────────────────────────────────────

    def _load_from_settings(self) -> None:
        qs = self._qs
        self._path_edit.setText(str(qs.value(_K_HELICON_EXE, "") or ""))
        self._params.set_params({
            "method": int(qs.value(_K_HELICON_METHOD, _DEFAULT_METHOD)),
            "radius": float(qs.value(_K_HELICON_RADIUS, _DEFAULT_RADIUS)),
            "smoothing": int(qs.value(_K_HELICON_SMOOTHING, _DEFAULT_SMOOTHING)),
        })
        fmt = str(qs.value(_K_HELICON_OUTPUT_FORMAT, "tif"))
        self._output_format_combo.setCurrentIndex(1 if fmt == "jpg" else 0)
        tiff = str(qs.value(_K_HELICON_TIFF_COMPRESSION, "u"))
        self._tiff_compression_combo.setCurrentIndex(
            _TIFF_VALS.index(tiff) if tiff in _TIFF_VALS else 0)
        run = str(qs.value(_K_HELICON_RUN_MODE, "silent"))
        self._run_mode_combo.setCurrentIndex(
            _RUN_VALS.index(run) if run in _RUN_VALS else 0)
        self._concurrency_spin.setValue(
            max(1, min(8, int(qs.value(_K_HELICON_CONCURRENCY, 1)))))
        self._save_depth_map_chk.setChecked(_as_bool(qs.value(_K_HELICON_SAVE_DEPTH_MAP, "false")))

    def _on_save_defaults(self) -> None:
        qs = self._qs
        p = self._params.get_params()
        qs.setValue(_K_HELICON_METHOD, int(p["method"]))
        qs.setValue(_K_HELICON_RADIUS, int(round(float(p["radius"]))))
        qs.setValue(_K_HELICON_SMOOTHING, int(p["smoothing"]))
        qs.setValue(_K_HELICON_OUTPUT_FORMAT,
                    "jpg" if self._output_format_combo.currentIndex() == 1 else "tif")
        qs.setValue(_K_HELICON_TIFF_COMPRESSION,
                    _TIFF_VALS[self._tiff_compression_combo.currentIndex()])
        qs.setValue(_K_HELICON_RUN_MODE,
                    _RUN_VALS[self._run_mode_combo.currentIndex()])
        qs.setValue(_K_HELICON_CONCURRENCY, self._concurrency_spin.value())
        qs.setValue(_K_HELICON_SAVE_DEPTH_MAP,
                    "true" if self._save_depth_map_chk.isChecked() else "false")
        try:
            self.ctx.settings.sync()
        except Exception:
            pass

    def _on_reset(self) -> None:
        self._params.set_params({
            "method": _DEFAULT_METHOD,
            "radius": _DEFAULT_RADIUS,
            "smoothing": _DEFAULT_SMOOTHING,
        })
        self._refresh_cli_preview()

    # ── path detection ──────────────────────────────────────────────────────────

    def _on_browse(self) -> None:
        path = ui.get_open_file_name(self, "选择 HeliconFocus 可执行文件", "",
                                     "可执行文件 (*.exe);;所有文件 (*)")
        if path:
            self._path_edit.setText(path)
            self._on_save_path()

    def _on_save_path(self) -> None:
        custom = self._path_edit.text().strip()
        self._qs.setValue(_K_HELICON_EXE, custom)
        try:
            self.ctx.settings.sync()
        except Exception:
            pass
        # Feed detection + compose: same pattern as project_settings_drawer.
        if custom:
            os.environ["HELICON_FOCUS_PATH"] = custom
        self._detect_and_refresh()

    def _on_clear_path(self) -> None:
        self._path_edit.clear()
        self._qs.setValue(_K_HELICON_EXE, "")
        os.environ.pop("HELICON_FOCUS_PATH", None)
        try:
            self.ctx.settings.sync()
        except Exception:
            pass
        self._detect_and_refresh()

    def _detect_and_refresh(self) -> None:
        from app.services.helicon_service import detect_helicon, reset_helicon_cache
        try:
            reset_helicon_cache()
            exe = detect_helicon()
        except Exception:
            exe = None
        if exe:
            self._status_label.setText("✓ 已检测到 Helicon Focus")
            self._effective_label.setText(f"生效路径：{exe}")
        else:
            self._status_label.setText("⚠ 未检测到 Helicon Focus")
            self._effective_label.setText("未检测到 —— 合成功能不可用，请填写自定义路径。")
        self._refresh_cli_preview()

    # ── CLI preview ─────────────────────────────────────────────────────────────

    def _refresh_cli_preview(self) -> None:
        from app.services.helicon_service import build_helicon_args, detect_helicon
        p = self._params.get_params()
        fmt_jpg = self._output_format_combo.currentIndex() == 1
        run_mode = _RUN_VALS[self._run_mode_combo.currentIndex()]
        quality = None
        tiff_comp = None
        out_ext = "tif"
        if fmt_jpg:
            out_ext = "jpg"
            quality = int(self._qs.value(_K_HELICON_QUALITY, 95))
        else:
            tiff_comp = _TIFF_VALS[self._tiff_compression_combo.currentIndex()]
        args = build_helicon_args(
            jpg_paths=["<所选 JPG 目录>"],
            output=f"<输出>/stack.{out_ext}",
            method=str(p["method"]),
            radius=str(p["radius"]),
            smoothing=str(p["smoothing"]),
            quality=quality,
            tiff_compression=tiff_comp,
            save_depth_map=self._save_depth_map_chk.isChecked(),
            silent=(run_mode == "silent"),
        )
        try:
            exe = detect_helicon() or "HeliconFocus.exe"
        except Exception:
            exe = "HeliconFocus.exe"
        cmd = " ".join([shlex.quote(exe)] + args)
        self._cli_preview.setPlainText(cmd)

    # ── slots ───────────────────────────────────────────────────────────────────

    def _on_adv_toggled(self, checked: bool) -> None:
        self._adv_body.setVisible(checked)
        self._adv_toggle.setText("▼ 收起" if checked else "▶ 展开")
