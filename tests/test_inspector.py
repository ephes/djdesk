from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django import forms
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from djdesk.inspector import data_lab
from djdesk.inspector import forms as inspector_forms
from djdesk.inspector.command_runner import CommandExecutionError, CommandResult
from djdesk.inspector.forms import TaskRunForm, WorkspaceWizardForm
from djdesk.inspector.models import ScanJob, TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task
from djdesk.inspector.views import DashboardView


def _approved_run(workspace, preset):
    return WorkspaceTaskRun.objects.create(
        workspace=workspace,
        preset=preset,
        metadata={
            "approval": {
                "command": preset.command,
                "workspace_id": workspace.pk,
                "workspace_path": workspace.project_path,
            }
        },
    )


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

    @override_settings(INSPECTOR_TASK_LOG_BYTES=256)
    def test_retained_log_budget_counts_timestamps_and_newlines(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        for _ in range(100):
            run.append_log("tiny line")
        self.assertTrue(run.log_truncated)
        run.refresh_from_db()
        self.assertLessEqual(len(run.log.encode()), 256)
        self.assertGreater(len(run.log.encode()), 256 - 40)
        self.assertTrue(run.log.endswith(WorkspaceTaskRun.LOG_TRUNCATION_MARKER))
        self.assertEqual(run.log.count(WorkspaceTaskRun.LOG_TRUNCATION_MARKER), 1)

    @override_settings(INSPECTOR_TASK_LOG_BYTES=256)
    def test_single_large_log_line_is_cut_on_a_character_boundary(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        run.append_log("é" * 1_000_000)
        self.assertTrue(run.log_truncated)
        run.refresh_from_db()
        self.assertLessEqual(len(run.log.encode()), 256)
        self.assertIn("é", run.log)
        self.assertTrue(run.log.endswith(WorkspaceTaskRun.LOG_TRUNCATION_MARKER))

    @override_settings(INSPECTOR_TASK_LOG_BYTES=256)
    def test_log_budget_includes_text_stored_before_this_instance(self) -> None:
        run = WorkspaceTaskRun.objects.create(
            workspace=self.workspace, preset=self.preset, log="x" * 200
        )
        run = WorkspaceTaskRun.objects.get(pk=run.pk)
        run.append_log("y" * 100)
        self.assertTrue(run.log_truncated)
        run.refresh_from_db()
        self.assertLessEqual(len(run.log.encode()), 256)

    @override_settings(INSPECTOR_TASK_LOG_BYTES=256)
    def test_line_that_fits_the_budget_is_stored_whole(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        message = "x" * (210 - len("[00:00:00] "))
        run.append_log(message)
        run.flush_log_buffer()
        self.assertFalse(run.log_truncated)
        run.refresh_from_db()
        self.assertEqual(len(run.log.encode()), 210)
        self.assertTrue(run.log.endswith(message))

    @override_settings(INSPECTOR_TASK_LOG_BYTES=256)
    def test_log_filling_the_budget_exactly_is_not_truncated(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        stamp = len("[00:00:00] ")
        run.append_log("a" * (100 - stamp))
        run.append_log("b" * (155 - stamp))
        run.flush_log_buffer()
        self.assertFalse(run.log_truncated)
        run.refresh_from_db()
        self.assertEqual(len(run.log.encode()), 256)
        self.assertNotIn(WorkspaceTaskRun.LOG_TRUNCATION_MARKER, run.log)

    @override_settings(INSPECTOR_TASK_LOG_BYTES=256)
    def test_overflow_after_a_full_log_makes_room_for_the_marker(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        stamp = len("[00:00:00] ")
        run.append_log("a" * (100 - stamp))
        run.append_log("b" * (155 - stamp))
        run.flush_log_buffer()
        run.append_log("c")
        self.assertTrue(run.log_truncated)
        run.refresh_from_db()
        self.assertLessEqual(len(run.log.encode()), 256)
        self.assertTrue(run.log.endswith(WorkspaceTaskRun.LOG_TRUNCATION_MARKER))
        self.assertEqual(run.log.count(WorkspaceTaskRun.LOG_TRUNCATION_MARKER), 1)
        self.assertIn("a" * (100 - stamp), run.log)

    @override_settings(INSPECTOR_TASK_LOG_BYTES=20)
    def test_budget_smaller_than_the_marker_still_holds(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        run.append_log("a line longer than the budget")
        self.assertTrue(run.log_truncated)
        run.refresh_from_db()
        self.assertLessEqual(len(run.log.encode()), 20)

    def test_log_within_budget_is_not_truncated(self) -> None:
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        for index in range(12):
            run.append_log(f"Line {index}")
        run.flush_log_buffer()
        self.assertFalse(run.log_truncated)
        run.refresh_from_db()
        self.assertEqual(len(run.log.splitlines()), 12)


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

    def confirmed_form(self):
        form = TaskRunForm(
            data={
                "workspace": self.workspace.slug,
                "preset": self.preset.key,
                "confirm_safe": "on",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_queue_uses_approved_command_and_path_after_edits(self):
        with mock.patch("djdesk.inspector.forms.execute_workspace_task") as dispatch:
            dispatch.enqueue.return_value.id = "queued"
            run = self.confirmed_form().save()
        original_path = self.workspace.project_path
        TaskPreset.objects.filter(pk=self.preset.pk).update(
            command="python manage.py showmigrations"
        )
        Workspace.objects.filter(pk=self.workspace.pk).update(project_path="/changed")
        result = CommandResult(
            exit_code=0, duration=0, output_lines=0, safe_prefix="python manage.py check"
        )
        with mock.patch("djdesk.inspector.tasks.run_command", return_value=result) as command:
            execute_workspace_task.call(run.pk)
        self.assertEqual(command.call_args.kwargs["command"], "python manage.py check")
        self.assertEqual(command.call_args.kwargs["workspace_path"], original_path)
        run.refresh_from_db()
        self.assertEqual(run.metadata["command"]["raw"], "python manage.py check")
        self.assertEqual(run.metadata["command"]["workspace_path"], original_path)

    def test_legacy_queue_without_approval_is_refused(self):
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        with mock.patch("djdesk.inspector.tasks.run_command") as command:
            execute_workspace_task.call(run.pk)
        command.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("Missing task approval", run.log)

    @override_settings(INSPECTOR_SAFE_COMMANDS=[])
    def test_snapshot_still_obeys_current_safety_policy(self):
        run = _approved_run(self.workspace, self.preset)
        with mock.patch("djdesk.inspector.tasks.run_command") as command:
            execute_workspace_task.call(run.pk)
        command.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)

    def test_enqueue_error_preserves_claimed_and_completed_runs(self):
        for complete in (False, True):
            with self.subTest(complete=complete):
                captured = []

                def dispatch(pk):
                    run = WorkspaceTaskRun.objects.get(pk=pk)
                    self.assertTrue(run.mark_running())
                    run.append_log("Worker claimed")
                    run.flush_log_buffer()
                    if complete:
                        run.mark_finished(success=True)
                    captured.append((run.status, run.started_at, run.completed_at, run.log))
                    raise RuntimeError("Lost acknowledgement")

                with mock.patch("djdesk.inspector.forms.execute_workspace_task") as task:
                    task.enqueue.side_effect = dispatch
                    with self.assertRaises(forms.ValidationError):
                        self.confirmed_form().save()
                run = WorkspaceTaskRun.objects.latest("pk")
                self.assertEqual(
                    (run.status, run.started_at, run.completed_at, run.log), captured[0]
                )

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
        self.assertEqual(run.progress, 100)
        self.assertIsNotNone(run.completed_at)
        with mock.patch("djdesk.inspector.tasks.run_command") as command:
            execute_workspace_task.call(run.pk)
        command.assert_not_called()


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
        response = Client().get(reverse("inspector:workspace-status", args=[self.workspace.slug]))
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

    def test_reexport_preserves_edits_and_existing_suffix(self):
        first = data_lab.export_notebook(self.workspace, "schema-audit")
        first.write_text('{"cells": [], "metadata": {"edited": true}}')
        original = first.read_bytes()
        existing = first.with_name("schema-audit-2.ipynb")
        existing.write_text('{"cells": [], "metadata": {}}')
        existing_bytes = existing.read_bytes()
        response = self.client.post(
            reverse("inspector:data-lab-export", args=[self.workspace.slug]),
            data={"template": "schema-audit"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(existing.read_bytes(), existing_bytes)
        payload = response.json()["data_lab"]
        self.assertEqual(payload["selected_notebook"], "schema-audit-3")
        self.assertEqual(len(payload["notebooks"]), 3)
        fresh = next(nb for nb in payload["notebooks"] if nb["slug"] == "schema-audit-3")
        self.assertContains(self.client.get(fresh["viewer_url"]), "Schema audit starter")
        self.assertNotEqual(
            first.read_bytes(), first.with_name("schema-audit-3.ipynb").read_bytes()
        )

    def test_edited_notebook_metadata_does_not_break_viewer(self):
        path = data_lab.export_notebook(self.workspace, "schema-audit")
        suffixed = path.with_name("schema-audit-2.ipynb")
        for metadata in (None, [], "edited", {"djdesk_template": []}):
            with self.subTest(metadata=metadata):
                suffixed.write_text(json.dumps({"cells": [], "metadata": metadata}))
                response = self.client.get(
                    reverse(
                        "inspector:data-lab-notebook", args=[self.workspace.slug, "schema-audit-2"]
                    )
                )
                self.assertContains(response, "Schema audit starter")
                listing = data_lab.list_workspace_exports(self.workspace)
                entry = next(nb for nb in listing if nb["slug"] == "schema-audit-2")
                self.assertEqual(response.context["notebook_title"], entry["title"])

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

    def _symlink(self, path, target, *, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"Filesystem symlinks unavailable: {type(exc).__name__}")

    def test_notebook_symlinks_and_nonregular_exports_are_unavailable(self):
        directory = data_lab.workspace_data_lab_dir(self.workspace)
        outside = Path(self.temp_dir.name) / "outside.json"
        outside.write_text('{"cells": [], "private": "synthetic secret"}')
        internal = directory / "ordinary.ipynb"
        internal.write_text('{"cells": []}')
        self._symlink(directory / "external.ipynb", outside)
        self._symlink(directory / "internal.ipynb", internal)
        (directory / "directory.ipynb").mkdir()
        for fallback in (False, True):
            with self.subTest(fallback=fallback):
                with mock.patch.object(
                    data_lab.os,
                    "supports_dir_fd",
                    set() if fallback else data_lab.os.supports_dir_fd,
                ):
                    self.assertEqual(
                        [
                            entry["slug"]
                            for entry in data_lab.list_workspace_exports(self.workspace)
                        ],
                        ["ordinary"],
                    )
                    for slug in ("external", "internal", "directory", "missing", "../outside"):
                        with self.assertRaises(FileNotFoundError):
                            data_lab.load_notebook(self.workspace, slug)
                    self.assertEqual(
                        data_lab.load_notebook(self.workspace, "ordinary"), {"cells": []}
                    )
        response = self.client.get(
            reverse("inspector:data-lab-notebook", args=[self.workspace.slug, "external"])
        )
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b"synthetic secret", response.content)

    def test_workspace_directory_symlink_cannot_escape_root(self):
        with TemporaryDirectory() as other:
            outside = Path(other)
            notebook = outside / "schema-audit.ipynb"
            notebook.write_text('{"cells": [], "private": "synthetic secret"}')
            original = notebook.read_bytes()
            self._symlink(Path(self.temp_dir.name) / self.workspace.slug, outside, directory=True)
            self.assertEqual(data_lab.list_workspace_exports(self.workspace), [])
            with self.assertRaises(FileNotFoundError):
                data_lab.load_notebook(self.workspace, "schema-audit")
            response = self.client.get(
                reverse("inspector:data-lab-notebook", args=[self.workspace.slug, "schema-audit"])
            )
            self.assertEqual(response.status_code, 404)
            response = self.client.post(
                reverse("inspector:data-lab-export", args=[self.workspace.slug]),
                data={"template": "schema-audit"},
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(notebook.read_bytes(), original)
            self.assertEqual(list(outside.iterdir()), [notebook])

    def test_portable_open_rejects_substituted_file_descriptor(self):
        path = data_lab.export_notebook(self.workspace, "schema-audit")
        outside = Path(self.temp_dir.name) / "outside.json"
        outside.write_text('{"cells": [], "private": "synthetic secret"}')
        open_file = data_lab.os.open

        def substitute_file(target, flags, *args, **kwargs):
            return open_file(outside if target == path else target, flags, *args, **kwargs)

        with (
            mock.patch.object(data_lab.os, "supports_dir_fd", set()),
            mock.patch.object(data_lab.os, "open", side_effect=substitute_file),
            mock.patch.object(data_lab.json, "loads") as decode,
        ):
            with self.assertRaises(FileNotFoundError):
                data_lab.load_notebook(self.workspace, path.stem)
            decode.assert_not_called()

    @override_settings(INSPECTOR_DATA_LAB_LIVE=True)
    def test_malformed_edited_notebooks_show_repair_page_without_changing_bytes(self):
        path = data_lab.export_notebook(self.workspace, "schema-audit")
        url = reverse("inspector:data-lab-notebook", args=[self.workspace.slug, path.stem])
        invalid = [
            b"",
            b'{"cells": [',
            b"\xff",
            b"[]",
            b"null",
            b'{"cells": null}',
            b'{"cells": {}}',
            b'{"cells": [1]}',
            b'{"cells": [{"source": [1]}]}',
            b'{"cells": [{"source": null}]}',
            b'{"cells": [{"cell_type": "code", "outputs": {}}]}',
            b'{"cells": [{"cell_type": "code", "outputs": [null]}]}',
            b'{"cells": [{"cell_type": "code", "outputs": [{"text": [2]}]}]}',
            b'{"cells": [{"cell_type": "code", "outputs": [{"text": null}]}]}',
        ]
        for content in invalid:
            with self.subTest(content=content):
                path.write_bytes(content)
                response = self.client.get(url)
                self.assertContains(response, "Notebook needs repair", status_code=422)
                self.assertContains(response, "Repair the file", status_code=422)
                self.assertNotContains(response, "Live kernels are disabled", status_code=422)
                self.assertNotIn(b"Traceback", response.content)
                self.assertLess(len(response.content), 6000)
                self.assertEqual(path.read_bytes(), content)

    def test_valid_user_edits_render_text_and_escape_html(self):
        path = data_lab.export_notebook(self.workspace, "schema-audit")
        path.write_text(
            json.dumps(
                {
                    "cells": [
                        {"cell_type": "markdown", "source": "<script>edited</script>"},
                        {
                            "cell_type": "code",
                            "source": ["print('edited')"],
                            "outputs": [
                                {"text": "<b>output</b>"},
                                {"data": {"text/html": "<script>ignored</script>"}},
                            ],
                        },
                    ]
                }
            )
        )
        original = path.read_bytes()
        response = self.client.get(
            reverse("inspector:data-lab-notebook", args=[self.workspace.slug, path.stem])
        )
        self.assertContains(response, "&lt;script&gt;edited&lt;/script&gt;")
        self.assertContains(response, "&lt;b&gt;output&lt;/b&gt;")
        self.assertNotIn(b"<script>", response.content)
        self.assertEqual(path.read_bytes(), original)


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
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project_root = Path(temporary.name)
        (self.project_root / "manage.py").write_text("print('synthetic check')")
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
        run = _approved_run(self.workspace, self.preset)

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
        run = _approved_run(self.workspace, preset)

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
        run = _approved_run(broken_workspace, self.preset)

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
        run = _approved_run(self.workspace, self.preset)

        execute_workspace_task.call(run.pk)
        run.refresh_from_db()

        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("timed out", run.log)

    @mock.patch(
        "djdesk.inspector.tasks.run_command",
        side_effect=CommandExecutionError("boom"),
    )
    def test_handles_command_execution_errors(self, mock_run_command: mock.MagicMock) -> None:
        run = _approved_run(self.workspace, self.preset)

        execute_workspace_task.call(run.pk)
        run.refresh_from_db()

        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("boom", run.log)

    def test_duplicate_deliveries_execute_once(self):
        run = _approved_run(self.workspace, self.preset)
        result = CommandResult(
            exit_code=0, duration=0.1, output_lines=0, safe_prefix=self.preset.command
        )
        with mock.patch("djdesk.inspector.tasks.run_command", return_value=result) as command:
            first = execute_workspace_task.call(run.pk)
            run.refresh_from_db()
            snapshot = (run.started_at, run.completed_at, run.log, run.metadata)
            second = execute_workspace_task.call(run.pk)
        self.assertEqual(first, second)
        self.assertEqual(command.call_count, 1)
        run.refresh_from_db()
        self.assertEqual(snapshot, (run.started_at, run.completed_at, run.log, run.metadata))

    def test_already_running_delivery_is_noop(self):
        run = _approved_run(self.workspace, self.preset)
        run.mark_running()
        snapshot = run.started_at
        with mock.patch("djdesk.inspector.tasks.run_command") as command:
            payload = execute_workspace_task.call(run.pk)
        command.assert_not_called()
        self.assertTrue(payload["skipped"])
        run.refresh_from_db()
        self.assertEqual(run.started_at, snapshot)
        self.assertEqual(run.status, WorkspaceTaskRun.Status.RUNNING)


class ConcurrentClaimTests(TransactionTestCase):
    def test_separate_connections_claim_once(self):
        workspace = Workspace.objects.create(name="Claim", project_path="/tmp/claim")
        preset = TaskPreset.objects.create(
            key="concurrent-check",
            label="Check",
            description="Concurrent claim",
            command="python manage.py check",
        )
        run = WorkspaceTaskRun.objects.create(workspace=workspace, preset=preset)
        barrier = threading.Barrier(2)

        def claim():
            close_old_connections()
            try:
                instance = WorkspaceTaskRun.objects.get(pk=run.pk)
                barrier.wait(timeout=5)
                return instance.mark_running()
            finally:
                connections["default"].close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: claim(), range(2)))
        self.assertEqual(sorted(outcomes), [False, True])
