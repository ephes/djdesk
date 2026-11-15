from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from datetime import timezone as datetime_timezone
from typing import Any

from django.conf import settings
from django.db import NotSupportedError, connections, transaction
from django.urls import reverse
from django.utils import timezone

from . import data_lab
from .models import (
    DocLink,
    ScanJob,
    TaskPreset,
    Workspace,
    WorkspaceTaskRun,
)

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
            status=(ScanJob.Status.RUNNING if auto_run and index == 0 else ScanJob.Status.PENDING),
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


@contextmanager
def _read_snapshot(using: str):
    connection = connections[using]
    nested = connection.in_atomic_block
    if connection.vendor not in {"sqlite", "postgresql"}:
        raise NotSupportedError("Workspace snapshots require SQLite or PostgreSQL")
    with transaction.atomic(using=using):
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                if nested:
                    cursor.execute("SHOW transaction_isolation")
                    if cursor.fetchone()[0] not in {"repeatable read", "serializable"}:
                        raise NotSupportedError(
                            "An outer workspace transaction requires repeatable read isolation"
                        )
                else:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield


def workspace_snapshot(workspace: Workspace, *, include_logs: bool = False):
    """Materialize one bounded database generation before formatting or filesystem I/O."""
    using = workspace._state.db or "default"
    with _read_snapshot(using):
        current = Workspace.objects.using(using).get(pk=workspace.pk)
        scans = current.scans.order_by("-created_at", "-pk")[:4]
        tasks = current.task_runs.select_related("preset").order_by("-created_at", "-pk")[:5]
        if not include_logs:
            scans = scans.defer("details", "log_excerpt")
            tasks = tasks.defer("log", "metadata")
        return current, list(scans), list(tasks)


def workspace_status_payload(workspace: Workspace) -> dict[str, Any]:
    """Return a JSON structure consumed by the dashboard polling logic."""
    workspace, scan_rows, task_rows = workspace_snapshot(workspace, include_logs=True)
    scans = [serialize_scan(job) for job in scan_rows]
    tasks = [serialize_task_run(run) for run in task_rows]
    docs = [
        {
            "title": link.title,
            "url": link.url,
            "icon": link.icon,
            "pane_target": link.pane_target,
            "description": link.description,
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
    exports, incomplete = data_lab.workspace_export_inventory(workspace)
    for entry in exports:
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
        "notebooks_incomplete": incomplete,
        "live_enabled": settings.INSPECTOR_DATA_LAB_LIVE,
    }


def task_catalog() -> dict[str, TaskPreset]:
    """Convenience helper for quick lookup in templates/tests."""
    return {preset.key: preset for preset in TaskPreset.objects.all()}
