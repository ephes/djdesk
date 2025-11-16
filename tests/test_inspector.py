from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django import forms
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from djdesk.inspector import forms as inspector_forms
from djdesk.inspector.command_runner import CommandExecutionError, CommandResult
from djdesk.inspector.forms import TaskRunForm, WorkspaceWizardForm
from djdesk.inspector.models import ScanJob, TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task
from djdesk.inspector.views import DashboardView


class WorkspaceModelTests(TestCase):
    def test_retries_slug_generation_on_collision(self) -> None:
        Workspace.objects.create(
            name="Atlas",
            project_path="/tmp/atlas",
            metadata={"recent_activity": []},
        )
        slug_sequence = ["atlas", "atlas-2"]
        with mock.patch(
            "djdesk.inspector.models.Workspace._generate_unique_slug",
            side_effect=slug_sequence,
        ):
            workspace = Workspace(
                name="Atlas Copy",
                project_path="/tmp/atlas-copy",
                metadata={"recent_activity": []},
            )
            workspace.save()

        self.assertEqual(workspace.slug, "atlas-2")


class TaskPresetModelTests(TestCase):
    def test_rejects_unsafe_commands_via_full_clean(self) -> None:
        preset = TaskPreset(
            key="blocked",
            label="Blocked",
            description="Unsafe command",
            command="python manage.py migrate",
        )
        with self.assertRaises(ValidationError):
            preset.full_clean()
        with self.assertRaises(ValidationError):
            preset.save()


