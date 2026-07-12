"""Compose and Helicon batch workflow for WorkbenchView."""
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


class WorkbenchComposeWorkflowMixin:
    """Compose and Helicon batch workflow for WorkbenchView."""

    @staticmethod
    def _compose_workbench_dialog_class():
        """Resolve via workbench_view re-export so existing tests can monkeypatch it."""
        try:
            from app.views import workbench_view
            return getattr(workbench_view, "_ComposeWorkbenchDialog")
        except Exception:
            return _DefaultComposeWorkbenchDialog

    # ── Grouping ──────────────────────────────────────────────────────────────

    def _get_grouping_for_uid(self, uid: str):
        """Load grouping from DB when a project is open, else from panel memory."""
        from app.services.capture_workflow_service import grouping_for_clean_uid

        panel = self._grouping
        if getattr(panel, "_uid", None) == uid and panel._grouping is not None:
            return panel._grouping
        return grouping_for_clean_uid(self.ctx.get_db(), uid)

    def _save_grouping_for_uid(self, uid: str, groups: list) -> None:
        """Persist grouping to DB when available; always refresh the open panel."""
        from app.services.capture_workflow_service import persist_grouping
        from app.services.grouping_service import SpecimenGrouping

        db = self.ctx.get_db()
        persist_grouping(db, uid, groups, clean_phantoms=False)
        panel = self._grouping
        if getattr(panel, "_uid", None) == uid:
            panel.load_grouping(uid, SpecimenGrouping(uid=uid, groups=groups))

    def _ensure_compose_incoming_dir(self, group) -> tuple[str, str]:
        """Return (incoming_dir, results_dir) for Helicon output.

        With a project: use configured incoming/results subdirs.
        Without: write beside the group's JPGs (or a temp fallback).
        """
        import tempfile

        project_dir = self.ctx.current_project_dir
        if project_dir:
            inc, res = self._resolve_capture_subdirs()
            incoming = os.path.join(project_dir, inc)
            results = os.path.join(project_dir, res)
            os.makedirs(incoming, exist_ok=True)
            return incoming, results
        if group and group.jpg_paths:
            incoming = os.path.dirname(os.path.abspath(group.jpg_paths[0]))
            os.makedirs(incoming, exist_ok=True)
            return incoming, incoming
        incoming = os.path.join(tempfile.gettempdir(), "specimen-photo-workbench")
        os.makedirs(incoming, exist_ok=True)
        return incoming, incoming

    def _on_compose_requested(
        self,
        uid: str,
        group_index: int,
        *,
        on_composed=None,
    ) -> None:
        """Compose the JPGs in the specified group via Helicon Focus CLI.

        Steps:
          1. Detect Helicon .exe (graceful failure if not found).
          2. Build output TIFF path using organize_service sequence.
          3. Call helicon_service.stack_single_subprocess with progress dialog.
          4. Update grouping DB with composedTiffPath + status="composed".

        Oracle: workbench.md, helicon.md; helicon_service.stack_single_subprocess.

        NOTE: QProcess real invocation requires a true machine with Helicon.
        """
        db = self.ctx.get_db()
        if not uid:
            return

        def _notify_composed(success: bool) -> None:
            if callable(on_composed):
                on_composed(success)

        try:
            from app.services.helicon_service import detect_helicon

            grouping = self._get_grouping_for_uid(uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None:
                QMessageBox.warning(self, "合成", f"找不到组{group_index + 1}")
                _notify_composed(False)
                return

            from app.services.helicon_service import resolve_existing_image_path
            resolved_jpgs: list[str] = []
            missing_jpgs: list[str] = []
            for p in group.jpg_paths:
                r = resolve_existing_image_path(p)
                if r:
                    resolved_jpgs.append(r)
                else:
                    missing_jpgs.append(p)
            if missing_jpgs:
                sample = "\n".join(missing_jpgs[:5])
                extra = f"\n…等 {len(missing_jpgs)} 个" if len(missing_jpgs) > 5 else ""
                QMessageBox.warning(
                    self,
                    "合成",
                    f"以下 JPG 在磁盘上找不到：\n{sample}{extra}\n\n"
                    "请确认照片仍在 incoming-jpg 目录；WSL 下请用 /mnt/n/... 打开项目。",
                )
                _notify_composed(False)
                return
            group.jpg_paths = resolved_jpgs

            if len(group.jpg_paths) < 2:
                # ── Implicit-batch fallback  #cursor ─────────────────────────
                # Mirrors web composeImplicitActiveBatch() app.js:5660–5706.
                # If group is empty/insufficient, offer to use all JPGs
                # currently attributed to this specimen in the monitor scan.
                attributed_paths = self._get_attributed_jpg_paths(uid)
                if len(attributed_paths) >= 2:
                    reply = QMessageBox.question(
                        self,
                        "该组 JPG 不足",
                        f"该分组 JPG 不足 2 张，但检测到 {len(attributed_paths)} 张"
                        f" 已归属到此标本的 JPG。\n\n"
                        "是否用这些照片作为隐式批次执行合成？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        _notify_composed(False)
                        return
                    group.jpg_paths = attributed_paths
                else:
                    QMessageBox.warning(
                        self, "合成", "该组 JPG 不足 2 张，无法合成。"
                    )
                    _notify_composed(False)
                    return

            # ── Pre-compose preview dialog  #cursor renderComposePreviewModal ─
            # Mirrors web renderComposePreviewModal() app.js:6597.
            # Shows JPG list so user can confirm / deselect before Helicon runs.
            selected_jpgs = self._show_compose_preview(group.jpg_paths)
            if selected_jpgs is None:
                return  # User cancelled
            if len(selected_jpgs) < 2:
                QMessageBox.warning(self, "合成", "选中的 JPG 不足 2 张，无法合成。")
                _notify_composed(False)
                return
            group.jpg_paths = selected_jpgs

            # Check Helicon availability first
            exe = detect_helicon()
            if not exe:
                QMessageBox.warning(
                    self,
                    "未检测到 Helicon Focus",
                    "未在常见安装目录找到 Helicon Focus，请确认已安装并设置 "
                    "HELICON_FOCUS_PATH 环境变量指向可执行文件。",
                )
                _notify_composed(False)
                return

            # Determine output path — TIFF lands in incoming first;
            # organize step moves it to results/ (oracle app.js:4336,8867).
            incoming_dir, results_dir = self._ensure_compose_incoming_dir(group)

            # 输出名统一走 _resolve_compose_output_name:覆盖值 > 编号-序号 > 组序.tif。
            # _seq:真编号=preview.next_seq;临时分组(ad-hoc)=组序。
            # 命名(用户 2026-07-12 最终拍板): **没有激活编号时允许 1.tif / 2.tif**,
            #   不弹框打断 —— 临时分组本来就是"先拍了再说"的场景, 用户想自己命名可以
            #   在该组的「输出 TIF」框里手填(那个覆盖值优先级最高)。
            # §7 上一版(同日, 已按用户指示撤回): 调 _ensure_group_output_name 弹
            #   「归属编号 / 自由输出名」对话框, 空名则不写盘。方法保留未删, 以便日后
            #   需要强制命名时直接接回。
            output_name, _seq = self._resolve_compose_output_name(
                db, uid, group, results_dir, incoming_dir)
            output_path = os.path.join(incoming_dir, output_name)  # incoming, not results
            # Honor 输出格式 (tif/jpg); default tif keeps the lossless archival master.
            output_path = self._with_output_ext(output_path, self._helicon_output_opts()["format"])
            output_name = os.path.basename(output_path)

            params = self._helicon_params.get_params()
            task_key = f"compose:{uid}:{group.group_index}:{output_name}"
            self._workflow_notice(
                "合成：正在生成 TIFF",
                f"正在合成 {len(group.jpg_paths)} 张 JPG → {output_name}。完成后会进入预览确认。",
                state="busy",
                force_show=True,
                task_key=task_key,
            )

            def _run_interactive_compose_with_preview(jpg_paths, out_path, cur_params):
                def _open_compose_review_after_helicon_success(tiff_path):
                    if not os.path.isfile(out_path):
                        self._workflow_notice(
                            "合成失败",
                            "Helicon 执行后未生成输出文件。",
                            state="error",
                            task_key=task_key,
                        )
                        QMessageBox.warning(self, "合成失败", "Helicon 执行后未生成输出文件。")
                        _notify_composed(False)
                        return
                    dialog_cls = self._compose_workbench_dialog_class()
                    dlg = dialog_cls(
                        jpg_paths,
                        out_path,
                        cur_params,
                        angle_label=group.angle_label,
                        parent=self,
                    )
                    dlg.exec()
                    action = dlg.action()
                    new_params = dlg.params()
                    self._helicon_params.set_params(new_params)

                    if action == dialog_cls.ACTION_RECOMPOSE:
                        selected = dlg.selected_jpgs()
                        if len(selected) < 2:
                            QMessageBox.warning(self, "合成", "选中的 JPG 不足 2 张，无法重合成。")
                            return
                        self._retire_tiff(out_path)
                        group.jpg_paths = selected
                        _run_interactive_compose_with_preview(selected, out_path, new_params)
                        return

                    if action == dialog_cls.ACTION_CANCEL:
                        self._retire_tiff(out_path)
                        return

                    # Save to result: persist grouping only after preview approval.
                    persisted = persist_composed_group(
                        db,
                        uid,
                        grouping,
                        group.group_index,
                        tiff_path=out_path,
                        result_sequence=_seq,
                    )
                    self._grouping.load_grouping(uid, persisted.grouping)
                    self._refresh_results_column(uid, persisted.grouping)
                    self._on_helicon_finished(uid)
                    self._workflow_notice(
                        "合成完成",
                        f"TIFF 已生成：{output_name}。JPG 尚未整理；需要归档时请继续点击整理。",
                        state="success",
                        task_key=task_key,
                    )
                    if callable(on_composed):
                        on_composed(True)
                        return
                    QMessageBox.information(self, "合成完成", f"TIFF 已生成：{output_name}")
                    # 自动归档打开时，合成完成后自动把源 JPG 打包 ZIP+命名+移
                    # results（省掉手动点[整理]）。开关默认关。
                    self._maybe_auto_organize(uid, group.group_index)

                def _report_interactive_compose_failure(msg: str):
                    if msg != "用户取消":
                        self._workflow_notice(
                            "合成失败",
                            msg,
                            state="error",
                            task_key=task_key,
                        )
                    if msg != "用户取消":
                        QMessageBox.warning(self, "合成失败", msg)
                    _notify_composed(False)

                self._run_helicon_stack(
                    jpg_paths,
                    out_path,
                    cur_params,
                    _open_compose_review_after_helicon_success,
                    _report_interactive_compose_failure,
                    workflow_task_key=task_key,
                )

            _run_interactive_compose_with_preview(group.jpg_paths, output_path, params)

        except RuntimeError as exc:
            QMessageBox.warning(self, "合成失败", str(exc))
            _notify_composed(False)
        except Exception as exc:
            QMessageBox.warning(self, "合成失败", f"意外错误：{exc}")
            _notify_composed(False)

    def _helicon_output_opts(self) -> dict:
        """Read Helicon output options from settings (mirrors oracle 输出选项).

        Returns ``{format, tiff_compression, quality}``. Default output is TIFF
        (per the software's design: 输出 TIF 或 JPG, 默认 TIF). For TIF the chosen
        TIFF compression is returned and quality is None; for JPG, quality is
        returned and tiff_compression is None — so only the relevant CLI flag
        (-tif: / -j:) gets emitted, exactly like the oracle.
        """
        from app.services.helicon_service import (
            _K_HELICON_OUTPUT_FORMAT,
            _K_HELICON_QUALITY,
            _K_HELICON_TIFF_COMPRESSION,
        )
        qs = self.ctx.settings._qs
        fmt = "jpg" if str(qs.value(_K_HELICON_OUTPUT_FORMAT, "tif")).lower() == "jpg" else "tif"
        if fmt == "jpg":
            return {
                "format": "jpg",
                "tiff_compression": None,
                "quality": int(qs.value(_K_HELICON_QUALITY, 95)),
            }
        return {
            "format": "tif",
            "tiff_compression": str(qs.value(_K_HELICON_TIFF_COMPRESSION, "u")) or "u",
            "quality": None,
        }

    @staticmethod
    def _with_output_ext(path: str, fmt: str) -> str:
        """Swap *path*'s extension to match output format (tif/jpg).

        Helicon infers the encoder from the -save: extension, so it MUST agree
        with the -tif:/-j: flag (oracle app.js:7283-7291).
        """
        base, _ = os.path.splitext(path)
        return base + (".jpg" if fmt == "jpg" else ".tif")

    def _run_helicon_stack(
        self,
        jpg_paths: list[str],
        output_path: str,
        params: dict,
        on_finished,
        on_failed,
        *,
        show_progress_dialog: bool = True,
        workflow_task_key: str = "",
    ) -> None:
        """Launch HeliconWorker (non-blocking), optionally with Helicon progress UI.

        *on_finished(tiff_path: Path)* is called on success.
        *on_failed(msg: str)* is called on error or cancel.
        """
        from app.services.helicon_service import build_helicon_cmd

        # Wire the output options (输出格式 / TIFF 压缩 / JPEG 质量) into the CLI —
        # oracle app.js:7290-7291 (-tif: for tif, -j: for jpg). Previously these
        # settings were saved but never applied (output was always uncompressed tif).
        opts = self._helicon_output_opts()
        try:
            cmd = build_helicon_cmd(
                jpg_paths=jpg_paths,
                output_file=output_path,
                method=str(params["method"]),
                radius=str(params["radius"]),
                smoothing=str(params["smoothing"]),
                tiff_compression=opts["tiff_compression"],
                quality=opts["quality"],
                input_list_dir=os.path.dirname(os.path.abspath(output_path)),
            )
        except FileNotFoundError as exc:
            on_failed(str(exc))
            return
        except RuntimeError as exc:
            on_failed(str(exc))
            return

        progress = None
        if show_progress_dialog:
            progress = QProgressDialog(
                f"正在合成 {len(jpg_paths)} 张 JPG…",
                "取消",
                0,
                0,
                self,
            )
            progress.setWindowModality(Qt.WindowModality.NonModal)
            progress.setWindowTitle("Helicon 合成")
            progress.setMinimumDuration(0)

        worker = HeliconWorker(cmd=cmd, output_path=output_path, parent=self)
        if not hasattr(self, "_helicon_workers"):
            self._helicon_workers = set()
        if not hasattr(self, "_helicon_progress_dialogs"):
            self._helicon_progress_dialogs = set()
        if not hasattr(self, "_helicon_worker_by_task_key"):
            self._helicon_worker_by_task_key = {}
        self._helicon_workers.add(worker)
        task_key = str(workflow_task_key or "")
        if task_key:
            self._helicon_worker_by_task_key[task_key] = worker
        if progress is not None:
            self._helicon_progress_dialogs.add(progress)
        self._helicon_worker = worker  # legacy single-worker reference
        callback_sent = {"value": False}

        def _release_helicon_worker() -> None:
            if progress is not None:
                progress.close()
            self._helicon_workers.discard(worker)
            if progress is not None:
                self._helicon_progress_dialogs.discard(progress)
            if getattr(self, "_helicon_worker", None) is worker:
                self._helicon_worker = None
            for key, mapped in list(getattr(self, "_helicon_worker_by_task_key", {}).items()):
                if mapped is worker:
                    self._helicon_worker_by_task_key.pop(key, None)
            if progress is not None and getattr(self, "_helicon_progress", None) is progress:
                self._helicon_progress = None
            worker.deleteLater()

        def _handle_helicon_worker_finished(tiff_path):
            _release_helicon_worker()
            if callback_sent["value"]:
                return
            callback_sent["value"] = True
            on_finished(tiff_path)

        def _handle_helicon_worker_failed(msg: str):
            _release_helicon_worker()
            if callback_sent["value"]:
                return
            callback_sent["value"] = True
            on_failed(msg)

        if progress is not None:
            def _cancel_running_helicon_worker():
                # 场景: 用户在 Helicon 合成进度框点「取消」。
                # 理由(Fable 5, 2026-07-11): worker.cancel() 会 kill 子进程并
                #   quit(), 但 HeliconWorker.run() 在 _cancel_requested 为真时
                #   直接 return **不 emit finished/failed** → 挂在这两个信号上的
                #   _release_helicon_worker(deleteLater + 从 set/map 移除) 永不
                #   触发 → 每取消一次泄漏一个 QThread 对象 + set/map 残留, 攒到
                #   退出才被 stop_background_work 兜底。此处取消后主动释放。
                worker.cancel()
                progress.setLabelText("正在取消 Helicon 合成…")
                if not callback_sent["value"]:
                    callback_sent["value"] = True
                    on_failed("用户取消")
                # cancel() 已 kill 子进程, run() 随即从 return 退出; 先 wait 让
                # 线程真正结束再 deleteLater, 避免 destroyed-while-running 崩溃。
                # wait 超时(线程卡死)则不 deleteLater, 留给 stop_background_work
                # 退出时兜底; 但仍从活动集合摘除并关进度框, 不残留。
                if worker.wait(3000):
                    _release_helicon_worker()
                else:
                    self._helicon_workers.discard(worker)
                    for key, mapped in list(
                        getattr(self, "_helicon_worker_by_task_key", {}).items()
                    ):
                        if mapped is worker:
                            self._helicon_worker_by_task_key.pop(key, None)
                    if progress is not None:
                        progress.close()
                        self._helicon_progress_dialogs.discard(progress)

        worker.finished.connect(_handle_helicon_worker_finished)
        worker.failed.connect(_handle_helicon_worker_failed)
        if progress is not None:
            progress.canceled.connect(_cancel_running_helicon_worker)

        if progress is not None:
            progress.show()
            self._helicon_progress = progress  # legacy single-progress reference
        worker.start()

    # ── 批量[合成]/[合成+整理] 顺序队列 ────────────────────────────────────────
    # 合成与整理都在后台 worker 完成。批量绝不能紧循环 emit——会同时启动
    # 多个 worker 互相覆盖,且整理会在合成完成前读到空 composed。
    # 故由 workbench 串行驱动:合成完成(异步回调)→ 后台整理该组 → 下一组。
    # 批量时走 `_compose_group_headless`(无预览/结果确认框),满足"一键直合"。

    def _resolve_compose_output_name(self, db, uid, group, results_dir, incoming_dir,
                                     *, allow_auto_seq: bool = True):
        """统一的「输出 TIF 名」解析(合成单组/批量共用)。返回 (name.tif, seq)。

        优先级:
          ① 用户在该组「输出 TIF」框手填的覆盖值(去后缀+.tif)
          ② 有真编号 → organize_preview 建议成果名(编号-序号.tif)
          ③ 无编号(临时分组 ad-hoc) → 组序.tif(组0→1.tif, 组1→2.tif)
             —— allow_auto_seq=False 时改为返回空名, 由调用方问用户(见下)。
        seq:真编号取 preview.next_seq;ad-hoc 取 group_index+1。
        """
        from app.services.compose_workflow_service import resolve_compose_output_name
        return resolve_compose_output_name(
            db, uid, group, results_dir, incoming_dir, allow_auto_seq=allow_auto_seq)

    def _ensure_group_output_name(self, db, uid, group, results_dir, incoming_dir):
        """**人点的**单组合成用: ad-hoc 组没名字就问用户。返回 (name, seq); 取消 -> (None, 0)。

        场景(用户裁定 PROJECT_MEMORY:76 + 2026-07-12 口头确认):
          没有激活编号时点分组面板的 [合成], 旧代码静默输出 1.tif / 2.tif —— 过几天
          没人知道那是什么标本。现在弹一次「归属到编号 / 自由输出名」(复用主工具栏
          那条路已有的对话框, 不另造)。
          **批量 / 合成+整理一条龙不走这里** —— 用户裁定那条路必须零弹框, 继续用
          组序.tif(见 _compose_group_headless)。(Fable 5, 2026-07-12)
        """
        name, seq = self._resolve_compose_output_name(
            db, uid, group, results_dir, incoming_dir, allow_auto_seq=False)
        if name:
            return name, seq

        prompt = getattr(self, "_prompt_selected_compose_target", None)
        if not callable(prompt):   # 兜底: 拿不到对话框就退回旧行为, 绝不卡住用户
            return self._resolve_compose_output_name(
                db, uid, group, results_dir, incoming_dir)
        target = prompt(len(list(getattr(group, "jpg_paths", []) or [])), organise=False)
        if target is None:
            self._status_message("已取消：未指定成果名或目标编号。")
            return None, 0

        target_uid = str(getattr(target, "uid", "") or "").strip()
        free_stem = str(getattr(target, "output_name", "") or "").strip()
        if target_uid:
            return self._resolve_compose_output_name(
                db, target_uid, group, results_dir, incoming_dir)
        if free_stem:
            group.output_name = free_stem
            return self._resolve_compose_output_name(
                db, uid, group, results_dir, incoming_dir)
        self._status_message("已取消：成果名不能为空。")
        return None, 0

    def _start_compose_batch(self, uid: str, organise: bool) -> None:
        """启动批量合成队列。organise=True 时每组合成完后立即整理该组。"""
        if not uid:
            return
        # 场景: 批量合成/合成+整理跑到一半, 用户又点了一次批量按钮。
        # 理由(Fable 5, 2026-07-11 拍照工作区审查 A3): 旧代码无守卫, 第二次点击
        #   直接 self._batch = {...} 覆盖在飞批次 → 已启动的合成回调
        #   (_batch_group_done)读到新队列, 旧队列组变孤儿、同组可能起两个归档
        #   worker(double-archive)。organise 侧本有守卫(workbench_organise_
        #   workflow.py:73)但只查自己; 这里补成双向互斥: 任一批量在跑, 都拒绝
        #   再启动并给可见提示(不是静默覆盖)。
        # §7 旧: (无守卫, 直接往下走到 self._batch = {...})
        running_label = None
        if getattr(self, "_batch", None) is not None:
            running_label = str(self._batch.get("label") or "批量合成")
        elif getattr(self, "_organise_batch", None) is not None:
            running_label = str(self._organise_batch.get("label") or "批量整理")
        if running_label:
            self._batch_status(f"{running_label}正在运行，请稍候。")
            self._workflow_notice(
                "批量任务已在运行",
                f"{running_label}尚未完成，请等待当前任务结束后再启动新的批量操作。",
                state="info",
                force_show=True,
                task_key=f"batch-compose-running:{uid}",
            )
            return
        grouping = self._get_grouping_for_uid(uid)
        selected = set()
        try:
            selected = set(self._grouping.selected_group_indexes())
        except Exception:
            selected = set()
        queue = list(compose_batch_queue(grouping, selected))
        if not queue:
            # 无待合成组:合成+整理 → 退而整理已合成组;纯合成 → 状态栏提示。
            if organise:
                self._organise_all_batch(
                    uid,
                    silent_batch=True,
                    workflow_label="批量合成+整理",
                )
            else:
                self._batch_status("无待合成组。")
                self._workflow_notice(
                    "批量合成无需处理",
                    "没有待合成的分组。",
                    state="info",
                    force_show=True,
                    task_key=f"batch-compose-empty:{uid}",
                )
            return
        # 只有确实存在待合成组时才需要 Helicon。已有 TIF 的组点
        # [合成+整理]应直接进入归档，不能被合成工具检测提前拦截。
        from app.services.helicon_service import detect_helicon
        if not detect_helicon():
            QMessageBox.warning(
                self, "未检测到 Helicon Focus",
                "未找到 Helicon Focus，无法批量合成。请安装并设置 "
                "HELICON_FOCUS_PATH 环境变量。",
            )
            self._workflow_notice(
                "批量合成失败",
                "未找到 Helicon Focus，无法批量合成。",
                state="error",
                force_show=True,
                task_key=f"batch-compose-no-helicon:{uid}",
            )
            return
        label = "批量合成+整理" if organise else "批量合成"
        task_key = f"batch-compose:{uid}:{'organise' if organise else 'compose'}"
        self._batch = {
            "uid": uid,
            "queue": queue,
            "organise": organise,
            "total": len(queue),
            "done": 0,
            "label": label,
            "task_key": task_key,
        }
        self._workflow_notice(
            f"{label}：准备开始",
            f"共 {len(queue)} 组，将按顺序在后台处理。",
            state="busy",
            force_show=True,
            task_key=task_key,
        )
        self._compose_next_in_batch()

    def _compose_next_in_batch(self) -> None:
        """取队首组合成;队列空 → 清状态 + 状态栏提示完成。"""
        b = self._batch
        if not b:
            return
        if not b["queue"]:
            label = str(b.get("label") or "批量合成")
            total = int(b.get("total", 0))
            task_key = str(b.get("task_key") or "")
            self._batch = None
            self._batch_status("批量合成完成。")
            self._workflow_notice(
                f"{label}完成",
                f"已处理 {total} 组。",
                state="success",
                task_key=task_key,
            )
            return
        uid = b["uid"]
        idx = b["queue"].pop(0)
        done = int(b.get("done", 0))
        total = int(b.get("total", done + 1))
        label = str(b.get("label") or "批量合成")
        task_key = str(b.get("task_key") or "")
        self._batch_status(f"批量合成 {done + 1}/{total}：组 {idx}")
        self._workflow_notice(
            f"{label}：正在合成",
            f"第 {done + 1}/{total} 组：组 {idx}。",
            state="busy",
            task_key=task_key,
        )
        self._compose_group_headless(
            uid,
            idx,
            lambda ok: self._batch_group_done(ok, uid, idx),
            background=True,
            show_progress_dialog=not bool(b.get("organise")),
            workflow_task_key=task_key,
        )

    def _batch_group_done(self, success: bool, uid: str, group_index: int) -> None:
        """单组合成回调：需要整理时，等待后台归档结束再处理下一组。"""
        if self._batch is None:
            return
        self._batch["done"] = int(self._batch.get("done", 0)) + 1
        if success and self._batch.get("organise"):
            # 一条龙:批量整理走静默模式——跳过激活拦截/TIF改名框/同名确认/成功提示,
            # 但 JPG删除四闸 + TIFF永不自动删 红线照常(都在 archive_group 内,未碰)。
            started = self._on_organise_requested(
                uid,
                group_index,
                silent_batch=True,
                workflow_task_key=str(self._batch.get("task_key") or ""),
                workflow_label=str(self._batch.get("label") or "批量合成+整理"),
                on_complete=lambda _ok: self._compose_next_in_batch(),
            )
            if not started:
                self._compose_next_in_batch()
            return
        elif not success:
            done = int(self._batch.get("done", 0))
            total = int(self._batch.get("total", done))
            label = str(self._batch.get("label") or "批量合成")
            task_key = str(self._batch.get("task_key") or "")
            self._batch_status(f"组 {group_index} 合成失败，继续下一组（{done}/{total}）")
            self._workflow_notice(
                f"{label}：合成失败，继续下一组",
                f"组 {group_index} 合成失败；已完成 {done}/{total}。",
                state="error",
                task_key=task_key,
            )
        self._compose_next_in_batch()

    def _batch_status(self, msg: str) -> None:
        """非阻塞反馈(状态栏);批量回调里绝不用模态框——会卡死且打断链路。"""
        try:
            self.window().statusBar().showMessage(msg, 4000)
        except Exception:
            pass

    def _compose_group_headless(
        self,
        uid: str,
        group_index: int,
        on_done,
        *,
        background: bool = False,
        show_progress_dialog: bool = True,
        workflow_task_key: str = "",
    ) -> None:
        """批量用:无确认框合成单组。完成调 on_done(success: bool)。

        复刻 `_on_compose_requested` 成功路径的保存块,但剥掉预览框/结果框
        (满足"一键直合不弹框")。组 JPG < 2 直接 on_done(False) 跳过。
        产出名:每组 output_name 覆盖 > 否则 organize_preview 的建议成果名。
        """
        db = self.ctx.get_db()
        if not uid:
            on_done(False)
            return
        try:
            grouping = self._get_grouping_for_uid(uid)
            group = next(
                (g for g in grouping.groups if g.group_index == group_index), None
            )
            if group is None or len(group.jpg_paths) < 2:
                on_done(False)  # 批量不弹隐式兜底问句,JPG 不足直接跳过
                return

            incoming_dir, results_dir = self._ensure_compose_incoming_dir(group)

            output_name, _seq = self._resolve_compose_output_name(
                db, uid, group, results_dir, incoming_dir)
            output_path = os.path.join(incoming_dir, output_name)
            output_path = self._with_output_ext(
                output_path, self._helicon_output_opts()["format"]
            )
            params = self._helicon_params.get_params()

            def _save_headless_compose_result(tiff_path):
                try:
                    persisted = persist_composed_group(
                        db,
                        uid,
                        grouping,
                        group.group_index,
                        tiff_path=output_path,
                        result_sequence=_seq,
                    )
                except Exception:
                    on_done(False)
                    return
                panel_uid = getattr(self._grouping, "_uid", None)
                if not background or self._current_uid == uid or panel_uid == uid:
                    self._grouping.load_grouping(uid, persisted.grouping)
                    self._refresh_results_column(uid, persisted.grouping)
                self._on_helicon_finished(uid, select_uid=not background)
                on_done(True)

            def _mark_headless_compose_failed(msg: str):
                on_done(False)

            self._run_helicon_stack(
                group.jpg_paths,
                output_path,
                params,
                _save_headless_compose_result,
                _mark_headless_compose_failed,
                show_progress_dialog=show_progress_dialog,
                workflow_task_key=workflow_task_key,
            )
        except Exception:
            on_done(False)
