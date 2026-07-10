"""Compatibility alias for project cover selection."""

import sys

from app.services.project import cover_pick_service as _impl

sys.modules[__name__] = _impl
