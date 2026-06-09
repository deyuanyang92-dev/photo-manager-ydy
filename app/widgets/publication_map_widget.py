"""publication_map_widget.py — 出版底图画布（matplotlib 嵌入 Qt）.

采集地图的「出版模式」：在选定底图上画聚合站位点，并导出论文级图（PNG/PDF/SVG/EPS）。
matplotlib 是统一的渲染 + 导出引擎（savefig 一次拿到各格式，免手写 QPainter/QPrinter）。

底图两类：
  - image  ：用户官方审图号图 / 随包栅格。imshow 底图 + 用控制点校准模型把经纬度→像素散点。
  - generated：Natural Earth + pyproj 投影底图（Phase C）。
未校准的 image 底图只画底图本身（提示需校准）。

站位样式由 set_style 注入（Phase B 的 MarkerStylePanel），默认值见 _DEFAULT_STYLE。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

import matplotlib
matplotlib.use("QtAgg")  # 与 PyQt6 兼容的 Agg 后端
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from app.services import basemap_registry as br
from app.services import geo_calibration as gc

# ── 中文字体（出版图标题/标签需 CJK 字形，否则 matplotlib 画方框）──────────────
_CJK_FAMILIES = [
    "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "Source Han Sans SC",
    "WenQuanYi Micro Hei", "Heiti SC", "SimHei", "Arial Unicode MS",
]
_CJK_FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc", "/Library/Fonts/Arial Unicode.ttf",
]


def _configure_cjk_font() -> Optional[str]:
    """注册一个可用的中文字体并设为 matplotlib 默认 sans。无则返回 None。"""
    from matplotlib import font_manager as fm, rcParams
    avail = {f.name for f in fm.fontManager.ttflist}
    fam = next((n for n in _CJK_FAMILIES if n in avail), None)
    if fam is None:
        # 随包字体优先（resources/fonts/*.ttf|ttc），再系统路径兜底
        bundled = Path(__file__).resolve().parents[2] / "resources" / "fonts"
        paths = [str(p) for p in sorted(bundled.glob("*.tt[fc]"))] + _CJK_FONT_PATHS
        for p in paths:
            if Path(p).is_file():
                try:
                    fm.fontManager.addfont(p)
                    fam = fm.FontProperties(fname=p).get_name()
                    break
                except Exception:
                    continue
    if fam:
        rcParams["font.sans-serif"] = [fam] + list(rcParams.get("font.sans-serif", []))
        rcParams["font.family"] = "sans-serif"
    rcParams["axes.unicode_minus"] = False
    return fam


_CJK_FAMILY = _configure_cjk_font()

# 站位默认样式（Phase B 面板可覆盖）。matplotlib marker 记号。
_DEFAULT_STYLE: dict = {
    "shape": "o",          # o 圆 / ^ 三角 / s 方 / * 星 / v 倒三角
    "size": 80,            # 散点面积 (pt^2)
    "fill": "#29b9ab",     # 填充色
    "edge": "#ffffff",     # 描边色
    "edge_width": 1.2,
    "alpha": 0.9,
    "show_label": False,
    "label_source": "label",   # label / count / none
    "label_size": 9,
    "label_color": "#17212b",
}

# 记号别名 → matplotlib marker
_SHAPE_MAP = {"圆": "o", "三角": "^", "方": "s", "星": "*", "倒三角": "v",
              "o": "o", "^": "^", "s": "s", "*": "*", "v": "v"}


class PublicationMapWidget(QWidget):
    """matplotlib 出版底图画布（支持鼠标滚轮缩放 + 拖拽平移）。"""

    zoom_changed = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entry: Optional[dict] = None
        self._calib_model: Optional[dict] = None
        self._points: list[dict] = []
        self._style: dict = dict(_DEFAULT_STYLE)
        self._img_cache: Optional[tuple[str, object]] = None  # (path, ndarray)
        self._with_points: bool = True

        # ── zoom / pan state ──
        self._ax: Optional[object] = None
        self._home_xlim: Optional[tuple[float, float]] = None
        self._home_ylim: Optional[tuple[float, float]] = None
        self._view_xlim: Optional[tuple[float, float]] = None
        self._view_ylim: Optional[tuple[float, float]] = None
        self._pan_start: Optional[tuple[float, float]] = None

        self._fig = Figure(figsize=(8, 5), constrained_layout=True)
        self._canvas = FigureCanvasQTAgg(self._fig)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._canvas)

        # matplotlib event connections
        self._canvas.mpl_connect("scroll_event", self._on_mpl_scroll)
        self._canvas.mpl_connect("button_press_event", self._on_mpl_press)
        self._canvas.mpl_connect("motion_notify_event", self._on_mpl_motion)
        self._canvas.mpl_connect("button_release_event", self._on_mpl_release)

    # ── public API ────────────────────────────────────────────────────────────

    def set_basemap(self, entry: Optional[dict], calibration: Optional[dict] = None) -> None:
        """选底图。*calibration* = sidecar dict（含 'model'）或 None。"""
        self._entry = entry
        self._calib_model = (calibration or {}).get("model") if calibration else None
        # 底图切换 → 重置视口
        self._view_xlim = None
        self._view_ylim = None

    def set_points(self, points: list[dict]) -> None:
        self._points = list(points or [])

    def set_style(self, style: dict) -> None:
        self._style = {**_DEFAULT_STYLE, **(style or {})}

    # ── zoom / pan public API ────────────────────────────────────────────────

    def zoom_in(self) -> None:
        self._zoom_by(1.5)

    def zoom_out(self) -> None:
        self._zoom_by(1 / 1.5)

    def zoom_to_fit(self) -> None:
        """重置到初始视口（全部可见）。"""
        if self._ax is None or self._home_xlim is None:
            return
        self._view_xlim = self._home_xlim
        self._view_ylim = self._home_ylim
        self._ax.set_xlim(self._home_xlim)
        self._ax.set_ylim(self._home_ylim)
        self._canvas.draw_idle()
        self.zoom_changed.emit(1.0)

    def current_zoom(self) -> float:
        """缩放倍率（1.0 = 全图可见）。"""
        if self._home_xlim is None or self._view_xlim is None:
            return 1.0
        home_span = self._home_xlim[1] - self._home_xlim[0]
        view_span = self._view_xlim[1] - self._view_xlim[0]
        return home_span / view_span if view_span > 0 else 1.0

    def set_zoom_factor(self, factor: float) -> None:
        """直接设置缩放倍率（1.0 = 全图可见），保持当前视口中心。"""
        if self._ax is None or self._home_xlim is None:
            return
        factor = max(0.1, factor)
        xlim = self._ax.get_xlim()
        ylim = self._ax.get_ylim()
        cx = (xlim[0] + xlim[1]) / 2
        cy = (ylim[0] + ylim[1]) / 2
        hw = (self._home_xlim[1] - self._home_xlim[0]) / 2 / factor
        hh = (self._home_ylim[1] - self._home_ylim[0]) / 2 / factor
        new_xlim = (cx - hw, cx + hw)
        new_ylim = (cy - hh, cy + hh)
        self._ax.set_xlim(new_xlim)
        self._ax.set_ylim(new_ylim)
        self._view_xlim = new_xlim
        self._view_ylim = new_ylim
        self._canvas.draw_idle()
        self.zoom_changed.emit(factor)

    # ── rendering ────────────────────────────────────────────────────────────

    def render(self, with_points: bool = True) -> None:
        """重画整图。with_points=False → 仅底图（不画站位点）。"""
        self._with_points = with_points
        self._fig.clear()
        ax = self._fig.add_subplot(111)
        kind = (self._entry or {}).get("kind")
        if kind == "image":
            self._render_image(ax)
        elif kind == "generated":
            self._render_generated(ax)
        else:
            # osm / 无底图：仅散点占位
            ax.set_facecolor("#eef3f2")
            self._scatter_lonlat(ax)
            ax.set_aspect("equal", adjustable="datalim")

        # 保存初始视口
        self._ax = ax
        self._home_xlim = ax.get_xlim()
        self._home_ylim = ax.get_ylim()
        # 恢复之前的缩放/平移状态
        if self._view_xlim is not None:
            ax.set_xlim(self._view_xlim)
            ax.set_ylim(self._view_ylim)
        self._canvas.draw_idle()

    def export(self, path: str, fmt: Optional[str] = None, dpi: int = 300,
               with_points: bool = True) -> None:
        """导出当前图。fmt ∈ png/pdf/svg/eps；不传则按文件后缀。
        with_points=False → 导出纯底图（不含站位点）。"""
        self.render(with_points=with_points)
        if fmt is None:
            fmt = Path(path).suffix.lstrip(".").lower() or "png"
        self._fig.savefig(path, format=fmt, dpi=dpi, bbox_inches="tight")
        self.render(with_points=True)   # 恢复屏显

    # ── rendering helpers ──────────────────────────────────────────────────────

    def _load_image(self):
        """解析并缓存底图栅格数组（EPS 经 gs 栅格化）。失败 → None。"""
        entry = self._entry or {}
        raster = br.resolve_image_path(entry)
        if raster is None:
            return None
        if self._img_cache and self._img_cache[0] == raster:
            return self._img_cache[1]
        try:
            import matplotlib.image as mpimg
            arr = mpimg.imread(raster)
        except Exception:
            return None
        self._img_cache = (raster, arr)
        return arr

    def _render_image(self, ax) -> None:
        arr = self._load_image()
        if arr is None:
            ax.text(0.5, 0.5, "底图无法载入", ha="center", va="center",
                    transform=ax.transAxes)
            ax.axis("off")
            return
        ax.imshow(arr)            # origin='upper'：像素 (0,0) 在左上
        ax.axis("off")
        if self._calib_model is None:
            ax.set_title(
                "未校准 · 点「校准」标定经纬度，或改用『世界·Robinson』等生成底图（免校准、精确落点）",
                fontsize=9,
            )
            return
        self._scatter_pixels(ax)

    # ── generated kind (Nature/R 风) ───────────────────────────────────────────

    def _render_generated(self, ax) -> None:
        from app.services import geo_basemap as gb
        proj = (self._entry or {}).get("proj", "EPSG:4326")
        extent = (self._entry or {}).get("extent")
        detail = (self._entry or {}).get("detail", 110)

        ax.set_facecolor("#eaf2f6")          # 海洋
        # 陆地填充
        for ring in gb.load_geometries(f"ne_{detail}m_land"):
            if len(ring) < 3:
                continue
            xs, ys = gb.project_points(proj, [p[0] for p in ring], [p[1] for p in ring])
            ax.fill(xs, ys, facecolor="#e9e7e0", edgecolor="none", zorder=1)
        # 国界（细线，110m 即可）
        for ring in gb.load_geometries("ne_110m_admin_0_countries"):
            if len(ring) < 2:
                continue
            xs, ys = gb.project_points(proj, [p[0] for p in ring], [p[1] for p in ring])
            ax.plot(xs, ys, color="#b7b2a8", linewidth=0.4, zorder=2)
        # 海岸线
        for ring in gb.load_geometries(f"ne_{detail}m_coastline"):
            if len(ring) < 2:
                continue
            xs, ys = gb.project_points(proj, [p[0] for p in ring], [p[1] for p in ring])
            ax.plot(xs, ys, color="#6b7780", linewidth=0.6, zorder=3)
        # 经纬网
        self._draw_graticule(ax, proj, extent)
        # 站位散点（投影坐标）
        self._scatter_generated(ax, proj)

        if extent:
            xs, ys = gb.project_points(
                proj, [extent[0], extent[1], extent[0], extent[1]],
                [extent[2], extent[2], extent[3], extent[3]])
            ax.set_xlim(min(xs), max(xs))
            ax.set_ylim(min(ys), max(ys))
        ax.set_aspect("equal", adjustable="box")
        ax.set_axis_off()

    def _draw_graticule(self, ax, proj, extent) -> None:
        from app.services import geo_basemap as gb
        import numpy as np
        lon0, lon1, lat0, lat1 = extent if extent else (-180, 180, -85, 85)
        for lon in range(int(lon0 // 30 * 30), int(lon1) + 1, 30):
            lats = list(np.linspace(lat0, lat1, 60))
            xs, ys = gb.project_points(proj, [lon] * len(lats), lats)
            ax.plot(xs, ys, color="#c9c4ba", linewidth=0.3, zorder=2)
        for lat in range(int(lat0 // 30 * 30), int(lat1) + 1, 30):
            lons = list(np.linspace(lon0, lon1, 120))
            xs, ys = gb.project_points(proj, lons, [lat] * len(lons))
            ax.plot(xs, ys, color="#c9c4ba", linewidth=0.3, zorder=2)

    def _scatter_generated(self, ax, proj) -> None:
        import math
        from app.services import geo_basemap as gb
        pts = [p for p in self._points
               if p.get("lon") is not None and p.get("lat") is not None]
        if not pts:
            return
        xs, ys = gb.project_points(proj, [p["lon"] for p in pts], [p["lat"] for p in pts])
        # 过滤掉投影后 NaN/inf 的点
        valid = [(x, y, p) for x, y, p in zip(xs, ys, pts)
                 if math.isfinite(x) and math.isfinite(y)]
        if not valid:
            return
        xs, ys, pts = [v[0] for v in valid], [v[1] for v in valid], [v[2] for v in valid]
        self._do_scatter(ax, xs, ys, pts)

    def _scatter_pixels(self, ax) -> None:
        """用校准模型把经纬度映射到像素后散点。"""
        pts = [p for p in self._points if p.get("lon") is not None and p.get("lat") is not None]
        if not pts:
            return
        lons = [p["lon"] for p in pts]
        lats = [p["lat"] for p in pts]
        xs, ys = gc.project_many(self._calib_model, lons, lats)
        self._do_scatter(ax, xs, ys, pts)

    def _scatter_lonlat(self, ax) -> None:
        """无校准底图：直接按经纬度散点（仅占位/生成底图用）。"""
        pts = [p for p in self._points if p.get("lon") is not None and p.get("lat") is not None]
        if not pts:
            return
        xs = [p["lon"] for p in pts]
        ys = [p["lat"] for p in pts]
        self._do_scatter(ax, xs, ys, pts)

    def _do_scatter(self, ax, xs, ys, pts) -> None:
        if not getattr(self, "_with_points", True):
            return                       # 仅底图导出：不画站位点
        st = self._style
        marker = _SHAPE_MAP.get(st.get("shape", "o"), "o")
        ax.scatter(
            list(xs), list(ys),
            s=st["size"], c=st["fill"],
            edgecolors=st["edge"], linewidths=st["edge_width"],
            alpha=st["alpha"], marker=marker, zorder=5,
        )
        if st.get("show_label") and st.get("label_source") not in (None, "none"):
            from app.services.collection_record_service import marker_label
            src = st["label_source"]
            for x, y, p in zip(list(xs), list(ys), pts):
                txt = marker_label(p, src)
                if txt:
                    ax.annotate(txt, (x, y), textcoords="offset points", xytext=(6, 4),
                                fontsize=st["label_size"], color=st["label_color"], zorder=6)

    # ── zoom / pan internals ─────────────────────────────────────────────────

    def _on_mpl_scroll(self, event) -> None:
        """鼠标滚轮缩放（以光标为中心）。"""
        if self._ax is None or event.inaxes != self._ax:
            return
        if event.xdata is None or event.ydata is None:
            return
        factor = 1.3 if event.button == "up" else 1 / 1.3
        xlim = self._ax.get_xlim()
        ylim = self._ax.get_ylim()
        xdata, ydata = event.xdata, event.ydata
        new_xlim = (xdata - (xdata - xlim[0]) / factor,
                    xdata + (xlim[1] - xdata) / factor)
        new_ylim = (ydata - (ydata - ylim[0]) / factor,
                    ydata + (ylim[1] - ydata) / factor)
        self._ax.set_xlim(new_xlim)
        self._ax.set_ylim(new_ylim)
        self._view_xlim = new_xlim
        self._view_ylim = new_ylim
        self._canvas.draw_idle()
        self.zoom_changed.emit(self.current_zoom())

    def _on_mpl_press(self, event) -> None:
        if event.button != 1 or self._ax is None or event.inaxes != self._ax:
            return
        if event.xdata is None or event.ydata is None:
            return
        self._pan_start = (event.xdata, event.ydata)

    def _on_mpl_motion(self, event) -> None:
        if self._pan_start is None:
            return
        if event.inaxes != self._ax or event.xdata is None or event.ydata is None:
            return
        dx = self._pan_start[0] - event.xdata
        dy = self._pan_start[1] - event.ydata
        xlim = self._ax.get_xlim()
        ylim = self._ax.get_ylim()
        new_xlim = (xlim[0] + dx, xlim[1] + dx)
        new_ylim = (ylim[0] + dy, ylim[1] + dy)
        self._ax.set_xlim(new_xlim)
        self._ax.set_ylim(new_ylim)
        self._view_xlim = new_xlim
        self._view_ylim = new_ylim
        self._canvas.draw_idle()

    def _on_mpl_release(self, event) -> None:
        self._pan_start = None

    def _zoom_by(self, factor: float) -> None:
        if self._ax is None:
            return
        xlim = self._ax.get_xlim()
        ylim = self._ax.get_ylim()
        cx = (xlim[0] + xlim[1]) / 2
        cy = (ylim[0] + ylim[1]) / 2
        hw = (xlim[1] - xlim[0]) / 2 / factor
        hh = (ylim[1] - ylim[0]) / 2 / factor
        new_xlim = (cx - hw, cx + hw)
        new_ylim = (cy - hh, cy + hh)
        self._ax.set_xlim(new_xlim)
        self._ax.set_ylim(new_ylim)
        self._view_xlim = new_xlim
        self._view_ylim = new_ylim
        self._canvas.draw_idle()
        self.zoom_changed.emit(self.current_zoom())
