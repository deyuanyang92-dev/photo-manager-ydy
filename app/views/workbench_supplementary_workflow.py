"""Supplementary archive and restore workflow for WorkbenchView."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.compose_workflow_service import (
    SelectedComposeTarget as _SelectedComposeTarget,
    persist_composed_group,
)
from app.services.organize_workflow_service import (
    compose_batch_queue,
    inspect_organize_group,
    organize_batch_targets,
    plan_archive_worker,
    plan_organize_gate_check,
    prepare_existing_tiff_group,
)
from app.utils import ui
from app.widgets.compose_workbench_dialog import _ComposeWorkbenchDialog as _DefaultComposeWorkbenchDialog
from app.workers.helicon_worker import HeliconWorker


class WorkbenchSupplementaryWorkflowMixin:
    """Supplementary archive and restore workflow for WorkbenchView."""

    def _on_supplementary_process(self) -> None:
        """补处理 button clicked → consume the monitor selection."""
        from app.utils import ui
        paths = self._monitor.selected_all_paths()
        if not paths:
            ui.info(self, "补处理", "请先在监控区选择 JPG 原片与 TIFF 成片")
            return
        self._run_supplementary(paths)

    def _on_supplementary_dropped(self, paths: list) -> None:
        """Files dropped onto the 补处理 button → archive them directly."""
        if paths:
            self._run_supplementary(list(paths))

    def _supp_autoname_tiff_by_active(self, db, project_dir, paths: list) -> list:
        """补处理前的兜底：外部名 TIF + 有激活编号 → 自动按激活编号成果名改名。

        只在「TIF 文件名反查不到标本」且「有激活编号」时改名；TIF 名本就规范则原样。
        返回（可能已把 TIF 路径替换为新名后的）路径列表。
        """
        try:
            from app.services.supplementary_service import resolve_specimen_for_tiff
            from app.services.organize_service import organize_preview, rename_tiff
        except Exception:
            return paths
        tiffs = [p for p in paths if str(p).lower().endswith((".tif", ".tiff"))]
        if len(tiffs) != 1:
            return paths
        tiff = tiffs[0]
        try:
            if resolve_specimen_for_tiff(db, Path(tiff).name) is not None:
                return paths  # 名能反查 → 不动
        except Exception:
            return paths
        active = self._get_active_uid()
        if not active:
            return paths  # 无激活编号 → 维持原状(会在 validate 报命名不规范)
        try:
            inc, res = self._resolve_capture_subdirs()
            preview = organize_preview(
                db, active,
                os.path.join(project_dir, res),
                os.path.join(project_dir, inc),
            )
            new_path = rename_tiff(tiff, preview.suggested_tiff_name)
        except Exception:
            return paths
        return [new_path if p == tiff else p for p in paths]

    def _run_supplementary(self, paths: list) -> None:
        from app.services.supplementary_service import (
            validate_supp_group,
            SuppGroupError,
        )
        from app.workers.supp_compression_worker import SuppCompressionWorker
        from app.utils import ui

        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        has_project = bool(db and project_dir)

        if has_project:
            # 激活编号兜底命名：补处理本来只从 TIF 文件名反查标本；若 TIF 是外部名(反查
            # 不到)但当前有激活编号 → 自动按激活编号的成果名给 TIF 改名，再走补处理。
            # 落地"激活 → 自动命名"（用户设计），免得外部 Helicon 的 TIF 因名不规范被卡。
            paths = self._supp_autoname_tiff_by_active(db, project_dir, list(paths))

            # Validate selection → resolve specimen from TIFF name.
            try:
                grp = validate_supp_group(db, paths)
            except SuppGroupError as exc:
                ui.warn(self, "补处理", str(exc))
                return
        else:
            from app.services.supplementary_service import SuppGroup
            jpgs = [
                str(p) for p in paths
                if str(p).lower().endswith((".jpg", ".jpeg"))
            ]
            tiffs = [
                str(p) for p in paths
                if str(p).lower().endswith((".tif", ".tiff"))
            ]
            if len(jpgs) < 1 or len(tiffs) != 1 or len(jpgs) + len(tiffs) != len(paths):
                ui.warn(self, "补处理", "请选择至少 1 张 JPG 原片和 1 张 TIFF 成片后再整理")
                return
            grp = SuppGroup(jpg_paths=jpgs, tiff_path=tiffs[0], uid="", specimen=None)

        # Collision guard: project → results/；no project → TIFF 同目录。
        if has_project:
            _inc, res = self._resolve_capture_subdirs()
            results_dir = Path(project_dir) / res
        else:
            results_dir = Path(grp.tiff_path).parent
        tiff_stem = Path(grp.tiff_path).stem
        existing_zip = results_dir / f"{tiff_stem}.zip"
        existing_tiff = results_dir / Path(grp.tiff_path).name
        if existing_zip.is_file() or (
            existing_tiff.is_file()
            and str(existing_tiff) != str(Path(grp.tiff_path))
        ):
            reply = ui.question(
                self,
                "归档文件已存在",
                f"{results_dir} 下已存在同名成果：\n{tiff_stem}.*\n\n是否覆盖并重新归档？",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        delete_jpg: bool = True
        try:
            delete_jpg = bool(
                getattr(self.ctx.settings, "delete_jpg_after_archive", True)
            )
        except Exception:
            pass

        # Stash for the finished handler (move-to-results + UI refresh).
        self._supp_pending = grp
        # Two-phase deletion (same as the organize path): the worker archives
        # with delete_jpg=False; loose JPGs are only removed AFTER
        # finalize_supplementary_archive succeeds, via
        # commit_jpg_deletion_after_archive. If finalize fails, JPGs survive.
        self._supp_request_delete_jpg = delete_jpg
        self._supp_worker = SuppCompressionWorker(
            grp.jpg_paths,
            grp.tiff_path,
            project_dir or str(results_dir),
            delete_jpg=False,
            method=getattr(self.ctx.settings, "jxl_effort_method", "standard"),
            concurrency=getattr(self.ctx.settings, "jxl_concurrency", 4),
            output_dir=str(results_dir),
            parent=self,
        )
        self._supp_task_key = f"supplementary:{tiff_stem}:{id(self._supp_worker)}"
        self._workflow_notice(
            "补处理：准备整理",
            f"正在把 {len(grp.jpg_paths)} 张 JPG 写入 ZIP：{tiff_stem}.zip",
            state="busy",
            force_show=True,
            task_key=self._supp_task_key,
        )
        self._supp_worker.started_archiving.connect(self._on_supp_started)
        self._supp_worker.progress.connect(self._on_supp_progress)
        self._supp_worker.finished.connect(self._on_supp_finished)
        self._supp_worker.failed.connect(self._on_supp_failed)
        self._supp_worker.start()

    def _on_supp_started(self, jpg_count: int, tiff_stem: str) -> None:
        self._workflow_notice(
            "补处理：正在整理",
            f"正在归档 {jpg_count} 张原片 → {tiff_stem}.zip",
            state="busy",
            task_key=str(getattr(self, "_supp_task_key", "") or ""),
        )
        try:
            win = self.window()
            bar = win.statusBar() if hasattr(win, "statusBar") else None
            if bar is not None:
                bar.showMessage(f"正在归档 {jpg_count} 张原片 → {tiff_stem}.zip", 4000)
        except Exception:
            pass

    def _on_supp_progress(self, current: int, total: int, filename: str) -> None:
        self._workflow_notice(
            "补处理：正在整理",
            f"正在打包第 {current}/{total} 张 JPG：{filename}",
            state="busy",
            task_key=str(getattr(self, "_supp_task_key", "") or ""),
        )

    def _on_supp_finished(self, result) -> None:
        """Move TIFF + ZIP into results/ (decision①), then refresh + toast."""
        from app.utils import ui
        grp = getattr(self, "_supp_pending", None)
        task_key = str(getattr(self, "_supp_task_key", "") or "")
        self._supp_pending = None
        self._supp_task_key = ""
        project_dir = self.ctx.current_project_dir
        if not result or not getattr(result, "ok", False) or grp is None:
            self._workflow_notice(
                "补处理失败",
                "归档过程出现错误。",
                state="error",
                task_key=task_key,
            )
            ui.warn(self, "补处理", "归档过程出现错误。")
            return

        res = "results"
        if project_dir:
            _inc, res = self._resolve_capture_subdirs()
        try:
            from app.services.capture_workflow_service import finalize_supplementary_archive

            finalized = finalize_supplementary_archive(
                result,
                grp,
                project_dir=project_dir or "",
                results_subdir=res,
            )
        except Exception as exc:
            self._workflow_notice(
                "补处理失败",
                f"归档已生成，但成果移动失败：{exc}",
                state="error",
                task_key=task_key,
            )
            ui.warn(self, "补处理", f"归档已生成，但成果移动失败：{exc}")
            return

        # Phase 2 of the deletion: only now that finalize succeeded may loose
        # JPGs be removed (and only after the ZIP re-verifies at its final path).
        if getattr(self, "_supp_request_delete_jpg", False) and grp.jpg_paths:
            from app.services.archive_service import commit_jpg_deletion_after_archive

            # finalize may have moved the ZIP — re-verify at its final path.
            result.zip_path = finalized.zip_path
            result = commit_jpg_deletion_after_archive(
                result,
                list(grp.jpg_paths),
            )

        # Refresh monitor; refresh results column if the archived specimen is loaded.
        self._refresh_monitor()
        try:
            from app.services.grouping_service import load_grouping
            db = self.ctx.get_db()
            if db is not None and grp.uid and getattr(self, "_current_uid", None) == grp.uid:
                self._refresh_results_column(grp.uid, load_grouping(db, grp.uid))
        except Exception:
            pass
        if grp.uid:
            self._on_organize_finished(grp.uid)

        msg = (
            f"归档完成：{Path(finalized.zip_path).name}\n"
            "ZIP 内为原始 JPG，可直接解压使用。\n"
        )
        if result.delete_jpg:
            msg += "JPG 原片已删除。"
            finish_detail = (
                f"已生成 {Path(finalized.zip_path).name}；"
                f"{result.file_count} 张 JPG 已写入 ZIP 并从待处理区删除。"
            )
        elif result.requested_delete_jpg and not result.delete_jpg:
            msg += f"JPG 保留（{result.deletion_skipped_reason}）。"
            finish_detail = (
                f"已生成 {Path(finalized.zip_path).name}；JPG 已写入 ZIP，"
                f"但删除前校验未通过，文件保留：{result.deletion_skipped_reason}"
            )
        else:
            msg += "JPG 原片已保留。"
            finish_detail = (
                f"已生成 {Path(finalized.zip_path).name}；JPG 已写入 ZIP，"
                "按当前设置保留在磁盘，但不再作为待处理照片显示。"
            )
        self._workflow_notice(
            "补处理完成",
            finish_detail,
            state="success",
            task_key=task_key,
        )
        ui.info(self, "补处理完成", msg)

    def _on_supp_failed(self, message: str) -> None:
        from app.utils import ui
        task_key = str(getattr(self, "_supp_task_key", "") or "")
        self._supp_pending = None
        self._supp_task_key = ""
        self._workflow_notice(
            "补处理失败",
            message or "归档失败。",
            state="error",
            task_key=task_key,
        )
        ui.warn(self, "补处理", f"归档失败: {message}")

    # ── 还原归档 JPG ──────────────────────────────────────────────────────────

    def _on_restore_archive(self, zip_path: str) -> None:
        """Recover the original JPGs from a result ZIP into a user-chosen folder.

        Read-only against the archive + additive (writes new JPGs, deletes
        nothing). Heavy extraction/legacy decode work runs off-thread in RestoreWorker.
        """
        from app.utils import ui
        from PyQt6.QtWidgets import QMessageBox
        from app.workers.restore_worker import RestoreWorker

        if not zip_path or not Path(zip_path).is_file():
            ui.warn(self, "还原原片", "归档文件不存在。")
            return

        out = ui.get_existing_directory(self, "选择还原 JPG 的输出文件夹")
        if not out:
            return

        overwrite = False
        try:
            if any(True for _ in os.scandir(out)):  # 目录非空
                reply = ui.question(
                    self, "目标文件夹非空",
                    "目标文件夹已有文件。同名 JPG 是否覆盖？\n（选「否」则跳过已存在的文件）",
                )
                overwrite = (reply == QMessageBox.StandardButton.Yes)
        except Exception:
            pass

        count = 0
        try:
            import zipfile
            with zipfile.ZipFile(zip_path) as zf:
                count = sum(
                    1 for n in zf.namelist()
                    if Path(n).suffix.lower() in {".jpg", ".jpeg", ".jxl"}
                )
        except Exception:
            pass

        self._restore_worker = RestoreWorker(
            zip_path, out, overwrite=overwrite, file_count=count, parent=self
        )
        self._restore_worker.started.connect(self._on_restore_started)
        self._restore_worker.finished.connect(self._on_restore_finished)
        self._restore_worker.failed.connect(self._on_restore_failed)
        self._restore_worker.start()

    def _on_restore_started(self, count: int) -> None:
        try:
            bar = self.window().statusBar()
            if bar is not None:
                n = f"{count} 张" if count else "原片"
                bar.showMessage(f"正在还原 {n} JPG …", 4000)
        except Exception:
            pass

    def _on_restore_finished(self, result) -> None:
        from app.utils import ui
        if result is None:
            ui.critical(self, "还原原片", "还原过程出现错误。")
            return
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", "") or "；".join(result.failures[:3])
            ui.critical(self, "还原失败", reason or "还原失败，未输出文件。")
            return

        msg = f"已还原 {result.count} 张 JPG →\n{result.output_dir}"
        if result.skipped:
            msg += f"\n已跳过 {len(result.skipped)} 个已存在文件。"
        if result.failures:
            msg += f"\n{len(result.failures)} 个失败：" + "；".join(result.failures[:3])
        ui.info(self, "还原完成", msg)

    def _on_restore_failed(self, message: str) -> None:
        from app.utils import ui
        ui.critical(self, "还原原片", f"还原失败: {message}")
