"""Custom storage-code workflow for ProjectSettingsDrawer."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QHBoxLayout, QMessageBox, QPushButton, QTableWidgetItem, QWidget


class ProjectSettingsStorageMixin:
    # ── Custom storages list ──────────────────────────────────────────────────

    def _refresh_builtin_storage_table(self, db) -> None:
        from app.services.project_settings_service import (
            BUILTIN_STORAGES,
            load_custom_storages,
            resolve_storage_detail,
            resolve_storage_transcriptome,
        )

        custom = load_custom_storages(db) if db is not None else []
        tbl = self._builtin_storage_table
        for i, entry in enumerate(BUILTIN_STORAGES):
            code = entry["code"]
            detail = resolve_storage_detail(code, custom) or entry["detail"]
            transcriptome = resolve_storage_transcriptome(code, custom)
            if transcriptome:
                detail = "[RNA] " + detail
            code_cell = QTableWidgetItem(code)
            detail_cell = QTableWidgetItem(detail)
            detail_cell.setToolTip(detail)
            tbl.setItem(i, 0, code_cell)
            tbl.setItem(i, 1, detail_cell)
            tbl.setRowHeight(i, 24)
        tbl.setColumnWidth(0, 72)

    def _load_storage_edit_form(self, code: str, detail: str) -> None:
        self._storage_edit_mode = "edit"
        self._storage_editor_title.setText(f"修改保存方式：{code}")
        self._storage_save_btn.setText("保存修改")
        self._new_code_edit.setReadOnly(True)
        self._new_code_edit.setText(code)
        self._new_detail_edit.setPlainText(detail)
        self._rna_hint_lbl.setText(
            "已取 RNA / RNAlater" if str(code).startswith("R") else ""
        )
        self._storage_save_status.clear()

    def _start_new_storage(self) -> None:
        """Enter an explicit blank form for creating a new storage code."""
        self._storage_edit_mode = "new"
        self._storage_editor_title.setText("新增保存方式")
        self._storage_save_btn.setText("添加")
        self._new_code_edit.setReadOnly(False)
        self._new_code_edit.clear()
        self._new_detail_edit.clear()
        self._rna_hint_lbl.clear()
        self._storage_save_status.setText("新增模式：填写新编码和详细说明，然后点击“添加”。")
        self._builtin_storage_table.clearSelection()
        self._new_code_edit.setFocus()

    def _on_builtin_storage_selected(self) -> None:
        row = self._builtin_storage_table.currentRow()
        if row < 0:
            return
        code_item = self._builtin_storage_table.item(row, 0)
        detail_item = self._builtin_storage_table.item(row, 1)
        if code_item is None or detail_item is None:
            return
        detail = detail_item.text()
        if detail.startswith("[RNA] "):
            detail = detail[6:]
        self._load_storage_edit_form(code_item.text(), detail)

    def _rebuild_custom_list(self, custom: list[dict], db) -> None:
        from app.services.project_settings_service import builtin_storage_codes

        while self._custom_list_lay.count():
            item = self._custom_list_lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        builtin_codes = builtin_storage_codes()
        if not custom:
            empty = QLabel("还没有新增保存方式。")
            empty.setObjectName("MutedSmall")
            empty.setWordWrap(True)
            self._custom_list_lay.addWidget(empty)
            return
        for entry in custom:
            code = str(entry.get("code", "")).upper()
            detail = entry.get("detail", "")
            prefix = "[RNA] " if entry.get("transcriptome") else ""
            kind = "修改内置" if code in builtin_codes else "自定义"
            row_w = QWidget()
            h = QHBoxLayout(row_w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            pick_btn = QPushButton(f"{code}　[{kind}]　{prefix}{detail}")
            pick_btn.setObjectName("Ghost")
            pick_btn.setFlat(True)
            pick_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pick_btn.setToolTip("点击后在下方继续编辑；× 表示撤销该项目修改")
            pick_btn.clicked.connect(
                lambda _, c=code, d=detail: self._load_storage_edit_form(c, d)
            )
            h.addWidget(pick_btn, 1)
            del_btn = QPushButton("×")
            del_btn.setObjectName("Ghost")
            del_btn.setFixedSize(24, 24)
            del_btn.clicked.connect(
                lambda _, c=code: self._delete_custom_storage_option(c)
            )
            h.addWidget(del_btn)
            self._custom_list_lay.addWidget(row_w)

    def _save_custom_storage_option(self) -> None:
        db = self.ctx.get_db()
        if db is None:
            self._storage_save_status.setText("保存失败：当前项目数据库未打开。")
            QMessageBox.warning(self, "保存方式", "保存失败：当前项目数据库未打开。")
            return
        code = self._new_code_edit.text().strip().upper()
        detail = self._new_detail_edit.toPlainText().strip()
        if not code or not detail:
            self._storage_save_status.setText("保存失败：编码和详细说明都必须填写。")
            return
        from app.services.project_settings_service import (
            load_custom_storages,
            save_setting,
        )

        custom = load_custom_storages(db)
        payload = {
            "code": code,
            "detail": detail,
            "transcriptome": code.startswith("R"),
        }
        replaced = False
        for index, entry in enumerate(custom):
            if str(entry.get("code", "")).upper() == code:
                custom[index] = payload
                replaced = True
                break
        if not replaced:
            custom.append(payload)
        try:
            save_setting(db, "custom_storages", custom)
            saved = load_custom_storages(db)
            verified = next(
                (
                    entry for entry in saved
                    if str(entry.get("code", "")).upper() == code
                    and str(entry.get("detail", "")) == detail
                ),
                None,
            )
            if verified is None:
                raise RuntimeError("数据库回读结果与保存内容不一致")
        except Exception as exc:
            self._storage_save_status.setText(f"保存失败：{exc}")
            QMessageBox.warning(self, "保存方式", f"保存失败：{exc}")
            return

        # Keep the edited values visible so the user can verify exactly what
        # was saved; the project list above is rebuilt from the DB read-back.
        self._refresh_builtin_storage_table(db)
        self._rebuild_custom_list(saved, db)
        action = "已添加" if self._storage_edit_mode == "new" else "已保存修改"
        self._storage_save_status.setText(f"{action}：{code}")
        self._load_storage_edit_form(code, detail)
        self._storage_save_status.setText(f"{action}：{code}")
        self.storages_changed.emit()

    def _save_new_custom_storage_option(self) -> None:
        self._save_custom_storage_option()

    def _delete_custom_storage_option(self, code: str) -> None:
        db = self.ctx.get_db()
        if db is None:
            return
        from app.services.project_settings_service import (
            load_custom_storages,
            save_setting,
        )
        custom = load_custom_storages(db)
        custom = [s for s in custom if s["code"] != code]
        save_setting(db, "custom_storages", custom)
        self._refresh_builtin_storage_table(db)
        self._rebuild_custom_list(custom, db)
        self.storages_changed.emit()

    def _start_blank_custom_storage_form(self) -> None:
        self._start_new_storage()
