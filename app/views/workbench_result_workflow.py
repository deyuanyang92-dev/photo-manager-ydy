"""Result binding, undo, metadata save, and result refresh hooks for WorkbenchView."""
from __future__ import annotations

import json
import logging
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


logger = logging.getLogger(__name__)


class WorkbenchResultWorkflowMixin:
    """Result binding, undo, metadata save, and result refresh hooks for WorkbenchView."""

    def _persist_imported_group_tiff(self, uid: str, group_index: int) -> None:
        """Persist the imported TIFF association from grouping panel to DB.

        Called after grouping_panel._import_existing_tiff_into_group successfully updated the
        in-memory grouping.  Flushes the updated grouping to DB and refreshes
        the results column.

        Oracle: app.js groupingImportTiff() app.js:6057.
        """
        db = self.ctx.get_db()
        if not db or not uid:
            return
        try:
            self._save_timer.stop()
        except Exception:
            pass
        try:
            from app.services.grouping_service import save_grouping
            grouping = getattr(self._grouping, "_grouping", None)
            if grouping:
                save_grouping(db, uid, grouping.groups, clean_phantoms=False)
                self._refresh_results_column(uid, grouping)
                self._refresh_monitor()
        except Exception as exc:
            from app.db.db_manager import is_database_locked
            if is_database_locked(exc):
                QMessageBox.warning(
                    self,
                    "导入 TIFF",
                    "数据库正忙，可能是后台扫描或另一个软件窗口正在写入。\n"
                    "请关闭重复打开的窗口，或稍后重试。\n\n"
                    f"详情：{exc}",
                )
            else:
                QMessageBox.warning(self, "导入 TIFF", f"保存失败：{exc}")
            return
        group = next(
            (
                g
                for g in (grouping.groups if grouping else [])
                if g.group_index == group_index
            ),
            None,
        )
        if group and group.composed_tiff_path:
            self._apply_tiff_filename_recognition(
                group.composed_tiff_path,
                overwrite=True,
            )

    def _on_archive_zip_registered(self, uid: str, group_index: int) -> None:
        """Persist an existing ZIP association selected in the grouping panel."""
        db = self.ctx.get_db()
        if not db or not uid:
            return
        try:
            self._save_timer.stop()
        except Exception:
            pass
        try:
            from app.services.grouping_service import save_grouping
            grouping = getattr(self._grouping, "_grouping", None)
            if grouping:
                save_grouping(db, uid, grouping.groups, clean_phantoms=False)
                self._refresh_results_column(uid, grouping)
                self._refresh_monitor()
        except Exception as exc:
            from app.db.db_manager import is_database_locked
            if is_database_locked(exc):
                QMessageBox.warning(
                    self,
                    "注册 ZIP",
                    "数据库正忙，可能是后台扫描或另一个软件窗口正在写入。\n"
                    "请关闭重复打开的窗口，或稍后重试。\n\n"
                    f"详情：{exc}",
                )
            else:
                QMessageBox.warning(self, "注册 ZIP", f"保存失败：{exc}")

    # ── 成果 ↔ 编号 关联纠错 ─────────────────────────────────────────────────
    #
    # 使用场景(用户 2026-07-10 提出):
    #   合成时选错了编号,或「自动归档」按文件名猜错了归属 —— 一张 TIF 挂到了
    #   错误的编号下。发现后有两种心态:
    #     (a) 已经知道它真正属于哪个编号  → 「改绑到其他编号…」一步改挂;
    #     (b) 还要回去核对标本才能确定    → 「解绑此成果」先摘下来。
    #   两条路径都只改数据库里的归属事实,**绝不移动或删除磁盘上的 TIF 母版**
    #   (全局红线)。解绑后 TIF 仍在 results/ 里,可在「全部」模式的
    #   「未关联成果」分组里找到并重新绑定。

    def _ask_target_uid(self, title: str, current_hint: str = "") -> str:
        """弹出编号选择框,返回选中的 uid;取消返回 ""。"""
        from PyQt6.QtWidgets import QInputDialog

        db = self.ctx.get_db()
        if not db:
            return ""
        try:
            rows = db.execute("SELECT uid FROM specimens ORDER BY uid").fetchall()
            uids = [str(r[0]) for r in rows if r and r[0]]
        except Exception:
            uids = []
        if not uids:
            QMessageBox.warning(self, title, "当前项目还没有编号可供关联。")
            return ""
        start = uids.index(current_hint) if current_hint in uids else 0
        uid, ok = QInputDialog.getItem(
            self, title, "选择这张成果真正所属的编号：", uids, start, False
        )
        return uid.strip() if ok and uid else ""

    def _on_unbind_result(self, tiff_path: str, zip_path: str) -> None:
        """解绑:把成果从当前挂着的编号上摘下来(文件不动)。"""
        from app.utils import ui

        db = self.ctx.get_db()
        if not db:
            self._status_message("请先打开项目")
            return
        name = Path(tiff_path).name if tiff_path else Path(zip_path).name
        reply = ui.question(
            self,
            "解绑此成果",
            f"将「{name}」从当前编号上解绑？\n\n"
            "· 只解除「这张成果属于哪个编号」的关联\n"
            "· 磁盘上的 TIF / ZIP 文件不会被移动或删除\n"
            "· 解绑后可在成果「全部」里重新绑定",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from app.services.capture_workflow_service import unbind_result_pair

            removed = unbind_result_pair(db, tiff_path, zip_path)
        except Exception as exc:
            QMessageBox.warning(self, "解绑此成果", f"解绑失败：{exc}")
            return
        self._after_binding_changed()
        if removed:
            self._status_message(f"已解绑：{name}（原属 {'、'.join(removed)}）")
        else:
            self._status_message(f"「{name}」本来就没有关联任何编号")

    def _on_rebind_result(self, tiff_path: str, zip_path: str) -> None:
        """改绑:一步把成果从错误编号改挂到用户指定的编号。"""
        db = self.ctx.get_db()
        if not db:
            self._status_message("请先打开项目")
            return
        target = self._ask_target_uid("改绑到其他编号", self._current_uid or "")
        if not target:
            return
        try:
            from app.services.capture_workflow_service import rebind_result_pair_to_uid

            linked = rebind_result_pair_to_uid(db, target, tiff_path, zip_path)
        except Exception as exc:
            QMessageBox.warning(self, "改绑到其他编号", f"改绑失败：{exc}")
            return
        self._after_binding_changed(uid=linked.uid, grouping=linked.grouping)
        old = "、".join(linked.removed_from) or "未关联"
        self._status_message(f"已改绑：{old} → {linked.uid}")

    def _after_binding_changed(self, uid: str = "", grouping=None) -> None:
        """归属变了 → 重刷成果区(按当前显示模式)与待处理区。

        改绑有明确目标编号 ⇒ 直接刷那个编号;解绑没有目标 ⇒ 沿用当前模式重载
        (「全部」模式要重扫全项目,否则被解绑的那张还挂在旧编号下面不消失)。
        """
        try:
            if uid:
                self._refresh_results_column(uid, grouping)
            else:
                results = getattr(self, "_results", None)
                many = getattr(results, "_display_mode", "") == "many"
                reload_fn = getattr(
                    self,
                    "_on_show_all_results" if many else "_on_show_current_results",
                    None,
                )
                if callable(reload_fn):
                    reload_fn()
        except Exception:
            pass
        refresh_monitor = getattr(self, "_refresh_monitor", None)
        if callable(refresh_monitor):
            try:
                refresh_monitor()
            except Exception:
                pass

    def _on_link_result_to_right_uid(self, tiff_path: str, zip_path: str) -> None:
        """Move/register an existing result pair under the voucher shown at right."""
        db = self.ctx.get_db()
        if not db:
            self._status_message("请先打开项目")
            return

        target_uid = (
            self._naming.current_uid()
            or self._current_uid
            or self._get_active_uid()
            or ""
        ).strip()
        if not target_uid:
            QMessageBox.warning(
                self,
                "关联成果",
                "右侧编号尚未填写完整，无法关联成果。",
            )
            return

        try:
            from app.services.capture_workflow_service import link_result_pair_to_clean_uid

            linked = link_result_pair_to_clean_uid(db, target_uid, tiff_path, zip_path)
            self._grouping.load_grouping(target_uid, linked.grouping)
            self._refresh_results_column(target_uid, linked.grouping)
            self._refresh_monitor()
            if linked.removed_from:
                self._status_message(f"成果已改挂到右侧编号：{target_uid}")
            else:
                self._status_message(f"成果已关联到右侧编号：{target_uid}")
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "关联成果", str(exc))
        except Exception as exc:
            QMessageBox.warning(self, "关联成果", f"关联失败：{exc}")

    def _on_undo_compose(self, uid: str, group_index: int) -> None:
        """Undo the latest group step.

        Organized group: undo organise first by restoring JPGs from ZIP and
        returning the group to composed/pending-organise state.

        Composed group: 删除这张合成 TIFF + 把关联 JPG 解组放回自由池。
        用户选定语义（拍照区核心 = 中间 JPG ↔ 对应 TIFF 的关联）：TIFF 一旦删除，
        关联失去意义 → 这组 JPG 退出分组、回到监控自由池（未分组，可重新分组/重拍）。
        因删 TIFF 不可恢复 → 删前弹确认框（默认否）。取消则全保留、原样不动。
        """
        db = self.ctx.get_db()
        if not db:
            return
        from app.services.grouping_service import load_grouping, save_grouping
        grouping = load_grouping(db, uid)
        target = next(
            (g for g in grouping.groups
             if g.group_index == group_index and g.composed_tiff_path),
            None,
        )
        if target is None:
            return

        if getattr(target, "archive_zip", None):
            self._on_undo_organise(uid, grouping, target)
            return

        reply = QMessageBox.question(
            self, "删除 TIF / 撤销合成",
            "将删除这张合成 TIFF（不可恢复），并把关联的 JPG 放回自由池"
            "（可重新分组/合成）。确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # ① 删除 TIFF（用户主权；非自动流程，手动确认后删）。
        try:
            if target.composed_tiff_path and os.path.isfile(target.composed_tiff_path):
                os.unlink(target.composed_tiff_path)
        except OSError as exc:
            QMessageBox.warning(self, "撤销合成", f"TIFF 删除失败：{exc}")
            return

        # ② JPG 解关联：移除整组 → 这些 JPG 回到自由池（未分组）。
        grouping.groups = [g for g in grouping.groups if g.group_index != group_index]
        # §7 旧: try: save_grouping(...); load_grouping; _refresh_monitor;
        #        _refresh_results_column  except Exception: pass
        #   -> TIFF 已经从磁盘删掉了, 写库失败却一声不吭: 重开工作区这组还在,
        #      指着一个不存在的 TIFF。而且 UI 刷新的异常也混在同一个 except 里,
        #      分不清到底是"没存上"还是"只是没刷新"。
        # 新: 写库单独走网关(失败会喊), UI 刷新另算。
        if not self._save_grouping_or_warn(db, uid, grouping.groups, what="撤销合成"):
            return
        try:
            self._grouping.load_grouping(uid, grouping)
            self._refresh_monitor()
            self._refresh_results_column(uid, grouping)
        except Exception:
            logger.exception("撤销合成后刷新界面失败: uid=%s", uid)

    def _on_undo_organise(self, uid: str, grouping, target) -> None:
        """Undo organise: restore JPGs from ZIP, keep TIFF, clear archive state."""
        db = self.ctx.get_db()
        if not db:
            return
        zip_path = str(getattr(target, "archive_zip", "") or "")
        jpg_paths = [str(p) for p in list(getattr(target, "jpg_paths", []) or [])]
        if not zip_path or not os.path.isfile(zip_path):
            QMessageBox.warning(self, "撤销整理", "找不到该组 ZIP，无法恢复 JPG。")
            return
        if not jpg_paths:
            QMessageBox.warning(self, "撤销整理", "该组没有记录原 JPG 路径，无法自动恢复。")
            return

        reply = QMessageBox.question(
            self,
            "撤销整理",
            "将从 ZIP 还原原始 JPG 到原位置，并把该组退回“已合成、待整理”。\n\n"
            "TIFF 不会删除；恢复和状态更新成功后，项目内 ZIP 将直接删除，"
            "不再保留备份。恢复失败时 ZIP 不会删除。确认？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from app.services.archive_service import restore_archive_to_original_paths
        from app.services.grouping_service import _utc_now_iso, save_grouping

        result = restore_archive_to_original_paths(
            zip_path,
            jpg_paths,
            overwrite=True,
        )
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", "") or "；".join(result.failures[:3])
            QMessageBox.warning(self, "撤销整理失败", reason or "部分 JPG 未能恢复。")
            return

        previous_archive_zip = target.archive_zip
        previous_status = target.status
        previous_updated_at = target.updated_at
        target.archive_zip = None
        target.status = "composed"
        target.updated_at = _utc_now_iso()
        try:
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
        except Exception as exc:
            target.archive_zip = previous_archive_zip
            target.status = previous_status
            target.updated_at = previous_updated_at
            QMessageBox.warning(
                self,
                "撤销整理",
                "JPG 已恢复，但状态更新失败；ZIP 已保留，请重试。\n\n"
                f"详情：{exc}",
            )
            return

        removed_zip = ""
        zip_delete_failed = False
        try:
            removed_zip = self._retire_zip(zip_path)
        except OSError as exc:
            zip_delete_failed = True
            QMessageBox.warning(
                self,
                "撤销整理",
                "JPG 已恢复且状态已更新，但项目内 ZIP 删除失败，请手动检查。\n\n"
                f"详情：{exc}",
            )

        try:
            self._grouping.load_grouping(uid, grouping)
            self._refresh_monitor()
            self._refresh_results_column(uid, grouping)
            restored_count = getattr(result, "count", 0)
            skipped_count = len(getattr(result, "skipped", []) or [])
            detail = f"已恢复 {restored_count} 张 JPG"
            if skipped_count:
                detail += f"，{skipped_count} 张原位置已有文件已跳过"
            if removed_zip:
                detail += "；项目内 ZIP 已删除"
            elif zip_delete_failed:
                detail += "；项目内 ZIP 删除失败"
            else:
                detail += "；ZIP 已取消登记，外部文件保留原处"
            self._status_message(f"撤销整理完成：{detail}")
        except Exception as exc:
            QMessageBox.warning(self, "撤销整理", f"界面刷新失败：{exc}")

    def _retire_tiff(self, tiff_path: str) -> None:
        """Move a TIFF to the project's _retired-tiff/ directory."""
        try:
            import shutil
            src = Path(tiff_path)
            if not src.is_file():
                return
            project_dir = self.ctx.current_project_dir
            if not project_dir:
                return
            retired_dir = Path(project_dir) / "_retired-tiff"
            retired_dir.mkdir(exist_ok=True)
            dest = retired_dir / src.name
            # Avoid overwriting — add a numeric suffix if needed
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                i = 1
                while dest.exists():
                    dest = retired_dir / f"{stem}_{i}{suffix}"
                    i += 1
            shutil.move(str(src), str(dest))
        except Exception:
            pass

    def _retire_zip(self, zip_path: str) -> str:
        """Delete a project-managed ZIP after a successful restore and DB save."""
        src = Path(zip_path)
        if not src.is_file():
            return ""
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            return ""
        project_root = Path(project_dir).resolve()
        try:
            src.resolve().relative_to(project_root)
        except ValueError:
            return ""
        src.unlink()
        return str(src)

    def _on_grouping_changed(self) -> None:
        """Debounce-save grouping to DB after edits."""
        self._pending_grouping = None  # will re-read from grouping panel
        self._save_timer.start()

    def _save_grouping_or_warn(self, db, uid: str, groups, *, what: str) -> bool:
        """写分组到 project.db —— 失败必须**看得见**, 返回 False, 绝不装成功。

        场景(Fable 5, 2026-07-12, 用户点名 C4): 撤销合成 / 整理前 TIFF 改名 / 补充
          归档退回 ZIP —— 这些流程都是**先动磁盘, 后写库**。写库那步原来包在
          `except Exception: pass` 里: 数据库被别的窗口锁住、磁盘满、WAL 写不进去时,
          磁盘已经变了、库里没变, 界面照样报成功。下次打开工作区: 组还在但 TIFF 没了、
          文件名对不上、照片挂在已删除的旧编号下 —— 而用户根本不知道哪一步出的错。
        理由: 复用 _flush_grouping_save 已有的报错三件套(日志 + 状态栏 + 错误通知),
          不新增重试、不改任何调用方的控制流, 只是把哑巴改成会喊。
        """
        from app.services.grouping_service import save_grouping
        try:
            save_grouping(db, uid, groups, clean_phantoms=False)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s 写库失败: uid=%s", what, uid)
            self._status_message(f"{what}保存失败：{exc}", 8000)
            self._workflow_notice(
                f"{what}保存失败",
                "磁盘上的文件已经变了，但这一步没能写进项目数据库。\n"
                "请不要关闭软件，稍后重试；若提示数据库忙，请关掉重复打开的窗口。\n"
                + str(exc),
                state="error",
                force_show=True,
                task_key=f"grouping-save:{uid or 'unknown'}",
            )
            return False

    def _flush_grouping_save(self) -> bool:
        """Persist current in-memory grouping to the DB."""
        # The GroupingPanel holds the authoritative in-memory state via its
        # _grouping attribute; reach in safely.
        uid = getattr(self._grouping, "_uid", None)
        grouping = getattr(self._grouping, "_grouping", None)
        db = self.ctx.get_db()
        try:
            from app.services.capture_workflow_service import flush_visible_grouping
            saved = flush_visible_grouping(db, uid, grouping)
            if db is not None and uid and grouping is not None and not saved:
                raise RuntimeError("当前分组与打开的编号不一致")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("分组保存失败: uid=%s", uid)
            self._status_message(f"分组保存失败：{exc}", 8000)
            self._workflow_notice(
                "分组保存失败",
                "当前分组尚未写入项目数据库，请不要关闭软件并重试。\n" + str(exc),
                state="error",
                force_show=True,
                task_key=f"grouping-save:{uid or 'unknown'}",
            )
            return False

    # ── Metadata save ─────────────────────────────────────────────────────────

    def _schedule_rail_save(self) -> None:
        """Debounce a right-rail autosave (卡2/卡3 live edits)."""
        if self._current_uid:
            self._rail_save_timer.start()

    def _flush_rail_save(self) -> None:
        if self._current_uid:
            self._on_save_metadata(self._current_uid, reload=False)

    def _merge_right_rail_raw_fields(self, raw: dict) -> dict:
        """Merge right-rail extension fields into specimens.raw_json."""
        merged = dict(raw) if isinstance(raw, dict) else {}
        try:
            merged.update(self._naming.naming_extra_field_values())
        except Exception:
            pass
        try:
            extra_identifications = self._taxon_card.additional_identifications()
        except Exception:
            extra_identifications = []
        if extra_identifications:
            merged["additional_identifications"] = extra_identifications
        else:
            merged.pop("additional_identifications", None)
            merged.pop("additionalIdentifications", None)
        return merged

    def _on_save_metadata(self, uid: str, reload: bool = True, *, commit: bool = True) -> None:
        """Persist right-rail edits to the DB specimens table.

        Mirrors the web whole-`sp` persist (scheduleRightPanelPersist): one save
        gathers every right-rail field across the three cards —
        卡1 命名(日期/保存方式/拍照备注), 卡2 分类(拉丁/中名/备注), 卡3 元数据
        (采集人/拍摄人/鉴定人/经纬度/地理区).  ``reload=False`` for autosave so the
        focused input does not lose its cursor mid-edit.
        """
        db = self.ctx.get_db()
        if not db:
            return
        if reload:
            self._flush_grouping_save()
        panel = self._metadata
        naming = self._naming
        fields: dict[str, str] = {
            # 卡3 元数据
            "collector":       panel._collector.text(),
            "photographer":    panel._photographer.text(),
            "identifier":      panel._identifier.text(),
            "geo_area":        panel._geo_area.text(),
            # 卡1 命名（日期 / 保存方式 / 拍照备注）
            "collection_date": naming._collection_date.text(),
            "photo_date":      naming._photo_date.text(),
            "storage":         naming._storage.text(),
            "photo_notes":     naming._photo_notes.toPlainText(),
        }
        # 卡2 分类字段（拉丁 + 中名 + 备注）来自独立的「分类标签」卡片
        fields.update(self._taxon_card.field_values())
        lon_str = panel._lon.text().strip()
        lat_str = panel._lat.text().strip()

        set_clauses = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())

        try:
            lon_val: Optional[float] = float(lon_str) if lon_str else None
        except ValueError:
            lon_val = None
        try:
            lat_val: Optional[float] = float(lat_str) if lat_str else None
        except ValueError:
            lat_val = None

        try:
            db.execute(
                f"UPDATE specimens SET {set_clauses}, lon = ?, lat = ? WHERE uid = ?",
                values + [lon_val, lat_val, uid],
            )
            row = db.execute(
                "SELECT raw_json FROM specimens WHERE uid = ?",
                (uid,),
            ).fetchone()
            try:
                raw = json.loads(row["raw_json"] or "{}") if row else {}
                if not isinstance(raw, dict):
                    raw = {}
            except Exception:
                raw = {}
            merged_raw = self._merge_right_rail_raw_fields(raw)
            if merged_raw != raw:
                db.execute(
                    "UPDATE specimens SET raw_json = ? WHERE uid = ?",
                    (json.dumps(merged_raw, ensure_ascii=False), uid),
                )
            if commit:
                db.commit()
            # 拍照界面经纬度 → 回写/新建采集记录（四键对齐；有则更新坐标，无则建行）
            try:
                self._sync_collection_record_coords(
                    db,
                    lon=lon_val,
                    lat=lat_val,
                    fields=fields,
                    commit=commit,
                )
            except Exception:
                # best-effort 保持, 但持续失败会导致坐标永远进不了采集记录
                # 且无任何征兆 —— 至少留一条日志 (v0.56, 原为双层纯静默)。
                import logging

                logging.getLogger(__name__).warning(
                    "采集记录坐标回写失败(忽略继续)", exc_info=True
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("右栏元数据保存失败: uid=%s", uid)
            self._status_message(f"元数据保存失败：{exc}", 8000)
            self._workflow_notice(
                "元数据保存失败",
                "当前编辑尚未写入项目数据库，请不要关闭软件并重试。\n" + str(exc),
                state="error",
                force_show=True,
                task_key=f"metadata-save:{uid}",
            )
            if not commit:
                raise
            return

        if reload:
            self._load_specimen(uid)

        # Push updated specimen to collaboration peers (fire-and-forget)
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None and uid:
            try:
                svc.push_specimen(uid)
            except Exception:
                pass

    def _sync_collection_record_coords(
        self,
        db,
        *,
        lon,
        lat,
        fields: dict,
        commit: bool = True,
    ) -> None:
        """Persist capture lon/lat into collection_records (create if missing)."""
        from app.services import collection_record_service as crs

        naming = self._naming
        province, site, station, col_date = naming.current_keys()
        # collection_date 以命名卡为准；fields 里也有一份作兜底
        if not col_date:
            col_date = str(fields.get("collection_date") or "").strip()
        action = crs.sync_coords_from_capture(
            db,
            province=province,
            site=site,
            station=station,
            collection_date=col_date,
            lon=lon,
            lat=lat,
            extra={
                "geo_area": fields.get("geo_area"),
                "collector": fields.get("collector"),
                "photographer": fields.get("photographer"),
                "identifier": fields.get("identifier"),
                "photo_date": fields.get("photo_date"),
            },
        )
        if action != "skipped" and commit:
            # sync_coords_from_capture 内部已 commit；若外层 commit=False
            #（批量保存路径）则上面的 upsert 已自行 commit —— 保持与现有
            # collection_record_service 一致。此处无需再 commit。
            pass

    # ── WoRMS fill hook ───────────────────────────────────────────────────────

    def worms_fill_specimen(self, rec: dict) -> str:
        """Fill current specimen with WoRMS Latin taxonomy fields.

        Mirrors web ``wormsFillToSpecimen``: Latin class/order/family/genus/
        species are updated, ``taxonomyConfirmed`` is reset in raw_json, and
        Chinese fields are left untouched.
        """
        uid = self._current_uid or self._get_active_uid()
        if not uid:
            raise RuntimeError("需先在工作区选择或激活标本")
        db = self.ctx.get_db()
        if not db:
            raise RuntimeError("请先打开项目工作区")

        row = db.execute("SELECT * FROM specimens WHERE uid = ?", (uid,)).fetchone()
        if row is None:
            raise RuntimeError(f"当前标本不存在: {uid}")

        try:
            raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
            if not isinstance(raw, dict):
                raw = {}
        except Exception:
            raw = {}

        from app.services.worms_service import WormsService
        raw = WormsService.merge_worms_into_record(raw, rec)
        if rec.get("class"):
            raw["taxonGroup"] = rec["class"]
        if rec.get("order"):
            raw["order"] = rec["order"]
        if rec.get("family"):
            raw["family"] = rec["family"]
        if rec.get("genus"):
            raw["genus"] = rec["genus"]
        if rec.get("scientificname"):
            raw["scientificName"] = rec["scientificname"]
        raw["taxonomyConfirmed"] = False

        db.execute(
            """
            UPDATE specimens
            SET taxon_group = ?, order_name = ?, family = ?, genus = ?,
                scientific_name = ?, raw_json = ?
            WHERE uid = ?
            """,
            (
                rec.get("class") or row["taxon_group"],
                rec.get("order") or row["order_name"],
                rec.get("family") or row["family"],
                rec.get("genus") or row["genus"],
                rec.get("scientificname") or row["scientific_name"],
                json.dumps(raw, ensure_ascii=False),
                uid,
            ),
        )
        db.commit()
        self._load_specimen(uid)
        return uid

    # ── Collab photo-index hooks ──────────────────────────────────────────────

    def _on_helicon_finished(self, uid: str, *, select_uid: bool = True) -> None:
        """Broadcast tiff photo-index to collab peers (oracle: collabPostPhotoIndex)."""
        try:
            self._sidebar.refresh()
            if select_uid:
                self._sidebar.select_uid(uid)
        except Exception:
            pass
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.post_photo_index(uid, "tiff")
            except Exception:
                pass

    def _on_organize_finished(self, uid: str, *, select_uid: bool = True) -> None:
        """Broadcast zip photo-index to collab peers (oracle: collabPostPhotoIndex)."""
        try:
            self._sidebar.refresh()
            if select_uid:
                self._sidebar.select_uid(uid)
        except Exception:
            pass
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None:
            try:
                svc.post_photo_index(uid, "zip")
            except Exception:
                pass

    # ── Results column ────────────────────────────────────────────────────────

    def _result_infos_from_grouping(self, grouping) -> tuple[list[dict], list[dict]]:
        """Return display-ready TIFF/ZIP info lists for one specimen grouping."""
        from app.services.capture_workflow_service import result_infos_from_grouping
        return result_infos_from_grouping(grouping)

    def _refresh_results_column(self, uid: str, grouping=None) -> None:
        """Populate the ② 成果内容 column from one specimen's grouping data."""
        composed_tiffs, archive_zips = self._result_infos_from_grouping(grouping)
        self._results.load_uid(uid, composed_tiffs, archive_zips)

    # ── Helpers ───────────────────────────────────────────────────────────────
