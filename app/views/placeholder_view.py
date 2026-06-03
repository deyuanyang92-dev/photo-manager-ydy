"""placeholder_view.py — Placeholder for unimplemented module views.

Used during skeleton bootstrap. Every nav entry that doesn't have a real
implementation yet gets a PlaceholderView showing the module name and a
"待实现" badge.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QVBoxLayout, QLabel

from app.views.base_view import BaseView

if TYPE_CHECKING := False:
    from app.app_context import AppContext


def make_placeholder(view_id: str, title: str, icon: str) -> type:
    """Factory: returns a concrete BaseView subclass with the given identity.

    This lets each nav item remain a distinct class (important for
    isinstance checks and future replacement) without boilerplate.

    Parameters
    ----------
    view_id:  Unique snake_case id.
    title:    Chinese module name shown in nav.
    icon:     Emoji or icon resource name.

    Returns
    -------
    type
        A concrete class inheriting BaseView.
    """

    class _Placeholder(BaseView):
        pass

    _Placeholder.view_id = view_id
    _Placeholder.nav_title = title
    _Placeholder.nav_icon = icon
    _Placeholder.__name__ = f"PlaceholderView_{view_id}"
    _Placeholder.__qualname__ = _Placeholder.__name__

    def _setup_ui(self: BaseView) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        icon_label = QLabel(icon)
        icon_label.setObjectName("Placeholder")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("font-size: 48px; background: transparent;")
        layout.addWidget(icon_label)

        name_label = QLabel(f"{title}")
        name_label.setObjectName("Title")
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        badge = QLabel("待实现")
        badge.setObjectName("Muted")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            "background: transparent; font-size: 12px; letter-spacing: 2px;"
        )
        layout.addWidget(badge)

    def on_activate(self: BaseView) -> None:
        pass

    _Placeholder._setup_ui = _setup_ui
    _Placeholder.on_activate = on_activate

    return _Placeholder
