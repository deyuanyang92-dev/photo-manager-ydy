"""calibration_dialog.py — 出版底图控制点校准对话框.

官方审图号图是投影图（经纬网弯曲）。用户在底图上点击已知经纬网交点、输入其经纬度，
累积控制点后用最小二乘拟合 经纬度→像素 变换（geo_calibration），实时显示残差 RMS，
保存为图片旁 sidecar（basemap_registry）。

交互：点图 → 像素坐标进入「待添加」→ 填经纬度 → 添加 → 重复 ≥3(仿射)/≥6(二次)。
逻辑方法 add_control_point / refit / save 与 UI 解耦，便于单测。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from app.services import basemap_registry as br
from app.services import geo_calibration as gc
from app.utils import ui

_MIN_FOR_ORDER = {1: 3, 2: 6}


class CalibrationDialog(QDialog):
    """控制点校准。校准成功 accept() 后，调用方读 self.model / 已写好的 sidecar。"""

    def __init__(self, image_path: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._img_path = Path(image_path)
        self._control_points: list[tuple] = []   # (lon, lat, px, py)
        self.model: Optional[dict] = None
        self._pending_px: Optional[float] = None
        self._pending_py: Optional[float] = None

        self.setWindowTitle(f"校准底图 — {self._img_path.stem}")
        self.resize(900, 640)
        self._build_ui()
        self._load_image()
        ui.center_on(self, parent)

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        # 左：图（可点击）
        self._fig = Figure(constrained_layout=True)
        self._canvas = FigureCanvasQTAgg(self._fig)
        self._canvas.mpl_connect("button_press_event", self._on_canvas_click)
        root.addWidget(self._canvas, 3)

        # 右：控制面板
        side = QWidget()
        v = QVBoxLayout(side)
        v.setSpacing(8)

        hint = QLabel(
            "校准 = 告诉程序「图上某像素 = 某经纬度」，之后站位才能精确落点。\n"
            "① 点击底图上一个已知经纬度的点：经纬网交点、城市标志（如北京★ 116.4,39.9）、\n"
            "   或明显的海岸/岛屿。\n"
            "② 在右侧输入该点真实经纬度。\n"
            "③ 「添加控制点」。重复 ≥3 点(仿射)；投影弯曲大可点 ≥6 点选「二次」。\n"
            "④ 看残差 RMS 越小越准 → 「保存校准」。"
        )
        hint.setWordWrap(True)
        v.addWidget(hint)

        self._pending_lbl = QLabel("待添加像素：—")
        v.addWidget(self._pending_lbl)

        row = QHBoxLayout()
        self._lon = QDoubleSpinBox()
        self._lon.setRange(-180.0, 180.0)
        self._lon.setDecimals(4)
        self._lon.setPrefix("经 ")
        self._lat = QDoubleSpinBox()
        self._lat.setRange(-90.0, 90.0)
        self._lat.setDecimals(4)
        self._lat.setPrefix("纬 ")
        row.addWidget(self._lon)
        row.addWidget(self._lat)
        v.addLayout(row)

        self._btn_add = QPushButton("添加控制点")
        self._btn_add.clicked.connect(self._on_add_clicked)
        v.addWidget(self._btn_add)

        self._list = QListWidget()
        v.addWidget(self._list, 1)

        self._btn_del = QPushButton("删除选中点")
        self._btn_del.clicked.connect(self._on_delete_selected)
        v.addWidget(self._btn_del)

        order_row = QHBoxLayout()
        order_row.addWidget(QLabel("拟合："))
        self._order_combo = QComboBox()
        self._order_combo.addItem("仿射 (≥3 点)", 1)
        self._order_combo.addItem("二次 (≥6 点，吸收弯曲)", 2)
        self._order_combo.currentIndexChanged.connect(lambda _i: self.refit())
        order_row.addWidget(self._order_combo, 1)
        v.addLayout(order_row)

        self._rms_lbl = QLabel("残差 RMS：—")
        v.addWidget(self._rms_lbl)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self._btn_save = QPushButton("保存校准")
        self._btn_save.setDefault(True)
        self._btn_save.clicked.connect(self.save)
        btns.addWidget(cancel)
        btns.addWidget(self._btn_save)
        v.addLayout(btns)

        root.addWidget(side, 2)

    def _load_image(self) -> None:
        raster = br.resolve_image_path(
            {"source": str(self._img_path), "ext": self._img_path.suffix.lower()}
        )
        self._fig.clear()
        self._ax = self._fig.add_subplot(111)
        if raster:
            try:
                import matplotlib.image as mpimg
                self._ax.imshow(mpimg.imread(raster))
            except Exception:
                self._ax.text(0.5, 0.5, "底图无法载入", ha="center", va="center")
        self._ax.axis("off")
        self._redraw_markers()

    # ── 逻辑（可单测）────────────────────────────────────────────────────────────

    def add_control_point(self, lon: float, lat: float, px: float, py: float) -> None:
        self._control_points.append((float(lon), float(lat), float(px), float(py)))
        self._refresh_list()
        self._redraw_markers()

    def refit(self) -> None:
        """按所选阶数拟合；点数不足该阶则降级到仿射，再不足则 model=None。"""
        order = self._order_combo.currentData() if hasattr(self, "_order_combo") else 1
        n = len(self._control_points)
        if order == 2 and n < _MIN_FOR_ORDER[2]:
            order = 1
        if n < _MIN_FOR_ORDER[1]:
            self.model = None
            self._update_rms()
            return
        try:
            self.model = gc.fit(self._control_points, order=order)
        except ValueError:
            self.model = None
        self._update_rms()

    def save(self) -> None:
        if self.model is None:
            self.refit()
        if self.model is None:
            ui.warn(self, "校准", "控制点不足，至少需要 3 个点（仿射）。")
            return
        br.save_calibration(
            self._img_path, self.model,
            [list(cp) for cp in self._control_points],
        )
        self.accept()

    # ── UI 事件 ───────────────────────────────────────────────────────────────

    def _on_canvas_click(self, event) -> None:
        if event.inaxes is None or event.xdata is None:
            return
        self._pending_px = float(event.xdata)
        self._pending_py = float(event.ydata)
        self._pending_lbl.setText(f"待添加像素：({self._pending_px:.0f}, {self._pending_py:.0f})")
        self._redraw_markers()

    def _on_add_clicked(self) -> None:
        if self._pending_px is None:
            ui.warn(self, "校准", "请先点击底图选取一个像素位置。")
            return
        self.add_control_point(self._lon.value(), self._lat.value(),
                               self._pending_px, self._pending_py)
        self._pending_px = self._pending_py = None
        self._pending_lbl.setText("待添加像素：—")
        self.refit()

    def _on_delete_selected(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(self._control_points):
            del self._control_points[row]
            self._refresh_list()
            self._redraw_markers()
            self.refit()

    def _refresh_list(self) -> None:
        self._list.clear()
        for lon, lat, px, py in self._control_points:
            self._list.addItem(f"经{lon:.3f} 纬{lat:.3f}  →  px({px:.0f},{py:.0f})")

    def _update_rms(self) -> None:
        if self.model is None:
            self._rms_lbl.setText(
                f"残差 RMS：— （已有 {len(self._control_points)} 点，需 ≥3）"
            )
        else:
            self._rms_lbl.setText(
                f"残差 RMS：{self.model['rms_px']:.2f} px"
                f"（{len(self._control_points)} 点，{self.model['order']} 阶）"
            )

    def _redraw_markers(self) -> None:
        ax = getattr(self, "_ax", None)
        if ax is None:
            return
        # 移除旧标注（保留 imshow）
        for art in list(ax.lines) + list(ax.texts):
            art.remove()
        for i, (_lon, _lat, px, py) in enumerate(self._control_points, 1):
            ax.plot(px, py, marker="+", color="#d33", markersize=12, mew=2)
            ax.annotate(str(i), (px, py), color="#d33", fontsize=9,
                        xytext=(4, 4), textcoords="offset points")
        if self._pending_px is not None:
            ax.plot(self._pending_px, self._pending_py, marker="x",
                    color="#2563eb", markersize=12, mew=2)
        self._canvas.draw_idle()
