from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, router, transaction
from django.db.models.deletion import ProtectedError
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone
from django.utils.text import slugify

from .command_runner import (
    CommandExecutionError,
    UnsafeCommandError,
    validate_safe_command,
)

_WORKSPACE_METADATA_SECTIONS = (
    "insights",
    "apps",
    "recent_activity",
    "schema",
    "log_excerpt",
    "status",
)


def _is_object_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(entry, dict) for entry in value)


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
        indexes = [
            models.Index(fields=["project_path"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["project_path"],
                name="inspector_workspace_unique_project_path",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - human readable helper
        return self.name

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None) -> None:
        using = using or router.db_for_write(type(self), instance=self)
        generated_slug = not self.slug
        attempts = 0
        while True:
            if not self.slug:
                self.slug = self._generate_unique_slug(using=using)
            try:
                with transaction.atomic(using=using):
                    super().save(
                        force_insert=force_insert,
                        force_update=force_update,
                        using=using,
                        update_fields=update_fields,
                    )
                return
            except IntegrityError as exc:
                if not generated_slug or not self._is_slug_integrity_error(exc):
                    raise
                attempts += 1
                if attempts >= 5:
                    raise
                # Force a new slug generation and retry.
                self.slug = ""

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.metadata, dict):
            raise ValidationError({"metadata": "Enter a JSON object (use {} for empty metadata)."})
        normalized = (self.project_path or "").strip()
        if not normalized:
            raise ValidationError({"project_path": "Project path cannot be empty."})
        self.project_path = normalized
        using = router.db_for_write(type(self), instance=self)
        exists = (
            Workspace.objects.using(using)
            .exclude(pk=self.pk)
            .filter(project_path=normalized)
            .exists()
        )
        if exists:
            raise ValidationError({"project_path": "A workspace already inspects this folder."})

    def _generate_unique_slug(self, *, using: str) -> str:
        base = slugify(self.name) or "workspace"
        candidate = base
        suffix = 1
        while Workspace.objects.using(using).filter(slug=candidate).exclude(pk=self.pk).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    def _is_slug_integrity_error(self, exc: IntegrityError) -> bool:
        cause = exc.__cause__
        if isinstance(cause, sqlite3.IntegrityError):
            return (
                getattr(cause, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
                and str(cause) == f"UNIQUE constraint failed: {self._meta.db_table}.slug"
            )
        # psycopg 3 exposes sqlstate; psycopg2 exposes pgcode.
        code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        diagnostic = getattr(cause, "diag", None)
        return (
            code == "23505"
            and getattr(diagnostic, "table_name", None) == self._meta.db_table
            and getattr(diagnostic, "constraint_name", None) == f"{self._meta.db_table}_slug_key"
        )

    def _metadata_section(self, key: str) -> Any:
        if not isinstance(self.metadata, dict):
            return None
        value = self.metadata.get(key)
        if key == "status":
            return value if isinstance(value, dict) else None
        if key == "schema":
            if not isinstance(value, dict):
                return None
            nodes = value.get("nodes", [])
            if not _is_object_list(nodes) or not _is_object_list(value.get("connections", [])):
                return None
            if any(
                not isinstance(node.get(field, []), list)
                for node in nodes
                for field in ("fields", "relations")
            ):
                return None
            return value
        return value if _is_object_list(value) else None

    @property
    def metadata_needs_repair(self) -> bool:
        return not isinstance(self.metadata, dict) or any(
            key in self.metadata and self._metadata_section(key) is None
            for key in _WORKSPACE_METADATA_SECTIONS
        )

    @property
    def status_metadata(self) -> dict[str, Any]:
        return self._metadata_section("status") or {}

    @property
    def insights(self) -> list[dict[str, Any]]:
        return list(self._metadata_section("insights") or [])

    @property
    def app_overview(self) -> list[dict[str, Any]]:
        return list(self._metadata_section("apps") or [])

    @property
    def recent_activity(self) -> list[dict[str, Any]]:
        return list(self._metadata_section("recent_activity") or [])

    @property
    def schema_graph(self) -> dict[str, Any]:
        return self._metadata_section("schema") or {}

    @property
    def log_excerpt(self) -> list[dict[str, Any]]:
        return list(self._metadata_section("log_excerpt") or [])


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
        indexes = [
            models.Index(
                fields=["workspace", "-created_at", "-id"], name="scan_workspace_recent_idx"
            ),
        ]

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

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        command = (self.command or "").strip()
        if not command:
            raise ValidationError({"command": "Command cannot be empty."})
        try:
            validate_safe_command(command)
        except (UnsafeCommandError, CommandExecutionError) as exc:
            raise ValidationError({"command": str(exc)}) from exc


class TaskRunRetired(RuntimeError):
    """The worker no longer owns the persisted running attempt."""


class WorkspaceTaskRun(models.Model):
    """Instance of a `django-tasks` command associated with a workspace."""

    LOG_BATCH_SIZE = 5
    LOG_TRUNCATION_MARKER = "[Output truncated: retained log byte budget exhausted.]"

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
    # Inputs recorded when the confirmed run is saved; the worker runs these, not later edits.
    confirmed_command = models.CharField(max_length=255, blank=True, editable=False)
    confirmed_workspace_path = models.CharField(max_length=500, blank=True, editable=False)
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
        indexes = [
            models.Index(
                fields=["workspace", "-created_at", "-id"], name="task_workspace_recent_idx"
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - helper
        return f"{self.preset.label} ({self.get_status_display()})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self._state.adding and not self.confirmed_command:
            # Record the command and project current when this run is requested, reading
            # uncached relations from the database the run is written to.
            using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
            preset = (
                self.preset
                if WorkspaceTaskRun.preset.is_cached(self)
                else TaskPreset.objects.using(using).get(pk=self.preset_id)
            )
            workspace = (
                self.workspace
                if WorkspaceTaskRun.workspace.is_cached(self)
                else Workspace.objects.using(using).get(pk=self.workspace_id)
            )
            self.confirmed_command = (preset.command or "").strip()
            self.confirmed_workspace_path = workspace.project_path
        super().save(*args, **kwargs)

    def mark_running(self) -> bool:
        """Atomically claim a requested run without holding a command-time lock."""
        started_at = timezone.now()
        claimed = (
            type(self)
            .objects.using(self._state.db)
            .filter(pk=self.pk, status=self.Status.REQUESTED)
            .update(
                status=self.Status.RUNNING,
                started_at=started_at,
            )
        )
        if claimed:
            self.status = self.Status.RUNNING
            self.started_at = started_at
        return bool(claimed)

    def mark_finished(self, *, success: bool, command: dict[str, Any] | None = None) -> None:
        self.flush_log_buffer()
        values: dict[str, Any] = {
            "status": self.Status.SUCCEEDED if success else self.Status.FAILED,
            "completed_at": timezone.now(),
            "progress": 100,
        }
        if command is not None:
            values["metadata"] = {**(self.metadata or {}), "command": command}
        self._publish_values(**values)
        for field, value in values.items():
            setattr(self, field, value)

    def publish_fields(self, *fields: str) -> None:
        """Publish only while this instance's claim still owns the row."""
        self._publish_values(**{field: getattr(self, field) for field in fields})

    def _publish_values(self, **values: Any) -> None:
        changed = (
            type(self)
            .objects.using(self._state.db)
            .filter(
                pk=self.pk,
                status=self.Status.RUNNING
                if self.started_at is not None
                else self.Status.REQUESTED,
                started_at=self.started_at,
            )
            .update(**values)
        )
        if not changed:
            raise TaskRunRetired("Workspace task attempt has been retired.")

    @property
    def log_truncated(self) -> bool:
        """Whether the retained log hit ``INSPECTOR_TASK_LOG_BYTES`` and dropped output."""
        return getattr(self, "_log_truncated", False)

    @classmethod
    def clamp_log(cls, text: str, limit: int) -> str:
        """
        Return ``text`` unchanged when it fits ``limit`` bytes, else its head plus the marker.

        The result never exceeds ``limit`` bytes; the head is cut on a character boundary and
        only as far as needed for the marker line to fit.
        """
        raw = text.encode("utf-8", errors="replace")
        if len(raw) <= limit:
            return text
        limit = max(limit, 0)
        marker = cls.LOG_TRUNCATION_MARKER
        keep = max(limit - len(marker.encode("utf-8")) - 1, 0)
        head = raw[:keep].decode("utf-8", errors="ignore").rstrip("\n")
        clamped = f"{head}\n{marker}" if head else marker
        # A budget smaller than the marker itself still holds.
        return clamped.encode("utf-8")[:limit].decode("utf-8", errors="ignore")

    def append_log(self, message: str) -> None:
        """
        Buffer one timestamped line, keeping the whole log within ``INSPECTOR_TASK_LOG_BYTES``.

        A line is stored whole whenever the complete log still fits the budget. Only the line
        that really exceeds it truncates the log: the tail is cut just far enough to end with
        the truncation marker and every later line is dropped; callers check ``log_truncated``.
        """
        if self.log_truncated:
            return
        stamp = timezone.now().strftime("%H:%M:%S")
        new_line = f"[{stamp}] {message}".encode("utf-8", errors="replace").decode("utf-8")
        size = len(new_line.encode("utf-8"))
        buffer = getattr(self, "_log_buffer", None)
        if buffer is None:
            buffer = []
            self._log_buffer = buffer
        used = getattr(self, "_log_bytes", None)
        if used is None:
            used = len("\n".join(part for part in [self.log, *buffer] if part).encode("utf-8"))
        limit = settings.INSPECTOR_TASK_LOG_BYTES
        # Each stored line costs its bytes plus the newline joining it to the text before it.
        if size > limit - used - (1 if used else 0):
            # Mark first so a failing save cannot lead to further appends.
            self._log_truncated = True
            complete = "\n".join(part for part in [self.log, *buffer, new_line] if part)
            self._log_buffer = []
            self.log = self.clamp_log(complete, limit)
            self._log_bytes = len(self.log.encode("utf-8"))
            self.publish_fields("log")
            return
        buffer.append(new_line)
        self._log_bytes = used + (1 if used else 0) + size
        if len(buffer) >= self.LOG_BATCH_SIZE:
            self.flush_log_buffer()

    def flush_log_buffer(self) -> None:
        buffer = getattr(self, "_log_buffer", None)
        if not buffer:
            return
        chunk = "\n".join(buffer)
        self._log_buffer = []
        if self.log:
            self.log = f"{self.log}\n{chunk}"
        else:
            self.log = chunk
        self.publish_fields("log")


@receiver(pre_delete, sender=Workspace)
@receiver(pre_delete, sender=TaskPreset)
def protect_active_task_runs(sender, instance, using, **kwargs):
    """Protect the recovery handle at both model and bulk deletion boundaries."""
    # Collector sends pre_delete inside its writer transaction, before any deletes.
    # The parent lock also fences concurrent FK insertions on PostgreSQL. SQLite
    # serializes writers (a conflicting snapshot must fail rather than erase a run).
    sender.objects.using(using).filter(pk=instance.pk).select_for_update().first()
    field = "workspace_id" if sender is Workspace else "preset_id"
    active = list(
        WorkspaceTaskRun.objects.using(using).filter(
            **{field: instance.pk},
            status__in=(WorkspaceTaskRun.Status.REQUESTED, WorkspaceTaskRun.Status.RUNNING),
        )
    )
    if active:
        raise ProtectedError("Cannot delete a parent with requested or running task runs.", active)


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
