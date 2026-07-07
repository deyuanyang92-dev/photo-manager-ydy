"""Selected-JPG implicit compose workflow for WorkbenchView."""
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


class WorkbenchImplicitComposeWorkflowMixin:
    """Selected-JPG implicit compose workflow for WorkbenchView."""

    def _get_active_uid(self) -> Optional[str]:
        """Return the currently active specimen UID from the tasks table."""
        db = self.ctx.get_db()
        if not db:
            return None
        try:
            from app.services.activation_service import get_active_uid
            return get_active_uid(db)
        except Exception:
            return None

    def _get_attributed_jpg_paths(self, uid: str) -> list[str]:
        """Return paths of all JPGs currently attributed to *uid* in the monitor.

        Used as implicit-batch fallback when a group has < 2 JPGs.
        Mirrors web composeImplicitActiveBatch() app.js:5660–5706.
        """
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db:
            return []
        try:
            from app.services.capture_workflow_service import attributed_jpg_paths
            inc, res = self._resolve_capture_subdirs()
            return attributed_jpg_paths(
                project_dir,
                db,
                uid,
                last_scan=self._last_scan_result,
                incoming_subdir=inc,
                results_subdir=res,
            )
        except Exception:
            return []

    def _build_implicit_group(
        self,
        uid: str,
        jpg_paths: Optional[list[str]] = None,
        *,
        output_name: Optional[str] = None,
    ) -> Optional[int]:
        """把明确来源的 JPG 建成一个待合成组。

        jpg_paths 不为空时，只消费这些手选 JPG；未传 jpg_paths 时才回退到
        uid 下已归属且未占用的 JPG。返回新组 group_index；可用 JPG <2 张则
        返回 None。占用 = 已在任何分组里（草稿或已合成）。
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return None
        from app.services.compose_workflow_service import create_implicit_group

        candidates = (
            list(jpg_paths)
            if jpg_paths is not None
            else self._get_attributed_jpg_paths(uid)
        )
        result = create_implicit_group(
            db,
            uid,
            candidates,
            output_name=output_name,
        )
        if result.group_index is None or result.grouping is None:
            return None
        self._grouping.load_grouping(uid, result.grouping)
        return result.group_index

    def _unoccupied_jpg_paths(self, uid: str, jpg_paths: list[str]) -> list[str]:
        """Filter out JPGs already present in any group for this UID."""
        db = self.ctx.get_db()
        if not db or not uid:
            return []
        try:
            from app.services.compose_workflow_service import unoccupied_jpg_paths
            return unoccupied_jpg_paths(db, uid, jpg_paths)
        except Exception:
            return list(jpg_paths)

    def _default_selected_compose_uid(self) -> str:
        candidates = []
        try:
            candidates.append(self._naming.current_uid())
        except Exception:
            pass
        candidates.extend([self._current_uid, self._get_active_uid()])
        for uid in candidates:
            text = str(uid or "").strip()
            if text:
                return text
        return ""

    def _prompt_selected_compose_target(
        self,
        jpg_count: int,
        *,
        organise: bool,
        default_uid: str = "",
    ) -> Optional[_SelectedComposeTarget]:
        """Ask how selected JPGs should be named/owned."""
        from app.services.grouping_service import ADHOC_GROUPING_UID

        default_uid = (default_uid or self._default_selected_compose_uid()).strip()
        dlg = QDialog(self)
        dlg.setWindowTitle("合成输出")
        dlg.setMinimumWidth(520)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        action = "合成+整理" if organise else "合成"
        title = QLabel(f"已选 {jpg_count} 张 JPG，准备{action}")
        title.setObjectName("Section")
        root.addWidget(title)

        uid_radio = QRadioButton("归属到编号，按编号自动命名")
        uid_edit = QLineEdit(default_uid)
        uid_edit.setPlaceholderText("输入或粘贴编号；成果将自动使用下一个序号")
        root.addWidget(uid_radio)
        root.addWidget(uid_edit)

        name_radio = QRadioButton("自由输出名，TIF 与 ZIP 使用同名")
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("例如 SC002-结果1；无需写 .tif/.zip")
        root.addWidget(name_radio)
        root.addWidget(name_edit)

        hint = QLabel("选择编号时不能手填成果名；软件会按该编号已有成果自动顺延 -1、-2、-3。")
        hint.setObjectName("MutedSmall")
        hint.setWordWrap(True)
        root.addWidget(hint)

        if default_uid:
            uid_radio.setChecked(True)
        else:
            name_radio.setChecked(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("继续")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        root.addWidget(buttons)

        def _accept() -> None:
            if uid_radio.isChecked():
                uid = uid_edit.text().strip()
                if not uid:
                    QMessageBox.warning(dlg, "合成输出", "请输入要归属的编号。")
                    return
                dlg.done(QDialog.DialogCode.Accepted)
                return
            name = Path(name_edit.text().strip()).stem
            if not name:
                QMessageBox.warning(dlg, "合成输出", "请输入输出名称。")
                return
            dlg.done(QDialog.DialogCode.Accepted)

        buttons.accepted.connect(_accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        if uid_radio.isChecked():
            uid = uid_edit.text().strip()
            return _SelectedComposeTarget(
                uid=uid,
                output_name=None,
                assign_to_uid=True,
            )
        return _SelectedComposeTarget(
            uid=ADHOC_GROUPING_UID,
            output_name=Path(name_edit.text().strip()).stem,
            assign_to_uid=False,
        )

    def _assign_selected_jpgs_to_uid(self, uid: str, jpg_paths: list[str]) -> None:
        project_dir = self.ctx.current_project_dir
        db = self.ctx.get_db()
        if not project_dir or not db or not uid or not jpg_paths:
            return
        try:
            from app.services.compose_workflow_service import assign_selected_jpgs_to_uid
            assign_selected_jpgs_to_uid(project_dir, db, uid, jpg_paths)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "JPG 归属写入失败 (uid=%s)", uid, exc_info=True,
            )

    def _resolve_implicit_compose_target(
        self,
        selected_jpgs: list[str],
        active_uid: Optional[str],
        *,
        organise: bool,
    ) -> Optional[_SelectedComposeTarget]:
        """Resolve the main compose button's target using the two real modes.

        Decision table:
        - selected JPGs + active UID: use active UID, auto-name, no prompt.
        - selected JPGs + no active UID: prompt for target UID or free output name.
        - no selected JPGs + active UID + auto archive: use active UID's
          unoccupied attributed JPGs.
        - no selected JPGs otherwise: nothing to compose from the main toolbar.

        Existing JPG attribution only pre-fills the prompt when no UID is active.
        It never overrides an active UID and never silently decides a filename.
        """
        owners = []
        if selected_jpgs and not active_uid:
            try:
                owners = self._monitor.selected_jpg_owner_uids()
            except Exception:
                owners = []

        from app.services.compose_workflow_service import resolve_implicit_compose_target

        target = resolve_implicit_compose_target(
            selected_jpgs,
            active_uid,
            auto_archive_enabled=self._auto_archive_enabled(),
            selected_owner_uids=owners,
            prompt_target=lambda jpg_count, default_uid: (
                self._prompt_selected_compose_target(
                    jpg_count,
                    organise=organise,
                    default_uid=default_uid,
                )
            ),
        )
        if target is None and not selected_jpgs:
            self._status_message("请先选中要合成的 JPG；或激活编号并开启自动归档。")
        return target

    def _auto_archive_enabled(self) -> bool:
        """Return true for the user's explicit incoming auto-archive mode."""
        try:
            if bool(getattr(self.ctx.settings, "auto_organize_after_compose", False)):
                return True
        except Exception:
            pass
        try:
            return bool(self._monitor.auto_compress_enabled())
        except Exception:
            return False

    def _on_compose_implicit(self, organise: bool = False) -> None:
        """主界面[合成]：优先处理手选 JPG；自动归档打开时可处理激活编号 JPG。

        有手选 JPG 时，手选优先。没有手选 JPG 时，只有“激活编号 + 自动归档”
        这个显式快速拍摄模式才会取该编号下未占用 JPG。
        """
        selected = []
        try:
            selected = self._monitor.selected_jpg_paths()
        except Exception:
            selected = []
        active_uid = self._get_active_uid()
        target = self._resolve_implicit_compose_target(
            selected,
            active_uid,
            organise=organise,
        )
        if target is None:
            return
        uid = target.uid
        if selected and target.assign_to_uid:
            self._assign_selected_jpgs_to_uid(uid, selected)
        idx = self._build_implicit_group(
            uid,
            selected or None,
            output_name=target.output_name,
        )
        if idx is None:
            self._status_message("没有可合成的未占用 JPG（至少 2 张）")
            return
        silent = bool(getattr(self.ctx.settings, "silent_compose", False))
        task_key = f"implicit-compose:{uid}:{idx}:{'organise' if organise else 'compose'}"
        if organise:
            jpg_count = len(selected) if selected else len(self._get_attributed_jpg_paths(uid))
            self._workflow_notice(
                "合成+整理：正在合成 TIFF",
                f"已接收 {jpg_count} 张 JPG。合成完成后会自动整理、生成 ZIP，并从待处理队列移出这些 JPG。",
                state="busy",
                force_show=True,
                task_key=task_key,
            )
            self._status_message("合成+整理已转入后台，可继续拍摄或新增编号。")
            self._compose_group_headless(
                uid,
                idx,
                lambda ok: self._implicit_compose_done(ok, uid, idx, organise, task_key),
                background=True,
                show_progress_dialog=False,
                workflow_task_key=task_key,
            )
            return
        if silent:
            jpg_count = len(selected) if selected else len(self._get_attributed_jpg_paths(uid))
            self._workflow_notice(
                "合成：正在生成 TIFF",
                f"已接收 {jpg_count} 张 JPG。合成完成后 TIFF 会留在待处理区，JPG 仍需整理归档。",
                state="busy",
                force_show=True,
                task_key=task_key,
            )
            self._compose_group_headless(
                uid,
                idx,
                lambda ok: self._implicit_compose_done(ok, uid, idx, organise, task_key),
                background=False,
                workflow_task_key=task_key,
            )
            return
        self._on_compose_requested(uid, idx)

    def _implicit_compose_done(
        self,
        success: bool,
        uid: str,
        group_index: int,
        organise: bool,
        task_key: str = "",
    ) -> None:
        if not success:
            if organise:
                self._workflow_notice(
                    "合成+整理失败",
                    "合成阶段没有生成 TIFF，因此整理没有启动。",
                    state="error",
                    task_key=task_key,
                )
            else:
                self._workflow_notice(
                    "合成失败",
                    "合成阶段没有生成 TIFF。",
                    state="error",
                    task_key=task_key,
                )
            self._status_message("合成失败或未生成成果")
            return
        if organise:
            self._workflow_notice(
                "合成+整理：正在整理",
                "TIFF 已生成，正在打包 JPG 原片并登记 ZIP。",
                state="busy",
                task_key=task_key,
            )
            self._status_message("合成完成，正在整理并打包 JPG…")
            def _done(ok: bool) -> None:
                if ok:
                    self._status_message("合成+整理完成")
                else:
                    reason = str(
                        getattr(self, "_last_organise_failure_reason", "") or ""
                    ).strip()
                    detail = reason or "整理或归档没有完成。"
                    self._workflow_notice(
                        "合成+整理失败",
                        detail,
                        state="error",
                        task_key=task_key,
                    )
                    self._status_message("合成完成，但整理失败或未完成")

            started = self._on_organise_requested(
                uid,
                group_index,
                silent_batch=True,
                workflow_task_key=task_key,
                workflow_label="合成+整理",
                on_complete=_done,
            )
            if not started:
                reason = str(getattr(self, "_last_organise_failure_reason", "") or "").strip()
                suffix = f"：{reason}" if reason else ""
                self._workflow_notice(
                    "合成+整理失败",
                    reason or "整理没有启动。",
                    state="error",
                    task_key=task_key,
                )
                self._status_message(f"合成完成，但整理未启动{suffix}")
        else:
            detail = "TIFF 已生成在待处理区；JPG 尚未整理归档。"
            try:
                grouping = self._get_grouping_for_uid(uid)
                group = next(
                    (g for g in grouping.groups if g.group_index == group_index),
                    None,
                )
                tiff_path = getattr(group, "composed_tiff_path", "") if group else ""
                if tiff_path:
                    detail = f"TIFF 已生成：{Path(tiff_path).name}。JPG 尚未整理归档。"
            except Exception:
                pass
            self._workflow_notice(
                "合成完成",
                detail,
                state="success",
                task_key=task_key,
            )
            self._status_message("合成完成，成果已生成在 incoming")

    def _show_compose_preview(self, jpg_paths: list[str]) -> Optional[list[str]]:
        """Pre-compose JPG checklist dialog.

        Mirrors web renderComposePreviewModal() app.js:6597 — simplified Qt
        version: shows all JPG filenames as a checkable list so the user can
        confirm or deselect files before Helicon runs.

        Returns:
            list[str]: selected (checked) JPG paths if user confirms.
            None:      if the user cancelled.
        """
        from PyQt6.QtWidgets import (
            QCheckBox,
            QDialog,
            QDialogButtonBox,
            QScrollArea,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("合成预览 — 确认原片")
        dlg.setMinimumWidth(460)

        root_lay = QVBoxLayout(dlg)
        root_lay.setContentsMargins(16, 16, 16, 16)
        root_lay.setSpacing(10)

        info = QLabel(
            f"即将合成 {len(jpg_paths)} 张 JPG。\n取消勾选可从本次合成中排除："
        )
        info.setObjectName("Muted")
        info.setWordWrap(True)
        root_lay.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(260)
        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setContentsMargins(4, 4, 4, 4)
        inner_lay.setSpacing(4)

        checkboxes: list[tuple[QCheckBox, str]] = []
        for p in jpg_paths:
            cb = QCheckBox(Path(p).name)
            cb.setChecked(True)
            cb.setToolTip(p)
            inner_lay.addWidget(cb)
            checkboxes.append((cb, p))
        inner_lay.addStretch()
        scroll.setWidget(inner)
        root_lay.addWidget(scroll)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("✓ 开始合成")
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        root_lay.addWidget(btns)

        # Center on parent screen (dual-monitor safe)
        try:
            from app.utils.ui import center_on
            center_on(dlg, self)
        except Exception:
            pass

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        return [path for cb, path in checkboxes if cb.isChecked()]
