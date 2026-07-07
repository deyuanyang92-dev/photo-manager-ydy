"""Project settings load/save workflow for ProjectSettingsDrawer."""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QMessageBox, QVBoxLayout, QWidget


class ProjectSettingsPersistenceMixin:
    # ── Public API ────────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Reload from DB + Helicon detection. Call after project open/change."""
        self._refresh_project_scope()

        # Helicon
        try:
            from app.services.helicon_service import detect_helicon
            exe = detect_helicon()
            if exe:
                self._helicon_status_lbl.setText(f"✅ 已检测到：{exe}")
            else:
                self._helicon_status_lbl.setText(
                    "⚠️ 未检测到 Helicon Focus。请安装后重新检测，"
                    "或在下方填写自定义路径。"
                )
        except Exception as e:
            self._helicon_status_lbl.setText(f"检测失败：{e}")

        # Subdir info
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if project_dir:
            from app.services.project_service import INCOMING_JPG_DIR, RESULTS_DIR
            self._dir_info_lbl.setText(
                f"相机 JPG：{INCOMING_JPG_DIR}/\n成果 TIFF/ZIP：{RESULTS_DIR}/"
            )
        else:
            self._dir_info_lbl.setText("（未选择项目）")

        # Auto-activate
        try:
            val = bool(getattr(self.ctx.settings, "auto_activate_on_new_specimen", False))
            self._auto_activate_cb.setChecked(val)
        except Exception:
            pass
        try:
            val = bool(getattr(self.ctx.settings, "silent_compose", False))
            self._silent_compose_cb.setChecked(val)
        except Exception:
            pass
        try:
            val = bool(getattr(self.ctx.settings, "delete_jpg_after_archive", True))
            self._delete_jpg_after_archive_cb.setChecked(val)
        except Exception:
            pass

        db = self.ctx.get_db()
        if db is None:
            self._set_fields_enabled(False)
            return
        self._set_fields_enabled(True)
        self._load_from_db(db)

    # ── Private: load/save ────────────────────────────────────────────────────

    def _current_project_label(self) -> tuple[str, str]:
        project_dir = getattr(self.ctx, "current_project_dir", None)
        if not project_dir:
            return "未选择项目", ""
        path_text = str(project_dir)
        name = os.path.basename(os.path.normpath(path_text)) or path_text
        return name, path_text

    def _refresh_project_scope(self) -> None:
        name, path_text = self._current_project_label()
        if path_text:
            db_text = os.path.join(path_text, "_data", "project.db")
            self._title_label.setText(f"当前项目设置：{name}")
            self._header_scope_lbl.setText(path_text)
            self._project_scope_lbl.setText(
                f"当前绑定项目：{name}\n项目级内容保存到：{db_text}"
            )
            name_edit = getattr(self, "_meta_edits", {}).get("name")
            if name_edit is not None:
                name_edit.setPlaceholderText(f"默认：{name}")
        else:
            self._title_label.setText("当前项目设置")
            self._header_scope_lbl.setText("未选择项目")
            self._project_scope_lbl.setText("未选择项目；项目级字段暂不可保存。")

    def _load_from_db(self, db) -> None:
        from app.services.project_settings_service import (
            load_setting,
            DEFAULT_PROJECT_META,
            DEFAULT_PERSONNEL,
            DEFAULT_CODE_LABELS,
            DEFAULT_NAMING_RULES,
            DEFAULT_CAPTURE_DEFAULTS,
            DEFAULT_TIFF_FIELDS,
            DEFAULT_TIFF_METADATA_WRITE,
            DEFAULT_PRINT_SETTINGS,
            effective_print_settings,
            load_setting_if_present,
            load_global_print_defaults,
            merge_print_settings,
        )
        from app.services.naming_field_catalog import (
            normalize_required,
            ordered_component_keys,
            normalize_custom_fields,
        )

        # 概要
        meta = load_setting(db, "project_meta", DEFAULT_PROJECT_META)
        for key, edit in self._meta_edits.items():
            edit.setText(meta.get(key, ""))

        # 人员预设
        pers = load_setting(db, "personnel", DEFAULT_PERSONNEL)
        for key, edit in self._person_edits.items():
            edit.setText(pers.get(key, ""))

        # 命名规则
        cl = load_setting(db, "code_labels", DEFAULT_CODE_LABELS)
        self._province_edit.setText(cl.get("province", ""))
        self._site_edit.setText(cl.get("site", ""))
        self._stations_kv.load_entries(cl.get("stations", {}))
        self._species_kv.load_entries(cl.get("species", {}))
        rules = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
        custom_fields = normalize_custom_fields(rules.get("custom_fields", []))
        self._naming_custom_fields.load_fields(custom_fields)
        self._naming_custom_field_snapshot = custom_fields
        required = normalize_required(
            rules.get("required", DEFAULT_NAMING_RULES["required"]),
            custom_fields,
        )
        components = rules.get("components", DEFAULT_NAMING_RULES["components"])
        if not isinstance(components, list):
            components = DEFAULT_NAMING_RULES["components"]
        self._naming_component_order = ordered_component_keys(components, custom_fields)
        self._rebuild_naming_unified_table(
            checked_required=required,
            checked_components={str(key) for key in components},
        )
        # 默认采集坐标 / 地理区
        cap = load_setting(db, "capture_defaults", DEFAULT_CAPTURE_DEFAULTS)
        self._cap_lon_edit.setText(str(cap.get("lon", "") or ""))
        self._cap_lat_edit.setText(str(cap.get("lat", "") or ""))
        self._cap_geo_edit.setText(cap.get("geoArea", "") or "")
        self._update_code_preview(db)

        # TIFF 字段
        tf = load_setting(db, "tiff_fields", DEFAULT_TIFF_FIELDS)
        for key, cb in self._tiff_checks.items():
            cb.blockSignals(True)
            cb.setChecked(tf.get(key, DEFAULT_TIFF_FIELDS.get(key, False)))
            cb.blockSignals(False)
        tw = load_setting(db, "tiff_metadata_write", DEFAULT_TIFF_METADATA_WRITE)
        self._tiff_write_enabled_cb.blockSignals(True)
        self._tiff_write_enabled_cb.setChecked(bool(tw.get("enabled", True)))
        self._tiff_write_enabled_cb.blockSignals(False)
        mode = str(tw.get("mode") or DEFAULT_TIFF_METADATA_WRITE["mode"])
        idx = self._tiff_write_mode_combo.findData(mode)
        self._tiff_write_mode_combo.blockSignals(True)
        self._tiff_write_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._tiff_write_mode_combo.blockSignals(False)

        # 自定义保存方式
        from app.services.project_settings_service import load_custom_storages

        custom = load_custom_storages(db)
        self._refresh_builtin_storage_table(db)
        self._rebuild_custom_list(custom, db)

        # 工作台单张打印
        project_dir = getattr(self.ctx, "current_project_dir", None)
        project_root = getattr(self.ctx, "current_project_root", None)
        if not isinstance(project_root, str):
            project_root = None
        if project_dir:
            pr = effective_print_settings(
                project_dir,
                root=project_root,
            )
        else:
            pr = load_setting(db, "print_settings", load_global_print_defaults())
        local_print_settings = load_setting_if_present(db, "print_settings")
        if local_print_settings is not None:
            pr = merge_print_settings(pr, local_print_settings)
            if "quick_print_mode" not in local_print_settings and "quick_print" in local_print_settings:
                pr["quick_print_mode"] = (
                    "direct" if bool(local_print_settings["quick_print"]) else "studio"
                )
        # backward compat: new quick_print_mode string wins;
        # old quick_print bool maps True→"direct", False→"studio".
        # DEFAULT_PRINT_SETTINGS now carries quick_print_mode="direct", so
        # default must be remapped when legacy quick_print=False is present.
        quick_mode = str(pr.get("quick_print_mode") or "")
        if not quick_mode:
            quick_mode = "direct" if bool(pr.get("quick_print", True)) else "studio"
        elif quick_mode == "direct" and not bool(pr.get("quick_print", True)):
            quick_mode = "studio"
        # Older versions exposed "open label studio" as a print action.  A
        # print button must print; template design now has its own explicit
        # management button.
        migrated_studio_mode = quick_mode == "studio"
        if migrated_studio_mode:
            quick_mode = "dialog"
        idx = self._quick_print_mode.findData(quick_mode)
        if idx < 0:
            idx = 0
        self._quick_print_mode.blockSignals(True)
        self._quick_print_mode.setCurrentIndex(idx)
        self._quick_print_mode.blockSignals(False)
        self._print_tissue_cb.blockSignals(True)
        self._print_tissue_cb.setChecked(bool(pr.get(
            "include_tissue", DEFAULT_PRINT_SETTINGS["include_tissue"]
        )))
        self._print_tissue_cb.blockSignals(False)
        self._refresh_printer_combo(
            self._sample_printer_combo,
            str(pr.get("sample_printer", DEFAULT_PRINT_SETTINGS["sample_printer"]) or ""),
        )
        self._refresh_printer_combo(
            self._tissue_printer_combo,
            str(pr.get("tissue_printer", DEFAULT_PRINT_SETTINGS["tissue_printer"]) or ""),
        )
        self._refresh_template_combo(
            self._sample_template_combo,
            "sample",
            str(pr.get("sample_template_key", DEFAULT_PRINT_SETTINGS["sample_template_key"]) or ""),
        )
        self._refresh_template_combo(
            self._tissue_template_combo,
            "tissue",
            str(pr.get("tissue_template_key", DEFAULT_PRINT_SETTINGS["tissue_template_key"]) or ""),
        )
        for combo, key in (
            (self._sample_paper_combo, "sample_paper_type"),
            (self._tissue_paper_combo, "tissue_paper_type"),
        ):
            value = str(pr.get(key, DEFAULT_PRINT_SETTINGS[key]) or "label")
            idx = combo.findData(value)
            combo.blockSignals(True)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)
        strategy = str(pr.get("tissue_strategy", DEFAULT_PRINT_SETTINGS["tissue_strategy"]) or "auto")
        idx = self._tissue_strategy_combo.findData(strategy)
        self._tissue_strategy_combo.blockSignals(True)
        self._tissue_strategy_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._tissue_strategy_combo.blockSignals(False)
        self._sync_imposition_buttons()
        self._sync_niimbot_print_hint()
        if migrated_studio_mode:
            self._save_print_settings()

    def _save_project_meta(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import load_setting, save_setting, DEFAULT_PROJECT_META
        data = load_setting(db, "project_meta", DEFAULT_PROJECT_META)
        for key, edit in self._meta_edits.items():
            data[key] = edit.text().strip()
        save_setting(db, "project_meta", data)

    def _save_personnel(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        data = {key: edit.text().strip() for key, edit in self._person_edits.items()}
        save_setting(db, "personnel", data)
        self.personnel_changed.emit(dict(data))

    def _save_code_labels(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        data = {
            "province": self._province_edit.text().strip(),
            "site": self._site_edit.text().strip(),
            "stations": self._stations_kv.entries(),
            "species": self._species_kv.entries(),
        }
        save_setting(db, "code_labels", data)
        self._update_code_preview(db)

    def _save_naming_rules(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import (
            DEFAULT_NAMING_RULES,
            load_setting,
            save_setting,
        )
        from app.services.naming_field_catalog import (
            component_fields,
            normalize_required,
            required_fields,
        )
        data = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
        custom_fields = self._custom_naming_fields()
        old_custom_fields = getattr(self, "_naming_custom_field_snapshot", [])
        data["custom_fields"] = custom_fields
        valid_required = {field.key for field in required_fields(custom_fields)}
        data["required"] = {
            key: cb.isChecked()
            for key, cb in self._naming_required_checks.items()
            if key in valid_required
        }
        data["required"] = normalize_required(data["required"], custom_fields)
        valid_components = {field.key for field in component_fields(custom_fields)}
        data["components"] = [
            key
            for key in getattr(self, "_naming_component_order", [])
            if self._naming_component_checks.get(key)
            and key in valid_components
            and self._naming_component_checks[key].isChecked()
        ]
        save_setting(db, "naming_rules", data)
        if custom_fields != old_custom_fields:
            self._naming_custom_fields.load_fields(custom_fields)
            self._naming_custom_field_snapshot = list(custom_fields)
            self._naming_component_order = [
                key for key in self._naming_component_order
                if key in self._naming_component_checks
                or any(field.get("key") == key for field in custom_fields)
            ]
            self._rebuild_naming_unified_table(
                checked_required=data["required"],
                checked_components=set(data["components"]),
            )
        self._update_code_preview(db)
        self.naming_rules_changed.emit()

    def _save_capture_defaults(self) -> None:
        """保存项目级默认采集坐标 / 地理区（capture_defaults）。"""
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        save_setting(db, "capture_defaults", {
            "lon": self._cap_lon_edit.text().strip(),
            "lat": self._cap_lat_edit.text().strip(),
            "geoArea": self._cap_geo_edit.text().strip(),
        })

    def _save_tiff_fields(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        data = {key: cb.isChecked() for key, cb in self._tiff_checks.items()}
        save_setting(db, "tiff_fields", data)

    def _save_tiff_metadata_write(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        save_setting(db, "tiff_metadata_write", {
            "enabled": self._tiff_write_enabled_cb.isChecked(),
            "mode": str(self._tiff_write_mode_combo.currentData() or "fill_empty"),
        })

    def _save_print_settings(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import save_setting
        save_setting(db, "print_settings", self._collect_print_settings())

    def _collect_print_settings(self) -> dict:
        quick_mode = str(self._quick_print_mode.currentData() or "direct")
        return {
            # Kept true for old readers: both modern modes are printing modes.
            "quick_print": True,
            "quick_print_mode": quick_mode,
            "include_tissue": self._print_tissue_cb.isChecked(),
            "sample_printer": str(self._sample_printer_combo.currentData() or ""),
            "tissue_printer": str(self._tissue_printer_combo.currentData() or ""),
            "sample_template_key": str(self._sample_template_combo.currentData() or ""),
            "tissue_template_key": str(self._tissue_template_combo.currentData() or ""),
            "sample_paper_type": str(self._sample_paper_combo.currentData() or ""),
            "tissue_paper_type": str(self._tissue_paper_combo.currentData() or ""),
            "tissue_strategy": str(self._tissue_strategy_combo.currentData() or "auto"),
        }

    def _save_print_defaults(self) -> None:
        from app.services.project_settings_service import save_global_print_defaults
        save_global_print_defaults(self._collect_print_settings())
        QMessageBox.information(self, "打印默认值", "已保存为全局打印默认值。")

    def _on_sample_paper_changed(self) -> None:
        self._save_print_settings()
        self._sync_imposition_buttons()
        self._sync_niimbot_print_hint()

    def _on_tissue_paper_changed(self) -> None:
        self._save_print_settings()
        self._sync_imposition_buttons()
        self._sync_niimbot_print_hint()

    def _update_code_preview(self, db) -> None:
        try:
            from app.services.naming_field_catalog import (
                field_label,
                ordered_component_keys,
            )
            from app.services.project_settings_service import (
                DEFAULT_CODE_LABELS,
                DEFAULT_NAMING_RULES,
                load_setting,
            )

            rules = load_setting(db, "naming_rules", DEFAULT_NAMING_RULES)
            components = rules.get("components") or DEFAULT_NAMING_RULES["components"]
            custom_fields = rules.get("custom_fields", [])
            ordered = ordered_component_keys(components, custom_fields)
            active_comps = [k for k in ordered if k in set(components)]

            # Gather segment values
            row = db.execute(
                "SELECT uid FROM specimens ORDER BY rowid LIMIT 1"
            ).fetchone()

            seg_pairs: list[tuple[str, str]] = []  # (value, label)
            if row and row[0]:
                uid = row[0]
                parts = uid.split("-")
                for i, key in enumerate(active_comps):
                    seg = parts[i] if i < len(parts) else "…"
                    seg_pairs.append((seg, field_label(key, custom_fields=custom_fields)))
            else:
                cl = load_setting(db, "code_labels", DEFAULT_CODE_LABELS)
                example = {
                    "province": cl.get("province") or "地区",
                    "site": cl.get("site") or "样地",
                    "station": "B2",
                    "species_id": "DLC001",
                    "storage": "D95E",
                    "date_seg": "20260101",
                }
                for key in active_comps:
                    val = example.get(key) or key
                    seg_pairs.append((val, field_label(key, custom_fields=custom_fields)))

            self._rebuild_code_preview_card(seg_pairs, is_example=not (row and row[0]))
        except Exception:
            self._rebuild_code_preview_card([], is_example=False)

    def _rebuild_code_preview_card(
        self,
        seg_pairs: list[tuple[str, str]],
        *,
        is_example: bool = False,
    ) -> None:
        """Clear and rebuild the UID preview card from (value, label) pairs."""
        lay = self._code_preview_card_lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not seg_pairs:
            placeholder = QLabel("（请先勾选参与编号的字段）")
            placeholder.setObjectName("Muted")
            lay.addWidget(placeholder)
            lay.addStretch()
            self._code_preview_lbl = placeholder
            return

        for idx, (val, lbl_text) in enumerate(seg_pairs):
            if idx > 0:
                sep = QLabel("-")
                sep.setObjectName("NamingGroupTitle")
                sep.setContentsMargins(4, 0, 4, 12)
                lay.addWidget(sep)

            seg_w = QWidget()
            seg_lay = QVBoxLayout(seg_w)
            seg_lay.setContentsMargins(0, 0, 0, 0)
            seg_lay.setSpacing(1)

            val_lbl = QLabel(val)
            val_lbl.setObjectName("Mono")
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            name_lbl = QLabel(lbl_text)
            name_lbl.setObjectName("NamingGroupTitle")
            name_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)

            seg_lay.addWidget(val_lbl)
            seg_lay.addWidget(name_lbl)
            lay.addWidget(seg_w)

        lay.addStretch()

        if is_example:
            hint = QLabel("示例")
            hint.setObjectName("MutedSmall")
            hint.setContentsMargins(6, 0, 0, 0)
            lay.addWidget(hint)
