from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from typing import Any

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    DocLink,
    ScanJob,
    TaskPreset,
    Workspace,
    WorkspaceTaskRun,
)
from . import data_lab

DEFAULT_SCAN_BLUEPRINT = (
    (ScanJob.Kind.SCHEMA, "Collecting models and relationships"),
    (ScanJob.Kind.MIGRATIONS, "Diffing unapplied migrations"),
    (ScanJob.Kind.LOGS, "Indexing runserver output"),
    (ScanJob.Kind.FIXTURES, "Exporting sample fixtures"),
)


def format_timestamp(value: timezone.datetime | None = None) -> str:
    """Consistent ISO-like timestamp for UI badges."""
    value = value or timezone.now()
    return value.strftime("%Y-%m-%dT%H:%M:%S%z")


def bootstrap_workspace_scans(
    workspace: Workspace,
    *,
    auto_run: bool = True,
) -> None:
    """Seed a predictable set of scans for tutorial screenshots."""
    if workspace.scans.exists():
        return

    now = timezone.now()
    for index, (kind, summary) in enumerate(DEFAULT_SCAN_BLUEPRINT):
        workspace.scans.create(
            kind=kind,
            summary=summary,
            status=(
                ScanJob.Status.RUNNING if auto_run and index == 0 else ScanJob.Status.PENDING
            ),
            progress=15 if auto_run and index == 0 else 0,
            started_at=now if auto_run and index == 0 else None,
        )


def serialize_scan(job: ScanJob) -> dict[str, Any]:
    return {
        "id": job.pk,
        "kind": job.kind,
        "kind_label": job.get_kind_display(),
        "status": job.status,
        "summary": job.summary,
        "progress": job.progress,
        "log_excerpt": job.log_excerpt,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def serialize_task_run(run: WorkspaceTaskRun) -> dict[str, Any]:
    return {
        "id": run.pk,
        "preset": run.preset.key,
        "label": run.preset.label,
        "status": run.status,
        "progress": run.progress,
        "log": run.log,
        "requested_at": run.created_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def workspace_status_payload(workspace: Workspace) -> dict[str, Any]:
    """Return a JSON structure consumed by the dashboard polling logic."""
    scans = [serialize_scan(job) for job in workspace.scans.all()[:4]]
    tasks = [serialize_task_run(run) for run in workspace.task_runs.all()[:5]]
    docs = [
        {
            "title": link.title,
            "url": link.url,
            "stage": link.stage,
            "icon": link.icon,
            "pane_target": link.pane_target,
        }
        for link in DocLink.objects.all()
    ]
    return {
        "workspace": workspace.slug,
        "insights": workspace.insights,
        "apps": workspace.app_overview,
        "activity": workspace.recent_activity,
        "schema": workspace.schema_graph,
        "log_excerpt": workspace.log_excerpt,
        "scans": scans,
        "tasks": tasks,
        "docs": docs,
        "data_lab": workspace_data_lab_payload(workspace),
    }


def workspace_data_lab_payload(workspace: Workspace) -> dict[str, Any]:
    """Serialize Data Lab templates + exported notebooks."""
    notebooks_payload = []
    for entry in data_lab.list_workspace_exports(workspace):
        modified = datetime.fromtimestamp(entry["modified_at"], tz=datetime_timezone.utc)
        notebooks_payload.append(
            {
                "slug": entry["slug"],
                "title": entry["title"],
                "description": entry["description"],
                "file": entry["display_path"],
                "modified_at": modified.isoformat(),
                "viewer_url": reverse(
                    "inspector:data-lab-notebook",
                    kwargs={"slug": workspace.slug, "notebook_slug": entry["slug"]},
                ),
            }
        )

    return {
        "templates": data_lab.template_summary(),
        "notebooks": notebooks_payload,
        "live_enabled": settings.INSPECTOR_DATA_LAB_LIVE,
    }


def task_catalog() -> dict[str, TaskPreset]:
    """Convenience helper for quick lookup in templates/tests."""
    return {preset.key: preset for preset in TaskPreset.objects.all()}
