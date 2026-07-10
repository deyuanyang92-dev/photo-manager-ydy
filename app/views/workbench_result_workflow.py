"""Result binding, undo, metadata save, and result refresh hooks for WorkbenchView."""
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
        try:
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
            self._grouping.load_grouping(uid, grouping)
            self._refresh_monitor()
            self._refresh_results_column(uid, grouping)
        except Exception:
            pass

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
            "TIFF 不会删除；项目内 ZIP 会移到 _retired-zip 作为备份。确认？",
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
            overwrite=False,
        )
        if not getattr(result, "ok", False):
            reason = getattr(result, "reason", "") or "；".join(result.failures[:3])
            QMessageBox.warning(self, "撤销整理失败", reason or "部分 JPG 未能恢复。")
            return

        retired_zip = self._retire_zip(zip_path)
        target.archive_zip = None
        target.status = "composed"
        target.updated_at = _utc_now_iso()
        try:
            save_grouping(db, uid, grouping.groups, clean_phantoms=False)
            self._grouping.load_grouping(uid, grouping)
            self._refresh_monitor()
            self._refresh_results_column(uid, grouping)
            restored_count = getattr(result, "count", 0)
            skipped_count = len(getattr(result, "skipped", []) or [])
            detail = f"已恢复 {restored_count} 张 JPG"
            if skipped_count:
                detail += f"，{skipped_count} 张原位置已有文件已跳过"
            if retired_zip:
                detail += f"；ZIP 已移到 {Path(retired_zip).parent.name}/"
            else:
                detail += "；ZIP 已取消登记，磁盘文件保留原处"
            self._status_message(f"撤销整理完成：{detail}")
        except Exception as exc:
            QMessageBox.warning(self, "撤销整理", f"状态更新失败：{exc}")

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
        """Move a project-managed ZIP to _retired-zip; leave external ZIPs alone."""
        try:
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
            retired_dir = project_root / "_retired-zip"
            retired_dir.mkdir(exist_ok=True)
            dest = retired_dir / src.name
            if dest.exists():
                stem, suffix = src.stem, src.suffix
                i = 1
                while dest.exists():
                    dest = retired_dir / f"{stem}_{i}{suffix}"
                    i += 1
            shutil.move(str(src), str(dest))
            return str(dest)
        except Exception:
            return ""

    def _on_grouping_changed(self) -> None:
        """Debounce-save grouping to DB after edits."""
        self._pending_grouping = None  # will re-read from grouping panel
        self._save_timer.start()

    def _flush_grouping_save(self) -> None:
        """Persist current in-memory grouping to the DB."""
        # The GroupingPanel holds the authoritative in-memory state via its
        # _grouping attribute; reach in safely.
        uid = getattr(self._grouping, "_uid", None)
        grouping = getattr(self._grouping, "_grouping", None)
        db = self.ctx.get_db()
        try:
            from app.services.capture_workflow_service import flush_visible_grouping
            flush_visible_grouping(db, uid, grouping)
        except Exception:
            pass

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
        except Exception:
            pass

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
