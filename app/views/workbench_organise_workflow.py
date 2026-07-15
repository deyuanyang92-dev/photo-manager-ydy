"""Organise/archive workflow for WorkbenchView."""
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


class WorkbenchOrganiseWorkflowMixin:
    """Organise/archive workflow for WorkbenchView."""

    def _organise_all_batch(
        self,
        uid: str,
        silent_batch: bool = False,
        workflow_label: str | None = None,
    ) -> None:
        """[整理] 批量串行归档已合成未归档组。"""
        if not uid:
            return
        label = workflow_label or "批量整理"
        grouping = self._get_grouping_for_uid(uid)
        selected = set()
        try:
            selected = set(self._grouping.selected_group_indexes())
        except Exception:
            selected = set()
        targets = organize_batch_targets(grouping, selected)
        if not targets:
            self._batch_status("无可整理组。")
            self._workflow_notice(
                f"{label}无需处理",
                "没有可整理的已合成分组。",
                state="info",
                force_show=True,
                task_key=f"organise-batch-empty:{uid}:{label}",
            )
            return
        # 场景: 批量合成+整理(_batch)跑一半, 用户点「批量整理」。
        # 理由(Fable 5, 2026-07-11 A3): 旧守卫只查自己的 _organise_batch, 不知道
        #   compose 批也在驱动整理 → 同一组可能被两条批量链各起一个归档 worker。
        #   补成双向: compose 批在跑也拒绝。
        # §7 旧: if getattr(self, "_organise_batch", None) is not None: (只查单侧)
        _compose_batch = getattr(self, "_batch", None)
        if getattr(self, "_organise_batch", None) is not None or _compose_batch is not None:
            running = (
                str(_compose_batch.get("label") or "批量合成")
                if _compose_batch is not None else "整理队列"
            )
            self._batch_status(f"{running}正在运行，请稍候。")
            self._workflow_notice(
                f"{label}已在运行",
                f"{running}正在运行，请等待当前任务完成。",
                state="info",
                force_show=True,
                task_key=f"organise-batch-running:{uid}:{label}",
            )
            return
        task_key = f"organise-batch:{uid}:{label}"
        self._organise_batch = {
            "uid": uid,
            "queue": list(targets),
            "silent_batch": silent_batch,
            "total": len(targets),
            "done": 0,
            "label": label,
            "task_key": task_key,
        }
        self._workflow_notice(
            f"{label}：准备开始",
            f"共 {len(targets)} 组，将按顺序后台整理。",
            state="busy",
            force_show=True,
            task_key=task_key,
        )
        self._organise_next_in_batch()

    def _organise_next_in_batch(self) -> None:
        """Run the next archive job after the previous worker has finished."""
        b = getattr(self, "_organise_batch", None)
        if not b:
            return
        if not b["queue"]:
            label = str(b.get("label") or "批量整理")
            total = int(b.get("total", 0))
            task_key = str(b.get("task_key") or "")
            self._organise_batch = None
            self._batch_status("批量整理完成。")
            self._workflow_notice(
                f"{label}完成",
                f"已整理 {total} 组。",
                state="success",
                task_key=task_key,
            )
            return
        uid = b["uid"]
        idx = b["queue"].pop(0)
        done = int(b.get("done", 0))
        total = int(b.get("total", done + 1))
        label = str(b.get("label") or "批量整理")
        task_key = str(b.get("task_key") or "")
        self._batch_status(f"批量整理 {done + 1}/{total}：组 {idx}")
        self._workflow_notice(
            f"{label}：正在整理",
            f"第 {done + 1}/{total} 组：组 {idx}。",
            state="busy",
            task_key=task_key,
        )
        callback_sent = {"value": False}

        def _done(_ok: bool) -> None:
            if callback_sent["value"]:
                return
            callback_sent["value"] = True
            current = getattr(self, "_organise_batch", None)
            if current is not None:
                current["done"] = int(current.get("done", 0)) + 1
            self._organise_next_in_batch()

        started = self._on_organise_requested(
            uid,
            idx,
            silent_batch=bool(b.get("silent_batch")),
            workflow_task_key=task_key,
            workflow_label=label,
            on_complete=_done,
        )
        if not started:
            _done(False)

    def _on_organise_selected(self) -> None:
        """整理监控区选中的 JPG + TIFF，不重新合成。

        有激活编号：TIFF 自动按激活编号的下一个成果名重命名，ZIP 同名。
        无激活编号：保留 TIFF 文件名，ZIP 直接用 TIFF stem 命名。
        """
        try:
            jpg_paths = self._monitor.selected_jpg_paths()
            tiff_paths = self._monitor.selected_tiff_paths()
        except Exception:
            jpg_paths, tiff_paths = [], []
        if len(tiff_paths) != 1 or not jpg_paths:
            QMessageBox.information(
                self,
                "整理",
                "请选中至少 1 张 JPG 原片和 1 个 TIFF 成片。",
            )
            return
        self._organise_jpgs_with_tiff(
            jpg_paths,
            tiff_paths[0],
            silent=True,
            confirm_tiff_uid_mismatch=True,
        )

    @staticmethod
    def _tiff_name_looks_mismatched_with_uid(tiff_path: str, uid: str) -> bool:
        """Return True when a TIFF filename appears to carry another specimen ID.

        Generic external names such as ``HeliconFocus.tif`` are intentionally
        ignored; the warning is for names with several UID-like tokens that
        barely overlap the active specimen.
        """
        if not tiff_path or not uid:
            return False
        stem = Path(tiff_path).stem.upper()
        uid_text = str(uid or "").upper()
        compact_stem = re.sub(r"[^A-Z0-9]+", "", stem)
        compact_uid = re.sub(r"[^A-Z0-9]+", "", uid_text)
        if not compact_stem or not compact_uid:
            return False
        if compact_uid in compact_stem or compact_stem in compact_uid:
            return False

        stem_tokens = [
            token for token in re.split(r"[^A-Z0-9]+", stem)
            if len(token) >= 2
        ]
        uid_tokens = {
            token for token in re.split(r"[^A-Z0-9]+", uid_text)
            if len(token) >= 2
        }
        if len(stem_tokens) < 3 or not uid_tokens:
            return False

        has_date_like = any(token.isdigit() and 6 <= len(token) <= 8 for token in stem_tokens)
        has_code_like = sum(bool(re.search(r"[A-Z]", token)) for token in stem_tokens) >= 2
        if not (has_date_like or has_code_like):
            return False

        overlap = sum(1 for token in set(stem_tokens) if token in uid_tokens)
        return overlap <= 1

    def _confirm_tiff_uid_mismatch(self, tiff_path: str, active_uid: str) -> bool:
        if not self._tiff_name_looks_mismatched_with_uid(tiff_path, active_uid):
            return True
        reply = QMessageBox.question(
            self,
            "确认 TIFF 归属",
            (
                "选中的 TIFF 文件名和当前激活编号差异很大。\n\n"
                f"当前激活编号：{active_uid}\n"
                f"TIFF 文件：{Path(tiff_path).name}\n\n"
                "如果继续，软件会按当前激活编号整理并重命名这个 TIFF。"
                "请确认这不是选错编号或选错文件。"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _organise_jpgs_with_tiff(
        self,
        jpg_paths: list[str],
        tiff_path: str,
        *,
        silent: bool,
        confirm_tiff_uid_mismatch: bool = False,
        on_complete=None,
    ) -> bool:
        """把已有 JPG + TIFF 登记成已合成组，然后走统一整理/归档入口。"""
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            self._status_message("请先打开项目")
            return False

        active_uid = self._get_active_uid()
        incoming_subdir = "incoming-jpg"
        results_subdir = "results"
        if active_uid:
            if confirm_tiff_uid_mismatch and not self._confirm_tiff_uid_mismatch(
                tiff_path,
                active_uid,
            ):
                self._status_message("已取消整理：TIFF 文件名与当前激活编号不一致")
                return False
            try:
                incoming_subdir, results_subdir = self._resolve_capture_subdirs()
            except Exception as exc:
                if silent:
                    self._status_message(f"TIFF 按编号命名失败：{exc}")
                else:
                    QMessageBox.warning(self, "整理", f"TIFF 按编号命名失败：{exc}")
                return False

        try:
            prepared = prepare_existing_tiff_group(
                db,
                active_uid=active_uid,
                jpg_paths=jpg_paths,
                tiff_path=tiff_path,
                project_dir=project_dir,
                incoming_subdir=incoming_subdir,
                results_subdir=results_subdir,
            )
        except Exception as exc:
            prefix = "TIFF 按编号命名失败" if active_uid else "整理准备失败"
            if silent:
                self._status_message(f"{prefix}：{exc}")
            else:
                QMessageBox.warning(self, "整理", f"{prefix}：{exc}")
            return False

        self._grouping.load_grouping(prepared.uid, prepared.grouping)
        return bool(self._on_organise_requested(
            prepared.uid,
            prepared.group_index,
            silent_batch=silent,
            allow_single_jpg=True,
            workflow_label="整理",
            on_complete=on_complete,
        ))

    def _maybe_auto_organize(self, uid: str, group_index: int) -> None:
        """合成成功后的自动整理钩子。

        「自动归档」打开时，直接复用手动整理入口 `_on_organise_requested`：
        把这组源 JPG 打包 ZIP、命名并移到 results。
        """
        if self._auto_archive_enabled():
            self._on_organise_requested(uid, group_index)

    def _load_naming_components(self) -> list[str]:
        from app.services.project_settings_service import (
            DEFAULT_NAMING_RULES,
            load_setting,
        )
        from app.utils.naming import normalize_naming_components

        db = self.ctx.get_db()
        rules = (
            load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
            if db
            else DEFAULT_NAMING_RULES
        )
        return normalize_naming_components(rules.get("components"))

    def _try_bind_adhoc_to_existing_specimen(self, uid: str) -> bool:
        """Link ad-hoc grouping to an existing voucher (no false duplicate warn)."""
        from app.services.grouping_service import ADHOC_GROUPING_UID, SpecimenGrouping

        panel_uid = getattr(self._grouping, "_uid", None)
        if panel_uid not in (None, ADHOC_GROUPING_UID):
            return False
        text = str(uid or "").strip()
        if not text:
            return False
        db = self.ctx.get_db()
        if not db:
            return False
        row = db.execute(
            "SELECT uid FROM specimens WHERE uid = ?", (text,)
        ).fetchone()
        if not row:
            return False
        self._current_uid = text
        self._naming.acknowledge_existing_uid(text)
        panel_grouping = self._grouping.grouping_state()  # §7 旧: getattr(self._grouping, "_grouping", None) —— 活对象, 调用方要就地改 output_name
        if panel_grouping is not None:
            self._grouping.load_grouping(
                text,
                SpecimenGrouping(uid=text, groups=panel_grouping.groups),
            )
        return True

    def _recognize_first_grouping_tiff(self, grouping) -> None:
        """Use the first linked TIFF in the open grouping to seed right-rail fields."""
        if grouping is None or not getattr(grouping, "groups", None):
            return
        if self._naming.has_result_identity():
            return
        for group in grouping.groups:
            tiff_path = getattr(group, "composed_tiff_path", None)
            if tiff_path:
                self._apply_tiff_filename_recognition(tiff_path)
                return

    def _apply_tiff_filename_recognition(
        self,
        tiff_path: str,
        *,
        overwrite: bool = False,
    ) -> None:
        """Parse legacy/standard TIFF names → fill right-rail fields (non-destructive)."""
        from app.services.grouping_service import ADHOC_GROUPING_UID
        from app.utils.naming import recognize_tiff_filename

        components = self._load_naming_components()
        rec = recognize_tiff_filename(Path(tiff_path).stem, components)
        if rec is None:
            return

        if overwrite:
            self._current_uid = None

        self._naming.apply_recognized_fields(
            rec.field_values,
            collection_date=rec.collection_date,
            photo_date=rec.photo_date,
            sequence=rec.sequence,
            inline_labels=rec.inline_labels,
            source_filename=Path(tiff_path).name,
            overwrite=overwrite,
        )

        panel_uid = getattr(self._grouping, "_uid", None)
        preview_uid = self._naming.current_uid()
        target_uid = preview_uid or rec.uid
        if panel_uid in (None, ADHOC_GROUPING_UID) and target_uid:
            if not self._try_bind_adhoc_to_existing_specimen(target_uid):
                db = self.ctx.get_db()
                if db:
                    row = db.execute(
                        "SELECT uid FROM specimens WHERE uid = ?", (target_uid,)
                    ).fetchone()
                    if row:
                        self._load_specimen(target_uid)
                    else:
                        self._current_uid = None
                panel_grouping = self._grouping.grouping_state()  # §7 旧: getattr(self._grouping, "_grouping", None) —— 活对象, 调用方要就地改 output_name
                if panel_grouping is not None:
                    from app.services.grouping_service import SpecimenGrouping
                    self._grouping.load_grouping(
                        target_uid,
                        SpecimenGrouping(uid=target_uid, groups=panel_grouping.groups),
                    )

        self._sync_grouping_outputs_from_naming()
        self._status_message(
            f"已从 TIF 识别编号（{target_uid or rec.uid}，成果 #{rec.sequence}）。"
            "未对应字段已写入拍照备注；保存方式等可稍后补全。"
        )

    def _on_naming_storage_applied(self, old_code: str, new_code: str) -> None:
        """保存方式选定后：刷新拍照备注 + 自动更新分组里的成果输出名（带提示）。"""
        if not (new_code or "").strip():
            return
        self._naming.refresh_legacy_photo_notes()
        updated = self._sync_grouping_outputs_from_naming()
        self._try_bind_adhoc_to_existing_specimen(self._naming.current_uid())
        if updated:
            self._status_message(
                f"已加入保存方式 {new_code.strip()}，成果文件名已同步更新。"
                "请在分组工具「输出」栏核对。"
            )
        elif old_code != new_code:
            self._status_message(
                f"已选择保存方式 {new_code.strip()}，编号预览已更新。"
            )

    def _sync_grouping_outputs_from_naming(self) -> bool:
        """Push standard result stems into auto-legacy group output fields."""
        grouping = self._grouping.grouping_state()  # §7 旧: getattr(self._grouping, "_grouping", None) —— 活对象, 调用方要就地改 output_name
        if grouping is None or not grouping.groups:
            return False
        if not self._naming.storage_code():
            return False
        if not self._naming.has_result_identity():
            return False
        changed = False
        for g in grouping.groups:
            if (
                getattr(g, "status", None) == "organized"
                or getattr(g, "archive_zip", None)
            ):
                continue
            current = getattr(g, "output_name", None) or ""
            if not self._naming.group_output_looks_auto_legacy(current):
                continue
            seq = (
                self._naming.sequence()
                if len(grouping.groups) == 1
                else g.group_index + 1
            )
            new_stem = self._naming.suggested_result_stem(seq=seq)
            if not new_stem or new_stem == current:
                continue
            g.output_name = new_stem
            changed = True
        if changed:
            self._grouping.rebuild()
            self._grouping.grouping_changed.emit()
        return changed

    def _maybe_rename_tiff_before_organize(self, db, uid, grouping, group, project_dir):
        """整理前的 TIFF 命名网关。返回 None=无需改名 / True=已改名 / False=用户取消。

        合成 TIFF 名不符成果规范时（导入的外部 Helicon TIFF），弹确认框按本组编号的下个
        成果名建议改名（可改），确认则磁盘改名 + 更新 group.composed_tiff_path + 持久化。
        """
        from app.utils.naming import (
            recognize_tiff_filename,
            suggest_tiff_filename_preserve_legacy,
            tiff_stem_needs_rename_for_organize,
            coalesce_specimen_dates,
            specimen_date_seg,
        )
        from app.services.organize_service import organize_preview, rename_tiff
        from app.services.grouping_service import save_grouping
        from app.widgets.tiff_rename_dialog import TiffRenameDialog

        tiff_path = group.composed_tiff_path
        current = Path(tiff_path).name
        components = self._load_naming_components()
        stem = Path(tiff_path).stem

        self._apply_tiff_filename_recognition(tiff_path)

        panel_uid = self._naming.current_uid()
        panel_storage = self._naming.storage_code()
        if not tiff_stem_needs_rename_for_organize(
            stem,
            components,
            panel_uid=panel_uid,
            panel_storage=panel_storage,
        ):
            return None

        rec = recognize_tiff_filename(stem, components)
        if rec:
            col, photo = coalesce_specimen_dates(
                self._naming.collection_date(),
                self._naming.photo_date(),
            )
            date_seg = specimen_date_seg(col or None, photo or None)
            values = {
                "province": self._naming.province(),
                "site": self._naming.site(),
                "station": self._naming.station(),
                "species_id": self._naming.species_id(),
                "storage": panel_storage,
                "date_seg": date_seg,
                "collection_date": col,
                "photo_date": photo,
            }
            suggested = suggest_tiff_filename_preserve_legacy(
                stem, components, values, seq=rec.sequence,
            ) + ".tif"
        else:
            inc, res = self._resolve_capture_subdirs()
            try:
                preview = organize_preview(
                    db, uid,
                    os.path.join(project_dir, res),
                    os.path.join(project_dir, inc),
                )
                suggested = preview.suggested_tiff_name
            except Exception:
                suggested = current

        dlg = TiffRenameDialog(current, suggested, parent=self._grouping_ui_parent())
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False  # 取消 → 中止整理
        new_name = dlg.new_name()
        if not new_name:
            return False
        try:
            new_path = rename_tiff(tiff_path, new_name)
        except Exception as exc:
            ui.warn(self._grouping_ui_parent(), "整理", f"TIFF 改名失败：{exc}")
            return False
        if new_path != tiff_path:
            group.composed_tiff_path = new_path
            # §7 旧: try: save_grouping(...) except Exception: pass
            #   -> 磁盘上 TIFF 已经改名了, 库里还记着旧路径, 而且一声不吭 ——
            #      成果区/后续整理会指向一个不存在的文件名。
            # 新(Fable 5, 2026-07-12): 走 _save_grouping_or_warn(失败会喊)。
            #   控制流不变 —— 仍返回 True 让整理继续(整理本身还会再写一次库,
            #   有机会把状态补回来), 只是用户现在**知道**这一步没写进去。
            self._save_grouping_or_warn(db, uid, grouping.groups, what="TIFF 改名")
        return True

    def _organize_tif_only_group(
        self,
        uid: str,
        grouping,
        group,
        *,
        db,
        project_dir: str,
        has_project: bool,
        silent_batch: bool,
        workflow_task_key: str = "",
        workflow_label: str = "整理",
        on_complete=None,
    ) -> bool:
        """Register an existing TIFF to the current specimen without JPG ZIP."""
        dlg_parent = self._grouping_ui_parent()
        if (
            not getattr(group, "composed_tiff_path", "")
            or not os.path.isfile(group.composed_tiff_path)
        ):
            if not silent_batch:
                ui.warn(dlg_parent, "整理", "找不到该组 TIFF 文件。")
            self._workflow_notice(
                f"{workflow_label}失败",
                "找不到该组 TIFF 文件。",
                state="error",
                task_key=workflow_task_key,
            )
            if on_complete is not None:
                on_complete(False)
            return False

        from app.services.capture_workflow_service import register_tif_only_group

        _inc, res = self._resolve_capture_subdirs() if has_project else ("", "results")
        project_root = getattr(self.ctx, "current_project_root", None)
        if not isinstance(project_root, str):
            project_root = None
        result = register_tif_only_group(
            db,
            uid,
            grouping,
            group,
            project_dir=project_dir if has_project else "",
            results_subdir=res,
            project_root=project_root,
        )
        if result.metadata.error:
            self._status_message(f"TIFF 元数据写入失败：{result.metadata.error}")
        elif (
            result.metadata.skipped
            and result.metadata.skipped not in {"disabled", "unchanged"}
        ):
            self._status_message(f"TIFF 元数据未写入：{result.metadata.skipped}")

        self._refresh_monitor()
        if not silent_batch or self._current_uid == uid:
            self._grouping.load_grouping(uid, grouping)
            self._refresh_results_column(uid, grouping)
        self._on_organize_finished(uid, select_uid=not silent_batch)

        if not silent_batch:
            ui.info(
                dlg_parent,
                "整理完成",
                f"TIFF 已登记到当前编号：\n{uid}\n\n"
                f"文件：{Path(result.tiff_path).name}\n"
                "未生成 ZIP：该组没有 JPG 原片。",
            )
        else:
            self._batch_status(f"TIF 已整理：{Path(result.tiff_path).name}")
        self._workflow_notice(
            f"{workflow_label}完成",
            f"TIFF 已登记：{Path(result.tiff_path).name}。该组没有 JPG 原片，未生成 ZIP。",
            state="success",
            task_key=workflow_task_key,
        )
        if on_complete is not None:
            on_complete(True)
        return True

    def _warn_unnumbered_result_tiffs(self) -> None:
        """整理后提醒:results 里有「命名不规范、算不进序号」的成片。

        场景(用户 2026-07-11 要求): 用户想知道哪些成片命名不规范。
        理由(Fable 5): 序号扫描只认权威解析器能解出 (uid, seq) 的文件名;
          解不出的成片(外部软件随手命名 / 手改过名)**不参与序号计算**,
          是撞号覆盖的风险源, 但用户看不见它们存在。整理成功后主动提醒一次。
        不打断操作: 只走 workflow_notice + 状态栏; 每个项目每次会话只提醒一次
          (避免每整理一次弹一遍)。
        """
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            return
        seen = getattr(self, "_unnumbered_tiff_warned_projects", None)
        if seen is None:
            seen = set()
            self._unnumbered_tiff_warned_projects = seen
        if str(project_dir) in seen:
            return
        try:
            from app.services.organize_service import list_unnumbered_result_tiffs

            _inc, res = self._resolve_capture_subdirs()
            bad = list_unnumbered_result_tiffs(os.path.join(str(project_dir), res))
        except Exception:
            return
        if not bad:
            return
        seen.add(str(project_dir))
        sample = "、".join(bad[:3]) + ("…" if len(bad) > 3 else "")
        self._batch_status(f"发现 {len(bad)} 个命名不规范的成片(不计入序号)")
        self._workflow_notice(
            "成片命名不规范",
            f"results/ 里有 {len(bad)} 个成片文件名无法解析出「编号+序号」:\n{sample}\n\n"
            "它们不参与序号计算，可能导致新成片撞号。建议用监控区「更多 → 检查 TIF "
            "命名」核对并改名。",
            state="info",
            force_show=True,
            task_key=f"unnumbered-tiffs:{project_dir}",
        )

    def _on_organise_requested(
        self,
        uid: str,
        group_index: int,
        silent_batch: bool = False,
        allow_single_jpg: bool = False,
        workflow_task_key: str = "",
        workflow_label: str | None = None,
        on_complete=None,
    ) -> bool:
        """Organise (archive) the composed group.

        silent_batch=True(批量[合成+整理]一条龙):跳过激活拦截框、TIF改名框、
        同名ZIP确认框、成功提示框,失败静默返回——给"一键直跑"用。红线不变:
        JPG删除四闸 + TIFF永不自动删 都在 archive_group 内,silent 不绕。


        Gate checks (via organize_service._check_organize_gate):
          - uid must be active (or user explicitly bypasses)
          - group must have ≥2 JPGs
          - TIFF must already be composed

        delete_jpg is READ from settings; defaults to True after verified archival.
        Calls archive_service.archive_group (JPG→ZIP + optional delete).

        Oracle: server.js:3615-3840 organizeSpecimen; archive.js:67-190.
        """
        dlg_parent = self._grouping_ui_parent()
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        has_project = bool(db and project_dir)
        notice_label = workflow_label or ("合成+整理" if silent_batch else "整理")
        notice_task_key = workflow_task_key or f"organise:{uid}:{group_index}:{notice_label}"

        def _organise_not_started(message: str) -> bool:
            text = str(message or "整理条件未通过").strip()
            self._last_organise_failure_reason = text
            self._workflow_notice(
                f"{notice_label}失败",
                text,
                state="error",
                task_key=notice_task_key,
            )
            if silent_batch:
                self._status_message(f"整理未启动：{text}")
            return False

        self._last_organise_failure_reason = ""
        if not uid:
            if not silent_batch:
                ui.warn(dlg_parent, "整理", "请先打开分组后再整理。")
            return _organise_not_started("没有打开分组或编号")

        try:
            from app.services.organize_service import _check_organize_gate, OrganizeGateError

            self._save_timer.stop()
            if not self._flush_grouping_save():
                return _organise_not_started(
                    "当前分组保存失败；为避免按旧数据整理，操作已中止"
                )

            grouping = self._get_grouping_for_uid(uid)
            group_state = inspect_organize_group(grouping, group_index)
            group = group_state.group
            if group_state.reason == "missing-group":
                if not silent_batch:
                    ui.warn(
                        dlg_parent,
                        "整理",
                        f"找不到组{group_index + 1}。请关闭分组工具后重新打开再试。",
                )
                return _organise_not_started(f"找不到组{group_index + 1}")

            if group_state.already_organized:
                if not silent_batch:
                    self._status_message("该组已整理，有 ZIP 归档，无需再次整理。")
                self._workflow_notice(
                    f"{notice_label}无需处理",
                    "该组已整理，有 ZIP 归档，无需再次整理。",
                    state="info",
                    task_key=notice_task_key,
                )
                if on_complete is not None:
                    on_complete(True)
                return True

            if group_state.reason == "missing-tiff" or group is None:
                if not silent_batch:
                    ui.warn(
                        dlg_parent, "整理", "该组尚未合成，请先合成 TIFF 再整理。"
                    )
                return _organise_not_started("该组尚未合成 TIFF")

            # 整理前：若合成 TIFF 名不符成果命名规范（多见于导入的外部 Helicon TIFF），
            # 弹「TIFF 命名需确认」框，按本组编号成果名建议改名（守 S5：默认本组号、可改）。
            # 取消则中止整理；in-app 合成的 TIFF 本就规范，此路不触发。
            # silent_batch:批量时输出名是我们自己定的(编号-序号 / 组序.tif),不弹改名。
            if has_project and not silent_batch and self._maybe_rename_tiff_before_organize(
                db, uid, grouping, group, project_dir
            ) is False:
                return False

            gate_plan = plan_organize_gate_check(
                grouping,
                group,
                has_project=has_project,
                allow_single_jpg=allow_single_jpg,
                silent_batch=silent_batch,
            )
            if gate_plan.required:
                try:
                    _check_organize_gate(
                        db,
                        uid,
                        list(gate_plan.groups_as_dicts),
                        allow_inactive=gate_plan.allow_inactive,
                    )
                except OrganizeGateError as e:
                    if gate_plan.silent_skip_on_error:
                        return _organise_not_started(
                            str(e) or "JPG 不足或整理条件未通过"
                        )
                    if gate_plan.prompt_on_error:
                        reply = ui.question(
                            dlg_parent,
                            "整理确认",
                            f"{e}\n\n是否跳过激活检查继续整理？",
                        )
                        if reply != QMessageBox.StandardButton.Yes:
                            return False
                    else:
                        return _organise_not_started(str(e) or "整理条件未通过")

            if group_state.is_tif_only:
                self._workflow_notice(
                    f"{notice_label}：正在登记 TIFF",
                    f"正在登记 TIFF：{Path(group.composed_tiff_path).name}",
                    state="busy",
                    force_show=not bool(workflow_task_key),
                    task_key=notice_task_key,
                )
                return self._organize_tif_only_group(
                    uid,
                    grouping,
                    group,
                    db=db,
                    project_dir=project_dir,
                    has_project=has_project,
                    silent_batch=silent_batch,
                    workflow_task_key=notice_task_key,
                    workflow_label=notice_label,
                    on_complete=on_complete,
                )

            # Default workflow: verified ZIP replaces loose JPGs. Organise does
            # not auto-delete TIFF; explicit delete/undo is a separate action.
            delete_jpg: bool = True
            try:
                delete_jpg = bool(
                    getattr(self.ctx.settings, "delete_jpg_after_archive", True)
                )
            except Exception:
                pass

            # Claude Code 修改 2026-07-14 — 用户裁定(2026-07-14): 手动"整理"入口
            # 必须先确认, 点"否"不得产生副作用(codex 回归 + 用户 grill-me 拍板)。
            # silent_batch(合成+整理一条龙)不受影响——用户明确要求批量流程保持
            # 零弹框, 这条确认只挡手动单次整理这一条路径。
            if not silent_batch:
                confirm_msg = f"确认整理第 {group_index + 1} 组吗？\n\n将把 JPG 归档为 ZIP"
                confirm_msg += "，并在验证归档完整后删除原 JPG。" if delete_jpg else "。"
                reply_confirm = ui.question(dlg_parent, "整理确认", confirm_msg)
                if reply_confirm != QMessageBox.StandardButton.Yes:
                    return False

            if group.jpg_paths:
                from app.services.organize_workflow_service import resolve_group_jpg_paths

                resolved_jpgs, missing_jpgs = resolve_group_jpg_paths(group.jpg_paths)
                if missing_jpgs:
                    sample = "\n".join(missing_jpgs[:5])
                    extra = f"\n…等 {len(missing_jpgs)} 个" if len(missing_jpgs) > 5 else ""
                    message = (
                        f"以下 JPG 在磁盘上找不到：\n{sample}{extra}\n\n"
                        "请确认照片仍在 incoming-jpg 目录；WSL 下请用 /mnt/n/... 打开项目。"
                    )
                    if silent_batch:
                        return _organise_not_started(message)
                    ui.warn(dlg_parent, "整理失败", message)
                    return False
                group.jpg_paths = resolved_jpgs

            # 有项目：归档直接进入当前项目 results/。
            # 无项目：就地整理，ZIP 放在 TIFF 同目录，文件名等于 TIFF 基础名。
            res = "results"
            if has_project:
                _inc, res = self._resolve_capture_subdirs()
            archive_plan = plan_archive_worker(
                project_dir=project_dir if has_project else "",
                results_subdir=res,
                tiff_path=group.composed_tiff_path,
                delete_jpg_after_archive=delete_jpg,
            )
            archive_output_dir = archive_plan.archive_output_dir
            os.makedirs(archive_output_dir, exist_ok=True)

            # ── Collision guard: if ZIP already exists at the target path,  #cursor
            #    warn and let user choose overwrite / skip. ──────────────
            existing_zip = archive_plan.existing_zip
            if archive_plan.existing_zip_exists:
                if silent_batch:
                    return _organise_not_started(
                        f"同名 ZIP 已存在：{Path(existing_zip).name}，为避免覆盖已跳过"
                    )
                reply_col = ui.question(
                    dlg_parent,
                    "归档文件已存在",
                    f"同名归档 ZIP 已存在：\n{Path(existing_zip).name}\n\n"
                    "是否覆盖并重新归档？",
                )
                if reply_col != QMessageBox.StandardButton.Yes:
                    return False

            # Archive in a worker thread.  Large photo groups can still take
            # time; doing this in the GUI thread made the whole
            # window appear hung and gave the user no indication that work was
            # still in progress.
            from app.workers.supp_compression_worker import SuppCompressionWorker

            # Progress is now shown in the unified background-task window.  Do
            # not open a second per-action dialog for archive jobs.
            progress = None

            # Two-phase archive: ZIP first, DB finalize, then delete JPGs.
            # Keep one immutable source snapshot for all three phases. The
            # live Group object can otherwise change while the worker runs.
            request_delete_jpg = archive_plan.delete_jpg
            archive_jpg_snapshot = tuple(group.jpg_paths)
            archive_tiff_snapshot = str(group.composed_tiff_path or "")

            def _path_signature(paths) -> tuple[str, ...]:
                return tuple(
                    sorted(
                        os.path.normcase(os.path.realpath(os.path.abspath(str(path))))
                        for path in paths
                    )
                )

            archive_jpg_signature = _path_signature(archive_jpg_snapshot)
            archive_tiff_signature = _path_signature([archive_tiff_snapshot])
            worker = SuppCompressionWorker(
                jpg_paths=list(archive_jpg_snapshot),
                tiff_path=archive_tiff_snapshot,
                project_dir=project_dir or archive_output_dir,
                delete_jpg=False,
                method=getattr(self.ctx.settings, "jxl_effort_method", "standard"),
                concurrency=getattr(self.ctx.settings, "jxl_concurrency", 4),
                output_dir=archive_output_dir,
                parent=self,
            )
            if not hasattr(self, "_archive_workers"):
                self._archive_workers = set()
            if not hasattr(self, "_archive_worker_by_task_key"):
                self._archive_worker_by_task_key = {}
            self._archive_workers.add(worker)
            self._archive_worker_by_task_key[notice_task_key] = worker
            self._workflow_notice(
                f"{notice_label}：正在整理",
                f"正在把 {len(archive_jpg_snapshot)} 张 JPG 写入 ZIP：{Path(archive_tiff_snapshot).name}",
                state="busy",
                force_show=not bool(workflow_task_key),
                task_key=notice_task_key,
            )
            if silent_batch:
                self._status_message(
                    f"正在整理：{Path(archive_tiff_snapshot).name}，打包 {len(archive_jpg_snapshot)} 张 JPG…"
                )

            grouping_was_enabled = self._grouping.isEnabled()
            self._grouping.setEnabled(False)

            def _release_worker() -> None:
                if progress is not None:
                    progress.close()
                self._archive_workers.discard(worker)
                for key, mapped in list(getattr(self, "_archive_worker_by_task_key", {}).items()):
                    if mapped is worker:
                        self._archive_worker_by_task_key.pop(key, None)
                worker.deleteLater()
                if not self._archive_workers and grouping_was_enabled:
                    self._grouping.setEnabled(True)

            def _archive_progress(current: int, total: int, filename: str) -> None:
                verifying = str(filename or "") == "__verify_zip__"
                detail = (
                    "正在校验 ZIP 内容；校验通过后才会删除待处理区散落 JPG。"
                    if verifying
                    else f"正在打包第 {current}/{total} 张 JPG：{filename}"
                )
                if progress is not None:
                    progress.setMaximum(total)
                    progress.setValue(max(0, current - 1))
                    progress.setLabelText(
                        "正在校验 ZIP\n请稍候"
                        if verifying
                        else f"正在打包第 {current}/{total} 张\n{filename}"
                    )
                self._workflow_notice(
                    f"{notice_label}：正在整理",
                    detail,
                    state="busy",
                    task_key=notice_task_key,
                )

            def _archive_failed(message: str) -> None:
                _release_worker()
                self._last_organise_failure_reason = message or "归档过程出现错误。"
                self._workflow_notice(
                    f"{notice_label}失败",
                    self._last_organise_failure_reason,
                    state="error",
                    task_key=notice_task_key,
                )
                self._batch_status(f"整理失败：{message}")
                if not silent_batch:
                    ui.warn(
                        dlg_parent, "整理失败", message or "归档过程出现错误。"
                    )
                if on_complete is not None:
                    on_complete(False)

            def _archive_cancelled(message: str) -> None:
                _release_worker()
                reason = message or "用户取消"
                self._last_organise_failure_reason = reason
                self._workflow_notice(
                    f"{notice_label}已取消",
                    "整理归档已取消。JPG 没有删除，未完成 ZIP 已清理；已生成的 TIFF 仍保留在待处理区。",
                    state="info",
                    task_key=notice_task_key,
                )
                self._status_message("整理归档已取消，JPG 已保留。")
                if on_complete is not None:
                    on_complete(False)

            def _archive_finished(result) -> None:
                if not result.ok:
                    _archive_failed("归档过程出现错误。")
                    return
                if (
                    _path_signature(group.jpg_paths) != archive_jpg_signature
                    or _path_signature([group.composed_tiff_path or ""])
                    != archive_tiff_signature
                ):
                    _archive_failed(
                        "整理期间分组内容发生变化，已停止登记并保留全部 JPG。"
                        "请核对分组后重新整理。"
                    )
                    return
                _release_worker()
                from app.services.capture_workflow_service import finalize_archived_group

                project_root = getattr(self.ctx, "current_project_root", None)
                if not isinstance(project_root, str):
                    project_root = None
                try:
                    organized = finalize_archived_group(
                        db,
                        uid,
                        grouping,
                        group,
                        result,
                        archive_output_dir=archive_output_dir,
                        project_dir=project_dir if has_project else "",
                        project_root=project_root,
                    )
                except Exception as exc:  # noqa: BLE001
                    message = (
                        f"归档 ZIP 已生成，但成果登记失败：{exc}\n\n"
                        "JPG 原片仍保留在待处理区，可修正问题后重新整理。"
                    )
                    self._last_organise_failure_reason = message
                    self._workflow_notice(
                        f"{notice_label}失败",
                        message,
                        state="error",
                        task_key=notice_task_key,
                    )
                    self._batch_status(f"整理失败：{message}")
                    if not silent_batch:
                        ui.warn(dlg_parent, "整理失败", message)
                    if on_complete is not None:
                        on_complete(False)
                    return

                if request_delete_jpg and archive_jpg_snapshot:
                    from app.services.archive_service import commit_jpg_deletion_after_archive

                    result = commit_jpg_deletion_after_archive(
                        result,
                        list(archive_jpg_snapshot),
                    )
                if organized.metadata.error:
                    self._status_message(f"TIFF 元数据写入失败：{organized.metadata.error}")
                elif (
                    organized.metadata.skipped
                    and organized.metadata.skipped not in {"disabled", "unchanged"}
                ):
                    self._status_message(f"TIFF 元数据未写入：{organized.metadata.skipped}")

                msg = (
                    f"归档完成：{Path(group.archive_zip).name}\n"
                    "ZIP 内为原始 JPG，可直接解压使用。\n"
                )
                if result.delete_jpg:
                    msg += "JPG 原片已删除。"
                    finish_detail = (
                        f"已生成 {Path(group.archive_zip).name}；"
                        f"{result.file_count} 张 JPG 已写入 ZIP 并从待处理区删除。"
                    )
                elif result.requested_delete_jpg and not result.delete_jpg:
                    msg += f"JPG 保留（{result.deletion_skipped_reason}）。"
                    finish_detail = (
                        f"已生成 {Path(group.archive_zip).name}；JPG 已写入 ZIP，"
                        f"但删除前校验未通过，文件保留：{result.deletion_skipped_reason}"
                    )
                else:
                    finish_detail = (
                        f"已生成 {Path(group.archive_zip).name}；JPG 已写入 ZIP，"
                        "按当前设置保留在磁盘，但不再作为待处理照片显示。"
                    )
                self._workflow_notice(
                    f"{notice_label}完成",
                    finish_detail,
                    state="success",
                    task_key=notice_task_key,
                )
                if on_complete is not None:
                    on_complete(True)
                if not silent_batch:
                    ui.info(dlg_parent, "整理完成", msg)
                    # 整理成功后提醒一次:有没有命名不规范、算不进序号的成片
                    # (撞号风险源)。批量静默模式不打扰。
                    self._warn_unnumbered_result_tiffs()
                else:
                    self._batch_status(
                        f"整理完成：{Path(group.archive_zip).name}（{result.file_count} 张 JPG）"
                    )

                def _deferred_refresh() -> None:
                    self._refresh_monitor()
                    panel_uid = getattr(self._grouping, "_uid", None)
                    if not silent_batch or self._current_uid == uid or panel_uid == uid:
                        self._grouping.load_grouping(uid, grouping)
                        self._refresh_results_column(uid, grouping)
                    self._on_organize_finished(uid, select_uid=not silent_batch)
                    dlg = getattr(self, "_compose_organise_progress_dialog", None)
                    if dlg is not None and dlg.isVisible():
                        dlg._ensure_on_top()

                QTimer.singleShot(0, _deferred_refresh)

            worker.progress.connect(_archive_progress)
            worker.finished.connect(_archive_finished)
            worker.cancelled.connect(_archive_cancelled)
            worker.failed.connect(_archive_failed)
            if progress is not None:
                progress.show()
                ui.center_on(progress, dlg_parent)
                progress.raise_()
            try:
                worker.start()
            except Exception:
                _release_worker()
                raise
            return True

        except FileNotFoundError as exc:
            if not silent_batch:
                ui.warn(dlg_parent, "整理失败", f"文件不存在：{exc}")
            return _organise_not_started(f"文件不存在：{exc}")
        except Exception as exc:
            if not silent_batch:
                ui.warn(dlg_parent, "整理失败", f"意外错误：{exc}")
            return _organise_not_started(f"意外错误：{exc}")

    # ── 补处理 (supplementary archival) ────────────────────────────────────────
    #   Archive a selected JPG + TIFF bundle WITHOUT requiring an active specimen.
    #   The specimen identity is read from the TIFF filename (oracle
    #   processSelectedMonitorFiles / startSmartCompression, app.js:4407/4282).
