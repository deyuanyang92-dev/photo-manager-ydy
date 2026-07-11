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

    def _restore_target_dir(self) -> str:
        """还原 JPG 的默认落点 = 当前工作区的待处理区(``incoming-jpg/``)。

        用户裁定(2026-07-10):合成错了要撤回时,原片就该回到它出发的地方 ——
        待处理区,直接重新出现在「待处理照片」里可以再合成。不再每次问路。
        无当前项目时返回 ""(调用方退回目录选择框)。
        """
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            return ""
        try:
            incoming, _results = self._resolve_capture_subdirs()
        except Exception:
            incoming = "incoming-jpg"
        target = os.path.join(str(project_dir), incoming)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError:
            return ""
        return target

    def _owning_group_for_zip(self, zip_path: str):
        """按 ZIP 绝对路径找它登记在哪个编号的哪个组;找不到返回 (None, None, None)。"""
        db = getattr(self.ctx, "get_db", lambda: None)()
        if not db or not zip_path:
            return None, None, None
        try:
            target = str(Path(zip_path).resolve())
        except OSError:
            target = str(zip_path)
        try:
            from app.services.grouping_service import load_grouping

            rows = db.execute("SELECT DISTINCT uid FROM grouping").fetchall()
            for row in rows:
                uid = str(row[0])
                grouping = load_grouping(db, uid)
                for g in grouping.groups:
                    zp = str(getattr(g, "archive_zip", "") or "")
                    if not zp:
                        continue
                    try:
                        zp = str(Path(zp).resolve())
                    except OSError:
                        pass
                    if zp == target:
                        return uid, grouping, g
        except Exception:
            pass
        return None, None, None

    def _on_restore_archive(self, zip_path: str) -> None:
        """还原原片。

        语义(用户 2026-07-10 裁定):点组内 ZIP 的「还原原片」= **撤销这次整理**。
        原片回到待处理区之后, ZIP 若还挂在成果区就是状态矛盾(同一批 JPG 既在
        待处理又在归档里, 看起来像什么都没发生)。所以:

        · ZIP 属于某编号的组 → 走「撤销整理」(_on_undo_organise):
          JPG 还原回原位 + 成功后直接删除项目内 ZIP +
          组退回 composed → 成果区那行 ZIP 消失, TIF 保留(红线:母版不动)。
        · 孤儿 ZIP(不属于任何组, 如外部拷入) → 旧行为:只抽副本到待处理区,
          ZIP 原地不动(additive, 重活在 RestoreWorker 线程)。
        """
        from app.utils import ui
        from app.workers.restore_worker import RestoreWorker

        if not zip_path or not Path(zip_path).is_file():
            ui.warn(self, "还原原片", "归档文件不存在。")
            return

        uid, grouping, group = self._owning_group_for_zip(zip_path)
        if group is not None and list(getattr(group, "jpg_paths", []) or []):
            undo = getattr(self, "_on_undo_organise", None)
            if callable(undo):
                undo(uid, grouping, group)
                return
        # 组存在但没记录原 JPG 路径(如改绑挂上来的成果):抽副本到待处理区,
        # 成功后同样清 ZIP 登记 + 删除项目内 ZIP + 组回 composed(在 _on_restore_finished
        # 消费; 失败绝不动登记 —— 原片没回来时归档记录不能丢)。
        self._pending_restore_finalize = (
            (uid, int(getattr(group, "group_index", 0)), zip_path)
            if group is not None else None
        )

        # §7 旧: 每次都弹目录选择框 + 目录非空再问一次是否覆盖 ——
        # out = ui.get_existing_directory(self, "选择还原 JPG 的输出文件夹")
        # if not out:
        #     return
        # overwrite = False
        # try:
        #     if any(True for _ in os.scandir(out)):
        #         reply = ui.question(self, "目标文件夹非空", "...是否覆盖?")
        #         overwrite = (reply == QMessageBox.StandardButton.Yes)
        # except Exception:
        #     pass
        out = self._restore_target_dir()
        if not out:
            # 无当前项目 → 无处可还原, 才退回让用户指定目录。
            out = ui.get_existing_directory(self, "选择还原 JPG 的输出文件夹")
            if not out:
                return
        # 同名 JPG 一律跳过, 不覆盖(还原是「加回原片」, 绝不动已有文件)。
        overwrite = False

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

    def _finalize_pending_restore(self) -> str:
        """抽副本成功后撤登记，并删除已经完成还原的项目内 ZIP。"""
        pending = getattr(self, "_pending_restore_finalize", None)
        self._pending_restore_finalize = None
        if not pending:
            return ""
        uid, group_index, zip_path = pending
        db = getattr(self.ctx, "get_db", lambda: None)()
        if not db:
            return ""
        try:
            from app.services.grouping_service import (
                _utc_now_iso,
                load_grouping,
                save_grouping,
            )

            grouping = load_grouping(db, uid)
            target = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if target is None:
                return ""
            target.archive_zip = None
            target.status = "composed"
            target.updated_at = _utc_now_iso()
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
        # §7 旧: except Exception: return ""
        #   -> 原片已经从 ZIP 抽回磁盘了, 撤登记却没写进库, 返回 "" 又被调用方
        #      (_on_restore_finished)直接忽略 —— 界面照样弹「已还原 N 张 JPG」,
        #      可库里那组还标着「已归档」。用户以为回来了, 下次打开又是归档态。
        # 新(Fable 5, 2026-07-12): 失败必须喊出来, 并说清楚"文件回来了但状态没存上"。
        except Exception as exc:  # noqa: BLE001
            from app.utils import ui as _ui
            _ui.warn(
                self,
                "还原原片",
                "原片已还原到磁盘，但「撤销归档登记」没能写入数据库。\n"
                "重开工作区可能仍显示为已归档，请重试或检查数据库是否被占用。\n\n"
                f"详情：{exc}",
            )
            return ""
        retire = getattr(self, "_retire_zip", None)
        removed = ""
        if callable(retire):
            try:
                removed = retire(zip_path)
            except OSError as exc:
                from app.utils import ui as _ui
                _ui.warn(
                    self,
                    "还原原片",
                    "原片已恢复且状态已更新，但项目内 ZIP 删除失败，请手动检查。\n\n"
                    f"详情：{exc}",
                )
        # 按当前显示模式重载成果区(传 grouping=None 会刷成空列表)
        after = getattr(self, "_after_binding_changed", None)
        if callable(after):
            try:
                after(uid=uid, grouping=grouping)
            except Exception:
                pass
        return removed

    def _on_restore_finished(self, result) -> None:
        from app.utils import ui
        if result is None:
            self._pending_restore_finalize = None
            ui.critical(self, "还原原片", "还原过程出现错误。")
            return
        if not getattr(result, "ok", False):
            # 失败绝不撤登记: 原片没回来, 归档记录必须保留。
            self._pending_restore_finalize = None
            reason = getattr(result, "reason", "") or "；".join(result.failures[:3])
            ui.critical(self, "还原失败", reason or "还原失败，未输出文件。")
            return
        self._finalize_pending_restore()

        msg = f"已还原 {result.count} 张 JPG →\n{result.output_dir}"
        if result.skipped:
            msg += f"\n已跳过 {len(result.skipped)} 个已存在文件。"
        if result.failures:
            msg += f"\n{len(result.failures)} 个失败：" + "；".join(result.failures[:3])
        ui.info(self, "还原完成", msg)
        # 还原回待处理区后立刻重扫,否则用户看不到原片「回来了」。
        refresh = getattr(self, "_refresh_monitor", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                pass

    def _on_restore_failed(self, message: str) -> None:
        from app.utils import ui
        ui.critical(self, "还原原片", f"还原失败: {message}")
