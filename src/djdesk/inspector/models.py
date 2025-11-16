from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Workspace(models.Model):
    """Represents a local Django project inspected inside DJDesk."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    project_path = models.CharField(max_length=500)
    python_version = models.CharField(max_length=32, default="3.14")
    django_version = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)
    docs_url = models.URLField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    manage_py_detected = models.BooleanField(default=False)
    last_scan_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    def _generate_unique_slug(self) -> str:
        base = slugify(self.name) or "workspace"
        candidate = base
        suffix = 1
        while Workspace.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    @property
    def insights(self) -> list[dict[str, Any]]:
        return list(self.metadata.get("insights", []))

    @property
    def app_overview(self) -> list[dict[str, Any]]:
        return list(self.metadata.get("apps", []))

    @property
    def recent_activity(self) -> list[dict[str, Any]]:
        return list(self.metadata.get("recent_activity", []))

    @property
    def schema_graph(self) -> dict[str, Any]:
        return self.metadata.get("schema", {})

    @property
    def log_excerpt(self) -> list[dict[str, Any]]:
        return list(self.metadata.get("log_excerpt", []))


class ScanJob(models.Model):
    """Background inspection job that collects schema/log metadata."""

    class Kind(models.TextChoices):
        SCHEMA = ("schema", "Schema ingest")
        MIGRATIONS = ("migrations", "Migration diff")
        LOGS = ("logs", "Log import")
        FIXTURES = ("fixtures", "Fixture export")

    class Status(models.TextChoices):
        PENDING = ("pending", "Pending")
        RUNNING = ("running", "Running")
        COMPLETED = ("completed", "Completed")
        FAILED = ("failed", "Failed")

    workspace = models.ForeignKey(
        Workspace,
        related_name="scans",
        on_delete=models.CASCADE,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    summary = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict, blank=True)
    log_excerpt = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - helper
        return f"{self.get_kind_display()} ({self.get_status_display()})"

    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


class TaskPreset(models.Model):
    """Pre-approved command template shown in the assistant drawer."""

    key = models.SlugField(unique=True, max_length=64)
    label = models.CharField(max_length=140)
    description = models.TextField()
    command = models.CharField(max_length=255)
    docs_url = models.URLField(blank=True)
    category = models.CharField(max_length=60, default="diagnostics")
    icon = models.CharField(max_length=40, default="terminal")
    order = models.PositiveIntegerField(default=0)
    requires_confirmation = models.BooleanField(default=True)
    default_arguments = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["order", "label"]

    def __str__(self) -> str:  # pragma: no cover - helper
        return self.label


class WorkspaceTaskRun(models.Model):
    """Instance of a `django-tasks` command associated with a workspace."""

    class Status(models.TextChoices):
        REQUESTED = ("requested", "Requested")
        RUNNING = ("running", "Running")
        SUCCEEDED = ("succeeded", "Succeeded")
        FAILED = ("failed", "Failed")
        CANCELLED = ("cancelled", "Cancelled")

    workspace = models.ForeignKey(
        Workspace,
        related_name="task_runs",
        on_delete=models.CASCADE,
    )
    preset = models.ForeignKey(
        TaskPreset,
        related_name="task_runs",
        on_delete=models.CASCADE,
    )
    requested_by = models.CharField(max_length=120, default="system")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REQUESTED,
    )
    progress = models.PositiveSmallIntegerField(default=0)
    log = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    result_id = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - helper
        return f"{self.preset.label} ({self.get_status_display()})"

    def mark_running(self) -> None:
        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def mark_finished(self, *, success: bool) -> None:
        self.status = self.Status.SUCCEEDED if success else self.Status.FAILED
        self.completed_at = timezone.now()
        self.progress = 100
        self.save(update_fields=["status", "completed_at", "progress"])

    def append_log(self, message: str) -> None:
        stamp = timezone.now().strftime("%H:%M:%S")
        new_line = f"[{stamp}] {message}"
        if self.log:
            self.log = f"{self.log}\n{new_line}"
        else:
            self.log = new_line
        self.save(update_fields=["log"])


class DocLink(models.Model):
    """Curated Read the Docs links used throughout the UI."""

    slug = models.SlugField(unique=True, max_length=80)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    url = models.URLField()
    stage = models.PositiveIntegerField(default=0)
    category = models.CharField(max_length=40, default="tutorial")
    icon = models.CharField(max_length=30, default="book-open")
    pane_target = models.CharField(max_length=40, blank=True, default="")

    class Meta:
        ordering = ["stage", "title"]

    def __str__(self) -> str:  # pragma: no cover - helper
        return self.title


@dataclass(slots=True)
class SimulationStep:
    """Represents a log message/progress change during task execution."""

    progress: int
    message: str
