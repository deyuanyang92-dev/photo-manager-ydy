"""tiff_jpeg_export_service.py — batch TIFF → JPEG export (read-only on masters).

Used by the standalone 「TIFF 转 JPG」 toolbox page.  Source TIFF files are
never modified or deleted; only new ``.jpg`` sidecars (or mirrored outputs)
are written.

Smart mode inspects dimensions / file size / bit depth and picks quality +
resize limits similar to Photoshop's export slider + 「存储为 Web」 presets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional

TIFF_SUFFIXES = {".tif", ".tiff"}


class OverwritePolicy(str, Enum):
    SKIP = "skip"
    OVERWRITE = "overwrite"
    RENAME = "rename"


@dataclass(frozen=True)
class TiffJpegExportSettings:
    quality: int = 90
    max_long_edge: int | None = None
    subsampling: int = 1
    progressive: bool = False
    optimize: bool = True
    preserve_exif: bool = False
    preserve_icc_profile: bool = False
    preserve_dpi: bool = False
    alpha_background: tuple[int, int, int] = (255, 255, 255)

    def clamped(self) -> "TiffJpegExportSettings":
        q = max(1, min(100, int(self.quality)))
        edge = self.max_long_edge
        if edge is not None and int(edge) <= 0:
            edge = None
        elif edge is not None:
            edge = max(64, int(edge))
        sub = max(0, min(2, int(self.subsampling)))
        bg = tuple(max(0, min(255, int(c))) for c in self.alpha_background[:3])
        if len(bg) != 3:
            bg = (255, 255, 255)
        return TiffJpegExportSettings(
            quality=q,
            max_long_edge=edge,
            subsampling=sub,
            progressive=bool(self.progressive),
            optimize=bool(self.optimize),
            preserve_exif=bool(self.preserve_exif),
            preserve_icc_profile=bool(self.preserve_icc_profile),
            preserve_dpi=bool(self.preserve_dpi),
            alpha_background=bg,
        )


@dataclass(frozen=True)
class ExportPreset:
    preset_id: str
    label: str
    settings: TiffJpegExportSettings
    smart: bool = False


EXPORT_PRESETS: tuple[ExportPreset, ...] = (
    ExportPreset("smart", "智能推荐", TiffJpegExportSettings(), smart=True),
    ExportPreset(
        "web",
        "网页分享",
        TiffJpegExportSettings(quality=82, max_long_edge=1920, subsampling=2),
    ),
    ExportPreset(
        "general",
        "通用平衡",
        TiffJpegExportSettings(quality=90, max_long_edge=4096, subsampling=1),
    ),
    ExportPreset(
        "archive",
        "高清存档",
        TiffJpegExportSettings(quality=95, max_long_edge=None, subsampling=0),
    ),
    ExportPreset(
        "max",
        "最高质量",
        TiffJpegExportSettings(
            quality=100,
            max_long_edge=None,
            subsampling=0,
            progressive=True,
            optimize=False,
        ),
    ),
)


@dataclass
class TiffAnalysis:
    path: str
    width: int
    height: int
    long_edge: int
    megapixels: float
    file_size: int
    mode: str
    bits_per_sample: int
    has_alpha: bool

    @property
    def summary(self) -> str:
        return (
            f"{self.width}×{self.height} · {self.megapixels:.1f}MP · "
            f"{self.file_size / (1024 * 1024):.1f} MB · {self.mode}"
        )


@dataclass
class ConversionResult:
    source: str
    output: str
    width: int
    height: int
    bytes_written: int
    settings: TiffJpegExportSettings
    metadata_preserved: list[str] = field(default_factory=list)


@dataclass
class BatchExportResult:
    converted: list[ConversionResult] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return len(self.converted)

    @property
    def total(self) -> int:
        return self.ok_count + len(self.skipped) + len(self.failed)


ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


def is_tiff_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in TIFF_SUFFIXES


def analyze_tiff(path: str) -> Optional[TiffAnalysis]:
    """Lightweight metadata read; does not decode full raster."""
    p = Path(path)
    if not p.is_file() or not is_tiff_path(p):
        return None
    try:
        from PIL import Image

        with Image.open(p) as im:
            w, h = im.size
            mode = im.mode or ""
            bits = im.bits if hasattr(im, "bits") else 8
            if callable(bits):
                bits = bits()
            has_alpha = "A" in mode or mode in {"RGBA", "LA", "PA"}
            file_size = p.stat().st_size
            long_edge = max(w, h)
            return TiffAnalysis(
                path=str(p.resolve()),
                width=int(w),
                height=int(h),
                long_edge=int(long_edge),
                megapixels=(w * h) / 1_000_000.0,
                file_size=int(file_size),
                mode=str(mode),
                bits_per_sample=int(bits or 8),
                has_alpha=bool(has_alpha),
            )
    except Exception:
        return None


def recommend_export_settings(analysis: TiffAnalysis) -> TiffJpegExportSettings:
    """Pick quality / resize / chroma subsampling from image stats."""
    edge = analysis.long_edge
    size_mb = analysis.file_size / (1024 * 1024)
    mp = analysis.megapixels
    high_bit = analysis.bits_per_sample > 8

    if edge >= 8000 or size_mb >= 120 or mp >= 80:
        return TiffJpegExportSettings(
            quality=88,
            max_long_edge=4096,
            subsampling=1,
            optimize=True,
        )
    if edge >= 6000 or size_mb >= 60 or mp >= 40:
        return TiffJpegExportSettings(
            quality=90,
            max_long_edge=4096,
            subsampling=1,
            optimize=True,
        )
    if edge >= 4000 or size_mb >= 25 or mp >= 16:
        return TiffJpegExportSettings(
            quality=92,
            max_long_edge=4096 if edge > 4096 else None,
            subsampling=0 if high_bit else 1,
            optimize=True,
        )
    if edge >= 2500 or mp >= 6:
        return TiffJpegExportSettings(
            quality=93,
            max_long_edge=None,
            subsampling=0,
            optimize=True,
        )
    return TiffJpegExportSettings(
        quality=95,
        max_long_edge=None,
        subsampling=0,
        optimize=True,
    )


def settings_for_preset(preset_id: str, analysis: TiffAnalysis | None = None) -> TiffJpegExportSettings:
    for preset in EXPORT_PRESETS:
        if preset.preset_id == preset_id:
            if preset.smart and analysis is not None:
                return recommend_export_settings(analysis)
            return preset.settings.clamped()
    return TiffJpegExportSettings().clamped()


def subsampling_for_quality(quality: int, manual: int | None = None) -> int:
    if manual is not None:
        return max(0, min(2, int(manual)))
    q = max(1, min(100, int(quality)))
    if q >= 95:
        return 0
    if q >= 85:
        return 1
    return 2


def collect_tiff_paths(
    sources: Iterable[str],
    *,
    recursive: bool = True,
) -> list[str]:
    """Expand files / folders into a sorted unique TIFF path list."""
    found: dict[str, None] = {}
    for raw in sources:
        p = Path(raw)
        if not p.exists():
            continue
        if p.is_file():
            if is_tiff_path(p):
                found[str(p.resolve())] = None
            continue
        if not p.is_dir():
            continue
        iterator = p.rglob("*") if recursive else p.glob("*")
        for child in iterator:
            if child.is_file() and is_tiff_path(child):
                found[str(child.resolve())] = None
    return sorted(found.keys())


def resolve_jpeg_output_path(
    source: str | Path,
    *,
    output_dir: str | Path | None = None,
    source_root: str | Path | None = None,
) -> Path:
    src = Path(source)
    stem = src.with_suffix(".jpg")
    if output_dir is None:
        return stem
    out_root = Path(output_dir)
    if source_root is not None:
        try:
            rel = src.resolve().relative_to(Path(source_root).resolve())
            return out_root / rel.with_suffix(".jpg")
        except ValueError:
            pass
    return out_root / stem.name


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path).casefold()


def _unique_reserved_path(path: Path, reserved: set[str], *, avoid_existing: bool) -> Path:
    if _path_key(path) not in reserved and (not avoid_existing or not path.exists()):
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if _path_key(candidate) not in reserved and (not avoid_existing or not candidate.exists()):
            return candidate
        n += 1


def allocate_jpeg_output_paths(
    sources: Iterable[str],
    *,
    output_dir: str | None = None,
    source_root: str | None = None,
    overwrite: OverwritePolicy = OverwritePolicy.OVERWRITE,
) -> dict[str, Path]:
    """Resolve one unique output path per source for the current batch."""
    avoid_existing = overwrite is OverwritePolicy.RENAME
    reserved: set[str] = set()
    allocated: dict[str, Path] = {}
    for source in sources:
        base = resolve_jpeg_output_path(
            source,
            output_dir=output_dir,
            source_root=source_root,
        )
        dest = _unique_reserved_path(base, reserved, avoid_existing=avoid_existing)
        reserved.add(_path_key(dest))
        allocated[source] = dest
    return allocated


def _prepare_output_path(
    dest: Path,
    *,
    policy: OverwritePolicy,
) -> tuple[Optional[Path], Optional[str]]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        return dest, None
    if policy is OverwritePolicy.SKIP:
        return None, "目标 JPG 已存在"
    if policy is OverwritePolicy.RENAME:
        return _unique_path(dest), None
    return dest, None


def _load_pil_image(path: str, max_long_edge: int | None) -> "object":
    from PIL import Image

    with Image.open(path) as im:
        source_info = _source_image_info(im)
        im.load()
        image = im.copy()
        image.info.update(source_info)
    if max_long_edge and max(image.size) > max_long_edge:
        image.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
    return image


def _load_via_thumbnail_fallback(path: str, max_long_edge: int | None) -> "object":
    import tempfile

    from PIL import Image

    from app.utils.image_thumbnail import decode_image_data

    edge = max_long_edge or 8192
    qimg = decode_image_data(path, max_size=edge, use_cache=False)
    if qimg is None or qimg.isNull():
        raise RuntimeError("无法解码 TIFF")
    qimg = qimg.convertToFormat(qimg.Format.Format_RGB888)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        if not qimg.save(tmp_path, "PNG"):
            raise RuntimeError("无法解码 TIFF")
        with Image.open(tmp_path) as im:
            image = im.copy()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    if max_long_edge and max(image.size) > max_long_edge:
        image.thumbnail((max_long_edge, max_long_edge), Image.Resampling.LANCZOS)
    return image


def _to_rgb(image, background: tuple[int, int, int]) -> "object":
    from PIL import Image

    if image.mode in {"RGB", "L"}:
        return image.convert("RGB")
    if image.mode in {"RGBA", "LA", "PA"}:
        bg = Image.new("RGB", image.size, background)
        alpha = image.split()[-1]
        bg.paste(image.convert("RGB"), mask=alpha)
        return bg
    if image.mode == "CMYK":
        return image.convert("RGB")
    return image.convert("RGB")


def _jpeg_metadata_kwargs(source_info: dict, cfg: TiffJpegExportSettings) -> tuple[dict, list[str]]:
    kwargs: dict = {}
    preserved: list[str] = []
    if cfg.preserve_exif:
        exif = source_info.get("exif")
        if exif:
            kwargs["exif"] = exif
            preserved.append("EXIF")
    if cfg.preserve_icc_profile:
        icc = source_info.get("icc_profile")
        if icc:
            kwargs["icc_profile"] = icc
            preserved.append("ICC")
    if cfg.preserve_dpi:
        dpi = source_info.get("dpi")
        if dpi:
            kwargs["dpi"] = dpi
            preserved.append("DPI")
    return kwargs, preserved


def _source_image_info(image) -> dict:
    info = dict(getattr(image, "info", {}) or {})
    if not info.get("exif"):
        try:
            exif = image.getexif()
            if exif:
                info["exif"] = exif.tobytes()
        except Exception:
            pass
    return info


def _with_preservation_options(
    base: TiffJpegExportSettings,
    options: TiffJpegExportSettings,
) -> TiffJpegExportSettings:
    return TiffJpegExportSettings(
        quality=base.quality,
        max_long_edge=base.max_long_edge,
        subsampling=base.subsampling,
        progressive=base.progressive,
        optimize=base.optimize,
        preserve_exif=options.preserve_exif,
        preserve_icc_profile=options.preserve_icc_profile,
        preserve_dpi=options.preserve_dpi,
        alpha_background=options.alpha_background,
    ).clamped()


def convert_tiff_to_jpeg(
    source: str,
    dest: str,
    settings: TiffJpegExportSettings,
    *,
    overwrite: OverwritePolicy = OverwritePolicy.OVERWRITE,
) -> ConversionResult:
    """Write one JPEG next to (or under) a TIFF master.  Source is read-only."""
    src = Path(source)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    cfg = settings.clamped()
    out_path, skip_reason = _prepare_output_path(Path(dest), policy=overwrite)
    if out_path is None:
        raise FileExistsError(skip_reason or "已跳过")

    before_stat = src.stat()
    source_info: dict = {}
    try:
        try:
            image = _load_pil_image(str(src), cfg.max_long_edge)
            source_info = _source_image_info(image)
        except Exception:
            image = _load_via_thumbnail_fallback(str(src), cfg.max_long_edge)
            source_info = _source_image_info(image)
        rgb = _to_rgb(image, cfg.alpha_background)
        metadata_kwargs, metadata_preserved = _jpeg_metadata_kwargs(source_info, cfg)
        rgb.save(
            str(out_path),
            format="JPEG",
            quality=cfg.quality,
            subsampling=cfg.subsampling,
            progressive=cfg.progressive,
            optimize=cfg.optimize,
            **metadata_kwargs,
        )
    finally:
        after_stat = src.stat()
        if (
            after_stat.st_size != before_stat.st_size
            or after_stat.st_mtime_ns != before_stat.st_mtime_ns
        ):
            raise RuntimeError("源 TIFF 被意外修改")

    written = out_path.stat().st_size
    w, h = rgb.size
    return ConversionResult(
        source=str(src.resolve()),
        output=str(out_path.resolve()),
        width=int(w),
        height=int(h),
        bytes_written=int(written),
        settings=cfg,
        metadata_preserved=metadata_preserved,
    )


def batch_convert_tiffs(
    sources: list[str],
    settings: TiffJpegExportSettings,
    *,
    output_dir: str | None = None,
    source_root: str | None = None,
    recursive: bool = True,
    overwrite: OverwritePolicy = OverwritePolicy.OVERWRITE,
    smart: bool = False,
    max_workers: int = 4,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
) -> BatchExportResult:
    paths = collect_tiff_paths(sources, recursive=recursive)
    result = BatchExportResult()
    total = len(paths)
    if total == 0:
        return result

    def _settings_for(path: str) -> TiffJpegExportSettings:
        cfg = settings.clamped()
        if smart:
            analysis = analyze_tiff(path)
            if analysis is not None:
                cfg = _with_preservation_options(
                    recommend_export_settings(analysis),
                    cfg,
                )
        return cfg

    output_paths = allocate_jpeg_output_paths(
        paths,
        output_dir=output_dir,
        source_root=source_root,
        overwrite=overwrite,
    )

    def _convert_one(path: str) -> tuple[str, str, ConversionResult | None, str]:
        if cancel_callback and cancel_callback():
            return ("cancel", path, None, "用户取消")
        dest = output_paths[path]
        try:
            conv = convert_tiff_to_jpeg(
                path,
                str(dest),
                _settings_for(path),
                overwrite=overwrite,
            )
            return ("ok", path, conv, "")
        except FileExistsError as exc:
            return ("skip", path, None, str(exc))

    workers = max(1, min(4, int(max_workers)))
    if workers == 1 or total == 1:
        for idx, path in enumerate(paths, start=1):
            if cancel_callback and cancel_callback():
                result.skipped.append((path, "用户取消"))
                break
            if progress_callback:
                progress_callback(idx, total, Path(path).name)
            try:
                kind, src, conv, msg = _convert_one(path)
                if kind == "ok" and conv is not None:
                    result.converted.append(conv)
                elif kind == "skip":
                    result.skipped.append((src, msg or "目标 JPG 已存在"))
                elif kind == "cancel":
                    result.skipped.append((src, msg or "用户取消"))
            except Exception as exc:
                result.failed.append((path, str(exc)))
        return result

    from concurrent.futures import ThreadPoolExecutor, as_completed

    cancelled = False
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_convert_one, path): path for path in paths}
        for fut in as_completed(futures):
            if cancel_callback and cancel_callback():
                cancelled = True
                break
            path = futures[fut]
            completed += 1
            if progress_callback:
                progress_callback(completed, total, Path(path).name)
            try:
                kind, src, conv, msg = fut.result()
                if kind == "ok" and conv is not None:
                    result.converted.append(conv)
                elif kind == "skip":
                    result.skipped.append((src, msg or "目标 JPG 已存在"))
                elif kind == "cancel":
                    result.skipped.append((src, msg or "用户取消"))
            except Exception as exc:
                result.failed.append((path, str(exc)))
        if cancelled:
            for fut in futures:
                if not fut.done():
                    fut.cancel()
            result.skipped.append(("", "用户取消"))
    return result


def format_batch_export_report(result: BatchExportResult) -> str:
    lines = [
        "TIFF 转 JPG 转换报告",
        f"成功: {len(result.converted)}",
        f"跳过: {len(result.skipped)}",
        f"失败: {len(result.failed)}",
        "",
    ]
    if result.converted:
        lines.append("[成功]")
        for item in result.converted:
            meta = ",".join(item.metadata_preserved) if item.metadata_preserved else "无"
            lines.append(
                f"{item.source} -> {item.output} "
                f"({item.width}x{item.height}, {item.bytes_written} bytes, metadata={meta})"
            )
        lines.append("")
    if result.skipped:
        lines.append("[跳过]")
        for source, reason in result.skipped:
            lines.append(f"{source} | {reason}")
        lines.append("")
    if result.failed:
        lines.append("[失败]")
        for source, reason in result.failed:
            lines.append(f"{source} | {reason}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
