"""workbench_monitor_workflow.py — Workbench monitor/import/TIFF check mixin."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QInputDialog, QMessageBox, QWidget

from app.services.compose_workflow_service import (
    detect_external_tiff_candidate,
    free_compose_output_name as _free_compose_output_name,
    pending_tiff_paths,
    resolve_external_tiff_jpg_source,
)
from app.utils import ui
from app.widgets.workbench_utility_dialogs import (
    _AutoGroupSourceDialog as _DefaultAutoGroupSourceDialog,
    _RetroactiveScanDialog as _DefaultRetroactiveScanDialog,
)


class WorkbenchMonitorWorkflowMixin:
    """Monitor scan, media import, free-compose, and TIFF naming helpers."""

    @staticmethod
    def _retroactive_scan_dialog_class():
        """Resolve via workbench_view re-export so existing tests can monkeypatch it."""
        try:
            from app.views import workbench_view
            return getattr(workbench_view, "_RetroactiveScanDialog")
        except Exception:
            return _DefaultRetroactiveScanDialog

    @staticmethod
    def _auto_group_source_dialog_class():
        """Resolve via workbench_view re-export so existing tests can monkeypatch it."""
        try:
            from app.views import workbench_view
            return getattr(workbench_view, "_AutoGroupSourceDialog")
        except Exception:
            return _DefaultAutoGroupSourceDialog

    # ── Monitor ───────────────────────────────────────────────────────────────

    def _refresh_monitor(self) -> None:
        """Re-scan the project directory and repopulate the monitor panel."""
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()
            return

        # Headless tests use mock/in-memory DBs, so keep their existing
        # synchronous semantics. Real GUI sessions use the worker below.
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            self._refresh_monitor_sync()
            return

        worker = self._monitor_scan_worker
        if worker is not None and worker.isRunning():
            # A refresh requested while a scan is in flight means the in-flight
            # result is older than the current file/DB state (for example after
            # archive completion).  Bump the request id now so that stale result
            # cannot briefly repopulate consumed JPGs before the queued scan runs.
            self._monitor_scan_request_id += 1
            self._monitor_scan_pending = True
            return

        inc, res = self._resolve_capture_subdirs()
        self._monitor_scan_request_id += 1
        request_id = self._monitor_scan_request_id

        from app.workers.monitor_scan_worker import MonitorScanWorker

        worker = MonitorScanWorker(request_id, project_dir, inc, res, parent=self)
        worker.finished_scan.connect(self._on_monitor_scan_finished)
        worker.failed.connect(self._on_monitor_scan_failed)
        worker.finished.connect(worker.deleteLater)
        self._monitor_scan_worker = worker
        worker.start()

    def _refresh_monitor_sync(self) -> None:
        """Synchronous scan path used by tests and explicit headless runs."""
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()
            return

        db = self.ctx.get_db()
        if not db:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()
            return

        try:
            from app.services.monitor_service import (
                build_attribution_context,
                scan_project,
            )

            attr = build_attribution_context(project_dir, db)
            inc, res = self._resolve_capture_subdirs()
            result = scan_project(
                project_dir, db, attr=attr,
                incoming_subdir=inc, results_subdir=res,
            )
            self._apply_monitor_scan_result(result)
        except FileNotFoundError:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()
        except Exception:
            self._monitor.clear()
            self._last_scan_result = None
            self._refresh_workflow_dashboard()

    def _on_monitor_scan_finished(self, request_id: int, result) -> None:
        worker = self._monitor_scan_worker
        if worker is not None and getattr(worker, "request_id", None) == request_id:
            self._monitor_scan_worker = None

        if request_id == self._monitor_scan_request_id:
            project_dir = getattr(result, "project_dir", None)
            current_project = self.ctx.current_project_dir
            try:
                same_project = (
                    bool(project_dir)
                    and bool(current_project)
                    and Path(project_dir).resolve() == Path(current_project).resolve()
                )
            except Exception:
                same_project = project_dir == current_project
            if not project_dir or same_project:
                self._apply_monitor_scan_result(result)

        self._run_pending_monitor_scan()

    def _on_monitor_scan_failed(self, request_id: int, _error) -> None:
        worker = self._monitor_scan_worker
        if worker is not None and getattr(worker, "request_id", None) == request_id:
            self._monitor_scan_worker = None
        if request_id == self._monitor_scan_request_id and self._last_scan_result is None:
            self._monitor.clear()
            self._refresh_workflow_dashboard()
        self._run_pending_monitor_scan()

    def _run_pending_monitor_scan(self) -> None:
        if not self._monitor_scan_pending:
            return
        self._monitor_scan_pending = False
        QTimer.singleShot(0, self._refresh_monitor)

    def _apply_monitor_scan_result(self, result) -> None:
        self._last_scan_result = result
        self._monitor.load_scan(self._monitor_display_scan_result(result))
        self._refresh_workflow_dashboard()
        self._maybe_auto_process_new_tiff(result)

    def _monitor_display_scan_result(self, result):
        """Keep monitor ownership display tied to the current activation state.

        The scan service may recover historical attribution from grouping rows,
        manual assignment events, or old activation windows.  When no specimen is
        active, showing that historical UID on JPG cards looks like the current
        sidebar selection owns those photos and also lets selected JPGs infer a
        compose target from stale state.  Preserve the raw scan for business
        logic, but clear the presentation field for the current monitor view.
        """
        if self._get_active_uid():
            return result
        import copy
        display_result = copy.copy(result)
        display_result.jpg_files = [
            copy.copy(entry)
            for entry in getattr(result, "jpg_files", []) or []
        ]
        for entry in display_result.jpg_files:
            entry.attributed_specimen_id = None
        return display_result

    def _pending_tiff_paths(self, scan_result) -> set[str]:
        return pending_tiff_paths(scan_result)

    def _on_auto_compress_toggled(self, on: bool) -> None:
        try:
            self.ctx.settings.auto_organize_after_compose = bool(on)
        except Exception:
            pass
        if on:
            self._auto_known_tiffs = self._pending_tiff_paths(self._last_scan_result) if self._last_scan_result else set()
            self._status_message("自动归档已开启：可按激活编号自动合成，并处理之后出现的外部 TIF")
        else:
            self._status_message("自动归档已关闭")

    def _on_compose_preview_toggled(self, on: bool) -> None:
        try:
            self.ctx.settings.silent_compose = not bool(on)
            self.ctx.settings.flush_to_disk()
        except Exception:
            pass
        self._status_message("合成预览已开启" if on else "合成预览已关闭：将直接合成")

    def _maybe_auto_process_new_tiff(self, scan_result) -> None:
        candidate = detect_external_tiff_candidate(
            enabled=self._auto_archive_enabled(),
            current_tiff_paths=self._pending_tiff_paths(scan_result),
            known_tiff_paths=self._auto_known_tiffs,
            busy=self._auto_tiff_busy,
        )
        if candidate.should_seed_known_tiffs:
            self._auto_known_tiffs = set(candidate.current_tiff_paths)
            return
        if not candidate.needs_jpg_source:
            return

        uid = self._get_active_uid()
        if uid:
            source = resolve_external_tiff_jpg_source(
                active_uid=uid,
                active_uid_jpg_paths=self._unoccupied_jpg_paths(
                    uid,
                    self._get_attributed_jpg_paths(uid),
                ),
            )
        else:
            try:
                selected_jpg_paths = self._monitor.selected_jpg_paths()
            except Exception:
                selected_jpg_paths = []
            source = resolve_external_tiff_jpg_source(
                active_uid=None,
                selected_jpg_paths=selected_jpg_paths,
            )
        if source.reason == "no-selected-jpgs":
            self._status_message("发现外部 TIF；未激活时请选中对应 JPG 后再自动整理")
            return
        if source.reason == "no-active-jpgs":
            self._status_message("发现外部 TIF；当前激活编号没有可整理 JPG")
            return

        target_tiff = candidate.target_tiff
        if not source.ready or not target_tiff:
            return
        jpg_paths = list(source.jpg_paths)

        def _auto_archive_done(ok: bool) -> None:
            self._auto_tiff_busy = False
            if ok:
                self._auto_known_tiffs.add(target_tiff)
                self._status_message("外部 TIF 自动整理完成")
            else:
                self._status_message("外部 TIF 自动整理失败，已保留待处理")

        self._auto_tiff_busy = True
        try:
            started = self._organise_jpgs_with_tiff(
                jpg_paths,
                target_tiff,
                silent=True,
                on_complete=_auto_archive_done,
            )
        except Exception as exc:
            self._auto_tiff_busy = False
            self._status_message(f"外部 TIF 自动整理失败：{exc}")
            return
        if started:
            self._status_message("发现外部 TIF，正在自动整理")
        else:
            self._auto_tiff_busy = False

    def _missing_meta_fields(self, uid: str) -> list:
        """返回该编号缺失的关键字段标签：保存方式 / 采集日期 / 拍摄日期。

        拍摄当时可能还不知道怎么处理标本（保存方式/日期没填），切到下一个号时用来
        提醒回填，免得遗漏。左侧点该号即可随时编辑补填。
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return []
        try:
            row = db.execute(
                "SELECT storage, collection_date, photo_date FROM specimens WHERE uid = ?",
                (uid,),
            ).fetchone()
        except Exception:
            return []
        if not row:
            return []
        missing = []
        if not (row[0] and str(row[0]).strip()):
            missing.append("保存方式")
        if not (row[1] and str(row[1]).strip()):
            missing.append("采集日期")
        if not (row[2] and str(row[2]).strip()):
            missing.append("拍摄日期")
        return missing

    def _on_sidebar_activate(self, uid: str) -> None:
        """Activate *uid* via activation_service and refresh the sidebar + monitor.

        Oracle: server.js:3844-3888 POST /api/specimen-log/activate.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not uid:
            return
        if not project_dir or not db:
            self._status_message("请先打开一个项目工作区。")
            return
        try:
            from app.services.activation_service import activate as svc_activate
            result = svc_activate(project_dir, db, uid)
            prev_uid = result.get("previous_uid") if isinstance(result, dict) else None
            self._sidebar.refresh()
            self._refresh_monitor()
            # Select and load the newly activated specimen
            self._sidebar.select_uid(uid)
            self._load_specimen(uid)
            # 激活即置「拍摄中」：仅当此号尚无阶段（None/空/created）时默认 shooting，
            # 已有更高阶段（已拍完/整理中/完成）保留不动 —— 对齐 oracle
            # activateSpecimen 的 status: existing!=="created" ? existing : "shooting"
            # (app.js:3531-3534) + collabUpdateTaskStatus(uid, status||shooting) (:3556)。
            phase = self._collab_phase_for(uid)
            if phase in (None, "", "created"):
                self._set_phase(uid, "shooting")
            else:
                self._refresh_batch_header()
            self._status_message(f"已激活：{uid}", 4000)
            # 切换激活号提醒：旧号在其激活期间到达的照片仍归旧号，不会改归新号
            # (oracle app.js:3517-3520)。用状态栏提示（非阻塞 toast 等价）。
            if prev_uid and prev_uid != uid:
                segs = prev_uid.split("-")
                short = segs[3] if len(segs) > 3 else prev_uid
                self._status_message(
                    f"已切到新号。提醒：旧号「{short}」此前拍的照片仍归旧号"
                    "（不推荐频繁切换）", 6000,
                )
                # 资料未填完提醒：离开旧号时若它缺 保存方式/采集日期/拍摄日期 → 弹提醒，
                # 免得拍完忘了回填（拍摄当时可能还没决定怎么处理标本）。
                missing = self._missing_meta_fields(prev_uid)
                if missing:
                    QMessageBox.information(
                        self, "上一个编号资料未填完",
                        f"编号「{short}」还缺：{'、'.join(missing)}。\n"
                        "已激活下一个；左侧点该编号可随时回填编辑。",
                    )
        except Exception as exc:
            QMessageBox.warning(self, "激活失败", str(exc))

    def _on_sidebar_deactivate(self, uid: str) -> None:
        """Deactivate *uid* via activation_service and refresh.

        Oracle: server.js:3857-3861 (active=false path).
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not uid:
            return
        try:
            from app.services.activation_service import deactivate as svc_deactivate
            svc_deactivate(project_dir, db, uid)
            if self._current_uid == uid or self._get_active_uid() is None:
                self._reset_to_unassigned_task()
            self._sidebar.refresh()
            self._refresh_monitor()
            self._refresh_batch_header()
            self._status_message(f"已取消激活：{uid}", 4000)
        except Exception as exc:
            QMessageBox.warning(self, "去激活失败", str(exc))

    def _on_assign_jpg(self, path: str) -> None:
        """Manual attribution: assign *path* to the currently active specimen.

        Writes a manual-assign event so the attribution P2 table picks it up.
        If no specimen is active, show an informational message.

        Oracle: server.js:3891-3913 POST /api/specimen-log/assign.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not path:
            return

        try:
            from app.services.capture_workflow_service import assign_jpg_to_active_specimen

            result = assign_jpg_to_active_specimen(project_dir, db, path)
        except Exception as exc:
            QMessageBox.warning(self, "手动归属失败", str(exc))
            return

        if not result.active_uid:
            QMessageBox.information(
                self,
                "手动归属",
                "请先激活一个标本，再手动归属 JPG。",
            )
            return

        self._refresh_monitor()

    def _on_unassign_jpg(self, path: str) -> None:
        """Explicit unassign: adds path to the P0 blacklist."""
        db = self.ctx.get_db()
        if not db or not path:
            return
        try:
            from app.services.capture_workflow_service import unassign_jpg
            unassign_jpg(db, path)
            self._refresh_monitor()
        except Exception:
            pass

    def _on_add_selection_to_group(self, group_index: int) -> None:
        """Resolve monitor selection → add JPGs (+ optional TIFF) to a group."""
        jpg_paths = self._monitor.selected_jpg_paths()
        tiff_paths = self._monitor.selected_tiff_paths()
        if not jpg_paths and not tiff_paths:
            self._status_message("请先在上方监控区选中 JPG 或 TIFF。")
            return
        if len(tiff_paths) > 1:
            self._status_message("一次只能关联 1 个 TIFF。")
            return
        tiff_path = tiff_paths[0] if tiff_paths else None
        self._grouping.drop_external_files(group_index, jpg_paths, tiff_path)
        from app.services.grouping_service import ADHOC_GROUPING_UID
        uid = getattr(self._grouping, "_uid", None)
        project_dir = self.ctx.current_project_dir
        if uid and uid != ADHOC_GROUPING_UID and project_dir and jpg_paths:
            try:
                from app.services.activation_service import manual_assign
                manual_assign(project_dir, uid, jpg_paths)
            except Exception:
                pass
        self._monitor._on_select_none()

    def _on_add_to_group(self, group_index: int, jpg_paths: list[str]) -> None:
        """Add selected monitor JPGs to the specified grouping group."""
        self._grouping.add_jpgs_to_group(group_index, jpg_paths)
        # Also mark those paths as manually assigned to the current uid
        uid = self._current_uid
        project_dir = self.ctx.current_project_dir
        if uid and project_dir and jpg_paths:
            try:
                from app.services.activation_service import manual_assign
                manual_assign(project_dir, uid, jpg_paths)
            except Exception:
                pass

    def _on_add_jpg_files(self) -> None:
        """Open file picker for JPG/TIFF → import into capture workspace.

        Oracle: app.js importJpgFiles() app.js:7944–7975.
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return

        from PyQt6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 JPG / TIFF 照片",
            filter=(
                "JPG 与 TIFF (*.jpg *.jpeg *.JPG *.JPEG *.tif *.tiff *.TIF *.TIFF);;"
                "JPG 照片 (*.jpg *.jpeg *.JPG *.JPEG);;"
                "TIFF 成片 (*.tif *.tiff *.TIF *.TIFF)"
            ),
        )
        if not paths:
            return

        self._start_import_media_paths(paths, source="添加照片")

    def _on_external_jpgs_dropped(self, paths: list[str]) -> None:
        """Import JPG/TIFF dropped from Explorer/Finder/file managers."""
        self._start_import_media_paths(paths, source="拖入照片")

    def _import_jpg_paths(self, paths: list[str], *, source: str) -> list[str]:
        """Compatibility wrapper for older JPG-only callers."""
        return self._import_media_paths(paths, source=source)

    def _import_media_paths(self, paths: list[str], *, source: str) -> list[str]:
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return []

        inc, _res = self._resolve_capture_subdirs()
        incoming_dir = os.path.join(project_dir, inc)
        from app.services.photo_import_service import import_media_to_project
        result = import_media_to_project(list(paths), incoming_dir)
        imported = self._handle_media_import_result(
            result,
            source=source,
            incoming_label=inc,
            project_dir=project_dir,
        )
        self._refresh_monitor()
        return imported

    def _start_import_media_paths(self, paths: list[str], *, source: str) -> None:
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return
        if not paths:
            return
        worker = getattr(self, "_photo_import_worker", None)
        if worker is not None and worker.isRunning():
            self._status_message("照片仍在导入，请稍后再添加。")
            return

        inc, _res = self._resolve_capture_subdirs()
        incoming_dir = os.path.join(project_dir, inc)
        from app.workers.photo_import_worker import PhotoImportWorker

        worker = PhotoImportWorker(list(paths), incoming_dir, parent=self)
        self._photo_import_worker = worker
        task_key = f"photo-import:{id(worker)}"

        def set_photo_import_busy_state(on: bool) -> None:
            try:
                self._monitor.set_import_busy(on)
            except Exception:
                pass

        def _cleanup() -> None:
            set_photo_import_busy_state(False)
            if self._photo_import_worker is worker:
                self._photo_import_worker = None
            worker.deleteLater()

        def _import_started(count: int) -> None:
            self._workflow_notice(
                f"{source}：正在导入",
                f"正在复制 {count} 个文件到 {inc}；导入在后台运行，可继续拍摄或整理。",
                state="busy",
                force_show=True,
                task_key=task_key,
            )
            self._status_message(f"正在导入 {count} 个文件到 {inc}，可继续操作其他区域。")

        worker.started_import.connect(_import_started)
        worker.completed.connect(
            lambda result: self._on_photo_import_finished(
                result,
                source=source,
                incoming_label=inc,
                project_dir=project_dir,
                task_key=task_key,
            )
        )
        worker.failed.connect(
            lambda message: self._on_photo_import_failed(
                source, message, task_key=task_key
            )
        )
        worker.finished.connect(_cleanup)
        set_photo_import_busy_state(True)
        worker.start()

    def _on_photo_import_finished(
        self,
        result,
        *,
        source: str,
        incoming_label: str,
        project_dir: str,
        task_key: str = "",
    ) -> None:
        imported = self._handle_media_import_result(
            result,
            source=source,
            incoming_label=incoming_label,
            project_dir=project_dir,
        )
        duplicate_count = len(getattr(result, "skipped_duplicate_paths", []) or [])
        error_count = len(getattr(result, "errors", []) or [])
        jpg_count = len(getattr(result, "imported_jpg_paths", []) or [])
        tiff_count = len(getattr(result, "imported_tiff_paths", []) or [])
        if imported:
            parts = []
            if jpg_count:
                parts.append(f"{jpg_count} 张 JPG")
            if tiff_count:
                parts.append(f"{tiff_count} 个 TIFF")
            detail = f"已导入 {'，'.join(parts)} 到 {incoming_label}。"
            if duplicate_count:
                detail += f" 已跳过 {duplicate_count} 个重复文件。"
            if error_count:
                detail += f" 有 {error_count} 个文件失败，请查看弹窗。"
            self._workflow_notice(
                f"{source}{'部分完成' if error_count else '完成'}",
                detail,
                state="error" if error_count else "success",
                task_key=task_key,
            )
        elif duplicate_count:
            self._workflow_notice(
                f"{source}完成",
                f"未新增文件；已跳过 {duplicate_count} 个重复文件。",
                state="info",
                task_key=task_key,
            )
        else:
            self._workflow_notice(
                f"{source}未导入",
                "未识别到 JPG/JPEG 或 TIFF 文件。",
                state="info",
                task_key=task_key,
            )
        self._refresh_monitor()

    def _on_photo_import_failed(
        self,
        source: str,
        message: str,
        *,
        task_key: str = "",
    ) -> None:
        self._workflow_notice(
            f"{source}失败",
            message or "导入过程出现错误。",
            state="error",
            task_key=task_key,
        )
        QMessageBox.warning(self, f"{source}失败", message or "导入过程出现错误。")
        self._status_message(f"{source}失败。")

    def _handle_media_import_result(
        self,
        result,
        *,
        source: str,
        incoming_label: str,
        project_dir: str,
    ) -> list[str]:
        try:
            from app.services.photo_import_service import record_imported_media
            record_imported_media(
                project_dir,
                list(getattr(result, "imported_records", []) or []),
            )
        except Exception:
            pass

        if result.errors:
            QMessageBox.warning(self, f"{source}部分失败", "\n".join(result.errors[:5]))
        duplicate_count = len(getattr(result, "skipped_duplicate_paths", []) or [])
        if result.imported_paths:
            parts = []
            if result.imported_jpg_paths:
                parts.append(f"{len(result.imported_jpg_paths)} 张 JPG 到 {incoming_label}")
            if result.imported_tiff_paths:
                parts.append(f"{len(result.imported_tiff_paths)} 个 TIFF 到 {incoming_label}")
            msg = "已导入 " + "，".join(parts)
            if duplicate_count:
                msg += f"；已跳过 {duplicate_count} 个重复文件"
            self._status_message(msg + "。")
        elif duplicate_count:
            self._status_message(f"已跳过 {duplicate_count} 个重复文件，队列未新增。")
        elif result.skipped_paths:
            self._status_message("未识别到 JPG/JPEG 或 TIFF 文件。")
        return result.imported_paths

    def _on_clear_pending_queue(self, paths: list[str]) -> None:
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return
        try:
            from app.services.photo_import_service import clear_pending_imports
            result = clear_pending_imports(project_dir, list(paths))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "清空队列失败", str(exc))
            return

        try:
            self._monitor._on_select_none()
        except Exception:
            pass
        self._refresh_monitor()

        if result.errors:
            QMessageBox.warning(self, "清空队列部分失败", "\n".join(result.errors[:5]))

        if result.changed_count:
            parts = []
            if result.returned_paths:
                parts.append(f"退回 {len(result.returned_paths)} 个")
            if result.stashed_paths:
                parts.append(f"安全移出 {len(result.stashed_paths)} 个")
            self._status_message("队列已清空：" + "，".join(parts) + "。")
        elif result.skipped_paths:
            self._status_message("队列未变化：没有可清空的文件。")

    def _on_free_compose(self) -> None:
        """Free compose: selected monitor JPGs → Helicon → incoming-jpg/.

        Stub — full implementation in Task 7.
        Oracle: app.js freeComposeSelected() app.js:7982–8010.
        """
        project_dir = self.ctx.current_project_dir
        if not project_dir:
            self._status_message("请先打开一个项目。")
            return

        jpg_paths = self._monitor.selected_jpg_paths()
        if not jpg_paths:
            self._status_message("请先在监控区选中要合成的 JPG。")
            return

        from app.services.helicon_service import detect_helicon
        exe = detect_helicon()
        if not exe:
            QMessageBox.warning(self, "未检测到 Helicon Focus",
                                "请确认 Helicon Focus 已安装并配置路径。")
            return

        from PyQt6.QtWidgets import QInputDialog
        user_name, ok = QInputDialog.getText(
            self, "无号合成", "输出文件名（留空自动命名）：", text=""
        )
        if not ok:
            return

        inc, _res = self._resolve_capture_subdirs()
        incoming_dir = os.path.join(project_dir, inc)
        os.makedirs(incoming_dir, exist_ok=True)
        output_name = _free_compose_output_name(incoming_dir, user_name.strip() or None)
        output_path = os.path.join(incoming_dir, output_name)
        # Honor 输出格式 (tif/jpg) — swap extension so -save: matches the encoder.
        output_path = self._with_output_ext(output_path, self._helicon_output_opts()["format"])
        output_name = os.path.basename(output_path)

        params = self._helicon_params.get_params()

        def _handle_free_compose_finished(tiff_path):
            if os.path.isfile(output_path):
                QMessageBox.information(self, "无号合成完成",
                                        f"TIFF 已保存到 {inc}/：\n{output_name}")
                self._refresh_monitor()
            else:
                QMessageBox.warning(self, "无号合成失败", "Helicon 执行后未生成输出文件。")

        def _handle_free_compose_failed(msg: str):
            if msg != "用户取消":
                QMessageBox.warning(self, "无号合成失败", msg)

        self._run_helicon_stack(
            jpg_paths,
            output_path,
            params,
            _handle_free_compose_finished,
            _handle_free_compose_failed,
        )

    def _on_retroactive_scan(self) -> None:
        """Launch retroactive organize modal.

        Oracle: app.js retroactiveScan() + renderRetroactiveModal().
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db:
            self._status_message("请先打开一个项目。")
            return

        _inc0, _res0 = self._resolve_capture_subdirs()
        pre = self._retroactive_scan_dialog_class()(
            project_dir,
            parent=self,
            results_subdir=_res0,
        )
        if pre.exec() != QDialog.DialogCode.Accepted:
            return
        selected_subdir = pre.selected_subdir()

        try:
            from app.services.retroactive_service import scan_project_retroactive
            # 存量整理也用项目配置的 incoming/results 子目录（与监控/合成一致）。
            inc, res = self._resolve_capture_subdirs()
            result = scan_project_retroactive(
                project_dir, db, subdir=selected_subdir,
                incoming_subdir=inc, results_subdir=res,
            )
        except Exception as exc:
            QMessageBox.warning(self, "扫描失败", str(exc))
            return

        total_groups = sum(len(sp["groups"]) for sp in result.get("specimens", []))
        if not total_groups and not result.get("unnamedTiffs"):
            QMessageBox.information(
                self, "存量整理",
                "没找到可整理的 TIF 成片（需 results/ 里有按编号命名的 TIF）。"
            )
            return

        from app.widgets.retroactive_modal import RetroactiveModal
        dlg = RetroactiveModal(self.ctx, result, parent=self)
        if dlg.exec() == RetroactiveModal.DialogCode.Accepted:
            panel_uid = getattr(self._grouping, "_uid", None)
            if panel_uid:
                try:
                    from app.services.grouping_service import load_grouping
                    db = self.ctx.get_db()
                    if db is not None:
                        self._grouping.load_grouping(
                            panel_uid, load_grouping(db, panel_uid)
                        )
                except Exception:
                    pass
            self._refresh_monitor()

    def _open_project_via_dialog(self, parent: QWidget) -> Optional[str]:
        """Open an existing workspace folder and activate it in the workbench."""
        from app.views.project_dialog import ProjectDialog
        from app.services.project_service import (
            default_user_projects_json_path,
            load_user_projects,
            save_project_descriptor,
        )

        dlg = ProjectDialog(
            mode="open",
            existing_projects=load_user_projects(),
            parent=parent,
        )
        if dlg.exec() != ProjectDialog.DialogCode.Accepted:
            return None
        proj = dlg.result_project()
        directory = str(proj.get("directory", "") or "").strip() if proj else ""
        if not directory:
            return None
        try:
            save_project_descriptor(
                default_user_projects_json_path(),
                proj,
                existing_projects=load_user_projects(),
            )
            from app.services.project_service import enter_workspace
            enter_workspace(self.ctx, directory)
            self.on_activate()
        except Exception as exc:
            ui.warn(parent, "打开项目失败", str(exc))
            return None
        return directory

    def _on_dashboard_open_project(self) -> None:
        self._open_project_via_dialog(self)

    def _pick_auto_group_source_folder(self, parent: QWidget) -> str:
        """Prompt folder vs project, then return the directory to scan."""
        dialog_cls = self._auto_group_source_dialog_class()
        chooser = dialog_cls(parent)
        if chooser.exec() != QDialog.DialogCode.Accepted:
            return ""

        start_dir = self.ctx.current_project_dir or str(Path.home())
        if chooser.selected_source_mode() == dialog_cls.MODE_FOLDER:
            folder = ui.get_existing_directory(
                parent,
                "选择需要自动分组整理的 JPG / TIF 目录",
                start=start_dir,
            )
            if not folder:
                return ""
            try:
                from app.services.project_service import enter_photo_folder
                enter_photo_folder(self.ctx, folder)
            except Exception as exc:
                ui.warn(
                    parent,
                    "打开照片文件夹失败",
                    f"无法在照片目录中保存管理数据：{exc}",
                )
                return ""
            return folder

        project_dir = self._open_project_via_dialog(parent)
        if not project_dir:
            return ""
        inc, _ = self._resolve_capture_subdirs()
        start = Path(project_dir) / inc
        if not start.is_dir():
            start = Path(project_dir)
        return ui.get_existing_directory(
            parent,
            "选择项目内要自动分组整理的目录",
            start=str(start),
        )

    def _on_auto_group_organize(self) -> None:
        """Scan → inline preview; second click opens archive dialog."""
        parent = self._grouping_ui_parent()

        if self._grouping.has_auto_group_preview():
            from app.widgets.retroactive_modal import RetroactiveModal
            result = self._grouping.auto_group_preview_result()
            if not result:
                return
            dlg = RetroactiveModal(self.ctx, result, parent=parent)
            if dlg.exec() == RetroactiveModal.DialogCode.Accepted:
                self._grouping.clear_auto_group_staging()
                panel_uid = getattr(self._grouping, "_uid", None)
                if panel_uid:
                    try:
                        from app.services.grouping_service import load_grouping
                        db = self.ctx.get_db()
                        if db is not None:
                            self._grouping.load_grouping(
                                panel_uid, load_grouping(db, panel_uid)
                            )
                    except Exception:
                        pass
                self._refresh_monitor()
            return

        fallback_uid = getattr(self._grouping, "_uid", None)
        from app.services.grouping_service import ADHOC_GROUPING_UID
        if fallback_uid == ADHOC_GROUPING_UID:
            fallback_uid = self._current_uid or self._get_active_uid()

        staged = self._grouping.staged_auto_group_paths()
        try:
            if staged:
                from app.services.retroactive_service import scan_paths_auto_groups
                result = scan_paths_auto_groups(staged, fallback_uid=fallback_uid)
            else:
                folder = self._pick_auto_group_source_folder(parent)
                if not folder:
                    return
                from app.services.retroactive_service import scan_folder_auto_groups
                result = scan_folder_auto_groups(
                    folder,
                    fallback_uid=fallback_uid,
                )
        except Exception as exc:
            ui.warn(parent, "自动分组扫描失败", str(exc))
            return

        total_groups = sum(
            len(sp.get("groups", [])) for sp in result.get("specimens", [])
        )
        if not total_groups and not result.get("unnamedTiffs"):
            ui.info(
                parent,
                "自动分组整理",
                "没有可配对的 JPG…TIF。\n\n"
                "请拖入原片+成片，或选择直接包含这些文件的文件夹"
                "（按修改时间排序，每个 TIF 前一批 JPG 为一组）。",
            )
            return

        # Drag-dropped legacy photos have no folder picker.  When they all come
        # from one directory, adopt that directory as a standalone photo folder
        # so confirmed grouping is persisted beside the photos as well.
        if not self.ctx.current_project_dir and result.get("scanFolder"):
            try:
                from app.services.project_service import enter_photo_folder
                enter_photo_folder(self.ctx, result["scanFolder"])
            except Exception as exc:
                ui.warn(
                    parent,
                    "保存分组数据失败",
                    f"无法在照片目录中创建 _data/project.db：{exc}",
                )
                return

        self._grouping.show_auto_group_preview(result)
        self._status_message(
            f"已识别 {total_groups} 组，请在下方核对预览。"
            "确认无误后再点「执行整理归档」。"
        )

    def _offer_tiff_naming_check(self, folder: str | None) -> None:
        """Ask user to run the read-only TIF naming audit on *folder*."""
        if not folder or not os.path.isdir(folder):
            return
        if ui.question(
            self,
            "检查 TIF 命名",
            "整理已完成。是否立即检查 TIF 命名并提取标本编号？",
        ):
            self._run_tiff_naming_check(folder)

    def _current_grouping_tiff_paths_for_check(self) -> tuple[list[str], bool]:
        """Return visible grouping TIFFs for the naming audit.

        Checked groups narrow the audit. With no checks, all visible linked
        TIFFs are audited; if there are none, the caller falls back to a folder
        picker for legacy bulk checks.
        """
        grouping = getattr(self._grouping, "_grouping", None)
        if grouping is None:
            return [], False
        try:
            selected = set(self._grouping.selected_group_indexes())
        except Exception:
            selected = set()
        paths: list[str] = []
        for group in list(getattr(grouping, "groups", []) or []):
            if selected and group.group_index not in selected:
                continue
            tiff_path = str(getattr(group, "composed_tiff_path", "") or "").strip()
            if tiff_path:
                paths.append(tiff_path)
        return paths, bool(selected)

    def _run_tiff_naming_check(
        self,
        folder: str | None = None,
        *,
        paths: list[str] | None = None,
    ) -> None:
        """Scan a folder or explicit TIF paths and show the naming audit."""
        project_dir = self.ctx.current_project_dir
        if not project_dir and not paths:
            self._status_message("请先打开一个项目。")
            return

        explicit_paths: list[str] = [
            str(p).strip() for p in (paths or []) if str(p).strip()
        ]
        selected_only = False
        if not folder and not explicit_paths:
            explicit_paths, selected_only = self._current_grouping_tiff_paths_for_check()
            if selected_only and not explicit_paths:
                ui.warn(self, "TIF 命名检查", "勾选的分组没有已关联 TIF。")
                return

        if not folder and not explicit_paths:
            start_dir = project_dir or str(Path.home())
            folder = ui.get_existing_directory(
                self,
                "选择需要检查 TIF 命名的目录",
                start=start_dir,
            )
        if not folder and not explicit_paths:
            return

        current_uid = getattr(self._grouping, "_uid", None)
        try:
            from app.services.grouping_service import ADHOC_GROUPING_UID
            if current_uid == ADHOC_GROUPING_UID:
                current_uid = None
            from app.services.project_settings_service import (
                DEFAULT_NAMING_RULES,
                load_setting,
            )
            from app.services.tiff_naming_service import (
                inspect_tiff_names,
                inspect_tiff_paths,
            )
            from app.utils.naming import component_values_from_specimen

            db = self.ctx.get_db()
            rules = (
                load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
                if db
                else DEFAULT_NAMING_RULES
            )
            components = rules.get("components", DEFAULT_NAMING_RULES["components"])

            specimen_values = None
            if current_uid and db:
                row = db.execute(
                    "SELECT * FROM specimens WHERE uid = ?", (current_uid,)
                ).fetchone()
                if row:
                    from app.models.specimen import Specimen

                    sp = Specimen.from_row(row)
                    sp_dict = dict(sp.raw)
                    sp_dict.update({
                        "province": sp.province,
                        "site": sp.site,
                        "station": sp.station,
                        "id": sp.id,
                        "storage": sp.storage,
                        "collection_date": sp.collection_date,
                        "collectionDate": sp.collection_date,
                        "photo_date": sp.photo_date,
                        "photoDate": sp.photo_date,
                        "collector": sp.collector,
                        "photographer": sp.photographer,
                        "identifier": sp.identifier,
                        "geo_area": sp.geo_area,
                        "taxon_group": sp.taxon_group,
                        "scientific_name": sp.scientific_name,
                        "scientific_name_cn": sp.scientific_name_cn,
                        "notes": sp.notes,
                        "photo_notes": sp.photo_notes,
                    })
                    specimen_values = component_values_from_specimen(sp_dict)

            if explicit_paths:
                audit = inspect_tiff_paths(
                    explicit_paths,
                    current_uid=current_uid,
                    naming_components=components,
                    specimen_values=specimen_values,
                )
            else:
                audit = inspect_tiff_names(
                    folder,
                    current_uid=current_uid,
                    naming_components=components,
                    specimen_values=specimen_values,
                )
        except Exception as exc:
            ui.warn(self, "TIF 命名检查失败", str(exc))
            return

        if audit.total == 0:
            message = (
                "所选文件不是 TIF/TIFF，或文件不存在。"
                if explicit_paths
                else "所选目录中没有 TIF/TIFF 文件。"
            )
            ui.info(self, "TIF 命名检查", message)
            return

        from app.widgets.tiff_naming_audit_dialog import TiffNamingAuditDialog
        TiffNamingAuditDialog(audit, parent=self).exec()

    def _on_tiff_naming_check(self) -> None:
        """Run the independent, read-only TIFF filename audit."""
        self._run_tiff_naming_check()

    def _on_tiff_naming_check_path(self, path: str) -> None:
        """Run the read-only naming audit for one TIFF file."""
        self._run_tiff_naming_check(paths=[path])

    def _group_for_tiff_path(self, path: str) -> tuple[str, int] | None:
        """Return ``(uid, group_index)`` for a registered TIFF path."""
        if not path:
            return None
        try:
            from app.services.grouping_service import result_path_key as _path_key
        except Exception:
            def _path_key(value):
                return str(value or "").casefold()

        target_key = _path_key(path)
        if not target_key:
            return None

        grouping = getattr(self._grouping, "_grouping", None)
        uid = getattr(self._grouping, "_uid", None)
        for group in list(getattr(grouping, "groups", []) or []):
            try:
                if _path_key(getattr(group, "composed_tiff_path", None)) == target_key:
                    return str(uid or ""), int(group.group_index)
            except Exception:
                continue

        db = self.ctx.get_db()
        if db is None:
            return None
        try:
            rows = db.execute(
                """
                SELECT uid, group_index, composed_tiff_path
                FROM grouping
                WHERE composed_tiff_path IS NOT NULL
                  AND composed_tiff_path != ''
                """
            ).fetchall()
        except Exception:
            return None
        for row in rows:
            row_uid = row["uid"] if hasattr(row, "keys") else row[0]
            group_index = row["group_index"] if hasattr(row, "keys") else row[1]
            tiff_path = row["composed_tiff_path"] if hasattr(row, "keys") else row[2]
            try:
                if _path_key(tiff_path) == target_key:
                    return str(row_uid or ""), int(group_index)
            except Exception:
                continue
        return None

    def _on_delete_result_tiff_path(self, path: str) -> None:
        """Delete a TIFF from the results context menu after confirmation."""
        path = str(path or "").strip()
        if not path:
            return
        registered = self._group_for_tiff_path(path)
        if registered is not None:
            uid, group_index = registered
            self._on_undo_compose(uid, group_index)
            return

        if not os.path.isfile(path):
            ui.warn(self, "删除 TIF", f"文件不存在：\n{path}")
            return
        reply = QMessageBox.question(
            self,
            "删除 TIF",
            "这张 TIF 尚未关联到任何分组。\n\n"
            "确认永久删除这个文件？\n"
            f"{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            os.unlink(path)
        except OSError as exc:
            ui.warn(self, "删除 TIF", f"删除失败：{exc}")
            return
        self._refresh_monitor()
        if getattr(self._results, "_display_mode", "single") == "many":
            self._on_show_all_results()
        else:
            self._on_show_current_results()
        self._status_message("TIF 已删除。", 4000)
