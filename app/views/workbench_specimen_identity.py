"""workbench_specimen_identity.py — Workbench specimen identity/right-rail mixin.

This Module owns specimen identity editing, naming-result synchronization,
right-rail loading, grouping dialog entry points, and collection/personnel
prefill helpers.  WorkbenchView keeps construction/wiring; workflow-heavy media
operations live in WorkbenchMediaWorkflowMixin.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QInputDialog, QMessageBox, QVBoxLayout, QWIDGETSIZE_MAX, QWidget,
)

from app.utils import ui


class WorkbenchSpecimenIdentityMixin:
    """Specimen identity and right-rail behaviour for WorkbenchView."""

    def _on_naming_uid_generated(self, uid: str) -> None:
        """Treat the current/active specimen UID as the row being edited.

        The naming card emits the specimen voucher UID while the result sequence
        can still change from 1 to 2, 3...  That must not be interpreted as a
        duplicate specimen when the left sidebar already shows the same UID as
        the current active/editing target.
        """
        text = str(uid or "").strip()
        if not text:
            return
        current_uid = self._current_uid
        active_uid = self._get_active_uid()
        if current_uid:
            if text != current_uid:
                return
        elif text != active_uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        try:
            row = db.execute("SELECT 1 FROM specimens WHERE uid = ?", (text,)).fetchone()
        except Exception:
            row = None
        if row:
            self._naming.acknowledge_existing_uid(text)

    def _on_uid_corrected(self, old_uid: str, new_uid: str) -> None:
        """Handle UID change after storage correction in NamingPanel.

        Updates _current_uid, refreshes the sidebar, and reloads migrated results.
        """
        if self._current_uid == old_uid:
            self._current_uid = new_uid
        self._sidebar.refresh()
        if new_uid:
            self._sidebar.select_uid(new_uid)
        db = self.ctx.get_db()
        if db and new_uid:
            try:
                from app.services.specimen_rename_service import (
                    repair_grouping_result_files_for_uid,
                )
                from app.services.grouping_service import load_grouping

                repair_grouping_result_files_for_uid(db, new_uid)
                grouping = load_grouping(db, new_uid)
                self._grouping.load_grouping(new_uid, grouping)
                self._refresh_results_column(new_uid, grouping)
            except Exception:
                self._refresh_results_column(new_uid, None)
        self._naming.refresh_legacy_photo_notes()
        self._try_bind_adhoc_to_existing_specimen(new_uid or self._naming.current_uid())
        if self._sync_grouping_outputs_from_naming():
            self._status_message("保存方式已写入编号，待整理输出名已同步更新。")
        else:
            self._status_message("保存方式已写入编号，成果文件名已同步更新。")

    def _claim_current_grouping_for_saved_uid(self, uid: str) -> bool:
        """Attach the currently-open temporary grouping to a newly saved UID.

        Users often create groups before they have saved the voucher number.
        In that flow the grouping panel is backed by the ad-hoc placeholder;
        once the right rail is saved, the visible groups must move with the
        new specimen so the sidebar progress and later organise actions target
        the real UID.
        """
        text = str(uid or "").strip()
        if not text:
            return False
        db = self.ctx.get_db()
        if not db:
            return False

        grouping = getattr(self._grouping, "_grouping", None)
        if grouping is None:
            return False

        panel_uid = getattr(self._grouping, "_uid", None)
        groups = list(getattr(grouping, "groups", []) or [])

        from app.services.capture_workflow_service import (
            persist_grouping_claim,
            prepare_grouping_claim,
        )
        from app.services.grouping_service import SpecimenGrouping

        plan = prepare_grouping_claim(db, text, panel_uid, groups)
        if not plan.should_persist:
            return False

        grouping.groups = plan.groups
        if plan.claimed:
            self._grouping.load_grouping(text, SpecimenGrouping(uid=text, groups=plan.groups))
        self._sync_grouping_outputs_from_naming()
        persist_grouping_claim(db, plan)
        return plan.claimed

    def _on_naming_save(self) -> None:
        """Persist the naming panel's current UID into the specimens table.

        Mirrors the web 「💾 保存」 button: upsert a specimen row keyed by the
        live-preview UID with the seven naming segments.  Chinese fields are
        never auto-filled (hard rule).
        """
        db = self.ctx.get_db()
        project_dir = self.ctx.current_project_dir
        if not db or not project_dir:
            self._status_message("请先打开一个项目工作区。")
            return
        uid = self._naming.current_uid()
        if not uid:
            self._status_message("编号尚未填写完整。")
            return
        dlg_parent = self._grouping_ui_parent()
        missing_required = [
            field for field in self._naming.missing_required_fields()
            if field not in ("采集日期", "拍照日期", "拍摄日期")
        ]
        if missing_required:
            self._status_message("编号信息未填写完整：" + "、".join(missing_required))
            QMessageBox.warning(
                dlg_parent,
                "编号信息未填写完整",
                "请先补全必填字段：" + "、".join(missing_required),
            )
            self._naming._check_compliance(uid)
            return
        persisted_uid = self._naming.persisted_uid()
        old_uid = (
            persisted_uid
            if persisted_uid and persisted_uid != uid
            else None
        )
        old_raw: dict = {}
        if old_uid:
            try:
                row = db.execute("SELECT raw_json FROM specimens WHERE uid=?", (old_uid,)).fetchone()
                old_raw = json.loads(row["raw_json"] or "{}") if row else {}
                if not isinstance(old_raw, dict):
                    old_raw = {}
            except Exception:
                old_raw = {}

        # 采集日期软必填：它是编号核心字段、会写入 UID 日期段。空着强提醒，但允许继续
        # （兼容采集日期确实未知的标本——编号自动少一段）。默认「返回填写」。
        if not self._naming._collection_date.text().strip():
            reply = QMessageBox.question(
                dlg_parent, "采集日期未填",
                "采集日期是编号核心字段，会写入唯一编号。\n\n"
                "未知可留空继续（编号自动少日期段），或返回填写。",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Save:
                return

        # ── UID uniqueness (mirrors web server.js HTTP 409) ─────────────────
        # A specimen UID is a museum-style catalogue number.
        # (a) Cross-workspace: scan every OTHER known workspace's project.db;
        #     if this UID is already owned there → refuse (global unique).
        # (b) Same-workspace cover: editing a loaded specimen (_current_uid) and
        #     changing its fields to another UID that already exists here →
        #     refuse (would overwrite a different specimen via upsert).
        # Re-saving the very same UID (update self, incl. _current_uid is None
        # and the row already exists) is allowed — PRIMARY KEY upsert handles it.
        from app.services import specimen_catalog_service as _catalog
        try:
            _current_resolved = str(Path(project_dir).resolve())
        except OSError:
            _current_resolved = project_dir
        _other_hits = []
        if persisted_uid != uid:
            for h in _catalog.find_uid(
                uid,
                current_project_dir=project_dir,
                current_project_root=getattr(self.ctx, "current_project_root", None),
            ):
                try:
                    hit_resolved = str(Path(h.project_dir).resolve())
                except OSError:
                    hit_resolved = h.project_dir
                if hit_resolved != _current_resolved:
                    _other_hits.append(h)
        _local_owner = db.execute(
            "SELECT owner_project_dir FROM specimens WHERE uid=?", (uid,)
        ).fetchone()
        _local_cover = (
            _local_owner is not None
            and persisted_uid
            and persisted_uid != uid
        )
        if _other_hits:
            self._status_message(f"编号已被占用：{uid}")
            QMessageBox.warning(
                dlg_parent, "编号已被占用",
                "⚠ " + _catalog.format_uid_conflict(uid, _other_hits),
            )
            self._naming.show_dup_warn(True)
            return
        if _local_cover:
            self._status_message(f"编号已存在于本项目：{uid}")
            QMessageBox.warning(
                dlg_parent, "编号已被占用",
                f"⚠ 编号 {uid} 已存在于本项目（另一个标本），不能覆盖。请修改字段后再保存。",
            )
            self._naming.show_dup_warn(True)
            return

        # ── Collaboration UID claim ───────────────────────────────────────
        # When collaboration is active (running + a group code), claim a NEW
        # UID across the LAN so no teammate can reuse it.  Re-saving a UID that
        # is already a local specimen is an update, not a new claim.
        svc = getattr(self.ctx, "collab_service", None)
        if svc is not None and svc.is_running() and svc.group_code:
            is_local = db.execute(
                "SELECT 1 FROM specimens WHERE uid=?", (uid,)
            ).fetchone() is not None
            if not is_local:
                ok, msg = svc.create_task(uid, assignee=self._collab_operator())
                if not ok:
                    self._status_message(f"协作编号占用：{uid}")
                    QMessageBox.warning(
                        dlg_parent, "编号已被占用",
                        f"编号 {uid} 已被占用：{msg}\n请改用其他编号后再保存。",
                    )
                    self._naming._apply_sequence_suggestion()
                    return

        n = self._naming
        from app.utils.naming import coalesce_specimen_dates
        collection_date, photo_date = coalesce_specimen_dates(
            n._collection_date.text().strip(),
            n._photo_date.text().strip(),
        )
        try:
            db.execute(
                """
                INSERT INTO specimens (uid, id, province, site, station,
                                       storage, collection_date, photo_date,
                                       photo_notes, owner_project_dir)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(uid) DO UPDATE SET
                    id=excluded.id, province=excluded.province,
                    site=excluded.site, station=excluded.station,
                    storage=excluded.storage,
                    collection_date=excluded.collection_date,
                    photo_date=excluded.photo_date,
                    photo_notes=excluded.photo_notes,
                    owner_project_dir=excluded.owner_project_dir
                """,
                (
                    uid,
                    n._species_id.text().strip(),
                    n._province.text().strip(),
                    n._site.text().strip(),
                    n._station.text().strip(),
                    n._storage.text().strip(),
                    collection_date,
                    photo_date,
                    n._photo_notes.toPlainText().strip(),
                    project_dir,
                ),
            )
            if collection_date and not n._collection_date.text().strip():
                n._collection_date.setText(collection_date)
            if photo_date and not n._photo_date.text().strip():
                n._photo_date.setText(photo_date)
            # 命名行已建/更新 → 标记为当前标本，再 flush 右栏三卡。
            # 关键修复（场景1 疑点1+2）：新草稿 _current_uid 原为 None，metadata
            # autosave 整段被 _schedule_rail_save 跳过；保存只写命名段 → 用户在
            # metadata 卡填的 采集人/经纬度/地理区/分类 会静默丢失。这里先设
            # _current_uid（行已存在），再调 _on_save_metadata 把右栏一并入库，
            # 使「保存」= 存全部。
            self._current_uid = uid
            self._on_save_metadata(uid, reload=False, commit=False)
            if old_uid:
                self._finalize_uid_rename(old_uid, uid, old_raw)
            claimed_grouping = self._claim_current_grouping_for_saved_uid(uid)
            db.commit()
            self._naming.acknowledge_existing_uid(uid)
            self._sidebar.refresh()
            self._sidebar.select_uid(uid)
            # 「新建编号后自动激活」开关（默认关，复刻 oracle
            # autoActivateOnNewSpecimen app.js:9396-9397）：开则保存即把此号设为
            # 当前激活标本，省去手动点激活；关则不动激活（守 oracle 默认）。
            if bool(getattr(self.ctx.settings, "auto_activate_on_new_specimen", False)):
                self._on_sidebar_activate(uid)
            elif claimed_grouping:
                self._status_message(f"已添加到左侧并绑定当前分组：{uid}")
            else:
                self._status_message(f"已保存编号：{uid}")
        except Exception as exc:
            self._status_message(f"保存失败：{exc}")
            QMessageBox.warning(dlg_parent, "保存失败", str(exc))

    def _on_naming_add(self) -> None:
        """Add the visible voucher as a new/current specimen, without renaming old."""
        self._naming.mark_current_values_as_new()
        self._current_uid = None
        self._on_naming_save()

    def _on_naming_update_results(self) -> None:
        """Save current naming fields, then rename registered result TIFF/ZIP files."""
        uid = self._naming.current_uid()
        if not uid:
            self._status_message("编号尚未填写完整，无法更新成果文件名。")
            return

        self._on_naming_save()
        persisted_uid = self._naming.persisted_uid()
        if persisted_uid != uid:
            self._status_message("编号尚未成功保存，未更新成果文件名。")
            return

        try:
            changed = self._sync_result_files_to_current_naming(persisted_uid)
        except Exception as exc:
            self._status_message(f"成果文件名更新失败：{exc}")
            QMessageBox.warning(self._grouping_ui_parent(), "成果文件名更新失败", str(exc))
            return

        if changed:
            self._status_message(f"已更新 {changed} 个成果文件名。")
        else:
            self._status_message("成果文件名已是当前编号，无需更新。")

    def _current_naming_result_values(self) -> dict[str, str]:
        from app.utils.naming import coalesce_specimen_dates, specimen_date_seg

        collection_date, photo_date = coalesce_specimen_dates(
            self._naming._collection_date.text().strip(),
            self._naming._photo_date.text().strip(),
        )
        values = {
            "province": self._naming._province.text().strip(),
            "site": self._naming._site.text().strip(),
            "station": self._naming._station.text().strip(),
            "species_id": self._naming._species_id.text().strip(),
            "storage": self._naming._storage.text().strip(),
            "date_seg": specimen_date_seg(collection_date, photo_date),
            "collection_date": collection_date,
            "photo_date": photo_date,
        }
        try:
            values.update(self._naming.naming_component_values())
        except Exception:
            pass
        return values

    def _suggest_current_result_filename(
        self,
        path: Path,
        *,
        components: list[str],
        values: dict[str, str],
        seq: int,
    ) -> tuple[str, str]:
        """Return (filename, parsed_uid) for *path* under current naming fields."""
        from app.utils.naming import recognize_tiff_filename, suggest_tiff_filename_preserve_legacy

        rec = recognize_tiff_filename(path.stem, components)
        parsed_uid = rec.uid if rec else ""
        effective_seq = rec.sequence if rec else seq
        if rec:
            stem = suggest_tiff_filename_preserve_legacy(
                path.stem,
                components,
                values,
                seq=effective_seq,
            )
        else:
            stem = self._naming.suggested_result_stem(seq=effective_seq)
        return stem + path.suffix, parsed_uid

    def _sync_result_files_to_current_naming(self, uid: str) -> int:
        """Rename registered result files for *uid* to match the right-rail naming."""
        db = self.ctx.get_db()
        if not db:
            return 0

        grouping = self._get_grouping_for_uid(uid)
        if grouping is None or not getattr(grouping, "groups", None):
            return 0

        components = self._load_naming_components()
        values = self._current_naming_result_values()
        plans: list[tuple[Path, Path, str, str]] = []
        planned_sources: set[Path] = set()
        group_updates: dict[int, dict[str, str]] = {}

        def add_result_file_rename_plan(
            src: Path, dst: Path, old_uid: str = "", new_uid: str = ""
        ) -> None:
            if src == dst:
                return
            if not src.is_file():
                return
            plans.append((src, dst, old_uid, new_uid))
            planned_sources.add(src)

        for group in grouping.groups:
            try:
                fallback_seq = int(
                    getattr(group, "result_sequence", None)
                    or getattr(group, "group_index", 0) + 1
                )
            except (TypeError, ValueError):
                fallback_seq = int(getattr(group, "group_index", 0) or 0) + 1

            updates: dict[str, str] = {}
            tiff_path_text = getattr(group, "composed_tiff_path", None) or ""
            tiff_path = Path(tiff_path_text) if tiff_path_text else None
            new_tiff_path = None
            old_uid_for_group = ""

            if tiff_path and tiff_path.is_file():
                new_name, old_uid_for_group = self._suggest_current_result_filename(
                    tiff_path,
                    components=components,
                    values=values,
                    seq=fallback_seq,
                )
                new_tiff_path = tiff_path.with_name(new_name)
                add_result_file_rename_plan(tiff_path, new_tiff_path, old_uid_for_group, uid)
                updates["composed_tiff_path"] = str(new_tiff_path)
                updates["output_name"] = new_tiff_path.stem

            zip_paths: list[Path] = []
            archive_zip = getattr(group, "archive_zip", None) or ""
            if archive_zip:
                zip_paths.append(Path(archive_zip))
            if tiff_path:
                sibling_zip = tiff_path.with_suffix(".zip")
                if sibling_zip.is_file() and sibling_zip not in zip_paths:
                    zip_paths.append(sibling_zip)

            for zip_path in zip_paths:
                if not zip_path.is_file():
                    continue
                if new_tiff_path is not None and tiff_path and zip_path.stem == tiff_path.stem:
                    new_zip_path = new_tiff_path.with_suffix(".zip")
                else:
                    new_zip_name, zip_old_uid = self._suggest_current_result_filename(
                        zip_path,
                        components=components,
                        values=values,
                        seq=fallback_seq,
                    )
                    old_uid_for_group = old_uid_for_group or zip_old_uid
                    new_zip_path = zip_path.with_name(new_zip_name)
                add_result_file_rename_plan(zip_path, new_zip_path, old_uid_for_group, uid)
                updates["archive_zip"] = str(new_zip_path)

            if updates:
                group_updates[int(getattr(group, "group_index", 0) or 0)] = updates

        seen_targets: set[Path] = set()
        for src, dst, _old_uid, _new_uid in plans:
            if dst.exists() and dst != src and dst not in planned_sources:
                raise ValueError(f"目标文件已存在，无法更新：{dst}")
            if dst in seen_targets:
                raise ValueError(f"多个成果会写到同一个目标文件，已中止：{dst}")
            seen_targets.add(dst)

        from app.services.specimen_rename_service import _rewrite_zip_manifest

        changed = 0
        for src, dst, old_uid, new_uid in plans:
            if src == dst:
                continue
            os.replace(str(src), str(dst))
            changed += 1
            if dst.suffix.lower() == ".zip" and old_uid and new_uid and old_uid != new_uid:
                _rewrite_zip_manifest(str(dst), old_uid, new_uid)

        if group_updates:
            for group in grouping.groups:
                updates = group_updates.get(int(getattr(group, "group_index", 0) or 0))
                if not updates:
                    continue
                if "composed_tiff_path" in updates:
                    group.composed_tiff_path = updates["composed_tiff_path"]
                if "archive_zip" in updates:
                    group.archive_zip = updates["archive_zip"]
                if "output_name" in updates:
                    group.output_name = updates["output_name"]
            self._save_grouping_for_uid(uid, grouping.groups)
            grouping = self._get_grouping_for_uid(uid)
            self._refresh_results_column(uid, grouping)

        return changed

    def _finalize_uid_rename(self, old_uid: str, new_uid: str, old_raw: Optional[dict] = None) -> None:
        """Move DB references after right-rail editing changes the generated UID."""
        if not old_uid or not new_uid or old_uid == new_uid:
            return
        db = self.ctx.get_db()
        if not db:
            return
        raw = dict(old_raw or {})
        n = self._naming
        raw.update({
            "uid": new_uid,
            "id": n._species_id.text().strip(),
            "province": n._province.text().strip(),
            "site": n._site.text().strip(),
            "station": n._station.text().strip(),
            "storage": n._storage.text().strip(),
            "collectionDate": n._collection_date.text().strip(),
            "photoDate": n._photo_date.text().strip(),
        })
        try:
            raw = self._merge_right_rail_raw_fields(raw)
        except Exception:
            pass
        prev = raw.get("previousUniqueIds") or []
        if not isinstance(prev, list):
            prev = []
        if old_uid not in prev:
            prev.append(old_uid)
        raw["previousUniqueIds"] = prev
        try:
            from app.services.specimen_rename_service import migrate_uid_references
            with db:
                migrate_uid_references(db, old_uid, new_uid)
                try:
                    db.execute(
                        "UPDATE photo_assignments SET specimen_uid=? WHERE specimen_uid=?",
                        (new_uid, old_uid),
                    )
                except Exception:
                    pass
                db.execute("DELETE FROM specimens WHERE uid=?", (old_uid,))
                db.execute(
                    "UPDATE specimens SET raw_json=? WHERE uid=?",
                    (json.dumps(raw, ensure_ascii=False), new_uid),
                )
        except Exception as exc:
            QMessageBox.warning(self, "编号迁移失败", str(exc))

    def _seed_helicon_defaults(self) -> None:
        """Seed the compose params panel from saved Helicon defaults (QSettings).

        Keeps the per-compose panel in sync with the 「保存为默认」 stored by the
        top-bar Helicon 配置 dialog. Failures are non-fatal (panel keeps its own
        hardcoded defaults).
        """
        try:
            from app.services.helicon_service import (
                _K_HELICON_METHOD, _K_HELICON_RADIUS, _K_HELICON_SMOOTHING,
            )
            qs = self.ctx.settings._qs
            self._helicon_params.set_params({
                "method": int(qs.value(_K_HELICON_METHOD, 1)),
                "radius": float(qs.value(_K_HELICON_RADIUS, 8.0)),
                "smoothing": int(qs.value(_K_HELICON_SMOOTHING, 4)),
            })
        except Exception:
            pass

    def _build_grouping_dialog(self, panel: QWidget) -> QDialog:
        """Host the GroupingPanel in a non-modal popup.

        Non-modal so the user can still drag/select files in the monitor while
        the grouping tool is open (the monitor → group flow relies on it).
        """
        dlg = QDialog(self)
        dlg.setObjectName("GroupingDialog")
        dlg.setWindowTitle("分组工具")
        dlg.setModal(False)
        lay = QVBoxLayout(dlg)
        # Pad so the inner WorkbenchSection card's drop-shadow is not clipped —
        # the card floats on the soft-gray dialog bg (QDialog#GroupingDialog).
        lay.setContentsMargins(14, 14, 14, 14)
        # Collapse header row is redundant inside a dedicated popup.
        header = getattr(panel, "_group_header_widget", None)
        if header is not None:
            header.setVisible(False)
        divider = getattr(panel, "_header_divider", None)
        if divider is not None:
            divider.setVisible(False)
        lay.addWidget(panel)
        # §7 旧: dlg.resize(760, 560) —— 4 组时横向拥挤、纵向留大片空带
        # (2026-07-10 用户截图投诉)。宽度容 4 张卡片, 高度贴合内容。
        dlg.resize(1100, 440)
        dlg.setMinimumSize(720, 400)
        return dlg

    def _grouping_ui_parent(self) -> QWidget:
        """Dialog parent while the grouping popup is open — keeps pickers on top."""
        g = getattr(self, "_grouping_dialog", None)
        if g is not None and g.isVisible():
            return g
        return self

    def _open_grouping_helicon_params(self) -> None:
        """从分组工具的“更多”按需弹出 Helicon 参数。"""
        dlg = getattr(self, "_grouping_helicon_params_dialog", None)
        if dlg is None:
            dlg = QDialog(self._grouping_dialog)
            dlg.setWindowTitle("Helicon 合成参数")
            layout = QVBoxLayout(dlg)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.addWidget(self._helicon_params)
            dlg.resize(720, 330)
            self._grouping_helicon_params_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _load_temporary_grouping_task(self, label: str = "未命名（临时分组）") -> None:
        """Bind the grouping editor to a fresh unassigned task."""
        from app.services.grouping_service import ADHOC_GROUPING_UID, SpecimenGrouping

        self._grouping.load_grouping(
            ADHOC_GROUPING_UID,
            SpecimenGrouping(uid=ADHOC_GROUPING_UID, groups=[]),
        )
        self._grouping._uid_label.setText(label)
        self._grouping._target_label.setText("临时")

    def _on_open_grouping(self) -> None:
        """Open (or re-focus) the grouping/compose popup — web 分组工具 toggle.

        有激活编号时归属激活编号；没有激活编号时进入未归属临时任务。
        左侧选中/右侧正在编辑的未激活编号不应隐式成为拍照分组目标。
        """
        from app.services.grouping_service import (
            ADHOC_GROUPING_UID,
            SpecimenGrouping,
            load_grouping,
        )
        uid = self._get_active_uid()
        if not uid:
            if getattr(self._grouping, "_uid", None) != ADHOC_GROUPING_UID:
                self._load_temporary_grouping_task("未归属（可后续归入编号）")
            else:
                self._grouping._uid_label.setText("未归属（可后续归入编号）")
                self._grouping._target_label.setText("临时")
            self._status_message("当前没有激活编号：新组将暂存为未归属分组。", 5000)
            uid = ADHOC_GROUPING_UID
        if getattr(self._grouping, "_uid", None) != uid:
            db = self.ctx.get_db()
            try:
                if db:
                    grouping = load_grouping(db, uid)
                else:
                    grouping = SpecimenGrouping(uid=uid, groups=[])
                self._grouping.load_grouping(uid, grouping)
            except Exception:
                pass
        self._recognize_first_grouping_tiff(getattr(self._grouping, "_grouping", None))
        dlg = self._grouping_dialog
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_legacy_photo_batch_organize(self) -> None:
        """Expose old JPG/TIF folder organising as a first-class monitor action."""
        self._on_open_grouping()
        self._on_auto_group_organize()

    def _reset_to_unassigned_task(self) -> None:
        """Clear specimen-bound UI after deactivation and start a fresh temp task."""
        self._current_uid = None
        self._pending_grouping = None
        try:
            self._save_timer.stop()
            self._rail_save_timer.stop()
        except Exception:
            pass

        self._naming.load_specimen({})
        try:
            self._metadata.clear()
            self._taxon_card.clear()
            self._apply_draft_project_defaults()
        except Exception:
            pass

        try:
            self._results.clear()
        except Exception:
            pass

        # Deactivation removes specimen ownership. Keep the grouping tool usable
        # as an unassigned task so new groups can later be saved into a UID.
        self._load_temporary_grouping_task("未归属（新任务）")
        self._refresh_batch_header()

    def _toggle_right_rail(self) -> None:
        """Collapse/expand the whole naming rail (web rightPanelCollapsed)."""
        self._right_rail_collapsed = not self._right_rail_collapsed
        c = self._right_rail_collapsed
        self._rail_collapse_btn.setText("展开" if c else "收起")
        for w in (self._naming, self._taxon_card, self._metadata):
            w.setVisible(not c)
        if c:
            self._last_expanded_rail_state = self._outer_splitter.saveState()
            width = self._right_rail_collapsed_width()
            self._right_scroll.setMinimumWidth(width)
            self._right_scroll.setMaximumWidth(width)
        else:
            self._right_scroll.setMinimumWidth(self._right_rail_min_width())
            self._right_scroll.setMaximumWidth(QWIDGETSIZE_MAX)
            if self._last_expanded_rail_state is not None:
                self._outer_splitter.restoreState(self._last_expanded_rail_state)
            else:
                self._restore_workbench_outer_splitter()

    def _on_open_taxon_edit(self) -> None:
        """Open the 「一次编辑五级」 taxon modal and write the result back."""
        from app.widgets.taxon_edit_dialog import TaxonEditDialog
        dlg = TaxonEditDialog(self._taxon_card.field_values(), parent=self)
        if dlg.exec():
            self._taxon_card.apply_values(dlg.result_values())
            if self._current_uid:
                self._on_save_metadata(self._current_uid)

    def _load_specimen(self, uid: str) -> None:
        """Load grouping + naming + metadata + results for *uid*."""
        self._current_uid = uid
        db = self.ctx.get_db()
        if not db:
            return

        # Load grouping for result display. The editable grouping target follows
        # only the active specimen; inactive sidebar selection must not become
        # the owner of a subsequent "新组".
        grouping = None
        try:
            from app.services.specimen_rename_service import (
                repair_grouping_result_files_for_uid,
            )
            from app.services.grouping_service import ADHOC_GROUPING_UID, load_grouping

            repair_grouping_result_files_for_uid(db, uid)
            grouping = load_grouping(db, uid)
            active_uid = self._get_active_uid()
            if active_uid == uid:
                self._grouping.load_grouping(uid, grouping)
            elif active_uid:
                if getattr(self._grouping, "_uid", None) != active_uid:
                    self._grouping.load_grouping(active_uid, load_grouping(db, active_uid))
            elif getattr(self._grouping, "_uid", None) != ADHOC_GROUPING_UID:
                self._load_temporary_grouping_task("未归属（可后续归入编号）")
        except Exception:
            if self._get_active_uid() == uid:
                self._grouping.clear()

        # Load specimen record for naming + metadata panels
        try:
            row = db.execute(
                "SELECT * FROM specimens WHERE uid = ?", (uid,)
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
                    "photo_notes": sp.photo_notes,
                    "photoNotes": sp.photo_notes,
                    "collector": sp.collector,
                    "photographer": sp.photographer,
                    "identifier": sp.identifier,
                    "geo_area": sp.geo_area,
                    "scientific_name": sp.scientific_name,
                    "scientificName": sp.scientific_name,
                    "scientific_name_cn": sp.scientific_name_cn,
                    "scientificNameCn": sp.scientific_name_cn,
                    "taxon_group": sp.taxon_group,
                    "taxonGroup": sp.taxon_group,
                    "order_name": sp.order_name,
                    "order": sp.order_name,
                    "family": sp.family,
                    "genus": sp.genus,
                    "notes": sp.notes,
                })
                sp_dict["uid"] = sp.uid
                sp_dict.setdefault("photo_notes", sp.photo_notes)
                self._naming.load_specimen(sp_dict)
                self._metadata.load_specimen(sp)
                self._taxon_card.load_specimen(sp)
                self._sync_uid_display_summary()
                self._collab_card.load_specimen(uid)
            else:
                self._load_naming_from_uid(uid)
        except Exception:
            pass
        self._recognize_first_grouping_tiff(grouping)

        # Populate ② 成果内容 column from grouping data
        self._refresh_results_column(uid, grouping)

        # Backfill empty capture fields from a matching 采集记录. load_specimen sets
        # the four location keys via direct setText (no keys_committed signal), so
        # _apply_collection_autofill would otherwise never run on the load path →
        # 已填的 采集人/拍摄人/坐标 在右栏不显示。非破坏：只填空字段。
        # 容错：采集记录表缺失/查询异常不能拖垮标本加载本身。
        try:
            self._apply_collection_autofill()
        except Exception:
            pass
        # 项目 personnel 默认值回填空字段。_on_project_personnel_changed 只灌新草稿
        # （_current_uid 为 None），已存标本的空 采集人/拍摄人 不会被它覆盖 → 建号时
        # personnel 还没设、之后才设的标本加载后永远空。空不是事实，加载时补默认值。
        # 非破坏：apply_autofill 只填空字段，已存值不动。采集记录优先级更高（已先跑）。
        try:
            self._backfill_personnel_defaults()
        except Exception:
            pass

    def _load_naming_from_uid(self, uid: str) -> bool:
        """Populate the naming card from a UID when no specimen row exists yet."""
        from app.utils.naming import parse_uid

        parsed = parse_uid(uid)
        if not parsed:
            return False
        date_seg = str(parsed.get("dateSegment") or "")
        collection_date = date_seg
        photo_date = date_seg
        if re.fullmatch(r"\d{8}-\d{4}", date_seg):
            collection_date, tail = date_seg.split("-", 1)
            photo_date = collection_date[:4] + tail
        elif re.fullmatch(r"\d{8}-\d{8}", date_seg):
            collection_date, photo_date = date_seg.split("-", 1)
        self._naming.load_specimen({
            "uid": uid,
            "province": parsed.get("province") or "",
            "site": parsed.get("site") or "",
            "station": parsed.get("station") or "",
            "id": parsed.get("speciesId") or "",
            "storage": parsed.get("storage") or "",
            "collectionDate": collection_date,
            "photoDate": photo_date,
            "nextResultSequenceHint": parsed.get("resultSequence") or 1,
        })
        try:
            self._metadata.clear()
            self._taxon_card.clear()
        except Exception:
            pass
        return True

    def _sync_uid_display_summary(self) -> None:
        """Mirror existing metadata into the UID card's non-identity summary."""
        values = {}
        try:
            values.update(self._metadata.current_values())
        except Exception:
            pass
        try:
            values.update(self._taxon_card.field_values())
        except Exception:
            pass
        try:
            values["photo_notes"] = self._naming._photo_notes.toPlainText().strip()
            self._naming.set_display_metadata(values)
        except Exception:
            pass

    # ── Collection-record auto-fill ─────────────────────────────────────────────

    def _apply_collection_autofill(self) -> None:
        """Fill empty capture fields from a matching 采集记录 (野外采集记录簿).

        Triggered when the four location keys (地区/样地/站位/采集日期) are
        finished editing or picked from the record menu. Non-destructive: only
        empty fields are filled (collection_record_service.autofill_values).
        Fields the capture cards lack (生境/潮水/…) stay in the record only,
        unless the project naming rules expose a matching dynamic naming field.
        """
        db = self.ctx.get_db()
        if not db:
            return
        province, site, station, col_date = self._naming.current_keys()
        if not (province and site and station and col_date):
            return
        from app.services import collection_record_service as crs
        rec = crs.lookup_record(db, province, site, station, col_date)
        if not rec:
            return
        dynamic_changed = False
        try:
            dynamic_changed = bool(self._naming.apply_dynamic_autofill(rec))
        except Exception:
            pass
        # 优先级 项目默认 < 站位记录 < 手动/已存：把「自动填」字段当作空看待，让站位
        # 采集记录能覆盖项目默认坐标；受保护字段（用户手填/加载已存的非空值）保留真实
        # 值 → autofill_values 视为已填、不再返回，从而不被覆盖。
        auto = self._metadata.auto_fields()
        current = {
            k: ("" if (k in auto or not v.strip()) else v)
            for k, v in self._metadata.current_values().items()
        }
        current["photo_date"] = self._naming._photo_date.text()
        vals = crs.autofill_values(rec, current)
        if not vals:
            if dynamic_changed and self._current_uid:
                self._schedule_rail_save()
            return
        if "photo_date" in vals and not self._naming._photo_date.text().strip():
            self._naming._photo_date.setText(str(vals["photo_date"]))
        # override_auto=True：覆盖项目默认（自动）坐标，但 apply_autofill 内部仍
        # 跳过手动字段。
        self._metadata.apply_autofill(vals, override_auto=True)
        # Persist for an already-saved specimen; a brand-new draft persists when
        # the user hits 保存 (fields are read straight off the panels then).
        if self._current_uid:
            self._schedule_rail_save()

    def _backfill_personnel_defaults(self) -> None:
        """把项目 personnel 默认值回填到已加载标本的【空】采集人/拍摄人/鉴定人。

        与 _on_project_personnel_changed 互补：后者只灌新草稿（_current_uid 为
        None 时），不碰已存标本。但已存标本的空 personnel 字段（建号时项目默认值
        还没设、之后才设的情况）应能从项目默认值补上。非破坏：apply_autofill 只填
        空字段，已存值（含采集记录已回填的）保留。
        """
        prefill = self._effective_prefill()
        values = {
            k: str(prefill.get(k) or "").strip()
            for k in ("collector", "photographer", "identifier")
        }
        if not any(values.values()):
            return
        before = self._metadata.current_values()
        self._metadata.apply_autofill(values, override_auto=True)
        after = self._metadata.current_values()
        if self._current_uid and before != after:
            self._schedule_rail_save()
        self._sync_uid_display_summary()
