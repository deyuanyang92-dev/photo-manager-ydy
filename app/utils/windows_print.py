"""Bridge label pages from the WSL Qt application to Windows printing.

Qt running inside WSL cannot enumerate or address the host's Windows printer
queue.  This module renders the existing label jobs to lossless PNG pages and
hands them to a small Windows Forms helper.  The helper uses the normal Windows
PrintDialog, including printer selection, Properties, page range and copies.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtGui import QColor, QImage, QPainter

from app.utils.label_core import effective_page_mm, plan_label_pages
from app.utils.label_render import render_label_onto


_POWERSHELL = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
_HELPER = Path(__file__).with_name("windows_print_dialog.ps1")


def is_available() -> bool:
    """Whether this process can invoke the Windows printing bridge."""
    # GUI tests intentionally exercise the portable Qt fallback and must never
    # open a real host-side modal print dialog.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return bool(os.environ.get("WSL_DISTRO_NAME")) and _POWERSHELL.exists() and _HELPER.exists()


def _windows_path(path: Path) -> str:
    proc = subprocess.run(
        ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def windows_printer_names() -> list[str]:
    """Return printers installed in Windows, not the empty WSL/CUPS list."""
    if not is_available():
        return []
    script = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
        "@(Get-Printer | Sort-Object Name | ForEach-Object Name) | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            [str(_POWERSHELL), "-NoProfile", "-Command", script],
            capture_output=True,
            check=True,
            timeout=10,
        )
        raw = proc.stdout.decode("utf-8-sig", errors="replace").strip()
        value = json.loads(raw) if raw else []
        if isinstance(value, str):
            value = [value]
        return sorted({str(name).strip() for name in value if str(name).strip()})
    except Exception:
        return []


def windows_default_printer_name() -> str:
    """Return the Windows user's default printer name."""
    if not is_available():
        return ""
    script = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
        "$p=Get-CimInstance Win32_Printer | Where-Object Default | Select-Object -First 1;"
        "if($p){$p.Name}"
    )
    try:
        proc = subprocess.run(
            [str(_POWERSHELL), "-NoProfile", "-Command", script],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return proc.stdout.decode("utf-8-sig", errors="replace").strip()
    except Exception:
        return ""


def _page_size(job: dict) -> tuple[float, float]:
    dims = job.get("dims") or {}
    paper_type = str(job.get("paperType") or "label")
    if paper_type in {"a4", "a5"}:
        return effective_page_mm(job.get("paper"), paper_type, job.get("gridOpts") or {})
    return float(dims.get("w", 60)), float(dims.get("h", 40))


def render_jobs_to_pages(
    jobs: list[dict],
    output_dir: Path,
    *,
    dpi: int = 300,
    cut_marks: bool = False,
    draw_crop_marks: Optional[Callable] = None,
) -> list[dict]:
    """Render print jobs to lossless PNG pages plus physical page dimensions."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict] = []
    px_per_mm = dpi / 25.4

    for job in (j for j in jobs if j and (j.get("items") or [])):
        items = job.get("items") or []
        dims = job.get("dims") or {}
        tmpl = job.get("template") or {}
        paper_type = str(job.get("paperType") or "label")
        w_mm, h_mm = _page_size(job)
        placements = plan_label_pages(
            items, dims, paper_type, job.get("paper"), job.get("gridOpts") or {}
        )
        page_count = max((int(p["page"]) for p in placements), default=-1) + 1

        for local_page in range(page_count):
            image = QImage(
                max(1, round(w_mm * px_per_mm)),
                max(1, round(h_mm * px_per_mm)),
                QImage.Format.Format_ARGB32,
            )
            image.fill(QColor("white"))
            painter = QPainter(image)
            for placement in placements:
                if int(placement["page"]) != local_page or not placement.get("data"):
                    continue
                scale = float(placement.get("scale", 1.0))
                x_off = int(float(placement["x_mm"]) * px_per_mm)
                y_off = int(float(placement["y_mm"]) * px_per_mm)
                render_label_onto(
                    painter,
                    tmpl,
                    dims,
                    placement["data"],
                    px_per_mm=px_per_mm * scale,
                    x_off=float(x_off),
                    y_off=float(y_off),
                    placeholder=False,
                    fill_bg=True,
                )
                if (
                    paper_type in {"a4", "a5"}
                    and cut_marks
                    and draw_crop_marks is not None
                ):
                    draw_crop_marks(
                        painter,
                        x_off,
                        y_off,
                        int(float(dims.get("w", 60)) * scale * px_per_mm),
                        int(float(dims.get("h", 40)) * scale * px_per_mm),
                        arm=int(2 * px_per_mm),
                        gap=int(0.5 * px_per_mm),
                    )
            painter.end()
            page_path = output_dir / f"page-{len(pages) + 1:04d}.png"
            if not image.save(str(page_path), "PNG"):
                raise RuntimeError(f"无法生成打印页面：{page_path.name}")
            pages.append({"path": str(page_path), "width_mm": w_mm, "height_mm": h_mm})
    return pages


def print_jobs_with_windows_dialog(
    jobs: list[dict],
    *,
    document_name: str = "标本标签",
    printer_name: str = "",
    show_dialog: bool = True,
    cut_marks: bool = False,
    draw_crop_marks: Optional[Callable] = None,
) -> tuple[bool, str]:
    """Show Windows PrintDialog and print; returns ``(printed, printer_name)``."""
    if not is_available():
        return False, ""
    with tempfile.TemporaryDirectory(prefix="specimen-print-") as tmp:
        tmp_path = Path(tmp)
        pages = render_jobs_to_pages(
            jobs,
            tmp_path,
            cut_marks=cut_marks,
            draw_crop_marks=draw_crop_marks,
        )
        if not pages:
            return False, ""
        manifest = tmp_path / "print-job.json"
        manifest.write_text(
            json.dumps(
                {
                    "document_name": document_name,
                    "pages": [dict(page, path=_windows_path(Path(page["path"]))) for page in pages],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        command = [
            str(_POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", _windows_path(_HELPER),
            "-ManifestPath", _windows_path(manifest),
        ]
        if printer_name:
            command += ["-PrinterName", printer_name]
        if not show_dialog:
            command += ["-NoDialog"]
        proc = subprocess.run(command, capture_output=True)
        stdout = proc.stdout.decode("utf-8-sig", errors="replace").strip()
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8-sig", errors="replace").strip()
            raise RuntimeError(stderr or stdout or "Windows 打印失败")
        if stdout.startswith("PRINTED|"):
            return True, stdout.partition("|")[2].strip()
        return False, ""
