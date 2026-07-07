"""label_designer_dialog.py — free-form label designer (canvas + property panel).

Canvas-first WYSIWYG editor the user asked for: click any text field or the QR
on the left canvas → its full set of adjustable properties appears on the right;
drag to move; arrow keys nudge; toolbar adds fields/rows and manages templates.

The canvas paints the SAME pixmap the printer/preview produce (via
``render_label_onto``) and overlays interactive hit-boxes the renderer emits —
so what you arrange is exactly what prints (no DOM/PDF drift like the web).

Reused, unchanged:
  * rendering + hit-boxes : app.utils.label_render.render_label_onto
  * QR image              : app.widgets.label_editor._generate_qr_pixmap
  * template shape        : app.utils.label_core.normalize_template
  * template library      : app.services.label_service.LabelTemplateLibrary
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QDialog, QWidget

from app.services.label_service import LabelTemplateLibrary
from app.utils.label_core import normalize_template
from app.widgets import label_designer_support as _ld
from app.widgets.label_designer_canvas import _DesignCanvas
from app.widgets.label_designer_edit_workflow import LabelDesignerEditMixin
from app.widgets.label_designer_interaction import LabelDesignerInteractionMixin
from app.widgets.label_designer_layout import LabelDesignerLayoutMixin
from app.widgets.label_designer_library import LabelDesignerLibraryMixin
from app.widgets.label_designer_panels import _FloatingToolbar, LayersPanel
from app.widgets.label_designer_properties import _PropertyPanel
from app.widgets.label_designer_state import LabelDesignerStateMixin

# Compatibility exports for older tests/imports that reached into this module.
MIN_EL_MM = _ld.MIN_EL_MM
_STYLE_KEYS = _ld._STYLE_KEYS
_BATCH_OPS = _ld._BATCH_OPS
_STRUCTURAL_OPS = _ld._STRUCTURAL_OPS
ELEMENT_TYPE_LABELS = _ld.ELEMENT_TYPE_LABELS
SHAPE_PRESETS = _ld.SHAPE_PRESETS
FIELD_LABELS = _ld.FIELD_LABELS
_default_element = _ld._default_element
_field_name = _ld._field_name
_make_designer_button = _ld._make_designer_button

_make_designer_button = _ld._make_designer_button

class LabelDesignerDialog(
    LabelDesignerLayoutMixin,
    LabelDesignerStateMixin,
    LabelDesignerInteractionMixin,
    LabelDesignerEditMixin,
    LabelDesignerLibraryMixin,
    QDialog,
):
    """Full free-form label designer."""

    def __init__(
        self,
        template: Optional[dict],
        dims: Optional[dict],
        label_data: Optional[dict],
        library: Optional[LabelTemplateLibrary] = None,
        title: str = "标签设计器",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(940, 640)
        self.setStyleSheet("background:#0c1e26; color:#eef3ef;")
        self._tmpl = normalize_template(template)
        self._dims = dims or {"w": 60, "h": 40}
        self._data = label_data or {}
        self._lib = library
        self._undo: list = []
        self._redo: list = []
        self._multi: set = set()       # extra element indices for group ops
        self._clipboard: list = []     # copied elements (normalized dicts)
        self._inline_editor = None     # QLineEdit overlay during in-place edit
        self._inline_index = -1
        self._drag_baseline: Optional[tuple] = None
        self._selected_key: Optional[str] = None  # chosen library key on accept
        self._fmt_pending: Optional[dict] = None   # captured style for format painter
        self._setup_ui()
        self._refresh_designer_state()
