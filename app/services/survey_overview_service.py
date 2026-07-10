"""Compatibility alias for project survey-overview aggregation."""

import sys

from app.services.project import survey_overview_service as _impl

sys.modules[__name__] = _impl
