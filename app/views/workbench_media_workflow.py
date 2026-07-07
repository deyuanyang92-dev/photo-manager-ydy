"""workbench_media_workflow.py — Workbench media workflow mixin facade.

The public Interface remains ``WorkbenchMediaWorkflowMixin`` for WorkbenchView.
The Implementation is split by workflow so compose, organise/archive,
supplementary restore, result binding, and selected-JPG compose can evolve with
better locality.
"""
from __future__ import annotations

from app.views.workbench_compose_workflow import WorkbenchComposeWorkflowMixin
from app.views.workbench_implicit_compose_workflow import WorkbenchImplicitComposeWorkflowMixin
from app.views.workbench_organise_workflow import WorkbenchOrganiseWorkflowMixin
from app.views.workbench_result_workflow import WorkbenchResultWorkflowMixin
from app.views.workbench_supplementary_workflow import WorkbenchSupplementaryWorkflowMixin


class WorkbenchMediaWorkflowMixin(
    WorkbenchComposeWorkflowMixin,
    WorkbenchOrganiseWorkflowMixin,
    WorkbenchSupplementaryWorkflowMixin,
    WorkbenchResultWorkflowMixin,
    WorkbenchImplicitComposeWorkflowMixin,
):
    """Facade mixin preserving WorkbenchView's existing media-workflow Interface."""


