"""base_view.py — Abstract base class for all module views.

Every module in the workbench implements a subclass of BaseView.
MainWindow's view registry uses the class-level attributes to populate
the navigation list, and calls on_activate() each time the user switches
to that module's page.

Contract (required by each module view)
----------------------------------------
class MyModuleView(BaseView):
    view_id   = "my_module"          # snake_case, unique, used as object name
    nav_title = "我的模块"            # text shown in the navigation sidebar
    nav_icon  = "🔬"                 # emoji or icon resource name (placeholder)

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx)
        # build your UI here

    def on_activate(self) -> None:
        # called each time user navigates to this page
        # refresh data, reset scroll, etc.
        pass
"""
from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from app.app_context import AppContext


class BaseView(QWidget):
    """Abstract base for all module views.

    Subclasses MUST define class attributes ``view_id``, ``nav_title``,
    ``nav_icon``, and override ``on_activate()``.
    """

    #: Unique snake_case identifier, used as QWidget object name.
    view_id: str = ""

    #: Text shown in the navigation sidebar (Chinese preferred).
    nav_title: str = ""

    #: Emoji or icon resource name shown beside nav_title.
    nav_icon: str = ""

    def __init__(self, ctx: "AppContext") -> None:
        super().__init__()
        self.ctx = ctx
        self.setObjectName(self.view_id)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Override to build widget tree.  Called from __init__ after ctx is set."""

    @abstractmethod
    def on_activate(self) -> None:
        """Called every time the user navigates to this view.

        Use this to refresh data, reset scroll position, or trigger
        any side effects that should happen on page entry.
        Guaranteed to run on the main thread.
        """

    def stop_background_work(self) -> None:
        """Cancel any background QThread / subprocess this view owns.

        Called from MainWindow._teardown() on every exit path so a view's
        in-flight worker (Helicon compose, WoRMS batch job, …) cannot keep a
        QThread + its SQLite/subprocess handles alive past app exit — the root
        cause of the "close → reopen → must reboot" lock leak on WSL/drvfs.
        Default no-op; override in views that own long-running workers.
        """

