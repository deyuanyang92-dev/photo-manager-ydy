"""Project settings side drawer compatibility facade.

The drawer is split into deep workflow mixins while preserving the original
ProjectSettingsDrawer import path and private helper names used by tests.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget

from app.widgets.project_settings_drawer_layout import ProjectSettingsLayoutMixin
from app.widgets.project_settings_drawer_lifecycle import ProjectSettingsLifecycleMixin
from app.widgets.project_settings_drawer_naming import ProjectSettingsNamingMixin
from app.widgets.project_settings_drawer_persistence import ProjectSettingsPersistenceMixin
from app.widgets.project_settings_drawer_printing import ProjectSettingsPrintMixin
from app.widgets.project_settings_drawer_storage import ProjectSettingsStorageMixin
from app.widgets.project_settings_drawer_support import (
    _CollapsibleSection,
    _CustomFieldEditor,
    _KVEditor,
    _divider,
    _scrollable_tab,
    _settings_form_row,
    _settings_group,
)

if TYPE_CHECKING:
    from app.app_context import AppContext


class ProjectSettingsDrawer(
    ProjectSettingsStorageMixin,
    ProjectSettingsPersistenceMixin,
    ProjectSettingsNamingMixin,
    ProjectSettingsPrintMixin,
    ProjectSettingsLayoutMixin,
    ProjectSettingsLifecycleMixin,
    QWidget,
):
    """Overlay drawer for project-level settings."""

    closed = pyqtSignal()
    helicon_path_changed = pyqtSignal(str)
    naming_rules_changed = pyqtSignal()
    personnel_changed = pyqtSignal(dict)
    storages_changed = pyqtSignal()

    def __init__(self, ctx: "AppContext", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName("SettingsDrawer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._setup_ui()
        self.hide()
