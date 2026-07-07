"""Background worker adapters used by the WoRMS view and dialogs."""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from app.services.worms_service import WormsService

# ── Workers ────────────────────────────────────────────────────────────────────

class _SearchWorker(QObject):
    """Run WormsService.search() on a background thread."""

    finished = pyqtSignal(list)   # list of AphiaRecord dicts
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, name: str, like: bool) -> None:
        super().__init__()
        self._svc  = service
        self._name = name
        self._like = like

    def run(self) -> None:
        try:
            self.finished.emit(self._svc.search(self._name, like=self._like))
        except Exception as exc:
            self.error.emit(str(exc))


class _DetailWorker(QObject):
    """Fetch classification + synonyms + children for one AphiaID."""

    finished = pyqtSignal(dict)   # {"chain": [...], "synonyms": [...], "children": [...]}
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, aphia_id: int) -> None:
        super().__init__()
        self._svc      = service
        self._aphia_id = aphia_id

    def run(self) -> None:
        try:
            raw_chain = self._svc.classification(self._aphia_id)
            chain     = self._svc.flatten_classification(raw_chain)
            synonyms  = self._svc.synonyms(self._aphia_id)
            try:
                kids = self._svc.children(self._aphia_id, offset=1)
            except Exception:
                kids = []
            self.finished.emit({"chain": chain, "synonyms": synonyms, "children": kids})
        except Exception as exc:
            self.error.emit(str(exc))


class _LoadMoreWorker(QObject):
    """Fetch the next page of children for a taxon (load-more pagination).

    Oracle: renderWormsChildrenTab app.js ~12599–12609.
    """

    finished = pyqtSignal(list)   # additional children
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, aphia_id: int, offset: int) -> None:
        super().__init__()
        self._svc      = service
        self._aphia_id = aphia_id
        self._offset   = offset

    def run(self) -> None:
        try:
            kids = self._svc.children(self._aphia_id, offset=self._offset)
            self.finished.emit(kids if isinstance(kids, list) else [])
        except Exception as exc:
            self.error.emit(str(exc))



# ── WormsMatchDialog ───────────────────────────────────────────────────────────

class _MatchSearchWorker(QObject):
    """Search WoRMS for a manual-match query from within WormsMatchDialog."""

    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, name: str, like: bool) -> None:
        super().__init__()
        self._svc  = service
        self._name = name
        self._like = like

    def run(self) -> None:
        try:
            self.finished.emit(self._svc.search(self._name, like=self._like))
        except Exception as exc:
            self.error.emit(str(exc))


class _MatchChainWorker(QObject):
    """Fetch classification chain for the selected candidate in WormsMatchDialog."""

    finished = pyqtSignal(list)   # flattened chain
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, aphia_id: int) -> None:
        super().__init__()
        self._svc      = service
        self._aphia_id = aphia_id

    def run(self) -> None:
        try:
            raw   = self._svc.classification(self._aphia_id)
            chain = self._svc.flatten_classification(raw)
            self.finished.emit(chain)
        except Exception as exc:
            self.error.emit(str(exc))




# ── WormsQuickFillDialog ───────────────────────────────────────────────────────

class _QuickSearchWorker(QObject):
    """Background search worker for WormsQuickFillDialog."""

    finished = pyqtSignal(list)
    error    = pyqtSignal(str)

    def __init__(self, service: WormsService, name: str) -> None:
        super().__init__()
        self._svc  = service
        self._name = name

    def run(self) -> None:
        try:
            # Always use like=True for quick popup (oracle: doWormsPopupSearch ~12753)
            self.finished.emit(self._svc.search(self._name, like=True))
        except Exception as exc:
            self.error.emit(str(exc))

