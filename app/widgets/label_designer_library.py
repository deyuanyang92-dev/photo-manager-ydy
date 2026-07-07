"""Custom template-library actions and result accessors for label designer."""
from __future__ import annotations

import copy
from typing import Optional

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from app.services.label_service import id_from_key, is_library_key, key_from_id


class LabelDesignerLibraryMixin:
    # ── Template library management ────────────────────────────────────────────
    def _save_as_new(self) -> None:
        name, ok = QInputDialog.getText(self, "另存为新模板", "模板名称:")
        if not ok or not name.strip():
            return
        if self._lib is not None:
            saved = copy.deepcopy(self._tmpl)
            saved["minSize"] = {
                "w": float(self._dims["w"]), "h": float(self._dims["h"])
            }
            rec = self._lib.upsert({"name": name.strip(), "template": saved})
            self._lib.set_selected_key(key_from_id(rec["id"]))
            self._selected_key = key_from_id(rec["id"])
        QMessageBox.information(self, "已保存", f"已保存模板「{name.strip()}」。")

    def _current_custom_id(self) -> Optional[str]:
        if self._lib is None:
            return None
        key = self._lib.selected_key()
        return id_from_key(key) if is_library_key(key) else None

    def _rename_current(self) -> None:
        cid = self._current_custom_id()
        if not cid:
            QMessageBox.information(self, "重命名", "当前是内置模板，先「另存为新模板」。")
            return
        name, ok = QInputDialog.getText(self, "重命名", "新名称:")
        if ok and name.strip():
            self._lib.rename(cid, name.strip())

    def _duplicate_current(self) -> None:
        cid = self._current_custom_id()
        if not cid:
            QMessageBox.information(self, "复制", "当前是内置模板，先「另存为新模板」。")
            return
        rec = self._lib.duplicate(cid)
        if rec:
            self._lib.set_selected_key(key_from_id(rec["id"]))
            self._selected_key = key_from_id(rec["id"])

    def _delete_current(self) -> None:
        cid = self._current_custom_id()
        if not cid:
            QMessageBox.information(self, "删除", "内置模板不可删除。")
            return
        if QMessageBox.question(self, "删除", "确定删除当前自定义模板？") == QMessageBox.StandardButton.Yes:
            self._lib.delete(cid)
            self._selected_key = None

    # ── Result ─────────────────────────────────────────────────────────────────
    def edited_template(self) -> dict:
        return copy.deepcopy(self._tmpl)

    def selected_key(self) -> Optional[str]:
        """Library key chosen via 另存为 (or None — caller decides how to persist)."""
        return self._selected_key
