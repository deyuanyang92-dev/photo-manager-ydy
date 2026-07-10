"""Compatibility alias for collaboration datatypes and helpers."""

import sys

from app.services.collab import collab_types as _impl

sys.modules[__name__] = _impl
