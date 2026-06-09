"""basemap_registry.py — 采集地图出版底图的发现 / 校准持久化 / EPS 栅格化.

底图来源：
  - 用户目录（默认仓库 `地图/`）的图片与 EPS —— 官方审图号世界/中国图等；
  - 随包栅格底图 `resources/geo/basemaps/`（Phase C，PlateCarree 已知 bounds）；
  - OSM 交互瓦片（沿用 v1 的 TileMapWidget）；
  - 程序生成投影底图（Phase C，pyproj + Natural Earth）。

每张图片底图的控制点校准（经纬度→像素）存为图片旁 `<image>.calib.json`，跨项目复用。
EPS 经 Ghostscript(`gs`) 栅格化为 PNG 缓存后再显示/导出；无 `gs` 时降级。

纯文件系统 + subprocess，无 Qt，可单测。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

# 识别为图片底图的扩展名。
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
_VECTOR_EXTS = {".eps", ".ps", ".pdf"}
_BASEMAP_EXTS = _IMAGE_EXTS | _VECTOR_EXTS
# 栅格优先级：显示底图优选栅格，jpg/png 先于 tif/bmp。
_RASTER_PREF = [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"]


def default_map_dir() -> Path:
    """仓库根下的 `地图/` 目录（用户放官方底图处）。"""
    return Path(__file__).resolve().parents[2] / "地图"


def bundled_basemap_dir() -> Path:
    """随包栅格底图目录（Phase C）。"""
    return Path(__file__).resolve().parents[2] / "resources" / "geo" / "basemaps"


# ── 发现 ───────────────────────────────────────────────────────────────────────

def _entry_for_group(stem: str, files: list[Path], id_prefix: str) -> dict:
    """把同名（同 stem）的栅格 + 矢量文件合成一条底图条目。

    source = 显示用栅格（jpg/png… 优先）；矢量(eps) 存 vector 备高 DPI 导出。
    都无栅格（只有 eps）时 source = 矢量本身（显示时经 gs 栅格化）。
    """
    rasters = [p for p in files if p.suffix.lower() in _IMAGE_EXTS]
    vectors = [p for p in files if p.suffix.lower() in _VECTOR_EXTS]
    rasters.sort(key=lambda p: _RASTER_PREF.index(p.suffix.lower())
                 if p.suffix.lower() in _RASTER_PREF else 99)
    display = rasters[0] if rasters else vectors[0]
    vector = vectors[0] if vectors else None
    return {
        "id": f"{id_prefix}:{stem}",
        "name": stem,
        "kind": "image",
        "source": str(display),
        "ext": display.suffix.lower(),
        "vector": str(vector) if vector else None,
    }


def _scan_dir(d: Path, id_prefix: str) -> list[dict]:
    """扫目录，按 stem 分组成底图条目（不递归）。目录不存在 → []。"""
    if not d.is_dir():
        return []
    groups: dict[str, list[Path]] = {}
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix.lower() in _BASEMAP_EXTS:
            groups.setdefault(p.stem, []).append(p)
    return [_entry_for_group(stem, files, id_prefix)
            for stem, files in sorted(groups.items())]


def list_user_basemaps(user_dir: Optional[Path]) -> list[dict]:
    """枚举用户目录下的图片/EPS 底图条目（按图名去重）。目录不存在 → []。"""
    if user_dir is None:
        user_dir = default_map_dir()
    return _scan_dir(Path(user_dir), "image")


def list_bundled_basemaps() -> list[dict]:
    """枚举随包底图（resources/geo/basemaps/，按图名去重）。"""
    return _scan_dir(bundled_basemap_dir(), "bundled")


def list_basemaps(user_dir: Optional[Path] = None) -> list[dict]:
    """全部底图条目：OSM 交互 + 用户图 + 随包栅格。

    生成投影底图（kind='generated'）在 Phase C 由 PublicationMapWidget 自带预设提供。
    """
    entries: list[dict] = [
        {"id": "osm", "name": "交互地图 (OSM)", "kind": "osm", "source": "", "ext": "",
         "vector": None},
    ]
    # 内置优先；用户目录同名图不再重复列出
    seen: set[str] = set()
    for e in list_bundled_basemaps() + list_user_basemaps(user_dir):
        if e["name"] in seen:
            continue
        seen.add(e["name"])
        entries.append(e)
    # 程序生成的 Nature/R 风格底图（投影精确、免校准）
    for g in list_generated_basemaps():
        g.setdefault("source", "")
        g.setdefault("vector", None)
        entries.append(g)
    return entries


def list_generated_basemaps() -> list[dict]:
    """程序生成底图预设（Natural Earth + pyproj）。"""
    from app.services import geo_basemap as gb
    return gb.generated_presets()


# ── 校准 sidecar ───────────────────────────────────────────────────────────────

def calib_path(image_path: Path) -> Path:
    """图片旁的校准文件路径 `<image>.calib.json`。"""
    image_path = Path(image_path)
    return image_path.with_name(image_path.name + ".calib.json")


def save_calibration(image_path: Path, model: dict, control_points: list) -> None:
    """写校准 sidecar：拟合模型 + 原始控制点（供以后重新编辑）。"""
    data = {"model": model, "control_points": control_points}
    calib_path(image_path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_calibration(image_path: Path) -> Optional[dict]:
    """读校准 sidecar；不存在或损坏 → None。"""
    p = calib_path(image_path)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def is_calibrated(image_path: Path) -> bool:
    return calib_path(image_path).is_file()


# ── EPS 栅格化 ─────────────────────────────────────────────────────────────────

def _gs_command(eps: Path, out_png: Path, dpi: int = 200) -> list[str]:
    """Ghostscript EPS→PNG 命令行。"""
    gs = shutil.which("gs") or "gs"
    return [
        gs, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dEPSCrop",
        "-sDEVICE=png16m", f"-r{dpi}",
        f"-sOutputFile={out_png}", str(eps),
    ]


def _default_cache_dir() -> Path:
    """EPS 栅格化缓存目录（系统临时目录下，不污染源目录/资源目录）。"""
    return Path(tempfile.gettempdir()) / "photo_platform_basemap_cache"


def rasterize_eps(eps: Path, cache_dir: Optional[Path] = None, dpi: int = 200) -> Optional[str]:
    """把 EPS 栅格化为 PNG，返回 PNG 路径；缺 `gs` 或失败 → None。

    缓存到 *cache_dir*（默认系统临时目录），命中（PNG 比 EPS 新）则跳过重转。
    """
    eps = Path(eps)
    if shutil.which("gs") is None:
        return None
    cdir = Path(cache_dir) if cache_dir else _default_cache_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    out_png = cdir / (eps.stem + f".r{dpi}.png")
    if out_png.is_file() and out_png.stat().st_mtime >= eps.stat().st_mtime:
        return str(out_png)
    try:
        subprocess.run(_gs_command(eps, out_png, dpi), check=True,
                       capture_output=True, timeout=120)
    except (subprocess.SubprocessError, OSError):
        return None
    return str(out_png) if out_png.is_file() else None


def resolve_image_path(entry: dict, cache_dir: Optional[Path] = None,
                       dpi: int = 200) -> Optional[str]:
    """把底图条目解析为一个可被 PIL/matplotlib 载入的栅格路径。

    栅格图原样返回；EPS/PS/PDF 经 `gs` 栅格化。失败 → None。
    """
    src = Path(entry.get("source", ""))
    if not src.is_file():
        return None
    ext = src.suffix.lower()
    if ext in _IMAGE_EXTS:
        return str(src)
    if ext in _VECTOR_EXTS:
        return rasterize_eps(src, cache_dir=cache_dir, dpi=dpi)
    return None
