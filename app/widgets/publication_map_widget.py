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
    """matplotlib 出版底图画布。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._entry: Optional[dict] = None
        self._calib_model: Optional[dict] = None
        self._points: list[dict] = []
        self._style: dict = dict(_DEFAULT_STYLE)
        self._img_cache: Optional[tuple[str, object]] = None  # (path, ndarray)

        self._fig = Figure(figsize=(8, 5), constrained_layout=True)
        self._canvas = FigureCanvasQTAgg(self._fig)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._canvas)

    # ── public API ────────────────────────────────────────────────────────────

    def set_basemap(self, entry: Optional[dict], calibration: Optional[dict] = None) -> None:
        """选底图。*calibration* = sidecar dict（含 'model'）或 None。"""
        self._entry = entry
        self._calib_model = (calibration or {}).get("model") if calibration else None

    def set_points(self, points: list[dict]) -> None:
        self._points = list(points or [])

    def set_style(self, style: dict) -> None:
        self._style = {**_DEFAULT_STYLE, **(style or {})}

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
            ax.set_title("未校准 — 点击「校准」后才能精确落点", fontsize=10)
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
        from app.services import geo_basemap as gb
        pts = [p for p in self._points
               if p.get("lon") is not None and p.get("lat") is not None]
        if not pts:
            return
        xs, ys = gb.project_points(proj, [p["lon"] for p in pts], [p["lat"] for p in pts])
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
            src = st["label_source"]
            for x, y, p in zip(list(xs), list(ys), pts):
                txt = str(p.get("count") if src == "count" else p.get("label") or "")
                if txt:
                    ax.annotate(txt, (x, y), textcoords="offset points", xytext=(6, 4),
                                fontsize=st["label_size"], color=st["label_color"], zorder=6)
