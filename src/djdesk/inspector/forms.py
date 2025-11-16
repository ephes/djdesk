from __future__ import annotations

from pathlib import Path

from django import forms

from . import services
from .models import TaskPreset, Workspace, WorkspaceTaskRun
from .tasks import execute_workspace_task

PROTECTED_PATHS: list[Path] = [
    Path("/etc"),
    Path("/sys"),
    Path("/proc"),
    Path.home() / ".ssh",
]


def _is_protected_path(path: Path) -> bool:
    """Return True if ``path`` points inside a protected system directory."""
    for raw in PROTECTED_PATHS:
        base = raw.expanduser()
        try:
            base_resolved = base.resolve(strict=False)
        except OSError:
            base_resolved = base
        if path == base_resolved:
            return True
        try:
            if path.is_relative_to(base_resolved):
                return True
        except ValueError:
            continue
    return False


class WorkspaceWizardForm(forms.ModelForm):
    """Validates user-provided project paths before creating a workspace."""

    auto_run_scan = forms.BooleanField(
        required=False,
        initial=True,
        label="Kick off schema + log scans immediately",
    )
    confirm_readonly = forms.BooleanField(
        required=True,
        initial=True,
        label="I understand DJDesk only performs read-only inspection commands.",
        error_messages={"required": "Confirmation is required to continue."},
    )

    class Meta:
        model = Workspace
        fields = [
            "name",
            "project_path",
            "python_version",
            "django_version",
            "description",
            "docs_url",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "docs_url": forms.URLInput(attrs={"placeholder": "https://"}),
        }

    def clean_project_path(self) -> str:
        raw_path = self.cleaned_data["project_path"]
        candidate = Path(raw_path).expanduser()

        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise forms.ValidationError("Path does not exist on disk.") from exc
        except PermissionError as exc:
            raise forms.ValidationError("Permission denied while accessing this path.") from exc
        except OSError as exc:  # includes too many symlinks, etc.
            raise forms.ValidationError("Unable to inspect the selected path.") from exc

        if not resolved.is_dir():
            raise forms.ValidationError("Project path must be a folder.")
        if _is_protected_path(resolved):
            raise forms.ValidationError("Cannot inspect protected system directories.")
        if not (resolved / "manage.py").exists():
            raise forms.ValidationError("manage.py not found in the provided folder.")
        normalized = str(resolved)
        existing_qs = Workspace.objects.filter(project_path=normalized)
        if self.instance.pk:
            existing_qs = existing_qs.exclude(pk=self.instance.pk)
        if existing_qs.exists():
            raise forms.ValidationError("This folder is already managed by another workspace.")

        return normalized

    def save(self, commit: bool = True) -> Workspace:
        workspace = super().save(commit=False)
        workspace.manage_py_detected = True
        metadata = workspace.metadata or {}
        metadata.setdefault("recent_activity", [])
        metadata["recent_activity"].insert(
            0,
            {
                "kind": "onboarding",
                "message": "Workspace imported via wizard",
                "timestamp": services.format_timestamp(),
                "status": "success",
            },
        )
        workspace.metadata = metadata

        if commit:
            workspace.save()
            services.bootstrap_workspace_scans(
                workspace,
                auto_run=self.cleaned_data.get("auto_run_scan", True),
            )
        return workspace


class TaskRunForm(forms.Form):
    """Creates a WorkspaceTaskRun backed by django-tasks."""

    workspace = forms.ModelChoiceField(
        queryset=Workspace.objects.all(),
        to_field_name="slug",
        label="Workspace",
    )
    preset = forms.ModelChoiceField(
        queryset=TaskPreset.objects.all(),
        to_field_name="key",
        label="Task",
    )
    notes = forms.CharField(
        required=False,
        max_length=280,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional notes"}),
    )
    confirm_safe = forms.BooleanField(
        required=True,
        initial=False,
        label="Allow DJDesk to run the pre-approved command.",
        error_messages={"required": "Confirmation is required to dispatch a task."},
    )

    def save(self) -> WorkspaceTaskRun:
        workspace: Workspace = self.cleaned_data["workspace"]
        preset: TaskPreset = self.cleaned_data["preset"]

        run = WorkspaceTaskRun.objects.create(
            workspace=workspace,
            preset=preset,
            requested_by=self.initial.get("requested_by", "inspector"),
            metadata={"notes": self.cleaned_data.get("notes", "")},
        )
        try:
            result = execute_workspace_task.enqueue(run.pk)
        except Exception as exc:
            run.status = WorkspaceTaskRun.Status.FAILED
            run.append_log("Unable to enqueue task; please try again later.")
            run.flush_log_buffer()
            run.save(update_fields=["status"])
            raise forms.ValidationError(
                {"__all__": ["Unable to dispatch the requested task."]}
            ) from exc

        run.result_id = result.id
        run.save(update_fields=["result_id"])
        return run
