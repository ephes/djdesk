from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from unittest import mock

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connections, transaction
from django.test import TransactionTestCase
from django.urls import reverse

from djdesk.inspector.command_runner import CommandResult
from djdesk.inspector.forms import TaskRunForm
from djdesk.inspector.models import TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task


class TaskLifecycleTests(TransactionTestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Synthetic", project_path="/synthetic")
        self.preset = TaskPreset.objects.create(
            key="synthetic", label="Check", description="Test", command="python manage.py check"
        )

    def form(self):
        form = TaskRunForm(
            data={"workspace": self.workspace.slug, "preset": self.preset.key, "confirm_safe": "on"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_rollback_never_dispatches(self):
        with mock.patch("djdesk.inspector.forms.execute_workspace_task") as task:
            task.enqueue.return_value.id = "queued-result"
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with transaction.atomic():
                    self.form().save()
                    raise RuntimeError("rollback")
            task.enqueue.assert_not_called()
        self.assertFalse(WorkspaceTaskRun.objects.exists())

    def test_commit_dispatches_once_after_other_connection_can_read(self):
        seen = []

        def read_run(pk):
            try:
                return WorkspaceTaskRun.objects.get(pk=pk).status
            finally:
                connections["default"].close()

        def enqueue(pk):
            self.assertFalse(connections["default"].in_atomic_block)
            with ThreadPoolExecutor(max_workers=1) as pool:
                seen.append(pool.submit(read_run, pk).result(timeout=5))
            execute_workspace_task.call(pk)
            return mock.Mock(id="queued-result")

        with (
            mock.patch(
                "djdesk.inspector.forms.execute_workspace_task", new=mock.Mock(enqueue=enqueue)
            ),
            mock.patch(
                "djdesk.inspector.tasks.run_command",
                return_value=CommandResult(
                    exit_code=0, duration=0.1, output_lines=0, safe_prefix=self.preset.command
                ),
            ) as command,
        ):
            with transaction.atomic():
                run = self.form().save()
                self.assertEqual(seen, [])
                self.assertEqual(run.status, WorkspaceTaskRun.Status.REQUESTED)
            self.assertEqual(seen, [WorkspaceTaskRun.Status.REQUESTED])
            command.assert_called_once()
        run.refresh_from_db()
        self.assertEqual(run.result_id, "queued-result")
        self.assertEqual(run.status, "succeeded")

    def test_enqueue_error_cannot_overwrite_claimed_or_finished_run(self):
        for finished in (False, True):

            def enqueue(pk):
                run = WorkspaceTaskRun.objects.get(pk=pk)
                run.mark_running()
                run.append_log("Retained output")
                run.flush_log_buffer()
                if finished:
                    run.mark_finished(success=True)
                raise RuntimeError("backend raised after execution")

            with mock.patch(
                "djdesk.inspector.forms.execute_workspace_task", new=mock.Mock(enqueue=enqueue)
            ):
                run = self.form().save()
            expected = "succeeded" if finished else "running"
            self.assertEqual(run.status, expected)
            self.assertIn("Retained output", run.log)

    def test_unclaimed_enqueue_failure_is_settled(self):
        with mock.patch(
            "djdesk.inspector.forms.execute_workspace_task",
            new=mock.Mock(enqueue=mock.Mock(side_effect=RuntimeError)),
        ):
            with self.assertRaises(ValidationError):
                self.form().save()
        run = WorkspaceTaskRun.objects.get()
        self.assertEqual(run.status, "failed")
        self.assertIsNotNone(run.completed_at)
        self.assertIn("Unable to enqueue", run.log)

    def test_explicit_recovery_retains_log_and_never_replays(self):
        run = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        run.mark_running()
        run.append_log("Saved output")
        run.flush_log_buffer()
        original_log = run.log
        # The command loads persisted state, as it would after a worker restart.
        call_command(
            "recover_task_runs",
            run_id=[run.pk],
            confirm_workers_stopped=True,
            reason="All owning workers stopped after interruption",
            stdout=StringIO(),
        )
        run.refresh_from_db()
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.log, original_log)
        self.assertTrue(run.metadata["command"]["interrupted"])
        self.assertIsNotNone(run.completed_at)
        with mock.patch("djdesk.inspector.tasks.run_command") as command:
            self.assertTrue(execute_workspace_task.call(run.pk)["interrupted"])
            command.assert_not_called()
        with self.assertRaises(CommandError):
            call_command(
                "recover_task_runs",
                run_id=[run.pk],
                confirm_workers_stopped=True,
                reason="Stopped",
                stdout=StringIO(),
            )

    def test_recovery_requires_confirmation_and_only_selected_running_rows(self):
        selected = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        other = WorkspaceTaskRun.objects.create(workspace=self.workspace, preset=self.preset)
        selected.mark_running()
        other.mark_running()
        for options in (
            {"run_id": [selected.pk], "reason": "Stopped"},
            {"run_id": [selected.pk], "confirm_workers_stopped": True},
            {"run_id": [selected.pk, 999999], "confirm_workers_stopped": True, "reason": "Stopped"},
        ):
            with self.assertRaises(CommandError):
                call_command("recover_task_runs", stdout=StringIO(), **options)
        selected.refresh_from_db()
        self.assertEqual(selected.status, "running")
        call_command(
            "recover_task_runs",
            run_id=[selected.pk],
            confirm_workers_stopped=True,
            reason="Stopped",
            stdout=StringIO(),
        )
        other.refresh_from_db()
        self.assertEqual(other.status, "running")

    def test_view_reports_immediate_enqueue_failure(self):
        with mock.patch(
            "djdesk.inspector.forms.execute_workspace_task",
            new=mock.Mock(enqueue=mock.Mock(side_effect=RuntimeError)),
        ):
            response = self.client.post(
                reverse("inspector:task-run-create"),
                {"workspace": self.workspace.slug, "preset": self.preset.key, "confirm_safe": "on"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unable to dispatch", response.content.decode())
        self.assertEqual(WorkspaceTaskRun.objects.get().status, "failed")
