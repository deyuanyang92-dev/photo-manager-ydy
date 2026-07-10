"""Compatibility alias for the specimen edit-lock service."""

import sys

from app.services.specimen import edit_lock_service as _impl

sys.modules[__name__] = _impl
