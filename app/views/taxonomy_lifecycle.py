"""Lifecycle and service initialization for the taxonomy view."""
from __future__ import annotations

from pathlib import Path

from app.services.taxonomy_service import TaxonomyService
from app.views import taxonomy_view_support as _tv


class TaxonomyLifecycleMixin:
    # ── Service init ──────────────────────────────────────────────────────────

    def _try_init_service(self) -> None:
        candidates = [
            (
                Path(__file__).parent.parent.parent.parent
                / "photo-platform-ydy"
                / "prototype-photo-gui"
                / "data"
                / "taxonomy_seed.json",
                _tv._PROJECT_ROOT / "data" / "user_taxonomy.json",
            ),
            (_tv._DEFAULT_SEED_PATH, _tv._DEFAULT_USER_PATH),
        ]
        for seed_p, user_p in candidates:
            if seed_p.exists():
                self._svc = TaxonomyService(seed_p, user_p)
                return
        _tv._DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._svc = TaxonomyService(_tv._DEFAULT_SEED_PATH, _tv._DEFAULT_USER_PATH)

    # ── BaseView contract ─────────────────────────────────────────────────────

    def on_activate(self) -> None:
        if self._svc:
            self._svc.reload()
        self._load_page()

    def stop_background_work(self) -> None:
        """Interrupt the WoRMS batch-job worker so it cannot keep a QThread +
        its DB reads alive past app exit (the must-reboot lock-leak path)."""
        w = getattr(self, "_job_worker", None)
        if w is not None and w.isRunning():
            w.requestInterruption()
            w.wait(2000)
