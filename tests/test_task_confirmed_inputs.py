from unittest import mock

from django.test import TestCase

from djdesk.inspector.command_runner import CommandResult
from djdesk.inspector.forms import TaskRunForm
from djdesk.inspector.models import TaskPreset, Workspace, WorkspaceTaskRun
from djdesk.inspector.tasks import execute_workspace_task


class ConfirmedTaskInputTests(TestCase):
    def setUp(self):
        self.workspace = Workspace.objects.create(name="Synthetic", project_path="/synthetic/a")
        self.preset = TaskPreset.objects.create(
            key="synthetic", label="Check", description="Test", command="python manage.py check"
        )

    def queue(self) -> WorkspaceTaskRun:
        form = TaskRunForm(
            data={"workspace": self.workspace.slug, "preset": self.preset.key, "confirm_safe": "on"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        with (
            mock.patch("djdesk.inspector.forms.execute_workspace_task") as task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            task.enqueue.return_value.id = "queued"
            run = form.save()
        return run

    def execute(self, run):
        with mock.patch(
            "djdesk.inspector.tasks.run_command",
            return_value=CommandResult(
                exit_code=0, duration=0.1, output_lines=0, safe_prefix="python manage.py check"
            ),
        ) as command:
            payload = execute_workspace_task.call(run.pk)
        run.refresh_from_db()
        return command, payload

    def test_request_records_confirmed_command_and_path(self):
        run = self.queue()
        self.assertEqual(run.confirmed_command, "python manage.py check")
        self.assertEqual(run.confirmed_workspace_path, "/synthetic/a")

    def test_unchanged_request_runs_confirmed_inputs(self):
        run = self.queue()
        command, payload = self.execute(run)
        command.assert_called_once()
        self.assertEqual(command.call_args.kwargs["command"], "python manage.py check")
        self.assertEqual(command.call_args.kwargs["workspace_path"], "/synthetic/a")
        self.assertEqual(run.status, WorkspaceTaskRun.Status.SUCCEEDED)
        self.assertEqual(payload["raw"], "python manage.py check")

    def test_edited_preset_and_workspace_never_execute(self):
        run = self.queue()
        self.preset.command = "python manage.py dumpdata"
        self.preset.save()
        self.workspace.project_path = "/synthetic/b"
        self.workspace.save()
        command, payload = self.execute(run)
        command.assert_not_called()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("changed after confirmation", run.log)
        self.assertEqual(payload["raw"], "python manage.py check")
        self.assertEqual(payload["workspace_path"], "/synthetic/a")

    def test_edited_preset_alone_never_executes(self):
        run = self.queue()
        self.preset.command = "python manage.py dumpdata"
        self.preset.save()
        command, _ = self.execute(run)
        command.assert_not_called()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)

    def test_moved_workspace_alone_never_executes(self):
        run = self.queue()
        self.workspace.project_path = "/synthetic/b"
        self.workspace.save()
        command, _ = self.execute(run)
        command.assert_not_called()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)

    def test_run_queued_without_receipt_never_executes(self):
        run = self.queue()
        # A row queued before receipts existed carries empty confirmed inputs.
        WorkspaceTaskRun.objects.filter(pk=run.pk).update(
            confirmed_command="", confirmed_workspace_path=""
        )
        command, _ = self.execute(run)
        command.assert_not_called()
        self.assertEqual(run.status, WorkspaceTaskRun.Status.FAILED)
        self.assertIn("no confirmed command receipt", run.log)
