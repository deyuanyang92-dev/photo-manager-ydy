"""Compatibility alias for the specimen naming-field catalog."""

import sys

from app.services.specimen import naming_field_catalog as _impl

sys.modules[__name__] = _impl
