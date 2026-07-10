"""Compatibility alias for the label-designer schema."""

import sys

from app.services.label import label_design_schema as _impl

sys.modules[__name__] = _impl
