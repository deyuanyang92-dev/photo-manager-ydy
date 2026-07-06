"""Application-level label print execution.

Low-level page setup and painting stay in ``app.utils.label_print`` as stateless
helpers.  This module owns the higher-level print session: choosing the Windows
bridge or Qt dialog path, invoking the paint adapter, recording audit events,
and reporting failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget

from app.utils.label_print import build_printer, paint_jobs


@dataclass(frozen=True)
class LabelPrintResult:
    """Outcome of one print session."""

    printed: bool
    accepted: bool = True
    printer_name: str = ""
    error: str = ""


class LabelPrintExecutor:
    """Run label print jobs through the available platform print path.

    The public interface is intentionally small: callers pass already-built
    label print jobs plus a few session options.  The implementation hides the
    Windows bridge, Qt printer setup, modal dialog, paint adapter, and audit
    recording in one place.
    """

    def __init__(
        self,
        *,
        ctx=None,
        parent: Optional[QWidget] = None,
        dialog_cls=None,
        build_printer_fn: Callable = build_printer,
        paint_jobs_fn: Callable = paint_jobs,
        windows_print_module=None,
        message_box=QMessageBox,
    ) -> None:
        self._ctx = ctx
        self._parent = parent
        self._dialog_cls = dialog_cls
        self._build_printer = build_printer_fn
        self._paint_jobs = paint_jobs_fn
        self._windows_print = windows_print_module
        self._message_box = message_box

    def print_with_dialog(
        self,
        jobs: list[dict],
        *,
        document_name: str = "标本标签",
        grid_opts: Optional[dict] = None,
        cut_marks: bool = False,
        draw_crop_marks: Optional[Callable] = None,
    ) -> LabelPrintResult:
        """Let the user choose a printer, then print all non-empty *jobs*."""
        printable_jobs = [job for job in jobs if job and (job.get("items") or [])]
        if not printable_jobs:
            return LabelPrintResult(printed=False, accepted=False)

        windows_print = self._windows_print
        if windows_print is None:
            from app.utils import windows_print as windows_print

        if windows_print.is_available():
            try:
                ok, printer_name = windows_print.print_jobs_with_windows_dialog(
                    printable_jobs,
                    document_name=document_name,
                    cut_marks=cut_marks,
                    draw_crop_marks=draw_crop_marks,
                )
            except Exception as exc:
                error = str(exc)
                self._message_box.critical(self._parent, "打印失败", error)
                return LabelPrintResult(printed=False, accepted=True, error=error)
            if ok:
                used = printer_name or "Windows 打印机"
                self._record_print_jobs(printable_jobs, used)
                return LabelPrintResult(printed=True, printer_name=used)
            return LabelPrintResult(printed=False, accepted=False)

        dialog_cls = self._dialog_cls
        if dialog_cls is None:
            from app.widgets.print_dialog import PrintJobDialog as dialog_cls

        dlg = dialog_cls(printable_jobs, self._parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return LabelPrintResult(printed=False, accepted=False)

        printer_name = str(dlg.selected_printer() or "")
        first_grid_opts = printable_jobs[0].get("gridOpts") or grid_opts
        printer = self._build_printer(printable_jobs[0], grid_opts=first_grid_opts)
        if printer_name:
            printer.setPrinterName(printer_name)

        ok = self._paint_jobs(
            printer,
            printable_jobs,
            grid_opts=grid_opts,
            cut_marks=cut_marks,
            draw_crop_marks=draw_crop_marks,
        )
        if not ok:
            return LabelPrintResult(printed=False, accepted=True)

        used = printer_name or printer.printerName() or "default"
        self._record_print_jobs(printable_jobs, used)
        return LabelPrintResult(printed=True, printer_name=used)

    def print_direct(
        self,
        jobs: list[dict],
        *,
        printer_name: str,
        document_name: str = "标本标签",
        grid_opts: Optional[dict] = None,
        cut_marks: bool = False,
        draw_crop_marks: Optional[Callable] = None,
    ) -> LabelPrintResult:
        """Print jobs to a known printer without showing a printer dialog."""
        printable_jobs = [job for job in jobs if job and (job.get("items") or [])]
        if not printable_jobs:
            return LabelPrintResult(printed=False, accepted=False)
        target = str(printer_name or "").strip()

        windows_print = self._windows_print
        if windows_print is None:
            from app.utils import windows_print as windows_print

        if windows_print.is_available():
            try:
                ok, used = windows_print.print_jobs_with_windows_dialog(
                    printable_jobs,
                    document_name=document_name,
                    printer_name=target,
                    show_dialog=False,
                    cut_marks=cut_marks,
                    draw_crop_marks=draw_crop_marks,
                )
            except Exception as exc:
                error = str(exc)
                self._message_box.critical(self._parent, "打印失败", error)
                return LabelPrintResult(printed=False, accepted=True, error=error)
            if not ok:
                return LabelPrintResult(printed=False, accepted=True)
            used = used or target or "Windows 打印机"
            self._record_print_jobs(printable_jobs, used)
            return LabelPrintResult(printed=True, printer_name=used)

        first_grid_opts = printable_jobs[0].get("gridOpts") or grid_opts
        printer = self._build_printer(printable_jobs[0], grid_opts=first_grid_opts)
        if target:
            printer.setPrinterName(target)
        ok = self._paint_jobs(
            printer,
            printable_jobs,
            grid_opts=grid_opts,
            cut_marks=cut_marks,
            draw_crop_marks=draw_crop_marks,
        )
        if not ok:
            return LabelPrintResult(printed=False, accepted=True)
        used = printer.printerName() or target or "default"
        self._record_print_jobs(printable_jobs, used)
        return LabelPrintResult(printed=True, printer_name=used)

    def _record_print_jobs(self, jobs: list[dict], printer_name: str) -> None:
        if self._ctx is None:
            return
        try:
            db = self._ctx.get_db()
        except Exception:
            db = None
        if db is None:
            return
        try:
            from app.services.activity_audit_service import (
                default_actor,
                record_print_jobs,
            )

            record_print_jobs(
                db,
                jobs,
                actor=default_actor(self._ctx),
                printer_name=printer_name,
            )
        except Exception:
            pass
