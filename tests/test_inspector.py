from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import Client, TestCase
from django.urls import reverse

from djdesk.inspector import forms as inspector_forms
from djdesk.inspector.forms import TaskRunForm, WorkspaceWizardForm
from djdesk.inspector.models import ScanJob, TaskPreset, Workspace


class WorkspaceWizardFormTests(TestCase):
    def test_creates_workspace_and_seeds_scans(self) -> None:
        with TemporaryDirectory() as temp:
            project_path = Path(temp)
            (project_path / "manage.py").write_text("# stub manage file")

            form = WorkspaceWizardForm(
                data={
                    "name": "Sample Workspace",
                    "project_path": str(project_path),
                    "python_version": "3.14.0",
                    "django_version": "5.2.8",
                    "description": "Demo workspace",
                    "docs_url": "https://example.com/docs",
                    "auto_run_scan": True,
                    "confirm_readonly": True,
                }
            )
            self.assertTrue(form.is_valid(), form.errors)

            workspace = form.save()
            self.assertTrue(workspace.manage_py_detected)
            self.assertEqual(workspace.scans.count(), len(ScanJob.Kind.choices))
            self.assertEqual(workspace.metadata["recent_activity"][0]["kind"], "onboarding")

    def test_rejects_protected_directories(self) -> None:
        with TemporaryDirectory() as temp:
            protected_path = Path(temp)
            (protected_path / "manage.py").write_text("# stub manage file")
            inspector_forms.PROTECTED_PATHS.append(protected_path)
            try:
                form = WorkspaceWizardForm(
                    data={
                        "name": "Unsafe Workspace",
                        "project_path": str(protected_path),
                        "python_version": "3.14.0",
                        "django_version": "5.2.8",
                        "description": "Should be blocked",
                        "docs_url": "",
                        "auto_run_scan": False,
                        "confirm_readonly": True,
                    }
                )
                self.assertFalse(form.is_valid())
                error_text = str(form.errors.get("project_path", []))
                self.assertIn("protected system directories", error_text)
            finally:
                inspector_forms.PROTECTED_PATHS.remove(protected_path)


class TaskRunFormTests(TestCase):
    def setUp(self) -> None:
        self.workspace = Workspace.objects.create(
            name="Atlas",
            project_path="/tmp/atlas",
            metadata={"recent_activity": []},
        )
        self.preset = TaskPreset.objects.create(
            key="demo",
            label="Demo task",
            description="Test task",
            command="python manage.py check",
        )

    def test_requires_confirmation_checkbox(self) -> None:
        form = TaskRunForm(
            data={
                "workspace": self.workspace.slug,
                "preset": self.preset.key,
                "notes": "Run checks",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("confirm_safe", form.errors)


class InspectorAPIViewTests(TestCase):
    def setUp(self) -> None:
        self.workspace = Workspace.objects.create(
            name="Convexity Studio",
            project_path="/tmp/studio",
            metadata={
                "insights": [{"title": "Apps", "value": "3", "delta": "+1"}],
                "apps": [],
                "recent_activity": [],
                "schema": {"nodes": []},
                "log_excerpt": [],
            },
        )
        ScanJob.objects.create(
            workspace=self.workspace,
            kind=ScanJob.Kind.SCHEMA,
            status=ScanJob.Status.COMPLETED,
            summary="Completed",
            progress=100,
        )
        self.preset, _ = TaskPreset.objects.get_or_create(
            key="check",
            defaults={
                "label": "Run checks",
                "description": "",
                "command": "python manage.py check",
            },
        )

    def test_workspace_status_payload(self) -> None:
        response = Client().get(
            reverse("inspector:workspace-status", args=[self.workspace.slug])
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["workspace"], self.workspace.slug)
        self.assertTrue(data["scans"])

    def test_task_run_create_endpoint(self) -> None:
        client = Client()
        payload = {
            "workspace": self.workspace.slug,
            "preset": self.preset.key,
            "confirm_safe": "on",
        }
        response = client.post(reverse("inspector:task-run-create"), data=payload)
        self.assertEqual(response.status_code, 201)
        self.assertIn("task_run", response.json())


class DashboardViewTests(TestCase):
    def test_dashboard_handles_workspace_without_slug(self) -> None:
        Workspace.objects.all().delete()
        workspace = Workspace.objects.create(
            name="Legacy Workspace",
            project_path="/tmp/legacy",
            metadata={"recent_activity": [], "schema": {"nodes": []}},
        )
        Workspace.objects.filter(pk=workspace.pk).update(slug="")

        response = self.client.get(reverse("inspector:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-status-endpoint=""', response.content)
