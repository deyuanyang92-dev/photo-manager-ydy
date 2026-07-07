"""Field enablement and local-setting slots for ProjectSettingsDrawer."""
from __future__ import annotations

import os


class ProjectSettingsLifecycleMixin:
    # ── Field enable/disable ──────────────────────────────────────────────────

    def _set_fields_enabled(self, enabled: bool) -> None:
        for edit in self._meta_edits.values():
            edit.setEnabled(enabled)
        for edit in self._person_edits.values():
            edit.setEnabled(enabled)
        self._province_edit.setEnabled(enabled)
        self._site_edit.setEnabled(enabled)
        self._naming_custom_fields.setEnabled(enabled)
        for cb in self._naming_required_checks.values():
            cb.setEnabled(enabled)
        for cb in self._naming_component_checks.values():
            cb.setEnabled(enabled)
        self._stations_kv.setEnabled(enabled)
        self._species_kv.setEnabled(enabled)
        for cb in self._tiff_checks.values():
            cb.setEnabled(enabled)
        self._tiff_write_enabled_cb.setEnabled(enabled)
        self._tiff_write_mode_combo.setEnabled(enabled)
        self._new_code_edit.setEnabled(enabled)
        self._new_detail_edit.setEnabled(enabled)
        self._quick_print_mode.setEnabled(enabled)
        self._print_tissue_cb.setEnabled(enabled)
        self._sample_printer_combo.setEnabled(enabled)
        self._tissue_printer_combo.setEnabled(enabled)
        self._sample_template_combo.setEnabled(enabled)
        self._tissue_template_combo.setEnabled(enabled)
        self._sample_template_btn.setEnabled(enabled)
        self._tissue_template_btn.setEnabled(enabled)
        self._sample_paper_combo.setEnabled(enabled)
        self._tissue_paper_combo.setEnabled(enabled)
        self._sample_imposition_btn.setEnabled(enabled)
        self._tissue_imposition_btn.setEnabled(enabled)
        self._tissue_strategy_combo.setEnabled(enabled)
        self._save_print_default_btn.setEnabled(True)
        self._silent_compose_cb.setEnabled(True)
        self._delete_jpg_after_archive_cb.setEnabled(True)
        self._sync_imposition_buttons()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _hide_drawer_and_emit_closed(self) -> None:
        self.hide()
        self.closed.emit()

    def _on_close(self) -> None:
        self._hide_drawer_and_emit_closed()

    def _detect_and_apply_helicon_path(self) -> None:
        custom_path = self._helicon_path_edit.text().strip()
        if custom_path:
            os.environ["HELICON_FOCUS_PATH"] = custom_path
        self.refresh()
        if custom_path:
            self.helicon_path_changed.emit(custom_path)

    def _save_auto_activate_new_specimen_setting(self, checked: bool) -> None:
        try:
            self.ctx.settings.auto_activate_on_new_specimen = checked
        except Exception:
            pass

    def _save_silent_compose_setting(self, checked: bool) -> None:
        try:
            self.ctx.settings.silent_compose = checked
            self.ctx.settings.flush_to_disk()
        except Exception:
            pass

    def _save_delete_jpg_after_archive_setting(self, checked: bool) -> None:
        try:
            self.ctx.settings.delete_jpg_after_archive = checked
            self.ctx.settings.flush_to_disk()
        except Exception:
            pass