class WorkspaceTaskRunLogTests(TestCase):
    def setUp(self) -> None:
        self.workspace = Workspace.objects.create(
            name="Log Workspace",
            project_path="/tmp/log-workspace",
            metadata={"recent_activity": [], "schema": {"nodes": []}},
        )
        self.preset = TaskPreset.objects.create(
            key="check-log",
            label="Check",
            description="Log flush test",
            command="python manage.py check",
        )

    def test_flush_log_buffer_persists_pending_lines(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        run.append_log("First line")
        self.assertEqual(run.log, "")
        run.flush_log_buffer()
        self.assertIn("First line", run.log)

    def test_auto_flushes_after_batch_size(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        for index in range(run.LOG_BATCH_SIZE):
            run.append_log(f"Line {index}")
        self.assertIn("Line 0", run.log)


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

    def test_rejects_duplicate_project_paths(self) -> None:
        with TemporaryDirectory() as temp:
            project_path = Path(temp)
            (project_path / "manage.py").write_text("# stub manage file")
            Workspace.objects.create(
                name="Existing",
                project_path=str(project_path.resolve()),
                metadata={"recent_activity": []},
            )

            form = WorkspaceWizardForm(
                data={
                    "name": "Duplicate",
                    "project_path": str(project_path),
                    "python_version": "3.14.0",
                    "django_version": "5.2.8",
                    "description": "Duplicate path",
                    "docs_url": "",
                    "auto_run_scan": False,
                    "confirm_readonly": True,
                }
            )
            self.assertFalse(form.is_valid())
            self.assertIn("already managed", str(form.errors.get("project_path", [])))


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

    def test_surface_enqueue_failures(self) -> None:
        form = TaskRunForm(
            data={
                "workspace": self.workspace.slug,
                "preset": self.preset.key,
                "notes": "Run checks",
                "confirm_safe": "on",
            }
        )
        self.assertTrue(form.is_valid())
        with mock.patch("djdesk.inspector.forms.execute_workspace_task") as task_mock:
            task_mock.enqueue.side_effect = RuntimeError("Queue offline")
            with self.assertRaises(forms.ValidationError):
                form.save()

        run = (
            WorkspaceTaskRun.objects.filter(workspace=self.workspace, preset=self.preset)
            .order_by("-pk")
            .first()
        )
        assert run is not None
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("Unable to enqueue", run.log)


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
                "description": "Run safe checks",
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
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def _build_view(self) -> DashboardView:
        view = DashboardView()
        request = self.factory.get("/")
        view.setup(request)
        return view

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

    def test_dashboard_prefers_offline_docs_when_available(self) -> None:
        Workspace.objects.all().delete()
        workspace = Workspace.objects.create(
            name="Docs Workspace",
            project_path="/tmp/docs-workspace",
            metadata={"recent_activity": [], "schema": {"nodes": []}},
        )
        workspace.scans.create(
            kind=ScanJob.Kind.SCHEMA,
            status=ScanJob.Status.COMPLETED,
            summary="Done",
        )
        with TemporaryDirectory() as temp_dir:
            bundle_root = Path(temp_dir)
            (bundle_root / "index.html").write_text("<html>Docs</html>")
            with override_settings(INSPECTOR_DOCS_BUNDLE_ROOT=bundle_root):
                response = self.client.get(reverse("inspector:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Offline bundle", response.content)
        self.assertIn(b'/docs/offline/"', response.content)

    @override_settings(INSPECTOR_DOCS_BASE_URL="https://docs.example.com/en/latest/")
    def test_doc_url_skips_external_hosts(self) -> None:
        view = self._build_view()
        malicious = "https://evil.example.org/en/latest/guide/"
        result = view._doc_url(malicious, offline=True)
        self.assertEqual(result, malicious)

    @override_settings(INSPECTOR_DOCS_BASE_URL="https://docs.example.com/en/latest/")
    def test_doc_url_rejects_traversal(self) -> None:
        view = self._build_view()
        payload = "https://docs.example.com/en/latest/../etc/passwd"
        result = view._doc_url(payload, offline=True)
        self.assertEqual(result, payload)

    @override_settings(INSPECTOR_DOCS_BASE_URL="https://docs.example.com/en/latest/")
    def test_doc_url_rewrites_valid_path(self) -> None:
        view = self._build_view()
        payload = "https://docs.example.com/en/latest/guide/page.html"
        expected = reverse(
            "inspector:docs-offline",
            kwargs={"resource": "guide/page.html"},
        )
        result = view._doc_url(payload, offline=True)
        self.assertEqual(result, expected)


class DataLabAPITests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.override = override_settings(INSPECTOR_DATA_LAB_ROOT=self.temp_dir.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.workspace = Workspace.objects.create(
            name="Data Lab Workspace",
            project_path="/tmp/datalab",
            metadata={"recent_activity": [], "schema": {"nodes": []}},
        )

    def test_export_endpoint_creates_notebook(self) -> None:
        response = self.client.post(
            reverse("inspector:data-lab-export", args=[self.workspace.slug]),
            data={"template": "schema-audit"},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        notebooks = payload["data_lab"]["notebooks"]
        self.assertTrue(notebooks)
        self.assertEqual(notebooks[0]["slug"], "schema-audit")

    def test_notebook_view_renders(self) -> None:
        self.client.post(
            reverse("inspector:data-lab-export", args=[self.workspace.slug]),
            data={"template": "schema-audit"},
        )
        response = self.client.get(
            reverse(
                "inspector:data-lab-notebook",
                args=[self.workspace.slug, "schema-audit"],
            )
        )
        self.assertContains(response, "Schema audit starter")


class OfflineDocsViewTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.bundle_root = Path(self.temp_dir.name)
        (self.bundle_root / "index.html").write_text("<html>Docs</html>")
        fonts = self.bundle_root / "fonts"
        fonts.mkdir()
        (fonts / "sample.woff2").write_bytes(b"font")
        self.override = override_settings(INSPECTOR_DOCS_BUNDLE_ROOT=self.bundle_root)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_serves_index_page(self) -> None:
        response = self.client.get(reverse("inspector:docs-offline-root"))
        self.assertContains(response, "Docs")

    def test_prevents_traversal(self) -> None:
        response = self.client.get(
            reverse("inspector:docs-offline", kwargs={"resource": "../secret.txt"})
        )
        self.assertEqual(response.status_code, 404)

    def test_blocks_absolute_paths(self) -> None:
        response = self.client.get(
            reverse("inspector:docs-offline", kwargs={"resource": "/etc/passwd"})
        )
        self.assertEqual(response.status_code, 404)

    def test_blocks_encoded_traversal(self) -> None:
        response = self.client.get(
            reverse("inspector:docs-offline", kwargs={"resource": "..%2Fsecret.txt"})
        )
        self.assertEqual(response.status_code, 404)

    def test_serves_fonts_with_expected_mimetype(self) -> None:
        response = self.client.get(
            reverse("inspector:docs-offline", kwargs={"resource": "fonts/sample.woff2"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "font/woff2")

class TaskExecutionIntegrationTests(TestCase):
    def setUp(self) -> None:
        self.project_root = Path(__file__).resolve().parents[1]
        self.workspace = Workspace.objects.create(
            name="Repo Workspace",
            project_path=str(self.project_root),
            metadata={"recent_activity": [], "schema": {"nodes": []}},
        )
        self.preset, _ = TaskPreset.objects.get_or_create(
            key="check",
            defaults={
                "label": "Run checks",
                "description": "Execute django check",
                "command": "python manage.py check",
            },
        )

    def test_execute_workspace_task_runs_safe_command(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)

        execute_workspace_task.call(run.pk)
        run.refresh_from_db()

        self.assertEqual(run.status, WorkspaceTaskRun.Status.SUCCEEDED)
        self.assertIn("command", run.metadata)
        command_meta = run.metadata["command"]
        self.assertEqual(command_meta["exit_code"], 0)
        self.assertFalse(command_meta["timed_out"])
        self.assertTrue(run.log)

    def test_rejects_unsafe_commands(self) -> None:
        TaskPreset.objects.bulk_create(
            [
                TaskPreset(
                    key="unsafe-migrate",
                    label="Run migrations",
                    description="Should be blocked",
                    command="python manage.py migrate",
                )
            ]
        )
        preset = TaskPreset.objects.get(key="unsafe-migrate")
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=preset)

        execute_workspace_task.call(run.pk)
        run.refresh_from_db()

        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("not part of", run.log)

    def test_missing_workspace_path(self) -> None:
        broken_workspace = Workspace.objects.create(
            name="Missing path",
            project_path="/tmp/djdesk/missing",
            metadata={"recent_activity": [], "schema": {"nodes": []}},
        )
        run = WorkspaceTaskRun.objects.create(workspace=broken_workspace, preset=self.preset)

        execute_workspace_task.call(run.pk)
        run.refresh_from_db()

        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("does not exist", run.log)

    @mock.patch("djdesk.inspector.tasks.run_command")
    def test_handles_timeout_result(self, mock_run_command: mock.MagicMock) -> None:
        mock_run_command.return_value = CommandResult(
            exit_code=124,
            duration=1.2,
            output_lines=4,
            safe_prefix="python manage.py check",
            timed_out=True,
        )
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)

        execute_workspace_task.call(run.pk)
        run.refresh_from_db()

        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("timed out", run.log)

    @mock.patch(
        "djdesk.inspector.tasks.run_command",
        side_effect=CommandExecutionError("boom"),
    )
    def test_handles_command_execution_errors(
        self, mock_run_command: mock.MagicMock
    ) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)

        execute_workspace_task.call(run.pk)
        run.refresh_from_db()

        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("boom", run.log)
